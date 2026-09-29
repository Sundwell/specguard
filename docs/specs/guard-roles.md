# guard-roles

The PreToolUse rules that depend on who is calling - what the tester may reach (rule 5), what the devils-advocate may reach and write (rule 6) and the executor test lock in feature and hard mode (rule 7). The rules that hold for every role are in `guard-shared.md`.

## Tests

`tests/test_spec_guard_roles.py`

## Public API

Everything is driven through `tests/helpers.py` and the hook entrypoint. Nothing else is visible.

- `helpers.make_project(tmp, config=<dict>)` writes `.claude/specguard.json` into `tmp` and returns it. A test creates any directory or file it needs itself; the guard only looks at path names, so files need not exist.
- `helpers.pre_tool_use(session_id, tool_name, tool_input, agent_type=<str or omitted>, agent_id=<str or omitted>)` builds the event payload. An omitted `agent_type` is the executor. `specguard:tester` and the bare `tester` are the tester, `specguard:devils-advocate` and the bare `devils-advocate` are the advocate. Any other `agent_type`, for example `oh-my-claudecode:executor`, is an executor.
- `helpers.run_hook("PreToolUse", payload, project_dir, state_home=<dir>)` returns `(exit_code, stdout_json, stderr)`. The exit code is always 0.
  - Allow prints nothing, `stdout_json` is `None`.
  - Deny gives `stdout_json["hookSpecificOutput"]["permissionDecision"] == "deny"` and a non-empty `permissionDecisionReason`.
- Which rule fired is not visible in the output. Where a row names a rule, read the last line of `<state_home>/specguard/<key>/log.jsonl`, where `<key>` is the absolute project path with every `/` replaced by `-`. The line is JSON with `"ev"` `"deny"`, `"rule"` the rule number as a string (`"5"`), `"role"` and `"tool"`.
- Tool inputs. `Read`, `Edit`, `Write`, `MultiEdit` take `file_path`, `NotebookEdit` takes `notebook_path`, `Grep` takes `path` (and `pattern`), `Glob` takes `pattern` and an optional `path`, `Bash` takes `command`. A path may be absolute or relative to the project directory, which is the hook's working directory.
- Only for GR-9, the file `<state_home>/specguard/<key>/advocate-reports.json` is a JSON object whose keys are the absolute paths of reports the advocate was allowed to write, each mapping to a list of `{"agent_id": ..., "at": <number>}`.
- Fixture config `ROLES`. The mode is set through `modes.default`, `feature` here, and approval is off so no spec gate interferes.

```json
{"version": 1, "repo": ".", "modes": {"default": "feature", "approval_default": false},
 "hidden": {"segments": ["src"], "names": ["secrets.env"], "paths": ["lib/core"]},
 "tester_readable": ["src/db/schema.ts"],
 "tests": {"segments": ["test"], "paths": ["qa/suites"], "file_regex": "\\.spec\\.ts$"},
 "docs_dirs": ["docs"], "specs_dir": "docs/specs",
 "reports": {"advocate_hidden": [".specguard/reports/tester"]}}
```

- Fixture config `NESTED` - the same as `ROLES` but with `"repo": "app"`, only `"hidden": {"segments": ["src"]}`, `"tests": {"segments": ["test"]}` and no `tester_readable`, `reports` or `paths`. Paths in its rows are relative to the project directory, so `app/src/a.ts` is `src/a.ts` inside the repo and `notes/x.md` lies outside the repo.
- Fixture configs `MODE_<m>` for the rule 7 rows. For `simple`, `feature` and `hard` it is `ROLES` with `modes.default` set to `m`. The config accepts only those single modes as a default, so `visual`, `visual+feature` and `visual+hard` are reached at runtime the way the user does it. Use `ROLES` with `modes.default` `simple`, send `helpers.user_prompt_submit(session_id, phrase)` through `run_hook("UserPromptSubmit", ...)` with the phrase `go visual`, `go visual and feature spec` or `go visual and hard mode`, then send the PreToolUse event with the same `session_id` and the same `state_home`.

Rows use the executor unless a role is named. "allow" means empty output, "deny 5" a deny whose log line has rule "5". `<outside>` is a second, separate temporary directory that is not an ancestor of the project directory.

