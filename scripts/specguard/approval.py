import hashlib
import json
import os
import re
import subprocess
import time

from specguard import core
from specguard import modes as modes_mod
from specguard import paths

_SPEC_TOKEN_RE = re.compile(r"[A-Za-z0-9_./\-]+\.md")


def _marker(spec, sha256):
    return "specguard-approve {}@{}".format(spec, sha256[:7])


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha1(data):
    header = "blob {}\0".format(len(data)).encode("utf-8")
    h = hashlib.sha1()
    h.update(header)
    h.update(data)
    return h.hexdigest()


def _gated(ctx):
    modes = ctx.modes()
    if "hard" in modes:
        return True
    if "feature" in modes and ctx.approval_on():
        return True
    return False


def _classify_role(cfg, agent_type):
    if not agent_type:
        return "executor"
    if agent_type in cfg.roles.tester:
        return "tester"
    if agent_type in cfg.roles.advocate:
        return "advocate"
    return "executor"


# ---------------------------------------------------------------------------
# rule_messages - rule 4 message part and the AskUserQuestion structure check
# ---------------------------------------------------------------------------

def _text_has_mode_or_approval(ctx, text):
    if not text:
        return False
    if modes_mod.would_switch(text):
        return True
    normalized = modes_mod._normalize(text)
    words = normalized.split()
    if 1 <= len(words) <= 2:
        packs = modes_mod.load_packs()
        approval_words = set()
        for pack in packs.values():
            approval_words.update(pack.get("approval_words", []))
        if all(w in approval_words for w in words):
            return True
    for phrase in _all_pack_words(packs=None, key="optout"):
        if normalized == phrase or normalized.startswith(phrase + " "):
            return True
    for phrase in _all_pack_words(packs=None, key="revoke"):
        if normalized == phrase or normalized.startswith(phrase + " "):
            return True
    return False


def _all_pack_words(packs, key):
    packs = packs or modes_mod.load_packs()
    words = set()
    for pack in packs.values():
        words.update(pack.get(key, []) or [])
    return words


def _check_ask_user_question(ctx):
    tool_input = ctx.tool_input or {}
    if "answers" in tool_input:
        return core.deny(
            ctx, "4msg", "specguard: AskUserQuestion may not carry pre-filled answers."
        )
    questions = tool_input.get("questions") or []
    session = ctx.session()
    requests = session.get("requests") or {}
    if not requests:
        return None
    markers = {_marker(spec, entry["sha256"]) for spec, entry in requests.items()}
    for q in questions:
        text = q.get("question") or ""
        mentioned = {m for m in markers if m in text}
        if not mentioned:
            continue
        missing = markers - mentioned
        if missing:
            return core.deny(
                ctx,
                "4msg",
                "specguard: the approval question must carry every open marker, missing {}.".format(
                    ", ".join(sorted(missing))
                ),
            )
        options = q.get("options") or []
        checked = [o for o in options if "✓" in (o.get("label") or "")]
        if len(checked) != 1:
            return core.deny(
                ctx,
                "4msg",
                "specguard: the approval question must carry exactly one option marked with ✓.",
            )
        if len(options) < 2:
            return core.deny(
                ctx,
                "4msg",
                "specguard: the approval question needs at least one option besides ✓.",
            )
    return None


def _resolve_agent_type(ctx, name):
    agents = ctx.state.read_json("agents.json", {}) or {}
    entry = agents.get(name)
    if entry:
        return entry.get("agent_type")
    return None


def _check_send_message(ctx):
    tool_input = ctx.tool_input or {}
    to = tool_input.get("to")
    if to and _gated(ctx):
        agent_type = _resolve_agent_type(ctx, to)
        role = _classify_role(ctx.cfg, agent_type)
        if role in ("tester", "advocate"):
            return core.deny(
                ctx,
                "4msg",
                "specguard: in {} mode a new tester or advocate task is a new Agent launch, "
                "so the gates can check the spec.".format(modes_mod._format_modes(ctx.modes())),
            )
    text = tool_input.get("message") or tool_input.get("prompt") or ""
    if _text_has_mode_or_approval(ctx, text):
        return core.deny(
            ctx,
            "4msg",
            "specguard: prompts the agent sends may not carry mode or approval phrases.",
        )
    return None


def _check_scheduled(ctx):
    tool_input = ctx.tool_input or {}
    text = tool_input.get("prompt") or tool_input.get("message") or ""
    if _text_has_mode_or_approval(ctx, text):
        return core.deny(
            ctx,
            "4msg",
            "specguard: prompts the agent schedules may not carry mode or approval phrases.",
        )
    return None


