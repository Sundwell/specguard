import os
import re

from specguard import core, paths

_FILE_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit")
_WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

_PROTECTED_REASON = (
    "specguard's state directory and the plugin root are protected from every role. "
    "Do not write there, and do not run Bash commands naming either."
)
_CONFIG_ASK_REASON = (
    "This changes specguard configuration or a Claude settings file. Confirm with the user before this edit."
)
_CONFIG_DENY_REASON = "Role sessions do not edit specguard configuration or Claude settings files."
_CLAUDE_DENY_REASON = (
    "Starting Claude Code from Bash is refused in a specguard project, it would bypass the mode and approval "
    "rules. Ask the user to run it in his own terminal; to search text, use the Grep tool."
)
_LAUNCH_NAMED_REASON = (
    "Launch the tester and the advocate as plain subagents; a named, team or isolated launch loses its role."
)
_SKILL_MODE_REASON = (
    "The mode switch is his to run directly as /specguard:mode; a model-invoked Skill call to it is refused."
)


def _tool_path(tool_input):
    return tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")


def _cwd(ctx):
    return ctx.data.get("cwd") or ctx.project_dir


def _hidden_list_text(cfg):
    items = list(cfg.hidden.segments) + list(cfg.hidden.names) + list(cfg.hidden.paths)
    return ", ".join(items) if items else "the hidden paths"


def _roots_text(cfg):
    test_roots = list(cfg.tests.segments) + list(cfg.tests.paths)
    tests = ", ".join(test_roots) if test_roots else "the test folders"
    docs = ", ".join(cfg.docs_dirs) if cfg.docs_dirs else "the docs folders"
    return tests, docs


# ---- rule 1, protected state dir and plugin root ----------------------------------------------

_ENTRYPOINT_NAME = "specguard_hook.py"
_STATUS_FLAGS = ("--status", "--check-config")


def _protected_patterns(ctx):
    patterns = set()
    patterns.add(ctx.state.dir)
    home = os.path.expanduser("~")
    patterns.add(os.path.join(home, ".local", "state", "specguard"))
    patterns.add("~/.local/state/specguard")
    patterns.add("$HOME/.local/state/specguard")
    patterns.add("${HOME}/.local/state/specguard")
    patterns.add("$XDG_STATE_HOME/specguard")
    patterns.add("${XDG_STATE_HOME}/specguard")
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        patterns.add(os.path.join(xdg, "specguard"))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        patterns.add(plugin_root)
    patterns.add("$CLAUDE_PLUGIN_ROOT")
    patterns.add("${CLAUDE_PLUGIN_ROOT}")
    return [p for p in patterns if p]


def _bash_hits_protected(ctx, command):
    if not command:
        return False
    return any(pattern in command for pattern in _protected_patterns(ctx))


def _is_status_invocation(command):
    tokens = paths.tokenize(command)
    if tokens is None:
        return False
    if any(t.operator for t in tokens):
        return False
    words = [t.text for t in tokens]
    if not words or words[-1] not in _STATUS_FLAGS:
        return False
    return any(w.endswith(_ENTRYPOINT_NAME) for w in words[:-1])


def _rule_protected(ctx):
    tool = ctx.tool_name
    ti = ctx.tool_input

    if tool == "Bash":
        command = ti.get("command", "")
        if _bash_hits_protected(ctx, command) and not _is_status_invocation(command):
            return core.deny(ctx, "1", _PROTECTED_REASON)
        return None

    if tool in _WRITE_TOOLS:
        raw = _tool_path(ti)
        if raw:
            info = paths.classify(ctx, raw, _cwd(ctx))
            if info.in_state_dir or info.in_plugin_root:
                return core.deny(ctx, "1", _PROTECTED_REASON)
    return None


# ---- rule 2, config and settings ---------------------------------------------------------------

_SETTINGS_BASH_RE = re.compile(
    r"(^|[^A-Za-z0-9_.-])(specguard\.json|settings\.local\.json|settings\.json)([^A-Za-z0-9_.-]|$)"
)