## What a "hidden" and a "test" path is

Paths are judged relative to the repo (`repo` in the config, the project directory for `.`). A path outside the repo is neither hidden nor a test path.

- Hidden - any path segment equals an entry of `hidden.segments`, or the last segment equals an entry of `hidden.names`, or the path is an entry of `hidden.paths` or lies under it (whole segments, so `lib/corex` is not under `lib/core`). A file listed exactly in `tester_readable` is not hidden for the tester and the advocate. `..` and `.` segments are resolved first.
- Test - any path segment equals an entry of `tests.segments`, or the path is an entry of `tests.paths` or lies under it, or the path matches `tests.file_regex`.
- Docs - the path is `docs_dirs` entry or lies under it.

## Rules

### Rule 5, tester

- **GR-1** A Read, Edit, Write, MultiEdit or NotebookEdit by the tester on a hidden path that is not in `tester_readable` is denied (rule 5). Every other path, including paths outside the repo, tests, docs, specs and `tester_readable` files, is allowed by rule 5. `tester` and `specguard:tester` behave identically. Why: the tester writes tests blind, from the spec alone.
- **GR-2** A Grep by the tester is allowed only with an explicit `path` that is inside the repo, is not hidden (unless it is a `tester_readable` file) and is a test path, a docs path or a `tester_readable` file. A missing `path`, the repo root, a path outside the repo, a hidden path, or any other path inside the repo is denied (rule 5). Why: a recursive search from the root would list implementation code.
- **GR-3** A Glob by the tester is denied (rule 5) when its `path` is hidden and not readable, or when its `pattern`, split on `/` and `\`, has a segment equal to a `hidden.segments` or `hidden.names` entry, or contains a `hidden.paths` entry as whole segments. Otherwise it is allowed. A segment that only contains a hidden word (`source`, `srcs`) or a wildcard is not a match. Why: a file listing is a read.
- **GR-4** A Bash command by the tester is denied (rule 5) when it names a hidden thing. The command text is scanned, after removing every `tester_readable` path that stands as a whole word. A hidden segment or name is named when it appears as a whole word - the characters around it are not letters, digits, `_`, `.` or `-` (so `src/a.ts`, `./src/`, `"src"` and `x/'src'/a.ts` name `src`, while `resrc`, `src-old`, `old-secrets.env` and `secrets.env.example` do not). A hidden path is named when it appears with a whole-word boundary on the left (start of text or a character that is not a letter, digit, `_`, `.` or `-`) and `/` or the end or a non-name character on the right, quotes included in the boundary (so `ls "lib/core"` names it and `ls lib/corex` does not). Why: the shell can reach a file in many spellings, so any mention counts.
- **GR-5** One quoting exception. A single-quoted or double-quoted word that stands alone as one shell word, contains no whitespace, none of `/ * ? [ ] { } ; & | $ ( ) < > `` ` `` and is not itself exactly a hidden segment, name or path, is ignored by the scan. So `grep -A5 '"src"' package.json` is allowed while `ls "src"` and `grep -rn x 'src'` are denied. Why: a quoted word like a JSON key is data, not a path, and a real user hit a false positive here.
- **GR-6** A `tester_readable` path exempts only itself. In a Bash command a longer name that merely starts with it (`src/db/schema.ts.bak`, `src/db/schema.tsx`) is still a hidden mention. Why: otherwise a suffix opens the rest of the hidden folder.
- **GR-7** When a rule 5 deny of a Grep or Bash is about allowed roots, the reason text names them - it contains every `tests.segments` and `tests.paths` entry and every `docs_dirs` entry of the config. Why: a blind tester that only gets "denied" wastes turns guessing.

### Rule 6, devils-advocate

- **GR-8** The advocate's Edit, MultiEdit, NotebookEdit and Bash calls are always denied (rule 6), whatever they name. A Write is allowed only when its target lies under the advocate report directory (`reports.advocate`, default `.specguard/reports/spec-review`, relative to the project directory, whole segments, `..` resolved); a Write elsewhere or without a path is denied (rule 6). Why: the advocate is read-only apart from its own report.
- **GR-9** An allowed advocate Write records the absolute path in `advocate-reports.json` with the payload's `agent_id`. A denied Write records nothing. Why: the hard-mode gate later accepts only reports an advocate wrote.
- **GR-10** The advocate's Read is denied (rule 6) on a hidden path that is not `tester_readable`, and on any path under an entry of `reports.advocate_hidden` (relative to the project directory). Every other Read is allowed. Why: the advocate sees what the tester sees and reviews the spec, not earlier test reports.
- **GR-11** The advocate's Grep needs an explicit `path`, and is denied (rule 6) when the path is hidden and not readable, is under `reports.advocate_hidden`, is the repo root or an ancestor of it, or is inside the repo without being a test path, a docs path or a `tester_readable` file. A path outside the repo (and not an ancestor of it) is allowed. A Grep without `path` is denied. Why: the advocate may search the docs and tests or things outside the repo, never the implementation.
- **GR-12** The advocate's Glob follows GR-3 (denied as rule 6).

### Rule 7, executor test lock

- **GR-13** When the session's modes include `feature` or `hard` (`feature`, `hard`, `visual+feature`, `visual+hard`), an executor's Edit, Write, MultiEdit or NotebookEdit on a test path is denied (rule 7). In `simple` and `visual` it is allowed. Non-test paths, paths outside the repo, Read, Grep, Glob and Bash are not touched by rule 7. The tester and the advocate are not subject to rule 7. Why: only the tester writes tests while a gated mode is on. Bash writes are a known limitation of this rule.
- **GR-14** The deny reason of rule 7 names the mode, `hard` when hard is among the modes and `feature` otherwise, and tells the executor to report the rule ID, the input and the observed value to the user when a test looks wrong. Why: an executor that cannot bend a test must escalate.

## Examples

| Rule | Config | Role | Tool | Input | Result |
|---|---|---|---|---|---|
| GR-1 | ROLES | tester | Read | `src/a.ts` | deny 5 |
| GR-1 | ROLES | tester | Read | `packages/x/src/deep/b.ts` (absolute) | deny 5 |
| GR-1 | ROLES | tester | Write | `src/new.ts` | deny 5 |
| GR-1 | ROLES | tester | Edit | `config/secrets.env` | deny 5 |
| GR-1 | ROLES | tester | Read | `lib/core/x.ts` | deny 5 |
| GR-1 | ROLES | tester | Read | `lib/core` | deny 5 |
| GR-1 | ROLES | tester | Read | `docs/../src/a.ts` | deny 5 |
| GR-1 | ROLES | tester | NotebookEdit | `notebook_path` `src/n.ipynb` | deny 5 |
| GR-1 | ROLES | `specguard:tester` | Read | `src/a.ts` | deny 5 |
| GR-1 | ROLES | tester | Read | `src/db/schema.ts` | allow |
| GR-1 | ROLES | tester | Read | `src/db/schema.ts.bak` | deny 5 |
| GR-1 | ROLES | tester | Read | `lib/corex/y.ts` | allow |
| GR-1 | ROLES | tester | Read | `source/a.ts` | allow |
| GR-1 | ROLES | tester | Read | `docs/specs/x.md` | allow |
| GR-1 | ROLES | tester | Write | `test/a.spec.ts` | allow |
| GR-1 | ROLES | tester | Write | `.specguard/reports/tester/x.md` | allow |
| GR-1 | ROLES | tester | Read | `<outside>/src/a.ts` | allow |
| GR-1 | NESTED | tester | Read | `app/src/a.ts` | deny 5 |
| GR-1 | NESTED | tester | Read | `src/a.ts` (outside the repo) | allow |
| GR-1 | NESTED | tester | Read | `app/test/a.ts` | allow |
| GR-2 | ROLES | tester | Grep | `path` `test` | allow |
| GR-2 | ROLES | tester | Grep | `path` `qa/suites` | allow |
| GR-2 | ROLES | tester | Grep | `path` `docs/specs` | allow |
| GR-2 | ROLES | tester | Grep | `path` `app.spec.ts` (matches the regex) | allow |
| GR-2 | ROLES | tester | Grep | `path` `src/db/schema.ts` | allow |
| GR-2 | ROLES | tester | Grep | `path` `src` | deny 5 |
| GR-2 | ROLES | tester | Grep | `path` `.` | deny 5 |
| GR-2 | ROLES | tester | Grep | no `path` | deny 5 |
| GR-2 | ROLES | tester | Grep | `path` `<outside>` | deny 5 |
| GR-2 | ROLES | tester | Grep | `path` `.specguard` | deny 5 |
| GR-2 | ROLES | tester | Grep | `path` `src/test` (test segment inside a hidden path) | deny 5 |
| GR-2 | NESTED | tester | Grep | `path` `notes` (outside the repo) | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `src/**/*.ts` | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `**/src/*.ts` | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `lib/core/*.ts` | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `**/secrets.env` | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `*.ts`, `path` `src` | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `*.ts`, `path` `lib/core` | deny 5 |
| GR-3 | ROLES | tester | Glob | `pattern` `test/**/*.ts` | allow |
| GR-3 | ROLES | tester | Glob | `pattern` `*.md`, `path` `docs` | allow |
| GR-3 | ROLES | tester | Glob | `pattern` `source/*.ts` | allow |
| GR-3 | ROLES | tester | Glob | `pattern` `*.ts` (no path) | allow |
| GR-3 | ROLES | tester | Glob | `pattern` `*.ts`, `path` `src/db/schema.ts` | allow |
| GR-4 | ROLES | tester | Bash | `ls src` | deny 5 |
| GR-4 | ROLES | tester | Bash | `cat packages/domain/src/phone.ts` | deny 5 |
| GR-4 | ROLES | tester | Bash | `ls ./src/` | deny 5 |
| GR-4 | ROLES | tester | Bash | `cat x/'src'/a.ts` | deny 5 |
| GR-4 | ROLES | tester | Bash | `cd app && grep -rn x src` | deny 5 |
| GR-4 | ROLES | tester | Bash | `bash -c "ls src"` | deny 5 |
| GR-4 | ROLES | tester | Bash | `find . -path '*src*'` | deny 5 |
| GR-4 | ROLES | tester | Bash | `ps aux \| grep -i "fake\|lib/core/index"` | deny 5 |
| GR-4 | ROLES | tester | Bash | `cat config/secrets.env` | deny 5 |
| GR-4 | ROLES | tester | Bash | `ls lib/core` | deny 5 |
| GR-4 | ROLES | tester | Bash | `ls "lib/core"` | deny 5 |
| GR-4 | ROLES | tester | Bash | `cat 'lib/core/x.ts'` | deny 5 |
| GR-4 | ROLES | tester | Bash | `ls lib/corex` | allow |
| GR-4 | ROLES | tester | Bash | `ls source` | allow |
| GR-4 | ROLES | tester | Bash | `ls my-src` | allow |
| GR-4 | ROLES | tester | Bash | `cat old-secrets.env` | allow |
| GR-4 | ROLES | tester | Bash | `cat secrets.env.example` | allow |
| GR-4 | ROLES | tester | Bash | `ls test` | allow |
| GR-4 | ROLES | tester | Bash | `cat docs/specs/x.md` | allow |
| GR-4 | ROLES | tester | Bash | `python3 -m unittest discover -s test` | allow |
| GR-4 | ROLES | tester | Bash | `git status` | allow |
| GR-4 | ROLES | tester | Bash | `cat src/db/schema.ts` | allow |
| GR-4 | ROLES | tester | Bash | `cat src/db/schema.ts src/a.ts` | deny 5 |
| GR-5 | ROLES | tester | Bash | `cat package.json \| grep -A5 '"src"'` | allow |
| GR-5 | ROLES | tester | Bash | `grep -n 'hello' test/a.spec.ts` | allow |
| GR-5 | ROLES | tester | Bash | `ls "src"` | deny 5 |
| GR-5 | ROLES | tester | Bash | `grep -rn x 'src'` | deny 5 |
| GR-5 | ROLES | tester | Bash | `cat 'src/a.ts'` (has `/`, not ignored) | deny 5 |
| GR-5 | ROLES | tester | Bash | `cat 'my src'` (has a space, `src` is a whole word) | deny 5 |
| GR-6 | ROLES | tester | Bash | `cat src/db/schema.ts.bak` | deny 5 |
| GR-6 | ROLES | tester | Bash | `cat src/db/schema.tsx` | deny 5 |
| GR-7 | ROLES | tester | Grep | `path` `src` - reason contains `test`, `qa/suites`, `docs` | deny 5 |
| GR-8 | ROLES | advocate | Edit | `.specguard/reports/spec-review/x.md` | deny 6 |
| GR-8 | ROLES | advocate | MultiEdit | `docs/specs/x.md` | deny 6 |
| GR-8 | ROLES | advocate | NotebookEdit | `notebook_path` `n.ipynb` | deny 6 |
| GR-8 | ROLES | advocate | Bash | `ls docs` | deny 6 |
| GR-8 | ROLES | advocate | Bash | `git status` | deny 6 |
| GR-8 | ROLES | advocate | Write | `.specguard/reports/spec-review/x-2026-09-29.md` | allow |
| GR-8 | ROLES | `specguard:devils-advocate` | Write | `.specguard/reports/spec-review/sub/y.md` | allow |
| GR-8 | ROLES | advocate | Write | `.specguard/reports/tester/x.md` | deny 6 |
| GR-8 | ROLES | advocate | Write | `docs/specs/x.md` | deny 6 |
| GR-8 | ROLES | advocate | Write | `.specguard/reports/spec-review-evil/x.md` | deny 6 |
| GR-8 | ROLES | advocate | Write | `.specguard/reports/spec-review/../../../src/a.ts` | deny 6 |
| GR-8 | ROLES | advocate | Write | no path | deny 6 |
| GR-9 | ROLES | advocate, `agent_id` `a1` | Write | `.specguard/reports/spec-review/x.md` | allow, and the file records `<project>/.specguard/reports/spec-review/x.md` with `agent_id` `a1` |
| GR-9 | ROLES | advocate | Write | `docs/specs/x.md` | deny 6, nothing recorded |
| GR-10 | ROLES | advocate | Read | `src/a.ts` | deny 6 |
| GR-10 | ROLES | advocate | Read | `config/secrets.env` | deny 6 |
| GR-10 | ROLES | advocate | Read | `lib/core/x.ts` | deny 6 |
| GR-10 | ROLES | advocate | Read | `.specguard/reports/tester/t.md` | deny 6 |
| GR-10 | ROLES | advocate | Read | `src/db/schema.ts` | allow |
| GR-10 | ROLES | advocate | Read | `docs/specs/x.md` | allow |
| GR-10 | ROLES | advocate | Read | `test/a.spec.ts` | allow |
| GR-10 | ROLES | advocate | Read | `.specguard/reports/spec-review/x.md` | allow |
| GR-11 | ROLES | advocate | Grep | `path` `test` | allow |
| GR-11 | ROLES | advocate | Grep | `path` `docs` | allow |
| GR-11 | ROLES | advocate | Grep | `path` `qa/suites` | allow |
| GR-11 | ROLES | advocate | Grep | `path` `src/db/schema.ts` | allow |
| GR-11 | ROLES | advocate | Grep | `path` `<outside>` | allow |
| GR-11 | ROLES | advocate | Grep | `path` `src` | deny 6 |
| GR-11 | ROLES | advocate | Grep | `path` `.` | deny 6 |
| GR-11 | ROLES | advocate | Grep | `path` the project directory (absolute) | deny 6 |
| GR-11 | ROLES | advocate | Grep | `path` the parent of the project directory | deny 6 |
| GR-11 | ROLES | advocate | Grep | `path` `.specguard` | deny 6 |
| GR-11 | ROLES | advocate | Grep | `path` `.specguard/reports/tester` | deny 6 |
| GR-11 | ROLES | advocate | Grep | `path` `src/test` | deny 6 |
| GR-11 | ROLES | advocate | Grep | no `path` | deny 6 |
| GR-11 | NESTED | advocate | Grep | `path` `notes` (outside the repo) | allow |
| GR-11 | NESTED | advocate | Grep | `path` `app/test` | allow |
| GR-11 | NESTED | advocate | Grep | `path` `app/docs/specs` | allow |
| GR-11 | NESTED | advocate | Grep | `path` `app` | deny 6 |
| GR-11 | NESTED | advocate | Grep | `path` `.` (the project directory, an ancestor of the repo) | deny 6 |
| GR-11 | NESTED | advocate | Grep | `path` `app/lib` | deny 6 |
| GR-11 | NESTED | advocate | Grep | `path` `app/src` | deny 6 |
| GR-12 | ROLES | advocate | Glob | `pattern` `src/**/*.ts` | deny 6 |
| GR-12 | ROLES | advocate | Glob | `pattern` `*.ts`, `path` `lib/core` | deny 6 |
| GR-12 | ROLES | advocate | Glob | `pattern` `docs/**/*.md` | allow |
| GR-13 | MODE_feature | executor | Edit | `test/a.ts` | deny 7 |
| GR-13 | MODE_feature | executor | Write | `packages/x/test/a.ts` | deny 7 |
| GR-13 | MODE_feature | executor | Write | `qa/suites/case.py` | deny 7 |
| GR-13 | MODE_feature | executor | Write | `src/app.spec.ts` (regex) | deny 7 |
| GR-13 | MODE_feature | executor | MultiEdit | `test/a.ts` | deny 7 |
| GR-13 | MODE_feature | executor | NotebookEdit | `notebook_path` `test/n.ipynb` | deny 7 |
| GR-13 | MODE_feature | `oh-my-claudecode:executor` | Write | `test/a.ts` | deny 7 |
| GR-13 | MODE_hard | executor | Write | `test/a.ts` | deny 7 |
| GR-13 | MODE_visual+feature | executor | Write | `test/a.ts` | deny 7 |
| GR-13 | MODE_visual+hard | executor | Write | `test/a.ts` | deny 7 |
| GR-13 | MODE_simple | executor | Write | `test/a.ts` | allow |
| GR-13 | MODE_visual | executor | Write | `test/a.ts` | allow |
| GR-13 | MODE_feature | executor | Write | `qa/suitesx/a.ts` | allow |
| GR-13 | MODE_feature | executor | Write | `contest/a.ts` | allow |
| GR-13 | MODE_feature | executor | Write | `src/a.ts` | allow |
| GR-13 | MODE_feature | executor | Write | `docs/specs/x.md` | allow |
| GR-13 | MODE_feature | executor | Write | `<outside>/test/a.ts` | allow |
| GR-13 | MODE_feature | executor | Read | `test/a.ts` | allow |
| GR-13 | MODE_feature | executor | Bash | `echo x > test/a.ts` | allow |
| GR-13 | MODE_feature | tester | Write | `test/a.spec.ts` | allow |
| GR-13 | NESTED with `modes.default` `feature` | executor | Write | `app/test/a.ts` | deny 7 |
| GR-13 | NESTED with `modes.default` `feature` | executor | Write | `test/a.ts` (outside the repo) | allow |
| GR-14 | MODE_feature | executor | Write | `test/a.ts` - reason contains `feature` and `rule ID` | deny 7 |
| GR-14 | MODE_hard | executor | Write | `test/a.ts` - reason contains `hard` | deny 7 |
| GR-14 | MODE_visual+hard | executor | Write | `test/a.ts` - reason contains `hard` | deny 7 |

## Differences from the intent

- The code let a tester Grep on a hidden path that also has a test segment (`src/test`) through. The spec follows the intent (deny) and the code was fixed.
- The code matched a hidden path in a Bash command only after `/` or at the start, so `ls lib/core` was not denied. The spec follows the intent and the code was fixed.
- The code removed a `tester_readable` path from a Bash command as a plain substring, so `src/db/schema.ts.bak` was not seen as a hidden mention. Fixed to whole-word removal.
- The rule 5 Bash deny reason did not name the allowed roots. Fixed.
- Rule 7 never fired because the session modes came out of the core as a string of letters. The guard now reads them itself.
- The config accepts only `simple`, `feature`, `hard` and `visual` as `modes.default`, so compound modes cannot be a fixture default. The spec was changed to set them with the user's phrase.

## Retired rules

None.
