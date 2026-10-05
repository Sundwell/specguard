import os

from specguard import core

MAX_CONTEXT_CHARS = 9000

SESSION_INTRO = (
    "This project runs the specguard plugin (role-separated TDD). For how it works, load the "
    "specguard:guide skill; do not search the disk for it."
)

MODE_CONFIRM_RULE = (
    "If the user asks for a mode in his own words, do not ask him to rephrase. Ask with "
    "AskUserQuestion carrying `specguard-mode <set>`, one ✓ option to switch and one to "
    "stay; without AskUserQuestion ask in chat and end the message with that marker line. Do "
    "not ask if his message already switched the mode (see the specguard line on his prompt)."
)

FEATURE_RULES = """Feature mode rules.
- Write the spec with behaviour and rule IDs per the specs README before you touch code.
- Bring in the devils-advocate only when the spec touches a money topic.
- When the spec is ready, launch the tester task on it. The approval gate refuses the launch and opens a request; show the user the spec's rules (and the advocate table, if there was one) and ask with AskUserQuestion, the question carrying every marker the deny gave you, one option carrying the check mark for approve and at least one other option.
- After approval, write code and tests in parallel, with the tester running in the background.
- A failing test is fixed in the code, never bent to pass. If a test itself looks wrong, escalate to the user with the rule ID, the input and what came back.
- Never paste implementation code into the tester's task.
- Talk to the user in the user's language."""

HARD_RULES = FEATURE_RULES + """

Hard mode adds.
- The devils-advocate reviews every spec before the tester runs on it, whatever the topic.
- Spec approval is mandatory and cannot be turned off.
- Before you report done, run a manual mutation check on every changed rule group: break at least three named things the rule group asserts, one at a time, name which test caught each break, then restore the code."""

VISUAL_RULES = """Visual mode rules.
- Before the first UI edit in a session, walk the design frame by frame against what the build shows now in a checklist (the frame, the current code, same or different, rows you plan to leave as is), each frame linked. Build the sheet with compare-sheet <out.png> DESIGN:<path> NOW:<path> (shipped with this plugin, needs ffmpeg) as DESIGN | NOW, the design always in the left column.
- Wait for the user's go before editing.
- Build.
- When you show a change, build a DESIGN | BEFORE | NOW sheet the same way, the design still in the left column.
- Log any deviation from the design in the deviations log.
- Keep a "what I decided myself" block for anything you chose on your own.
- Talk to the user in the user's language."""


def _mode_label(modes):
    if not modes:
        return "simple"
    return "+".join(sorted(modes, key=lambda m: m != "visual"))


def mode_rules(modes, approval):
    modes = set(modes or [])
    if not modes or modes == {"simple"}:
        return ""

    blocks = []
    if "hard" in modes:
        blocks.append(HARD_RULES)
    elif "feature" in modes:
        blocks.append(FEATURE_RULES)
    if "visual" in modes:
        blocks.append(VISUAL_RULES)

    if "feature" in modes or "hard" in modes:
        blocks.insert(0, "Approval is {}.".format("on" if approval else "off"))

    return "\n\n".join(blocks)


def _prompt_status_line(ctx):
    sess = ctx.session()
    modes = sess["modes"]
    line = "Active specguard mode is {}".format(_mode_label(modes))
    if "feature" in modes or "hard" in modes:
        line += ", approval {}".format("on" if sess["approval"] else "off")
    line += "."
    reqs = sess["requests"]
    if reqs:
        line += " Open approval requests - {}.".format(", ".join(sorted(reqs.keys())))
    return line


def prompt_line(ctx):
    if ctx.role != "executor":
        return None
    return core.context("UserPromptSubmit", _prompt_status_line(ctx))


def _grep_roots(ctx):
    cfg = ctx.cfg
    roots = list(cfg.tests.paths) + list(cfg.docs_dirs)
    if ctx.role == "advocate":
        roots = roots + list(cfg.tester_readable)
    return roots


def _resolved_summary(ctx):
    cfg = ctx.cfg
    report_dir = cfg.reports.tester if ctx.role == "tester" else cfg.reports.advocate
    hidden = list(cfg.hidden.segments) + list(cfg.hidden.names) + list(cfg.hidden.paths)
    lines = [
        "Project root: {}".format(ctx.project_dir),
        "Repo: {}".format(cfg.repo),
        "Specs dir: {}".format(cfg.specs_dir),
        "Report dir: {}".format(report_dir),
        "Allowed grep roots: {}".format(", ".join(_grep_roots(ctx)) or "none configured"),
        "Hidden: {}".format(", ".join(hidden) or "none"),
        "Readable exceptions: {}".format(", ".join(cfg.tester_readable) or "none"),
        "Hands-on tag: {}".format(cfg.hands_on_tag),
        "Test command: {}".format(cfg.stop.run or "none configured"),
    ]
    return "\n".join(lines)


def _notes(ctx):
    rel = ctx.cfg.notes.tester if ctx.role == "tester" else ctx.cfg.notes.advocate
    abs_path = os.path.join(ctx.project_dir, rel)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return rel, f.read()
    except OSError:
        return rel, None


def _role_context(ctx, event):
    rel, notes = _notes(ctx)
    summary = _resolved_summary(ctx)
    if notes:
        full = notes + "\n\n" + summary
        if len(full) <= MAX_CONTEXT_CHARS:
            return core.context(event, full)
    if not notes:
        return core.context(event, summary)
    return core.context(event, summary + "\nRead {} first.".format(rel))


def on_session_start(ctx):
    if ctx.role in ("tester", "advocate"):
        return _role_context(ctx, "SessionStart")
    sess = ctx.session()
    text = mode_rules(sess["modes"], sess["approval"])
    body = SESSION_INTRO + "\n\n" + MODE_CONFIRM_RULE
    if text:
        body += "\n\n" + text
    return core.context("SessionStart", body)


def on_subagent_start(ctx):
    if ctx.role in ("tester", "advocate"):
        return _role_context(ctx, "SubagentStart")
    line = _prompt_status_line(ctx) + " Only the main session can ask the user; report gate refusals to it."
    return core.context("SubagentStart", line)
