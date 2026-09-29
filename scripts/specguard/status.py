import hashlib
import json
import os
import time

from specguard import config
from specguard.state import State

TREE_HASH_DIRS = (".claude-plugin", "hooks", "scripts", "agents", "skills", "phrases")


def _plugin_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def compute_tree_hash(plugin_root):
    rel_paths = []
    for name in TREE_HASH_DIRS:
        base = os.path.join(plugin_root, name)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in filenames:
                if filename.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, filename)
                rel_paths.append(os.path.relpath(full, plugin_root).replace(os.sep, "/"))

    digest = hashlib.sha256()
    for rel in sorted(rel_paths):
        with open(os.path.join(plugin_root, rel), "rb") as f:
            content = f.read()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _load_raw_config(project_dir):
    path = config.config_path(project_dir)
    if not os.path.isfile(path):
        return None, ["no config at {}".format(path)]
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        return None, ["invalid JSON in {} ({})".format(path, exc)]
    return raw, config.validate(raw)


def _check_config(project_dir):
    raw, problems = _load_raw_config(project_dir)
    if raw is None or problems:
        for problem in problems:
            print("specguard: {}".format(problem))
        return 1
    print("OK")
    return 0


def _short_hash(value):
    return (value or "")[:7]


def _format_last_green(path):
    if not os.path.exists(path):
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))


def _status(project_dir):
    raw, problems = _load_raw_config(project_dir)
    modes_cfg = raw.get("modes") if isinstance(raw, dict) else None
    if not isinstance(modes_cfg, dict):
        modes_cfg = {}
    default_mode = modes_cfg.get("default", "simple")
    default_approval = modes_cfg.get("approval_default", True)
    state = State(project_dir)
    lines = ["specguard: status for {}".format(project_dir)]

    sessions_dir = state.path("sessions")
    if os.path.isdir(sessions_dir):
        names = sorted(n for n in os.listdir(sessions_dir) if n.endswith(".json"))
        if names:
            for name in names:
                sid = name[:-5]
                sess = state.read_json(os.path.join("sessions", name), {}) or {}
                modes = sess.get("modes") or [default_mode]
                approval = True if "hard" in modes else sess.get("approval", default_approval)
                lines.append(
                    "session {} - modes {} - approval {} - open requests {}".format(
                        sid,
                        "+".join(modes),
                        "on" if approval else "off",
                        ", ".join(sorted((sess.get("requests") or {}).keys())) or "none",
                    )
                )
        else:
            lines.append("no sessions recorded yet")
    else:
        lines.append("no sessions recorded yet")

    approved = state.read_json("approved-specs.json", {}) or {}
    if approved:
        for spec in sorted(approved):
            lines.append("approved {} ({})".format(spec, _short_hash(approved[spec].get("sha256"))))
    else:
        lines.append("no approved specs recorded")

    lines.append("last green: {}".format(_format_last_green(state.path("last-green"))))

    log_path = state.path("log.jsonl")
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            tail = f.readlines()[-5:]
        if tail:
            for entry in tail:
                lines.append("log: {}".format(entry.strip()))
        else:
            lines.append("log: empty")
    else:
        lines.append("log: empty")

    if problems:
        for problem in problems:
            lines.append("config problem: {}".format(problem))
    else:
        lines.append("config: OK")

    lines.append("tree hash: {}".format(compute_tree_hash(_plugin_root())))

    print("\n".join(lines))
    return 0


def main(argv):
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not argv:
        print("specguard: usage --status | --check-config")
        return 1
    if argv[0] == "--check-config":
        return _check_config(project_dir)
    if argv[0] == "--status":
        return _status(project_dir)
    print("specguard: unknown argument {}".format(argv[0]))
    return 1
