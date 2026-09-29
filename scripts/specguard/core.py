import os

from specguard import config
from specguard.state import State


def _classify_role(cfg, agent_type):
    if not agent_type:
        return "executor"
    if agent_type in cfg.roles.tester:
        return "tester"
    if agent_type in cfg.roles.advocate:
        return "advocate"
    return "executor"


class Ctx:
    def __init__(self, event, data, cfg, project_dir, state):
        self.event = event
        self.data = data
        self.cfg = cfg
        self.project_dir = project_dir
        self.repo_dir = os.path.normpath(os.path.join(project_dir, cfg.repo))
        self.state = state
        self.agent_type = data.get("agent_type")
        self.agent_id = data.get("agent_id")
        self.session_id = data.get("session_id")
        self.root_session = state.resolve_root(self.session_id, self.agent_id)
        self.tool_name = data.get("tool_name")
        self.tool_input = data.get("tool_input") or {}
        self.role = _classify_role(cfg, self.agent_type)

    def session(self):
        raw = self.state.session(self.root_session) or {}
        return {
            "modes": raw.get("modes") or [self.cfg.modes.default],
            "approval": raw.get("approval", self.cfg.modes.approval_default),
            "set_at": raw.get("set_at"),
            "set_by": raw.get("set_by", "default"),
            "transcript_path": raw.get("transcript_path") or self.data.get("transcript_path"),
            "requests": raw.get("requests", {}),
            "last_approval": raw.get("last_approval", []),
            "visual": raw.get("visual", {}),
        }

    def modes(self):
        return set(self.session()["modes"])

    def approval_on(self):
        if "hard" in self.modes():
            return True
        return bool(self.session()["approval"])

    def save_session(self, fn):
        return self.state.update_session(self.root_session, fn)


def deny(ctx, rule, reason):
    ctx.state.log("deny", sid=ctx.session_id, role=ctx.role, rule=rule, tool=ctx.tool_name)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def ask(ctx, rule, reason):
    ctx.state.log("ask", sid=ctx.session_id, role=ctx.role, rule=rule, tool=ctx.tool_name)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }


def block(ctx, rule, reason):
    ctx.state.log("block", sid=ctx.session_id, role=ctx.role, rule=rule)
    return {"decision": "block", "reason": reason}


def message(text):
    return {"systemMessage": text}


def context(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def merge(*outs):
    outs = [o for o in outs if o]
    if not outs:
        return None
    result = {}

    messages = [o["systemMessage"] for o in outs if o.get("systemMessage")]
    if messages:
        result["systemMessage"] = "\n".join(messages)

    decision_out = next(
        (o for o in outs if o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"), None
    )
    if decision_out is None:
        decision_out = next(
            (o for o in outs if o.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"), None
        )
    if decision_out is not None:
        result["hookSpecificOutput"] = dict(decision_out["hookSpecificOutput"])
    else:
        ctx_outs = [o["hookSpecificOutput"] for o in outs if "additionalContext" in o.get("hookSpecificOutput", {})]
        if ctx_outs:
            result["hookSpecificOutput"] = dict(ctx_outs[0])
            result["hookSpecificOutput"]["additionalContext"] = "\n\n".join(
                h["additionalContext"] for h in ctx_outs if h["additionalContext"]
            )

    block_out = next((o for o in outs if o.get("decision") == "block"), None)
    if block_out is not None:
        result["decision"] = "block"
        result["reason"] = block_out.get("reason")

    return result or None


def _pre_tool_use(ctx):
    from specguard import approval, guard, hardgate, visual

    for step in (
        lambda: guard.rules_base(ctx),
        lambda: guard.rule_launch(ctx),
        lambda: approval.rule_messages(ctx),
        lambda: guard.rules_roles(ctx),
    ):
        out = step()
        if out is not None:
            return out

    if "hard" in ctx.modes():
        out = hardgate.rule_advocate_gate(ctx)
        if out is not None:
            return out

    out = approval.rule_spec_gate(ctx)
    if out is not None:
        return out

    if "visual" in ctx.modes():
        out = visual.rule_visual_gate(ctx)
        if out is not None:
            return out

    return None


def _post_tool_use(ctx):
    from specguard import approval, modes, visual

    return merge(approval.post_tool_use(ctx), visual.post_tool_use(ctx), modes.post_tool_use(ctx))


def _user_prompt_submit(ctx):
    from specguard import approval, modes, visual
    from specguard import context as context_mod

    out, switched_to = modes.on_prompt(ctx)
    outs = [out]
    if switched_to is not None:
        outs.append(visual.on_mode_switch(ctx, switched_to))
    outs.append(modes.on_text_confirm(ctx))
    outs.append(approval.on_prompt(ctx))
    outs.append(visual.on_prompt(ctx))
    outs.append(context_mod.prompt_line(ctx))
    return merge(*outs)


def _user_prompt_expansion(ctx):
    from specguard import modes, visual

    out, switched_to = modes.on_command(ctx)
    outs = [out]
    if switched_to is not None:
        outs.append(visual.on_mode_switch(ctx, switched_to))
    return merge(*outs)


def _session_start(ctx):
    from specguard import context as context_mod

    return context_mod.on_session_start(ctx)


def _subagent_start(ctx):
    from specguard import context as context_mod

    ctx.state.record_agent(ctx.agent_id, ctx.session_id, ctx.agent_type)
    return context_mod.on_subagent_start(ctx)


def _stop(ctx):
    from specguard import stop as stop_mod

    return stop_mod.on_stop(ctx)


_DISPATCH = {
    "PreToolUse": _pre_tool_use,
    "PostToolUse": _post_tool_use,
    "UserPromptSubmit": _user_prompt_submit,
    "UserPromptExpansion": _user_prompt_expansion,
    "SessionStart": _session_start,
    "SubagentStart": _subagent_start,
    "Stop": _stop,
}


def main(event, data, limited):
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    cfg = config.load(project_dir)
    state = State(project_dir)
    ctx = Ctx(event, data, cfg, project_dir, state)
    if limited and ctx.role == "executor":
        ctx.role = "tester"

    dispatch = _DISPATCH.get(event)
    if dispatch is None:
        return None
    return dispatch(ctx)