_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z0-9_.-]+)\1")
_HEREDOC_SINKS = {"cat", "tee"}
_CODE_WORD_RE = re.compile(
    r"(^|[^A-Za-z0-9_.-])(python[0-9.]*|node|nodejs|bash|sh|zsh|dash|ksh|fish|ruby|perl|php|lua|deno|bun|eval|source|exec|xargs)"
    r"([^A-Za-z0-9_.-]|$)"
)

_READONLY_SPLIT_OPS = {";", "&&", "||", "|", "\n"}
_READONLY_PROGRAMS = {
    "cat", "less", "head", "tail", "grep", "rg", "ls", "stat", "wc", "diff", "file",
    "echo", "printf", "sort", "uniq", "cut", "tr", "pwd", "cd", "basename", "dirname",
    "realpath", "readlink", "tree", "du", "true", "nl", "column", "comm", "md5sum", "sha256sum",
}
_FIND_DENY_EXACT = {"-exec", "-execdir", "-delete", "-ok"}
_GIT_READONLY_SUBCOMMANDS = {"status", "log", "diff", "show", "blame", "grep", "ls-files"}

_OUT_REDIR_RE = re.compile(r"^(\d*)(>{1,2})(.*)$")
_DUP_TARGET_RE = re.compile(r"^\d+$")
_SED_WRITE_RE = re.compile(r"(?:^|[;\n{])\s*[0-9]*\s*[wW](?:\s|$)|/[a-zA-Z]*[wW](?:\s|$)")


def _basename_word(text):
    return text.rsplit("/", 1)[-1]


def _redirection_target_safe(text):
    return text == "/dev/null"


def _strip_safe_redirections(tokens):
    """Drop fd-dup (2>&1, >&2, 1>&2) and *>/dev/null redirections; a real write target is left in place."""
    out = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]

        if tok.operator and tok.text == "&" and i + 1 < n:
            nxt = tokens[i + 1]
            if not nxt.operator and not nxt.quoted:
                m = _OUT_REDIR_RE.match(nxt.text)
                if m and not m.group(1) and _redirection_target_safe(m.group(3)):
                    i += 2
                    continue

        if not tok.operator and not tok.quoted:
            m = _OUT_REDIR_RE.match(tok.text)
            if m:
                rest = m.group(3)
                if rest and _redirection_target_safe(rest):
                    i += 1
                    continue
                if not rest and i + 1 < n:
                    nxt = tokens[i + 1]
                    if nxt.operator and nxt.text == "&" and i + 2 < n:
                        tgt = tokens[i + 2]
                        if not tgt.operator and _DUP_TARGET_RE.match(tgt.text):
                            i += 3
                            continue
                    elif not nxt.operator and _redirection_target_safe(nxt.text):
                        i += 2
                        continue

        out.append(tok)
        i += 1
    return out


def _sed_word_is_inplace(word):
    return word.startswith("-i") or word == "--in-place" or word.startswith("--in-place=")


def _sed_word_has_n(word):
    if word in ("-n", "--quiet", "--silent"):
        return True
    return word.startswith("-") and not word.startswith("--") and "n" in word[1:]


def _sed_is_readonly(rest):
    if any(_sed_word_is_inplace(w) for w in rest):
        return False
    if not any(_sed_word_has_n(w) for w in rest):
        return False
    return not any(_SED_WRITE_RE.search(w) for w in rest)


def _group_is_readonly(words):
    if not words:
        return False
    head = _basename_word(words[0])
    rest = words[1:]

    if head in _READONLY_PROGRAMS:
        return True
    if head == "sed":
        return _sed_is_readonly(rest)
    if head == "jq":
        return not any(w == "-i" for w in rest)
    if head == "find":
        return not any(w in _FIND_DENY_EXACT or w.startswith("-fprint") for w in rest)
    if head == "git":
        return bool(rest) and rest[0] in _GIT_READONLY_SUBCOMMANDS
    return False


