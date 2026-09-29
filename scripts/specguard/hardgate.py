import os
import re
import subprocess

from specguard import approval

_REPORT_DATE_SUFFIX = r"-\d{4}-\d{2}-\d{2}.*"


def _stem(spec):
    base = os.path.basename(spec)
    if base.endswith(".md"):
        base = base[:-3]
    return base


def _report_name_matches(basename, stem):
    pattern = r"^" + re.escape(stem) + r"(\.md|" + _REPORT_DATE_SUFFIX + r"\.md)$"
    return re.match(pattern, basename) is not None


def _head_blob_ids_strict(ctx, specs):
    try:
        proc = subprocess.run(
            ["git", "-C", ctx.repo_dir, "ls-tree", "-r", "HEAD", "--"] + list(specs),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False, {}
    if proc.returncode != 0:
        return False, {}
    blobs = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        meta, path = parts
        meta_parts = meta.split()
        if len(meta_parts) < 3:
            continue
        blobs[path] = meta_parts[2]
    return True, blobs


def _has_advocate_report(reports, stem, after_ts):
    for path, entries in reports.items():
        if not _report_name_matches(os.path.basename(path), stem):
            continue
        for entry in entries:
            at = entry.get("at")
            if at is None:
                continue
            if after_ts is None or at > after_ts:
                return True
    return False


def _deny(ctx, specs, reason):
    ctx.state.log(
        "deny", sid=ctx.session_id, role=ctx.role, rule="8", tool=ctx.tool_name,
        spec=",".join(specs),
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _needs_advocate_reason(ctx, specs):
    return (
        "specguard: hard mode requires the devils-advocate on {} first, report at "
        "{}/<spec>-<date>.md. The task holds only the spec path, the plan row and the report path."
    ).format(", ".join(specs), ctx.cfg.reports.advocate)


def rule_advocate_gate(ctx):
    if ctx.role != "executor":
        return None
    if ctx.tool_name != "Agent":
        return None
    tool_input = ctx.tool_input or {}
    subagent_type = str(tool_input.get("subagent_type") or "")
    if subagent_type not in ctx.cfg.roles.tester:
        return None

    try:
        prompt_text = tool_input.get("prompt") or ""
        specs = approval._extract_spec_refs(ctx, prompt_text)
        if not specs:
            return None

        ok, head_blobs = _head_blob_ids_strict(ctx, specs)
        if not ok:
            return _deny(
                ctx, specs,
                "specguard: could not verify the spec against git; hard mode denies.",
            )

        launches = ctx.state.read_json("launches.json", {}) or {}
        reports = ctx.state.read_json("advocate-reports.json", {}) or {}

        needing = []
        for spec in specs:
            data = approval._read_spec_bytes(ctx, spec)
            if data is None:
                needing.append(spec)
                continue
            sha256 = approval._sha256_hex(data)
            head_blob = head_blobs.get(spec)
            if head_blob is not None and approval._git_blob_sha1(data) == head_blob:
                continue
            last_launch = launches.get(spec)
            if last_launch and last_launch.get("sha256") == sha256:
                continue
            after_ts = last_launch.get("at") if last_launch else None
            if _has_advocate_report(reports, _stem(spec), after_ts):
                continue
            needing.append(spec)

        if not needing:
            return None
        return _deny(ctx, needing, _needs_advocate_reason(ctx, needing))
    except Exception:
        return _deny(ctx, [], "specguard: hard-mode advocate gate failed; the call is refused.")
