import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "scripts", "specguard_hook.py")


def make_project(tmp, config=None, files=None, git=False, commit=False):
    project_dir = tmp
    os.makedirs(project_dir, exist_ok=True)

    if config is not None:
        claude_dir = os.path.join(project_dir, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        with open(os.path.join(claude_dir, "specguard.json"), "w", encoding="utf-8") as f:
            json.dump(config, f)

    if files:
        for rel_path, content in files.items():
            full = os.path.join(project_dir, rel_path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

    if git:
        run = lambda *args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=project_dir, check=True, capture_output=True
        )
        run("init", "-q")
        run("config", "user.email", "test@example.com")
        run("config", "user.name", "specguard tests")
        if commit:
            run("add", "-A")
            run("commit", "-q", "-m", "initial")

    return project_dir


def run_hook(event, payload, project_dir, env=None, state_home=None, hook_path=None, plugin_root=None):
    body = dict(payload)
    body.setdefault("hook_event_name", event)
    body.setdefault("cwd", project_dir)

    run_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    run_env["CLAUDE_PROJECT_DIR"] = project_dir
    run_env["CLAUDE_PLUGIN_ROOT"] = plugin_root or REPO_ROOT
    run_env["XDG_STATE_HOME"] = state_home or tempfile.mkdtemp(prefix="specguard-state-")
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        run_env.update(env)

    proc = subprocess.run(
        [sys.executable, "-B", hook_path or HOOK_PATH],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        cwd=project_dir,
        env=run_env,
        timeout=10,
    )

    stdout_json = None
    if proc.stdout.strip():
        try:
            stdout_json = json.loads(proc.stdout)
        except ValueError:
            stdout_json = None

    return proc.returncode, stdout_json, proc.stderr


def _base(event, session_id, extra):
    payload = {
        "hook_event_name": event,
        "session_id": session_id,
        "permission_mode": extra.pop("permission_mode", "default"),
        "prompt_id": extra.pop("prompt_id", "p1"),
        "transcript_path": extra.pop("transcript_path", "/tmp/specguard-transcript.md"),
    }
    agent_type = extra.pop("agent_type", None)
    agent_id = extra.pop("agent_id", None)
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if agent_id is not None:
        payload["agent_id"] = agent_id
    payload.update(extra)
    return payload


def pre_tool_use(session_id, tool_name, tool_input, **extra):
    payload = _base("PreToolUse", session_id, extra)
    payload["tool_name"] = tool_name
    payload["tool_input"] = tool_input
    payload.setdefault("tool_use_id", "tu1")
    return payload


def post_tool_use(session_id, tool_name, tool_input, tool_response, **extra):
    payload = _base("PostToolUse", session_id, extra)
    payload["tool_name"] = tool_name
    payload["tool_input"] = tool_input
    payload["tool_response"] = tool_response
    payload.setdefault("tool_use_id", "tu1")
    payload.setdefault("duration_ms", 10)
    return payload


def user_prompt_submit(session_id, prompt, **extra):
    payload = _base("UserPromptSubmit", session_id, extra)
    payload["prompt"] = prompt
    return payload


def user_prompt_expansion(session_id, command_name, command_args, **extra):
    extra.setdefault("command_source", "plugin")
    extra.setdefault("expansion_type", "slash_command")
    payload = _base("UserPromptExpansion", session_id, extra)
    payload["command_name"] = command_name
    payload["command_args"] = command_args
    payload["prompt"] = "/{} {}".format(command_name, command_args).strip()
    return payload


def session_start(session_id, source="startup", **extra):
    payload = _base("SessionStart", session_id, extra)
    payload["source"] = source
    return payload


def subagent_start(session_id, agent_id, agent_type, **extra):
    extra["agent_id"] = agent_id
    extra["agent_type"] = agent_type
    return _base("SubagentStart", session_id, extra)


def stop(session_id, stop_hook_active=False, background_tasks=None, **extra):
    payload = _base("Stop", session_id, extra)
    payload["stop_hook_active"] = stop_hook_active
    payload["background_tasks"] = background_tasks or []
    payload.setdefault("last_assistant_message", "")
    payload.setdefault("session_crons", [])
    return payload