def _bash_is_readonly(command):
    if not command or not command.strip():
        return False
    tokens = paths.tokenize(command)
    if tokens is None:
        return False
    tokens = _strip_safe_redirections(tokens)

    for tok in tokens:
        if tok.operator:
            if tok.text not in _READONLY_SPLIT_OPS:
                return False
        elif not tok.quoted and (">" in tok.text or _basename_word(tok.text) == "tee"):
            return False

    groups = [[]]
    for tok in tokens:
        if tok.operator:
            groups.append([])
        else:
            groups[-1].append(tok.text)
    while groups and not groups[-1]:
        groups.pop()
    if not groups:
        return False
    return all(_group_is_readonly(g) for g in groups)


def _shell_c_inner(group):
    """If `group` (a list of tokens) invokes bash/sh/zsh -c '<command>', return that inner command text."""
    words = [t.text for t in group]
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if _VAR_ASSIGN_RE.match(w):
            i += 1
            continue
        if w == "timeout":
            i += 1
            if i < n:
                i += 1
            continue
        if w in _PREFIX_WORDS:
            i += 1
            continue
        break
    if i >= n or _basename_word(words[i]) not in _SHELL_WORDS:
        return None
    if i + 1 < n and words[i + 1] == "-c" and i + 2 < n:
        return group[i + 2].text
    return None


def _simple_commands(command):
    """Split `command` into its simple commands on ; && || | newline, recursing into bash -c / sh -c strings."""
    tokens = paths.tokenize(command)
    if tokens is None:
        return
    tokens = _strip_safe_redirections(tokens)

    groups = [[]]
    for i, tok in enumerate(tokens):
        redirect_amp = tok.operator and tok.text == "&" and i + 1 < len(tokens) and tokens[i + 1].text.startswith(">")
        if tok.operator and not redirect_amp:
            groups.append([])
        else:
            groups[-1].append(tok)
    while groups and not groups[-1]:
        groups.pop()

    for group in groups:
        if not group:
            continue
        inner = _shell_c_inner(group)
        if inner is not None:
            for sub in _simple_commands(inner):
                yield sub
            continue
        yield " ".join(t.raw for t in group)


def _strip_data_heredocs(command):
    """Drop the body of heredocs fed to cat or tee; a body an interpreter may run stays in the scanned text."""
    lines = command.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _HEREDOC_RE.search(line)
        if not m or line[:m.start()].count("'") % 2 or line[:m.start()].count('"') % 2:
            continue
        segment = re.split(r"[;&|]", line[:m.start()])[-1].split()
        while segment and _VAR_ASSIGN_RE.match(segment[0]):
            segment.pop(0)
        if not segment or _basename_word(segment[0]) not in _HEREDOC_SINKS or _CODE_WORD_RE.search(line):
            continue
        end = i
        while end < len(lines) and lines[end].lstrip("\t") != m.group(2):
            end += 1
        if not m.group(1) and any("$(" in b or "`" in b for b in lines[i:end]):
            continue
        i = end + 1 if end < len(lines) else end
        if end < len(lines):
            out.append(lines[end])
    return "\n".join(out)


def _rule_config(ctx):
    tool = ctx.tool_name
    ti = ctx.tool_input

    hit = False
    hit_segments = []
    if tool == "Bash":
        command = _strip_data_heredocs(ti.get("command", "") or "")
        hit_segments = [seg for seg in _simple_commands(command) if _SETTINGS_BASH_RE.search(seg)]
        hit = bool(hit_segments)
    elif tool in _WRITE_TOOLS:
        raw = _tool_path(ti)
        if raw:
            info = paths.classify(ctx, raw, _cwd(ctx))
            hit = info.is_config or info.is_settings

    if not hit:
        return None
    if ctx.role in ("tester", "advocate"):
        return core.deny(ctx, "2", _CONFIG_DENY_REASON)
    if tool == "Bash" and all(_bash_is_readonly(seg) for seg in hit_segments):
        return None
    return core.ask(ctx, "2", _CONFIG_ASK_REASON)