def rule_messages(ctx):
    if ctx.tool_name == "AskUserQuestion":
        return _check_ask_user_question(ctx)
    if ctx.tool_name == "SendMessage":
        return _check_send_message(ctx)
    if ctx.tool_name in ("CronCreate", "ScheduleWakeup"):
        return _check_scheduled(ctx)
    return None


# ---------------------------------------------------------------------------
# rule_spec_gate - rule 9
# ---------------------------------------------------------------------------

def _extract_spec_refs(ctx, text):
    # A reference is a *.md token (quotes and backticks are already outside
    # the token's character class, so they and any trailing punctuation like
    # "," "." ")" ":" fall away on their own). It counts when, resolved
    # against the repo dir, the project root or the call's own cwd in turn,
    # it lands on a non-README *.md file under <repo>/<specs_dir> - covering
    # repo-relative, project-relative (repo-prefixed), absolute and
    # "./"-prefixed forms. A bare name with no "/" also counts if it exists
    # directly under specs_dir. hardgate.py imports this helper by name.
    specs_dir = (ctx.cfg.specs_dir or "").strip("/")
    cwd = ctx.data.get("cwd") or ctx.project_dir
    bases = []
    for base in (ctx.repo_dir, ctx.project_dir, cwd):
        if base and base not in bases:
            bases.append(base)

    found = []
    seen = set()
    for tok in _SPEC_TOKEN_RE.findall(text or ""):
        rel = None
        for base in bases:
            info = paths.classify(ctx, tok, base)
            if info.spec:
                rel = info.repo_rel
                break
        if rel is None and specs_dir and "/" not in tok:
            maybe = "{}/{}".format(specs_dir, tok)
            if os.path.isfile(os.path.join(ctx.repo_dir, maybe)):
                rel = maybe
        if rel and rel not in seen:
            seen.add(rel)
            found.append(rel)
    return found


