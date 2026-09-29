import os
import re

from specguard.state import compute_state_dir

_OPERATOR_CHARS = (";", "|", "&", "(", ")", "`")


class Info:
    def __init__(self, abs, repo_rel):
        self.abs = abs
        self.repo_rel = repo_rel
        self.hidden = False
        self.tester_readable = False
        self.test = False
        self.docs = False
        self.spec = False
        self.in_state_dir = False
        self.in_plugin_root = False
        self.is_config = False
        self.is_settings = False
        self.report_tester = False
        self.report_advocate = False
        self.advocate_hidden = False

    def __repr__(self):
        fields = ", ".join(
            "{}={!r}".format(k, v) for k, v in sorted(vars(self).items())
        )
        return "Info({})".format(fields)


class Token:
    __slots__ = ("raw", "text", "quoted", "operator")

    def __init__(self, raw, text, quoted=False, operator=False):
        self.raw = raw
        self.text = text
        self.quoted = quoted
        self.operator = operator

    def __repr__(self):
        return "Token(raw={!r}, text={!r}, quoted={}, operator={})".format(
            self.raw, self.text, self.quoted, self.operator
        )


def _rel(abs_path, base_dir):
    if not base_dir:
        return None
    base = os.path.normpath(base_dir)
    normalized = os.path.normpath(abs_path)
    if normalized == base:
        return ""
    prefix = base + os.sep
    if normalized.startswith(prefix):
        return normalized[len(prefix):].replace(os.sep, "/")
    return None


def _is_or_under(rel, prefix):
    if rel is None or not prefix:
        return False
    prefix = prefix.strip("/")
    return rel == prefix or rel.startswith(prefix + "/")


def _is_or_under_abs(abs_path, base_dir):
    if not base_dir:
        return False
    base = os.path.normpath(base_dir)
    normalized = os.path.normpath(abs_path)
    return normalized == base or normalized.startswith(base + os.sep)


def classify(ctx_or_cfg, path, cwd):
    cfg = getattr(ctx_or_cfg, "cfg", ctx_or_cfg)
    project_dir = getattr(ctx_or_cfg, "project_dir", None)
    base = cwd or project_dir or os.getcwd()
    abs_path = path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))

    repo_dir = getattr(ctx_or_cfg, "repo_dir", None)
    if repo_dir is None:
        repo_dir = os.path.normpath(os.path.join(project_dir or base, cfg.repo))

    repo_rel = _rel(abs_path, repo_dir)
    root_rel = _rel(abs_path, project_dir or base)

    info = Info(abs=abs_path, repo_rel=repo_rel)

    if repo_rel is not None:
        segs = repo_rel.split("/")
        info.hidden = (
            any(seg in cfg.hidden.segments for seg in segs)
            or os.path.basename(repo_rel) in cfg.hidden.names
            or any(_is_or_under(repo_rel, p) for p in cfg.hidden.paths)
        )
        info.tester_readable = repo_rel in cfg.tester_readable
        info.test = (
            any(seg in cfg.tests.segments for seg in segs)
            or any(_is_or_under(repo_rel, p) for p in cfg.tests.paths)
            or bool(cfg.tests.file_regex and re.search(cfg.tests.file_regex, repo_rel))
        )
        info.docs = any(_is_or_under(repo_rel, d) for d in cfg.docs_dirs)
        info.spec = (
            _is_or_under(repo_rel, cfg.specs_dir)
            and repo_rel.endswith(".md")
            and os.path.basename(repo_rel) != "README.md"
        )

    info.is_config = (
        os.path.basename(abs_path) == "specguard.json"
        and os.path.basename(os.path.dirname(abs_path)) == ".claude"
    )
    info.is_settings = (
        os.path.basename(abs_path) in ("settings.json", "settings.local.json")
        and os.path.basename(os.path.dirname(abs_path)) == ".claude"
    )

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    info.in_plugin_root = _is_or_under_abs(abs_path, plugin_root)

    state_dir = getattr(getattr(ctx_or_cfg, "state", None), "dir", None)
    if state_dir is None:
        root_for_state = project_dir or os.environ.get("CLAUDE_PROJECT_DIR")
        state_dir = compute_state_dir(root_for_state) if root_for_state else None
    info.in_state_dir = _is_or_under_abs(abs_path, state_dir)

    if root_rel is not None:
        info.report_tester = _is_or_under(root_rel, cfg.reports.tester)
        info.report_advocate = _is_or_under(root_rel, cfg.reports.advocate)
        info.advocate_hidden = any(_is_or_under(root_rel, p) for p in cfg.reports.advocate_hidden)

    return info


def _is_single_quoted_span(raw):
    if len(raw) < 2:
        return False
    quote = raw[0]
    if quote not in ("'", '"') or raw[-1] != quote:
        return False
    body = raw[1:-1]
    if quote == "'":
        return "'" not in body
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            i += 2
            continue
        if body[i] == '"':
            return False
        i += 1
    return True


def tokenize(command):
    if command is None:
        return None
    try:
        return _tokenize(str(command))
    except ValueError:
        return None


def _tokenize(command):
    tokens = []
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch in (" ", "\t"):
            i += 1
            continue
        if ch == "\n":
            tokens.append(Token("\n", "\n", operator=True))
            i += 1
            continue
        if command.startswith("$(", i):
            tokens.append(Token("$(", "$(", operator=True))
            i += 2
            continue
        two = command[i:i + 2]
        if two in ("&&", "||"):
            tokens.append(Token(two, two, operator=True))
            i += 2
            continue
        if ch in _OPERATOR_CHARS:
            tokens.append(Token(ch, ch, operator=True))
            i += 1
            continue
        raw_parts, text_parts = [], []
        while i < n:
            c = command[i]
            if c in (" ", "\t", "\n") or c in _OPERATOR_CHARS or command.startswith("$(", i):
                break
            if c in ("'", '"'):
                quote = c
                raw_parts.append(c)
                i += 1
                while i < n and command[i] != quote:
                    if quote == '"' and command[i] == "\\" and i + 1 < n and command[i + 1] in ('"', "\\", "$", "`"):
                        raw_parts.append(command[i])
                        raw_parts.append(command[i + 1])
                        text_parts.append(command[i + 1])
                        i += 2
                        continue
                    raw_parts.append(command[i])
                    text_parts.append(command[i])
                    i += 1
                if i >= n:
                    raise ValueError("unterminated quote in command")
                raw_parts.append(quote)
                i += 1
                continue
            if c == "\\" and i + 1 < n:
                raw_parts.append(c)
                raw_parts.append(command[i + 1])
                text_parts.append(command[i + 1])
                i += 2
                continue
            raw_parts.append(c)
            text_parts.append(c)
            i += 1
        raw = "".join(raw_parts)
        tokens.append(Token(raw, "".join(text_parts), quoted=_is_single_quoted_span(raw)))
    return tokens


def _word_regex(words):
    escaped = [re.escape(w) for w in (words or []) if w]
    if not escaped:
        return None
    return re.compile(r"(^|[^A-Za-z0-9_.-])(" + "|".join(escaped) + r")([^A-Za-z0-9_.-]|$)")


def hidden_word_regex(cfg):
    return _word_regex(cfg.hidden.segments)


def name_regex(cfg):
    return _word_regex(cfg.hidden.names)


def path_regex(cfg):
    paths = getattr(cfg.hidden, "paths", []) or []
    escaped = [re.escape(p.strip("/")) for p in paths if p]
    if not escaped:
        return None
    return re.compile(r"(^|[^A-Za-z0-9_.-])(" + "|".join(escaped) + r")($|[^A-Za-z0-9_.-])")