# ---- rule 3, no child Claude --------------------------------------------------------------------

_PREFIX_WORDS = {"env", "exec", "nohup", "nice", "setsid", "sudo", "command", "time", "xargs"}
_SHELL_WORDS = {"bash", "sh", "zsh"}
_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ANTHROPIC_MARKER = "@anthropic-ai/claude-code"


def _is_claude_word(text):
    return text == "claude" or text.endswith("/claude")


def _scan_claude(tokens):
    if any(_ANTHROPIC_MARKER in t.text for t in tokens if not t.operator):
        return True

    n = len(tokens)
    i = 0
    expect_start = True
    while i < n:
        tok = tokens[i]
        if tok.operator:
            expect_start = True
            i += 1
            continue
        if not expect_start:
            i += 1
            continue

        j = i
        while j < n and not tokens[j].operator:
            word = tokens[j].text
            if _VAR_ASSIGN_RE.match(word):
                j += 1
                continue
            if word == "timeout":
                j += 1
                if j < n and not tokens[j].operator:
                    j += 1
                continue
            if word in _PREFIX_WORDS:
                j += 1
                continue
            break

        if j < n and not tokens[j].operator:
            head = tokens[j].text
            if _is_claude_word(head):
                return True
            base = head.rsplit("/", 1)[-1]
            if base in _SHELL_WORDS:
                k = j + 1
                if k < n and not tokens[k].operator and tokens[k].text == "-c":
                    k += 1
                    if k < n and not tokens[k].operator:
                        inner_src = tokens[k].text
                        inner_tokens = paths.tokenize(inner_src)
                        if inner_tokens is not None:
                            if _scan_claude(inner_tokens):
                                return True
                        elif _raw_text_denies_claude(inner_src):
                            return True
            i = j + 1
        else:
            i = j
        expect_start = False
    return False


def _raw_text_denies_claude(command):
    if _ANTHROPIC_MARKER in command:
        return True
    return bool(re.search(r"(^|[^A-Za-z0-9_./-])claude($|[^A-Za-z0-9_.-])", command))


def _bash_denies_claude(command):
    if not command:
        return False
    tokens = paths.tokenize(command)
    if tokens is None:
        return _raw_text_denies_claude(command)
    return _scan_claude(tokens)


def _rule_no_child_claude(ctx):
    if ctx.tool_name != "Bash":
        return None
    command = ctx.tool_input.get("command", "")
    if _bash_denies_claude(command):
        return core.deny(ctx, "3", _CLAUDE_DENY_REASON)
    return None


def rules_base(ctx):
    for step in (_rule_protected, _rule_config, _rule_no_child_claude):
        out = step(ctx)
        if out is not None:
            return out
    return None


# ---- rule 4, launch part and Skill deny ---------------------------------------------------------

_LAUNCH_FIELDS = ("name", "team_name", "isolation")
_MODE_SKILL_NAMES = ("specguard:mode", "mode")


def rule_launch(ctx):
    tool = ctx.tool_name
    ti = ctx.tool_input

    if tool == "Agent":
        subagent_type = ti.get("subagent_type")
        role_launch = subagent_type in ctx.cfg.roles.tester or subagent_type in ctx.cfg.roles.advocate
        if role_launch and any(ti.get(field) for field in _LAUNCH_FIELDS):
            return core.deny(ctx, "4", _LAUNCH_NAMED_REASON)
        return None

    if tool == "Skill":
        skill = ti.get("skill")
        if skill in _MODE_SKILL_NAMES:
            return core.deny(ctx, "4", _SKILL_MODE_REASON)
        return None

    return None


# ---- rule 5, tester -------------------------------------------------------------------------------


def _tester_hidden_reason(cfg):
    return (
        "The tester does not read or change implementation code ({}). "
        "Work from the spec in {}; report anything missing as a spec gap."
    ).format(_hidden_list_text(cfg), cfg.specs_dir)


