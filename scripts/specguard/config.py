import json
import os
from types import SimpleNamespace

CONFIG_REL_PATH = os.path.join(".claude", "specguard.json")

DEFAULT_ROLES_TESTER = ["specguard:tester", "tester"]
DEFAULT_ROLES_ADVOCATE = ["specguard:devils-advocate", "devils-advocate"]
DEFAULT_NOTES_TESTER = ".claude/specguard/tester-notes.md"
DEFAULT_NOTES_ADVOCATE = ".claude/specguard/advocate-notes.md"
DEFAULT_REPORTS_TESTER = ".specguard/reports/tester"
DEFAULT_REPORTS_ADVOCATE = ".specguard/reports/spec-review"
MAX_STOP_TIMEOUT = 1800

_TOP_KEYS = {
    "version", "repo", "roles", "hidden", "tester_readable", "tests", "docs_dirs",
    "specs_dir", "notes", "reports", "plan_file", "money_topics", "hands_on_tag",
    "modes", "stop", "api_report", "visual",
}
_ROLES_KEYS = {"tester", "advocate"}
_HIDDEN_KEYS = {"segments", "names", "paths"}
_TESTS_KEYS = {"segments", "paths", "file_regex"}
_NOTES_KEYS = {"tester", "advocate"}
_REPORTS_KEYS = {"tester", "advocate", "advocate_hidden"}
_MODES_KEYS = {"default", "approval_default"}
_STOP_KEYS = {
    "modes", "dirty_pathspec", "fresh_paths", "fresh_globs", "exclude_dirs",
    "skip_preset", "skip_regex", "skip_paths", "run", "summary_regex", "timeout",
    "require_glob",
}
_VISUAL_KEYS = {"ui_paths", "deviations_log", "notes"}


def config_path(project_dir):
    return os.path.join(project_dir, CONFIG_REL_PATH)


def load(project_dir):
    with open(config_path(project_dir), "r", encoding="utf-8") as f:
        raw = json.load(f)
    return Config(raw)


def _unknown(problems, prefix, present, known):
    for key in present:
        if key not in known:
            problems.append("unknown key {}{}".format(prefix, key))


def validate(raw):
    problems = []
    if not isinstance(raw, dict):
        return ["config is not a JSON object"]
    _unknown(problems, "", raw.keys(), _TOP_KEYS)
    if "version" not in raw:
        problems.append("missing required key version")
    elif raw.get("version") != 1:
        problems.append("unsupported version {!r}".format(raw.get("version")))

    roles = raw.get("roles")
    if roles is not None:
        if not isinstance(roles, dict):
            problems.append("roles must be an object")
        else:
            _unknown(problems, "roles.", roles.keys(), _ROLES_KEYS)

    hidden = raw.get("hidden")
    if hidden is not None:
        if not isinstance(hidden, dict):
            problems.append("hidden must be an object")
        else:
            _unknown(problems, "hidden.", hidden.keys(), _HIDDEN_KEYS)

    tests = raw.get("tests")
    if tests is not None:
        if not isinstance(tests, dict):
            problems.append("tests must be an object")
        else:
            _unknown(problems, "tests.", tests.keys(), _TESTS_KEYS)

    notes = raw.get("notes")
    if notes is not None:
        if not isinstance(notes, dict):
            problems.append("notes must be an object")
        else:
            _unknown(problems, "notes.", notes.keys(), _NOTES_KEYS)

    reports = raw.get("reports")
    if reports is not None:
        if not isinstance(reports, dict):
            problems.append("reports must be an object")
        else:
            _unknown(problems, "reports.", reports.keys(), _REPORTS_KEYS)

    modes = raw.get("modes")
    if modes is not None:
        if not isinstance(modes, dict):
            problems.append("modes must be an object")
        else:
            _unknown(problems, "modes.", modes.keys(), _MODES_KEYS)
            default = modes.get("default", "simple")
            if default not in ("simple", "feature", "hard", "visual"):
                problems.append("modes.default {!r} is not a known mode".format(default))

    stop = raw.get("stop")
    if stop is not None:
        if not isinstance(stop, dict):
            problems.append("stop must be an object")
        else:
            _unknown(problems, "stop.", stop.keys(), _STOP_KEYS)
            timeout = stop.get("timeout", 300)
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                problems.append("stop.timeout must be a positive number")
            elif timeout > MAX_STOP_TIMEOUT:
                problems.append("stop.timeout {} exceeds the {} second ceiling".format(timeout, MAX_STOP_TIMEOUT))
            for required in ("dirty_pathspec", "fresh_paths", "fresh_globs", "exclude_dirs", "run"):
                if required not in stop:
                    problems.append("stop.{} is required when stop is set".format(required))

    visual = raw.get("visual")
    if visual is not None:
        if not isinstance(visual, dict):
            problems.append("visual must be an object")
        else:
            _unknown(problems, "visual.", visual.keys(), _VISUAL_KEYS)

    return problems