def _head_blob_ids(ctx, specs):
    try:
        proc = subprocess.run(
            ["git", "-C", ctx.repo_dir, "ls-tree", "-r", "HEAD", "--"] + list(specs),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        meta, path = parts
        meta_parts = meta.split()
        if len(meta_parts) < 3:
            continue
        result[path] = meta_parts[2]
    return result


def _read_spec_bytes(ctx, spec):
    try:
        with open(os.path.join(ctx.repo_dir, spec), "rb") as f:
            return f.read()
    except OSError:
        return None


def _check_specs(ctx, specs):
    head_blobs = _head_blob_ids(ctx, specs)
    approved_map = ctx.state.read_json("approved-specs.json", {}) or {}
    result = {}
    for spec in specs:
        data = _read_spec_bytes(ctx, spec)
        if data is None:
            result[spec] = (False, None)
            continue
        sha256 = _sha256_hex(data)
        ok = False
        head_blob = head_blobs.get(spec)
        if head_blob is not None and _git_blob_sha1(data) == head_blob:
            ok = True
        else:
            entry = approved_map.get(spec)
            if entry and entry.get("sha256") == sha256:
                ok = True
        result[spec] = (ok, sha256)
    return result


def _open_request(ctx, spec, sha256):
    def update(session):
        requests = session.get("requests") or {}
        requests[spec] = {"sha256": sha256, "opened_at": time.time()}
        session["requests"] = requests
        return session

    ctx.save_session(update)


def rule_spec_gate(ctx):
    if ctx.role != "executor":
        return None
    if ctx.tool_name != "Agent":
        return None
    tool_input = ctx.tool_input or {}
    subagent_type = str(tool_input.get("subagent_type") or "")
    if subagent_type not in ctx.cfg.roles.tester:
        return None
    if not _gated(ctx):
        return None

    prompt_text = tool_input.get("prompt") or ""
    specs = _extract_spec_refs(ctx, prompt_text)
    if not specs:
        return core.deny(ctx, "9", "specguard: name the spec file in the tester task.")

    statuses = _check_specs(ctx, specs)
    unapproved = [spec for spec, (ok, _sha) in statuses.items() if not ok]
    if unapproved:
        markers = []
        for spec in unapproved:
            sha256 = statuses[spec][1]
            if sha256 is None:
                sha256 = _sha256_hex(b"")
            _open_request(ctx, spec, sha256)
            markers.append(_marker(spec, sha256))
        ctx.state.log(
            "deny", sid=ctx.session_id, role=ctx.role, rule="9", tool=ctx.tool_name,
            spec=",".join(unapproved),
        )
        marker_text = ", ".join(markers)
        reason = (
            "specguard: spec not approved in its current form ({}). Show the user its rules "
            "and ask with AskUserQuestion; the question must carry every marker above, exactly "
            "one option marked with ✓ and at least one other option. If you ask in plain "
            "text instead, end the message with the markers, or the reply will not count. If "
            "you are a subagent, stop and pass these markers verbatim to the main session."
        ).format(marker_text)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def update_launches(launches):
        for spec in specs:
            launches[spec] = {"at": time.time(), "sha256": statuses[spec][1]}
        return launches

    ctx.state.update_json("launches.json", update_launches)
    return None


# ---------------------------------------------------------------------------
# post_tool_use - AskUserQuestion approval recording and request closing
# ---------------------------------------------------------------------------

def post_tool_use(ctx):
    if ctx.tool_name != "AskUserQuestion":
        return None
    tool_input = ctx.tool_input or {}
    tool_response = ctx.data.get("tool_response") or {}
    answers = tool_input.get("answers") or tool_response.get("answers") or {}
    questions = tool_input.get("questions") or tool_response.get("questions") or []
    session = ctx.session()
    requests = session.get("requests") or {}
    if not requests or not answers:
        return None

    to_approve = []
    to_close = []
    for q in questions:
        text = q.get("question") or ""
        matched_specs = [
            spec for spec, entry in requests.items() if _marker(spec, entry["sha256"]) in text
        ]
        if not matched_specs:
            continue
        chosen_label = answers.get(text)
        if chosen_label is None:
            continue
        options = q.get("options") or []
        check_option = next((o for o in options if "✓" in (o.get("label") or "")), None)
        is_check = check_option is not None and chosen_label == check_option.get("label")
        for spec in matched_specs:
            entry = requests[spec]
            data = _read_spec_bytes(ctx, spec)
            current_sha = _sha256_hex(data) if data is not None else None
            if current_sha != entry["sha256"]:
                continue
            if is_check:
                to_approve.append((spec, entry["sha256"]))
            else:
                to_close.append(spec)

    if not to_approve and not to_close:
        return None

    def update_session(session_raw):
        reqs = dict(session_raw.get("requests") or {})
        last_approval = list(session_raw.get("last_approval") or [])
        for spec, sha256 in to_approve:
            reqs.pop(spec, None)
            last_approval.append([spec, sha256])
        for spec in to_close:
            reqs.pop(spec, None)
        session_raw["requests"] = reqs
        session_raw["last_approval"] = last_approval
        return session_raw

    ctx.save_session(update_session)

    lines = []
    if to_approve:
        def update_approved(store):
            for spec, sha256 in to_approve:
                store[spec] = {
                    "sha256": sha256,
                    "at": time.time(),
                    "session": ctx.root_session,
                    "via": "ask",
                }
            return store

        ctx.state.update_json("approved-specs.json", update_approved)
        for spec, sha256 in to_approve:
            ctx.state.log(
                "approval", sid=ctx.session_id, role=ctx.role, spec=spec, via="ask"
            )
            lines.append("specguard: approved {} ({})".format(spec, sha256[:7]))

    for spec in to_close:
        ctx.state.log(
            "request-closed", sid=ctx.session_id, role=ctx.role, spec=spec, via="ask"
        )
        lines.append("specguard: request closed for {}".format(spec))

    return core.message("\n".join(lines)) if lines else None


# ---------------------------------------------------------------------------
# on_prompt - text approval, revoke, opt-out notices
# ---------------------------------------------------------------------------

def _read_last_assistant_text(transcript_path):
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                texts = [
                    c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"
                ]
                texts = [t for t in texts if t]
                if texts:
                    return "\n".join(texts)
            elif isinstance(content, str):
                return content
    return None


def _silent_words(packs):
    silent = set()
    for pack in packs.values():
        trigger = set(pack.get("trigger", []))
        approval_words = set(pack.get("approval_words", []))
        silent.update(trigger & approval_words)
    return silent


def _try_revoke(ctx, normalized):
    packs = modes_mod.load_packs()
    phrases = sorted(_all_pack_words(packs, "revoke"), key=len, reverse=True)
    matched = None
    for phrase in phrases:
        if normalized == phrase or normalized.startswith(phrase + " ") or normalized.startswith(phrase + ","):
            matched = phrase
            break
    if matched is None:
        return None

    session = ctx.session()
    last_approval = [list(pair) for pair in (session.get("last_approval") or [])]
    approved_map = ctx.state.read_json("approved-specs.json", {}) or {}
    revoked = []
    remaining = []
    for spec, sha256 in last_approval:
        entry = approved_map.get(spec)
        if entry and entry.get("sha256") == sha256:
            revoked.append((spec, sha256))
        else:
            remaining.append([spec, sha256])

    if not revoked:
        return core.message("specguard: nothing to revoke")

    def update_approved(store):
        for spec, sha256 in revoked:
            if store.get(spec, {}).get("sha256") == sha256:
                store.pop(spec, None)
        return store

    ctx.state.update_json("approved-specs.json", update_approved)

    def update_session(session_raw):
        session_raw["last_approval"] = remaining
        return session_raw

    ctx.save_session(update_session)

    lines = []
    for spec, sha256 in revoked:
        ctx.state.log("revoke", sid=ctx.session_id, role=ctx.role, spec=spec)
        lines.append("specguard: approval revoked - {} ({})".format(spec, sha256[:7]))
    return core.message("\n".join(lines))


def _try_bare_optout(ctx, normalized):
    packs = modes_mod.load_packs()
    phrases = sorted(_all_pack_words(packs, "optout"), key=len, reverse=True)
    for phrase in phrases:
        if normalized == phrase or normalized.startswith(phrase + " ") or normalized.startswith(phrase + ","):
            modes_set = ctx.modes()
            if "hard" in modes_set:
                return core.message(
                    "specguard: no-approval refused, hard mode always requires approval"
                )

            def update(session):
                session["approval"] = False
                session["set_by"] = "phrase"
                session["set_at"] = time.time()
                return session

            ctx.save_session(update)
            return core.message(
                "specguard: mode {}, approval off".format(modes_mod._format_modes(modes_set))
            )
    return None


def _try_text_approval(ctx, normalized):
    session = ctx.session()
    requests = session.get("requests") or {}
    words = normalized.split()
    if not (1 <= len(words) <= 2):
        return None

    packs = modes_mod.load_packs()
    approval_words = _all_pack_words(packs, "approval_words")
    single_excluded = _all_pack_words(packs, "approval_single_exclude")
    if not all(w in approval_words for w in words):
        return None
    if len(words) == 1 and words[0] in single_excluded:
        return None

    is_silent = len(words) == 1 and words[0] in _silent_words(packs)

    if not requests:
        if is_silent:
            return None
        return core.message("specguard: nothing to approve, no request is open")

    transcript_path = session.get("transcript_path") or ctx.data.get("transcript_path")
    last_text = _read_last_assistant_text(transcript_path)
    if last_text is None:
        if is_silent:
            return None
        return core.message("specguard: could not read the transcript, approval not recorded")

    matched = []
    for spec, entry in requests.items():
        if _marker(spec, entry["sha256"]) not in last_text:
            continue
        data = _read_spec_bytes(ctx, spec)
        current_sha = _sha256_hex(data) if data is not None else None
        if current_sha == entry["sha256"]:
            matched.append((spec, entry["sha256"]))

    if not matched:
        if is_silent:
            return None
        return core.message("specguard: approval not recorded, no marker in the last message")

    def update_session(session_raw):
        reqs = dict(session_raw.get("requests") or {})
        last_approval = list(session_raw.get("last_approval") or [])
        for spec, sha256 in matched:
            reqs.pop(spec, None)
            last_approval.append([spec, sha256])
        session_raw["requests"] = reqs
        session_raw["last_approval"] = last_approval
        return session_raw

    ctx.save_session(update_session)

    def update_approved(store):
        for spec, sha256 in matched:
            store[spec] = {
                "sha256": sha256,
                "at": time.time(),
                "session": ctx.root_session,
                "via": "text",
            }
        return store

    ctx.state.update_json("approved-specs.json", update_approved)

    lines = []
    for spec, sha256 in matched:
        ctx.state.log("approval", sid=ctx.session_id, role=ctx.role, spec=spec, via="text")
        lines.append("specguard: approved {} ({})".format(spec, sha256[:7]))
    return core.message("\n".join(lines))


def on_prompt(ctx):
    if ctx.role != "executor":
        return None
    prompt = ctx.data.get("prompt")
    if not isinstance(prompt, str):
        return None
    stripped = prompt.strip()
    if not stripped or not stripped[0].isalpha():
        return None

    normalized = modes_mod._normalize(prompt)
    if not normalized or normalized.endswith("?"):
        return None

    revoke_out = _try_revoke(ctx, normalized)
    if revoke_out is not None:
        return revoke_out

    optout_out = _try_bare_optout(ctx, normalized)
    if optout_out is not None:
        return optout_out

    return _try_text_approval(ctx, normalized)