def _tester_grep_reason(cfg):
    tests, docs = _roots_text(cfg)
    return "The tester greps only inside {} or {} of {}.".format(tests, docs, cfg.repo)


def _tester_bash_reason(cfg):
    tests, docs = _roots_text(cfg)
    return "The tester may not run commands that name {}. Allowed roots are {} and {}.".format(
        _hidden_list_text(cfg), tests, docs
    )


def _glob_hidden(ctx, tool_input):
    cfg = ctx.cfg
    path = tool_input.get("path")
    if path:
        info = paths.classify(ctx, path, _cwd(ctx))
        if info.repo_rel is not None and info.hidden and not info.tester_readable:
            return True

    pattern = tool_input.get("pattern")
    if pattern:
        pattern = str(pattern)
        segs = pattern.replace("\\", "/").split("/")
        if any(seg in cfg.hidden.segments or seg in cfg.hidden.names for seg in segs):
            return True
        rx = paths.path_regex(cfg)
        if rx and rx.search(pattern):
            return True
    return False


def _tester_grep_allowed(ctx, raw):
    if not raw:
        return False
    info = paths.classify(ctx, raw, _cwd(ctx))
    if info.repo_rel is None:
        return False
    if info.hidden and not info.tester_readable:
        return False
    return info.test or info.docs or info.tester_readable


_FORBIDDEN_BASH_CHARS = set(" \t/*?[]{};&|$()<>`")


def _blankable(token, cfg):
    if not token.quoted:
        return False
    content = token.text
    if not content:
        return False
    if any(c in _FORBIDDEN_BASH_CHARS for c in content):
        return False
    bare = content.rstrip("/")
    if bare in cfg.hidden.segments or bare in cfg.hidden.names:
        return False
    stripped_paths = [p.strip("/") for p in cfg.hidden.paths]
    if bare.strip("/") in stripped_paths:
        return False
    return True


def _tester_bash_denied(ctx, command):
    if not command:
        return False
    cfg = ctx.cfg
    stripped = command
    for readable in cfg.tester_readable:
        stripped = re.sub(r"(?<![A-Za-z0-9_.-])" + re.escape(readable) + r"(?![A-Za-z0-9_.-])", "", stripped)

    hidden_rx = paths.hidden_word_regex(cfg)
    name_rx = paths.name_regex(cfg)
    path_rx = paths.path_regex(cfg)
    if not (hidden_rx or name_rx or path_rx):
        return False

    tokens = paths.tokenize(stripped)
    if tokens is None:
        rest = stripped
    else:
        rest = " ".join("" if _blankable(t, cfg) else t.raw for t in tokens)

    return bool(
        (hidden_rx and hidden_rx.search(rest))
        or (name_rx and name_rx.search(rest))
        or (path_rx and path_rx.search(rest))
    )


def _rule_tester(ctx):
    tool = ctx.tool_name
    ti = ctx.tool_input

    if tool in _FILE_TOOLS:
        raw = _tool_path(ti)
        if raw:
            info = paths.classify(ctx, raw, _cwd(ctx))
            if info.repo_rel is not None and info.hidden and not info.tester_readable:
                return core.deny(ctx, "5", _tester_hidden_reason(ctx.cfg))
        return None

    if tool == "Grep":
        if not _tester_grep_allowed(ctx, ti.get("path")):
            return core.deny(ctx, "5", _tester_grep_reason(ctx.cfg))
        return None

    if tool == "Glob":
        if _glob_hidden(ctx, ti):
            return core.deny(ctx, "5", _tester_hidden_reason(ctx.cfg))
        return None

    if tool == "Bash":
        if _tester_bash_denied(ctx, ti.get("command", "")):
            return core.deny(ctx, "5", _tester_bash_reason(ctx.cfg))
        return None

    return None


# ---- rule 6, advocate -----------------------------------------------------------------------------


