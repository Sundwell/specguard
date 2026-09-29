import fnmatch
import glob
import os
import re
import signal
import subprocess
import time

from specguard import core

MSG_TESTER_INFLIGHT = "specguard: tester still running, tests run after it"

GIT_TIMEOUT_S = 5

_SKIP_PRESETS = {
    "vitest": r"\b(test|it|describe)\.(skip|only|todo)\(",
    # seenby's check-tests.sh regex (@pytest.mark.(skip|xfail)|pytest.skip(|unittest.skip), plus skipif,
    # pytest.xfail( and raise unittest.SkipTest.
    "pytest": r"@pytest\.mark\.(skip|skipif|xfail)|pytest\.skip\(|pytest\.xfail\(|unittest\.skip|raise unittest\.SkipTest",
    # snimak's regex (Skip *=|\[Fact\(Skip|\[Theory\(Skip), plus a bare [Ignore].
    "xunit": r"Skip *=|\[Fact\(Skip|\[Theory\(Skip|\[Ignore\]",
}


def _repo_label(cfg):
    return cfg.repo if cfg.repo and cfg.repo != "." else "the project"


def _tester_in_flight(ctx):
    tasks = ctx.data.get("background_tasks") or []
    tester_types = set(ctx.cfg.roles.tester)
    agents = None
    for task in tasks:
        if task.get("type") != "subagent" or task.get("status") != "running":
            continue
        agent_type = task.get("agent_type")
        if agent_type in tester_types:
            return True
        if not agent_type:
            if agents is None:
                agents = ctx.state.read_json("agents.json", {}) or {}
            entry = agents.get(task.get("id"))
            if entry and entry.get("agent_type") in tester_types:
                return True
    return False


def _is_dirty(cfg, repo_dir):
    pathspec = cfg.stop.dirty_pathspec or []
    cmd = ["git", "-C", repo_dir, "status", "--porcelain"]
    if pathspec:
        cmd.append("--")
        cmd.extend(pathspec)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return True
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def _iter_files(base_dir, rel_roots, globs, exclude_dirs):
    exclude_dirs = set(exclude_dirs or [])
    for rel in rel_roots or []:
        root = os.path.join(base_dir, rel)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
            for name in filenames:
                if not globs or any(fnmatch.fnmatch(name, g) for g in globs):
                    yield os.path.join(dirpath, name)


def _is_fresh(cfg, repo_dir, last_green_path):
    if not os.path.exists(last_green_path):
        return True
    last_green_mtime = os.path.getmtime(last_green_path)
    for path in _iter_files(repo_dir, cfg.stop.fresh_paths, cfg.stop.fresh_globs, cfg.stop.exclude_dirs):
        try:
            if os.path.getmtime(path) > last_green_mtime:
                return True
        except OSError:
            continue
    return False


def _require_glob_missing(cfg, repo_dir):
    if not cfg.stop.require_glob:
        return False
    pattern = os.path.join(repo_dir, cfg.stop.require_glob)
    return not glob.glob(pattern, recursive=True)


def _should_skip(ctx):
    cfg = ctx.cfg
    if not _is_dirty(cfg, ctx.repo_dir):
        return True
    if not _is_fresh(cfg, ctx.repo_dir, ctx.state.path("last-green")):
        return True
    if _require_glob_missing(cfg, ctx.repo_dir):
        return True
    return False


def _skip_pattern(cfg):
    if cfg.stop.skip_regex:
        return re.compile(cfg.stop.skip_regex)
    regex = _SKIP_PRESETS.get(cfg.stop.skip_preset or "none")
    return re.compile(regex) if regex else None


def _skip_scan_hit(ctx):
    cfg = ctx.cfg
    pattern = _skip_pattern(cfg)
    if pattern is None:
        return False
    roots = cfg.stop.skip_paths or ["."]
    exclude_dirs = set(cfg.stop.exclude_dirs or [])
    for root in roots:
        base = os.path.join(ctx.repo_dir, root)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, ctx.repo_dir).replace(os.sep, "/")
                if cfg.tests.file_regex and not re.search(cfg.tests.file_regex, rel):
                    continue
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except OSError:
                    continue
                if pattern.search(content):
                    return True
    return False


def _summary_lines(cfg, output):
    if not cfg.stop.summary_regex:
        return []
    pattern = re.compile(cfg.stop.summary_regex)
    matched = [line for line in output.splitlines() if pattern.search(line)]
    return matched[-25:]


def _touch_last_green(ctx):
    path = ctx.state.path("last-green")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


def on_stop(ctx):
    if ctx.role in ("tester", "advocate"):
        return None
    if ctx.data.get("stop_hook_active"):
        return None
    if _tester_in_flight(ctx):
        ctx.state.log("stop-inflight", sid=ctx.session_id, role=ctx.role)
        return core.message(MSG_TESTER_INFLIGHT)

    cfg = ctx.cfg
    if not cfg.stop.run:
        return None
    if not (ctx.modes() & set(cfg.stop.modes)):
        return None
    if _should_skip(ctx):
        return None

    if _skip_scan_hit(ctx):
        reason = "specguard: {} has skipped, focused or todo tests. They are not evidence; name the blocked spec rule in the report instead.".format(
            _repo_label(cfg)
        )
        return core.block(ctx, "stop-skip-scan", reason)

    proc = subprocess.Popen(
        cfg.stop.run,
        shell=True,
        cwd=ctx.project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=cfg.stop.timeout)
    except subprocess.TimeoutExpired:
        # killing only the shell leaves a child holding the pipes open, so the whole group goes
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        reason = "specguard: stop.run timed out after {}s. Fix or speed up the test command.".format(cfg.stop.timeout)
        return core.block(ctx, "stop-timeout", reason)

    if proc.returncode != 0:
        lines = _summary_lines(cfg, (stdout or "") + (stderr or ""))
        reason = "specguard: tests failed. Fix the code, never the tests, or say in the report which spec rules are red and why."
        if lines:
            reason += "\n\n" + "\n".join(lines)
        return core.block(ctx, "stop-red", reason)

    _touch_last_green(ctx)
    return None
