#!/usr/bin/env python3
import sys

sys.dont_write_bytecode = True

import json
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TESTER_ADVOCATE_RE = re.compile(r"(^|:)(tester|devils-advocate)$")
_TESTER_RE = re.compile(r"(^|:)tester$")

HOOK_ERROR_MESSAGE = "specguard: hook error, checks off, see errors.log"
LIMITED_DENY_REASON = "specguard guard error, the call is refused; tell the user"
HARD_LAUNCH_DENY_REASON = (
    "specguard: hook error, hard mode denies tester launches until it is fixed; see errors.log"
)


def _state_dir(project_dir):
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    key = os.path.abspath(project_dir).replace(os.sep, "-")
    return os.path.join(base, "specguard", key)


def _read_config_plain(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _is_limited(agent_type, raw_cfg):
    if not agent_type:
        return False
    if isinstance(raw_cfg, dict):
        roles = raw_cfg.get("roles")
        if isinstance(roles, dict):
            tester = roles.get("tester") or ["specguard:tester", "tester"]
            advocate = roles.get("advocate") or ["specguard:devils-advocate", "devils-advocate"]
            return agent_type in tester or agent_type in advocate
    return bool(_TESTER_ADVOCATE_RE.search(agent_type))


def _log_error(project_dir):
    import traceback

    try:
        state_dir = _state_dir(project_dir)
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "errors.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
            f.write("\n")
    except BaseException:
        pass


def _once_per_session(state_dir, session_id):
    try:
        marker_dir = os.path.join(state_dir, "notified")
        os.makedirs(marker_dir, exist_ok=True)
        marker = os.path.join(marker_dir, "{}.flag".format(session_id or "unknown"))
        if os.path.exists(marker):
            return False
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1")
        return True
    except BaseException:
        return True


def _session_says_hard(state_dir, session_id):
    try:
        path = os.path.join(state_dir, "sessions", "{}.json".format(session_id))
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
        return "hard" in (session.get("modes") or [])
    except BaseException:
        return False


def _deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _fail_safe(event, data, limited, project_dir):
    state_dir = _state_dir(project_dir)
    session_id = data.get("session_id")

    if event == "PreToolUse":
        if limited:
            return _deny(LIMITED_DENY_REASON)

        tool_name = data.get("tool_name")
        tool_input = data.get("tool_input") or {}
        subagent_type = str(tool_input.get("subagent_type") or "")
        if tool_name == "Agent" and _TESTER_RE.search(subagent_type) and _session_says_hard(state_dir, session_id):
            return _deny(HARD_LAUNCH_DENY_REASON)

        if _once_per_session(state_dir, session_id):
            return {"systemMessage": HOOK_ERROR_MESSAGE}
        return None

    if _once_per_session(state_dir, session_id):
        return {"systemMessage": HOOK_ERROR_MESSAGE}
    return None


def _run_hook():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    event = data.get("hook_event_name")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    config_path = os.path.join(project_dir, ".claude", "specguard.json")
    if not os.path.isfile(config_path):
        sys.exit(0)

    raw_cfg = _read_config_plain(config_path)
    agent_type = data.get("agent_type")
    limited = _is_limited(agent_type, raw_cfg)

    try:
        from specguard import core

        out = core.main(event, data, limited)
    except BaseException:
        _log_error(project_dir)
        out = _fail_safe(event, data, limited, project_dir)

    if out:
        sys.stdout.write(json.dumps(out))
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("--status", "--check-config"):
        from specguard import status

        sys.exit(status.main(argv))
    _run_hook()


if __name__ == "__main__":
    main()