def _advocate_readonly_reason(cfg):
    return "The devils-advocate is read-only. It writes only its report under {}.".format(cfg.reports.advocate)


def _advocate_write_reason(cfg):
    return "The devils-advocate writes only its report under {}.".format(cfg.reports.advocate)


def _advocate_grep_path_reason():
    return "The devils-advocate greps with an explicit path."


def _advocate_answers_reason(cfg):
    return "The devils-advocate works from the spec, not from earlier reviews of the tests."


def _advocate_hidden_reason(cfg):
    return "The devils-advocate sees what the tester sees and does not read {}.".format(_hidden_list_text(cfg))


def _advocate_grep_roots_reason(cfg):
    tests, docs = _roots_text(cfg)
    return "The devils-advocate greps only inside {} or {} of {}, or outside {}.".format(
        tests, docs, cfg.repo, cfg.repo
    )


def _is_ancestor_or_equal(candidate_abs, target_abs):
    candidate_abs = os.path.normpath(candidate_abs)
    target_abs = os.path.normpath(target_abs)
    return target_abs == candidate_abs or target_abs.startswith(candidate_abs + os.sep)


def _rule_advocate(ctx):
    tool = ctx.tool_name
    ti = ctx.tool_input
    cfg = ctx.cfg

    if tool in ("Edit", "MultiEdit", "NotebookEdit", "Bash"):
        return core.deny(ctx, "6", _advocate_readonly_reason(cfg))

    if tool == "Write":
        raw = _tool_path(ti)
        info = paths.classify(ctx, raw, _cwd(ctx)) if raw else None
        if info is None or not info.report_advocate:
            return core.deny(ctx, "6", _advocate_write_reason(cfg))
        ctx.state.record_advocate_report(info.abs, ctx.agent_id)
        return None

    if tool == "Glob":
        if _glob_hidden(ctx, ti):
            return core.deny(ctx, "6", _advocate_hidden_reason(cfg))
        return None

    if tool in ("Read", "Grep"):
        raw = ti.get("file_path") or ti.get("path")
        if tool == "Grep" and not raw:
            return core.deny(ctx, "6", _advocate_grep_path_reason())
        if raw:
            info = paths.classify(ctx, raw, _cwd(ctx))
            if info.advocate_hidden:
                return core.deny(ctx, "6", _advocate_answers_reason(cfg))
            if info.repo_rel is not None and info.hidden and not info.tester_readable:
                return core.deny(ctx, "6", _advocate_hidden_reason(cfg))
            if tool == "Grep":
                ancestor = _is_ancestor_or_equal(info.abs, ctx.repo_dir)
                inside_ok = info.repo_rel is not None and (info.test or info.docs or info.tester_readable)
                if ancestor or (info.repo_rel is not None and not inside_ok):
                    return core.deny(ctx, "6", _advocate_grep_roots_reason(cfg))
        return None

    return None


# ---- rule 7, executor test lock --------------------------------------------------------------------


def _executor_lock_reason(mode_word):
    return (
        "In {} mode the executor does not write tests. The tester writes them from the spec. "
        "If a test looks wrong, tell the user the rule ID, the input and what came back. "
        "If you are a subagent, report this to the main session."
    ).format(mode_word)


def _rule_executor_lock(ctx):
    modes = ctx.modes()
    if not ({"feature", "hard"} & modes):
        return None
    if ctx.tool_name not in _WRITE_TOOLS:
        return None
    raw = _tool_path(ctx.tool_input)
    if not raw:
        return None
    info = paths.classify(ctx, raw, _cwd(ctx))
    if info.repo_rel is not None and info.test:
        mode_word = "hard" if "hard" in modes else "feature"
        return core.deny(ctx, "7", _executor_lock_reason(mode_word))
    return None


def rules_roles(ctx):
    if ctx.role == "tester":
        return _rule_tester(ctx)
    if ctx.role == "advocate":
        return _rule_advocate(ctx)
    return _rule_executor_lock(ctx)
