from specguard import approval as approval_mod
from specguard import core
from specguard import modes as modes_mod
from specguard import paths

_WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

_DENY_REASON = (
    "specguard: visual mode - his go is needed before the first UI edit of the session. Walk the "
    "design frame by frame against what the build shows now and show him a DESIGN | NOW checklist "
    "(compare-sheet, shipped with this plugin, design in the left column). Then ask with AskUserQuestion, "
    "the question carrying the marker {} and exactly one option marked with ✓, plus at least "
    "one other option. If you ask in plain text instead, end the message with the marker, or his "
    "reply will not count."
)


def _marker(n):
    return "specguard-approve visual@{}".format(n)


def _tool_path(tool_input):
    return tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")


def _under_ui_paths(rel, ui_paths):
    for raw in ui_paths or []:
        prefix = (raw or "").strip("/")
        if not prefix:
            continue
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


def _open_gate(ctx):
    def update(session):
        visual = dict(session.get("visual") or {})
        visual["open"] = True
        visual["pending"] = None
        session["visual"] = visual
        return session

    ctx.save_session(update)


def _set_pending(ctx, n):
    def update(session):
        visual = dict(session.get("visual") or {})
        visual["open"] = False
        visual["pending"] = n
        visual["counter"] = n
        session["visual"] = visual
        return session

    ctx.save_session(update)


def rule_visual_gate(ctx):
    if ctx.role != "executor":
        return None
    if ctx.tool_name not in _WRITE_TOOLS:
        return None
    if ctx.cfg.visual is None:
        return None

    raw = _tool_path(ctx.tool_input)
    if not raw:
        return None
    info = paths.classify(ctx, raw, ctx.data.get("cwd") or ctx.project_dir)
    if info.repo_rel is None or not _under_ui_paths(info.repo_rel, ctx.cfg.visual.ui_paths):
        return None

    visual_state = ctx.session().get("visual") or {}
    if visual_state.get("open"):
        return None

    n = visual_state.get("pending")
    if n is None:
        n = visual_state.get("counter", 0) + 1
        _set_pending(ctx, n)

    return core.deny(ctx, "V", _DENY_REASON.format(_marker(n)))


def post_tool_use(ctx):
    if ctx.tool_name != "AskUserQuestion":
        return None
    tool_input = ctx.tool_input or {}
    tool_response = ctx.data.get("tool_response") or {}
    answers = tool_input.get("answers") or tool_response.get("answers") or {}
    questions = tool_input.get("questions") or tool_response.get("questions") or []
    if not answers:
        return None

    visual_state = ctx.session().get("visual") or {}
    pending = visual_state.get("pending")
    if pending is None:
        return None
    marker = _marker(pending)

    for q in questions:
        text = q.get("question") or ""
        if marker not in text:
            continue
        chosen_label = answers.get(text)
        if chosen_label is None:
            continue
        options = q.get("options") or []
        check_option = next((o for o in options if "✓" in (o.get("label") or "")), None)
        is_check = check_option is not None and chosen_label == check_option.get("label")
        if not is_check:
            return None
        _open_gate(ctx)
        ctx.state.log("approval", sid=ctx.session_id, role=ctx.role, rule="V", via="ask")
        return core.message("specguard: visual gate open")
    return None


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

    words = normalized.split()
    if not (1 <= len(words) <= 2):
        return None

    session = ctx.session()
    visual_state = session.get("visual") or {}
    pending = visual_state.get("pending")

    packs = modes_mod.load_packs()
    approval_words = approval_mod._all_pack_words(packs, "approval_words")
    single_excluded = approval_mod._all_pack_words(packs, "approval_single_exclude")
    if not all(w in approval_words for w in words):
        return None
    if len(words) == 1 and words[0] in single_excluded:
        return None

    is_silent = len(words) == 1 and words[0] in approval_mod._silent_words(packs)

    if pending is None:
        if is_silent:
            return None
        return core.message("specguard: nothing to approve, the visual gate is not pending")

    transcript_path = session.get("transcript_path") or ctx.data.get("transcript_path")
    last_text = approval_mod._read_last_assistant_text(transcript_path)
    if last_text is None:
        if is_silent:
            return None
        return core.message("specguard: could not read the transcript, the visual gate stays closed")

    marker = _marker(pending)
    if marker not in last_text:
        if is_silent:
            return None
        return core.message("specguard: the visual gate stays closed, no marker in the last message")

    _open_gate(ctx)
    ctx.state.log("approval", sid=ctx.session_id, role=ctx.role, rule="V", via="text")
    return core.message("specguard: visual gate open")


def on_mode_switch(ctx, switched_to):
    if not switched_to or "visual" not in switched_to:
        return None

    def update(session):
        visual = dict(session.get("visual") or {})
        visual["open"] = False
        visual["pending"] = None
        session["visual"] = visual
        return session

    ctx.save_session(update)
    ctx.state.log("visual-close", sid=ctx.session_id, role=ctx.role, rule="V")
    return None
