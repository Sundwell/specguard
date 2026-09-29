import json
import os
import re

from specguard import core
from specguard import context as context_mod

_PACK_NAMES = ("en", "ru", "uk")
_ACCEPTED_SETS = [
    frozenset(["simple"]),
    frozenset(["feature"]),
    frozenset(["hard"]),
    frozenset(["visual"]),
    frozenset(["visual", "feature"]),
    frozenset(["visual", "hard"]),
]
_MODE_ORDER = ["visual", "feature", "hard", "simple"]
_END_TOKENS = {".", ",", "!", ":", ";", "-"}
_DASHES = ("‐", "‑", "–", "—")
_PUNCT_RE = re.compile(r"([.,!:;?+\-])")
_LETTER_JOIN_RE = re.compile(r"(?<=[^\W\d_])-(?=[^\W\d_])", re.UNICODE)

_packs_cache = None


def _plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def load_packs():
    global _packs_cache
    if _packs_cache is not None:
        return _packs_cache
    packs = {}
    phrases_dir = os.path.join(_plugin_root(), "phrases")
    for name in _PACK_NAMES:
        path = os.path.join(phrases_dir, "{}.json".format(name))
        try:
            with open(path, "r", encoding="utf-8") as f:
                packs[name] = json.load(f)
        except (OSError, ValueError):
            packs[name] = {}
    _packs_cache = packs
    return packs


def _normalize(text):
    if not text:
        return ""
    t = text.lower()
    t = t.replace("ё", "е")
    for dash in _DASHES:
        t = t.replace(dash, "-")
    t = _LETTER_JOIN_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokenize(text):
    spaced = _PUNCT_RE.sub(r" \1 ", text)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced.split(" ") if spaced else []


def _candidates(packs, key):
    words = set()
    for pack in packs.values():
        words.update(pack.get(key, []) or [])
    return sorted((tuple(w.split(" ")) for w in words if w), key=len, reverse=True)


def _mode_candidates(packs):
    out = []
    for pack in packs.values():
        for mode_name, phrases in (pack.get("modes") or {}).items():
            for phrase in phrases:
                out.append((tuple(phrase.split(" ")), mode_name))
    return sorted(out, key=lambda item: len(item[0]), reverse=True)


def _match_at(tokens, pos, candidates):
    for words in candidates:
        n = len(words)
        if pos + n <= len(tokens) and tuple(tokens[pos:pos + n]) == words:
            return n
    return 0


def _match_mode_at(tokens, pos, mode_candidates):
    for words, name in mode_candidates:
        n = len(words)
        if pos + n <= len(tokens) and tuple(tokens[pos:pos + n]) == words:
            return n, name
    return 0, None


def _at_end(tokens, pos):
    return pos >= len(tokens) or tokens[pos] in _END_TOKENS


def _parse_phrase(text):
    packs = load_packs()
    tokens = _tokenize(text)
    if not tokens:
        return None

    triggers = _candidates(packs, "trigger")
    joins = _candidates(packs, "join")
    fillers = _candidates(packs, "mode_filler")
    optouts = _candidates(packs, "optout")
    mode_candidates = _mode_candidates(packs)

    trig_len = _match_at(tokens, 0, triggers)
    if trig_len == 0:
        return None
    trigger_word = tokens[0]
    pos = trig_len
    if pos < len(tokens) and tokens[pos] == ",":
        pos += 1

    mode_len, mode_name = _match_mode_at(tokens, pos, mode_candidates)
    if mode_len == 0:
        return None
    pos += mode_len
    modes_found = [mode_name]

    join_len = _match_at(tokens, pos, joins)
    if join_len == 0 and pos < len(tokens) and tokens[pos] in ("+", ","):
        join_len = 1
    if join_len > 0:
        trial_pos = pos + join_len
        mode2_len, mode2_name = _match_mode_at(tokens, trial_pos, mode_candidates)
        if mode2_len > 0:
            pos = trial_pos + mode2_len
            modes_found.append(mode2_name)

    filler_pos = pos + 1 if pos < len(tokens) and tokens[pos] == "," else pos
    filler_len = _match_at(tokens, filler_pos, fillers)
    if filler_len > 0:
        pos = filler_pos + filler_len

    optout = False
    optout_pos = pos + 1 if pos < len(tokens) and tokens[pos] == "," else pos
    optout_len = _match_at(tokens, optout_pos, optouts)
    if optout_len > 0:
        trial_pos = optout_pos + optout_len
        if _at_end(tokens, trial_pos):
            pos = trial_pos
            optout = True

    if _at_end(tokens, pos):
        return {"modes": modes_found, "optout": optout}

    free_tail = (
        trigger_word in ("го", "go")
        and not optout
        and set(modes_found) <= {"feature", "hard", "visual"}
    )
    if free_tail:
        return {"modes": modes_found, "optout": optout}
    return None