class Config:
    def __init__(self, raw):
        self.raw = raw
        self.version = raw.get("version", 1)
        self.repo = raw.get("repo", ".")

        roles = raw.get("roles") or {}
        self.roles = SimpleNamespace(
            tester=roles.get("tester", list(DEFAULT_ROLES_TESTER)),
            advocate=roles.get("advocate", list(DEFAULT_ROLES_ADVOCATE)),
        )

        hidden = raw.get("hidden") or {}
        self.hidden = SimpleNamespace(
            segments=hidden.get("segments", []),
            names=hidden.get("names", []),
            paths=hidden.get("paths", []),
        )

        self.tester_readable = raw.get("tester_readable", [])

        tests = raw.get("tests") or {}
        self.tests = SimpleNamespace(
            segments=tests.get("segments", []),
            paths=tests.get("paths", []),
            file_regex=tests.get("file_regex", ""),
        )

        self.docs_dirs = raw.get("docs_dirs", ["docs"])
        self.specs_dir = raw.get("specs_dir", "docs/specs")

        notes = raw.get("notes") or {}
        self.notes = SimpleNamespace(
            tester=notes.get("tester", DEFAULT_NOTES_TESTER),
            advocate=notes.get("advocate", DEFAULT_NOTES_ADVOCATE),
        )

        reports = raw.get("reports") or {}
        self.reports = SimpleNamespace(
            tester=reports.get("tester", DEFAULT_REPORTS_TESTER),
            advocate=reports.get("advocate", DEFAULT_REPORTS_ADVOCATE),
            advocate_hidden=reports.get("advocate_hidden", []),
        )

        self.plan_file = raw.get("plan_file")
        self.money_topics = raw.get("money_topics", [])
        self.hands_on_tag = raw.get("hands_on_tag", "[hands-on]")

        modes = raw.get("modes") or {}
        self.modes = SimpleNamespace(
            default=modes.get("default", "simple"),
            approval_default=modes.get("approval_default", True),
        )

        stop = raw.get("stop")
        if stop is not None:
            self.stop = SimpleNamespace(
                modes=stop.get("modes", ["feature", "hard"]),
                dirty_pathspec=stop.get("dirty_pathspec"),
                fresh_paths=stop.get("fresh_paths"),
                fresh_globs=stop.get("fresh_globs"),
                exclude_dirs=stop.get("exclude_dirs"),
                skip_preset=stop.get("skip_preset", "none"),
                skip_regex=stop.get("skip_regex"),
                skip_paths=stop.get("skip_paths"),
                run=stop.get("run"),
                summary_regex=stop.get("summary_regex", "FAIL|Error|failed"),
                timeout=stop.get("timeout", 300),
                require_glob=stop.get("require_glob"),
            )
        else:
            self.stop = SimpleNamespace(
                modes=["feature", "hard"],
                dirty_pathspec=None,
                fresh_paths=None,
                fresh_globs=None,
                exclude_dirs=None,
                skip_preset="none",
                skip_regex=None,
                skip_paths=None,
                run=None,
                summary_regex="FAIL|Error|failed",
                timeout=300,
                require_glob=None,
            )

        self.api_report = raw.get("api_report")

        visual = raw.get("visual")
        if visual is not None:
            self.visual = SimpleNamespace(
                ui_paths=visual.get("ui_paths", []),
                deviations_log=visual.get("deviations_log"),
                notes=visual.get("notes"),
            )
        else:
            self.visual = None