def _format_modes(modes_set):
    ordered = [m for m in _MODE_ORDER if m in modes_set]
    return "+".join(ordered)


def _accepted_list_text():
    return ", ".join(_format_modes(s) for s in _ACCEPTED_SETS)


def would_switch(text):
    normalized = _normalize(text)
    if normalized.endswith("?"):
        normalized = normalized[:-1].strip()
    return _parse_phrase(normalized) is not None


def _save(ctx, modes_set, approval, set_by):
    import time

    def update(session):
        session["modes"] = sorted(modes_set)
        session["approval"] = approval
        session["set_at"] = time.time()
        session["set_by"] = set_by
        session["transcript_path"] = ctx.data.get("transcript_path")
        return session

    ctx.save_session(update)


def _apply_switch(ctx, modes_set, optout, set_by):
    hard_refused = "hard" in modes_set and optout
    if hard_refused:
        approval = True
    elif optout:
        approval = False
    else:
        approval = "hard" in modes_set or bool(ctx.cfg.modes.approval_default)

    _save(ctx, modes_set, approval, set_by)
    ctx.state.log(
        "mode-switch",
        sid=ctx.session_id,
        role=ctx.role,
        modes=sorted(modes_set),
        via=set_by,
    )

    lines = [
        "specguard: mode {}, approval {}".format(_format_modes(modes_set), "on" if approval else "off")
    ]
    if hard_refused:
        lines.append("specguard: no-approval refused, hard mode always requires approval")

    rules_text = context_mod.mode_rules(sorted(modes_set), approval)
    ctx_body = context_mod.SESSION_INTRO if not rules_text else context_mod.SESSION_INTRO + "\n\n" + rules_text
    msg_out = core.message("\n".join(lines))
    ctx_out = core.context("UserPromptSubmit", ctx_body)
    return core.merge(msg_out, ctx_out), modes_set


def _reject(text):
    return core.message(text), None


def on_prompt(ctx):
    if ctx.role != "executor":
        return None, None
    prompt = ctx.data.get("prompt")
    if not isinstance(prompt, str):
        return None, None
    stripped = prompt.strip()
    if not stripped or not stripped[0].isalpha():
        return None, None

    normalized = _normalize(prompt)
    if not normalized:
        return None, None
    ends_with_q = normalized.endswith("?")
    text_for_match = normalized[:-1].strip() if ends_with_q else normalized

    parsed = _parse_phrase(text_for_match)
    if parsed is None:
        return None, None

    if ends_with_q:
        return _reject('specguard: mode not switched, message ends with "?"')

    modes_set = frozenset(parsed["modes"])
    if modes_set not in _ACCEPTED_SETS:
        return _reject(
            "specguard: mode not switched, {} is not a valid combination; accepted are {}".format(
                _format_modes(modes_set), _accepted_list_text()
            )
        )

    return _apply_switch(ctx, modes_set, parsed["optout"], "phrase")


_COMMAND_NAME_RE = re.compile(r"(^|:)mode$")
_MODE_NAMES = {"simple", "feature", "hard", "visual"}


def on_command(ctx):
    if ctx.role != "executor":
        return None, None
    command_name = ctx.data.get("command_name") or ""
    if not _COMMAND_NAME_RE.search(command_name):
        return None, None

    args = (ctx.data.get("command_args") or "").strip()
    usage = (
        "specguard: mode not switched, usage /specguard:mode <mode>[+<mode>] [no-approval]; "
        "accepted are {}".format(_accepted_list_text())
    )
    parts = args.split()
    if not parts or len(parts) > 2:
        return _reject(usage)
    if len(parts) == 2 and parts[1] != "no-approval":
        return _reject(usage)
    optout = len(parts) == 2

    mode_names = parts[0].split("+")
    if any(name not in _MODE_NAMES for name in mode_names):
        return _reject(usage)
    modes_set = frozenset(mode_names)
    if modes_set not in _ACCEPTED_SETS:
        return _reject(usage)

    return _apply_switch(ctx, modes_set, optout, "command")
