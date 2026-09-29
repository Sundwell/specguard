# Stop test gate

The Stop hook runs the project's test command when the executor's turn ends and blocks a red exit once. This spec covers only the Stop event.

**Tests** - `tests/test_spec_stop.py`

## Public API

Tests drive the hook as a process through `tests/helpers.py`, never by importing anything from `scripts/`.

- Build a project with `helpers.make_project(tmp, config=..., files=..., git=True, commit=True)`. The config is the `.claude/specguard.json` dict. For a gated session set `"modes": {"default": "feature"}` and a `stop` object with the keys `dirty_pathspec`, `fresh_paths`, `fresh_globs`, `exclude_dirs`, `run` and optionally `modes`, `skip_preset` (`vitest`, `pytest`, `xunit` or `none`), `skip_regex`, `skip_paths`, `summary_regex`, `timeout` (seconds) and `require_glob`. Tests that need a clean tree commit the files first (`commit=True`), tests that need a dirty tree write or change a file after the commit.
- Send the event with `helpers.run_hook("Stop", helpers.stop(session_id, stop_hook_active=False, background_tasks=[...], agent_type=None), project_dir, state_home=state_home)`. It returns `(exit_code, stdout_json, stderr)`. The exit code is always 0. `stdout_json` is `None` when the hook prints nothing (the stop is allowed), `{"decision": "block", "reason": "..."}` for a block, and `{"systemMessage": "..."}` for the tester notice.
- A background task is a dict `{"id": ..., "type": "subagent"|"shell"|"teammate", "status": "running"|..., "agent_type": "specguard:tester"}`. A task without `agent_type` is resolved by its `id` against a prior `helpers.run_hook("SubagentStart", helpers.subagent_start(session_id, agent_id, agent_type), ...)` call with the same `state_home`.
- To see whether `stop.run` was executed, make the command append a line to a file outside the repo, for example `echo x >> /tmp/dir/runs`, and count the lines. Use a shell exit code for red (`exit 1`) and green (`exit 0`).
- Pass the same `state_home` to every call of one test, it holds the memory of the last green run. Mtimes are set with `os.utime`.
- Only where the printed output cannot show the behaviour, read `<state_home>/specguard/<project dir with every "/" replaced by "-">/log.jsonl`, one JSON object per line with the keys `ev` and, for blocks, `rule`.

## Rules

- **ST-1** With no `.claude/specguard.json`, or a config without `stop.run`, Stop prints nothing and exits 0. Why - a project switches the gate on by config alone.
- **ST-2** A Stop from a tester or devils-advocate session (`agent_type` `specguard:tester`, `tester`, `specguard:devils-advocate`, `devils-advocate`) prints nothing and never runs `stop.run`, even with a red command and a tester in `background_tasks`. Why - roles do not gate themselves.
- **ST-3** When `stop_hook_active` is true, Stop prints nothing and does not run `stop.run`. Why - a red run blocks once, the second Stop after the block must let the turn end.
- **ST-4** When `background_tasks` holds a task with `type` `subagent`, `status` `running` and a tester `agent_type` (`specguard:tester` or `tester`), Stop prints exactly `{"systemMessage": "specguard: tester still running, tests run after it"}` and does not run `stop.run`. A task with no `agent_type` counts when its `id` was recorded by SubagentStart as a tester. A task of type `shell` or `teammate`, a status other than `running`, or a non-tester `agent_type` does not count. This applies in every mode, also when the tree is clean. Why - the tester's tests must not be judged before it finishes.
- **ST-5** The gate runs only when the session's modes intersect `stop.modes`, which defaults to `["feature", "hard"]`. Session modes come from `modes.default`, which holds exactly one of `simple`, `feature`, `hard`, `visual` (a combination such as `visual+feature` is not a valid default and is out of reach of this spec). So `simple` and `visual` alone do not run it, and `stop.modes: ["simple"]` makes a simple session run it. Why - no Stop test run in simple (decision O-6), a project can opt in with one line.
- **ST-6** The gate is skipped when `git status --porcelain -- <dirty_pathspec>` in the repo is empty, so a clean tree, or a change only outside the pathspec, prints nothing and does not run `stop.run`. Untracked files inside the pathspec count as dirty. When the repo folder is not a git repository, the tree counts as dirty and the gate goes on. Why - unknown state is treated like a git timeout, fail closed.
- **ST-7** Freshness. With no earlier green run in this state dir the tree counts as fresh. After a green run it counts as fresh only if a file under `fresh_paths` (relative to the repo) that matches one of `fresh_globs` by file name, outside every folder named in `exclude_dirs`, has an mtime newer than the last green run. Otherwise the gate is skipped. Why - a dirty tree that no code change touched since the last green run is not tested again.
- **ST-8** When `require_glob` is set and no file matches it (relative to the repo, recursive `**` allowed), the gate is skipped. When something matches, the gate goes on. Why - no tests yet means nothing to run.
- **ST-9** Skip scan. After the checks above and before running the command, files under `skip_paths` (default the repo root) are scanned, skipping `exclude_dirs` folders and, when `tests.file_regex` is set, every file whose repo-relative path does not match it. If a file contains the pattern of `skip_preset` (or `skip_regex`, which wins over the preset), Stop blocks with a reason beginning `specguard: ` that names skipped, focused or todo tests, and `stop.run` is not executed. The scan has no exemption of its own, so a config file or any other file under `skip_paths` that contains the pattern counts too; with `tests.file_regex` unset every file is read, so a project that writes `skip_regex` literally in `.claude/specguard.json` narrows `skip_paths` or sets `tests.file_regex`. The reason is the same text for every preset and does not begin with `specguard: tests failed`, so a scan block and a red block (ST-11) differ by that prefix. Preset `none` and no `skip_regex` scan nothing. Presets - `vitest` matches `test.skip(`, `it.only(`, `describe.todo(` and the like; `pytest` matches `@pytest.mark.skip`, `@pytest.mark.skipif`, `@pytest.mark.xfail`, `pytest.skip(`, `pytest.xfail(`, `unittest.skip`, `raise unittest.SkipTest`; `xunit` matches `Skip =`, `[Fact(Skip`, `[Theory(Skip`, `[Ignore]`. Why - a skipped test is not evidence.
- **ST-10** Green. When `stop.run` exits 0, Stop prints nothing and records the run, so an immediate second Stop with no newer file is skipped by ST-7. Why - do not re-run for nothing.
- **ST-11** Red. When `stop.run` exits non-zero, Stop blocks with a reason beginning `specguard: tests failed` that tells the executor to fix the code, never the tests. The reason then carries a blank line and the last 25 lines of the command's stdout plus stderr that match `summary_regex` (default `FAIL|Error|failed`), in order, one per line; with no matching line the reason has nothing after the first sentence. A red run does not count as green, so the next Stop without `stop_hook_active` runs the command again. Why - the executor sees which tests are red.
- **ST-12** Timeout. When `stop.run` runs longer than `stop.timeout` seconds, Stop blocks with a reason containing `stop.run timed out after <timeout>s`, and the hook returns within a few seconds of the timeout, also when the command starts a child process that outlives the shell (`sleep 30; true`). Why - a hung test run must not hang the session.
- **ST-13** Every block from Stop is logged as `{"ev": "block", "rule": ...}` with the rule `stop-red` (ST-11), `stop-skip-scan` (ST-9) or `stop-timeout` (ST-12), and the tester notice (ST-4) as `{"ev": "stop-inflight"}`. Why - the log is the only trace of a gate decision.

## Examples

| Rule | Setup and event | Expected |
|---|---|---|
| ST-1 | no config file, Stop | no output, exit 0 |
| ST-1 | config with `modes.default` feature and no `stop` key, dirty tree, Stop | no output |
| ST-2 | red `run`, dirty tree, `agent_type` `specguard:tester` | no output, `run` not executed |
| ST-2 | same with `agent_type` `devils-advocate` | no output |
| ST-2 | `agent_type` `specguard:tester` and a running tester task | no output (no notice) |
| ST-3 | red `run`, dirty tree, `stop_hook_active` true | no output, `run` not executed |
| ST-3 | same with `stop_hook_active` false | block |
| ST-4 | task `{"id": "a1", "type": "subagent", "status": "running", "agent_type": "specguard:tester"}`, red `run`, dirty tree | `{"systemMessage": "specguard: tester still running, tests run after it"}`, `run` not executed |
| ST-4 | same task with `agent_type` `tester` | the same notice |
| ST-4 | same task with `type` `shell` | block (task ignored) |
| ST-4 | same task with `status` `done` | block |
| ST-4 | same task with `agent_type` `general-purpose` | block |
| ST-4 | task with no `agent_type`, id `a1` recorded as `specguard:tester` by SubagentStart | the notice |
| ST-4 | task with no `agent_type`, id `a1` recorded as `general-purpose` | block |
| ST-4 | tester task running, clean tree, simple mode | the notice |
| ST-5 | `modes.default` simple, red `run`, dirty tree | no output, `run` not executed |
| ST-5 | `modes.default` visual, red `run`, dirty tree | no output |
| ST-5 | `modes.default` feature | block |
| ST-5 | `modes.default` hard | block |
| ST-5 | `modes.default` simple and `stop.modes` `["simple"]` | block |
| ST-5 | `stop.modes` `["hard"]` and session feature | no output |
| ST-6 | committed tree, nothing changed, red `run` | no output, `run` not executed |
| ST-6 | committed tree, `notes.txt` changed, `dirty_pathspec` `["src"]` | no output |
| ST-6 | committed tree, new untracked `src/a.py`, `dirty_pathspec` `["src"]`, red `run` | block |
| ST-6 | project folder is not a git repository, red `run`, a fresh `.py` file | block |
| ST-7 | dirty, no earlier green, `run` green | `run` executed once |
| ST-7 | after that green, no file touched, Stop again | `run` not executed again |
| ST-7 | after green, `src/a.py` touched with an mtime one hour ahead, `fresh_globs` `["*.py"]` | `run` executed again |
| ST-7 | after green, only `src/notes.md` touched, `fresh_globs` `["*.py"]` | not executed |
| ST-7 | after green, only `node_modules/x.py` touched, `exclude_dirs` `["node_modules"]`, `fresh_paths` `["."]` | not executed |
| ST-7 | after green, `docs/a.py` touched, `fresh_paths` `["src"]` | not executed |
| ST-8 | `require_glob` `"tests/**/*.py"`, no such file, dirty, red `run` | no output, not executed |
| ST-8 | same, `tests/unit/t.py` exists | block |
| ST-9 | preset `vitest`, `tests/a.test.ts` holds `it.skip('x', () => {})`, `tests.file_regex` `\.test\.ts$` | block, reason names skipped tests, `run` not executed |
| ST-9 | preset `vitest`, file holds `it.only(` | block |
| ST-9 | preset `vitest`, file holds `describe.todo(` | block |
| ST-9 | preset `pytest`, file holds `@pytest.mark.skipif(` | block |
| ST-9 | preset `pytest`, file holds `raise unittest.SkipTest` | block |
| ST-9 | preset `xunit`, file holds `[Ignore]` | block |
| ST-9 | preset `xunit`, file holds `[Theory(Skip = "x")]` | block |
| ST-9 | preset `none`, file holds `it.skip(` | no block, `run` executed |
| ST-9 | preset `pytest`, file holds `it.skip(` | not blocked by the scan |
| ST-9 | `skip_regex` `XXX_SKIP`, preset `vitest`, file holds `XXX_SKIP` | block |
| ST-9 | `skip_regex` `XXX_SKIP`, preset `vitest`, file holds only `it.skip(` | not blocked by the scan |
| ST-9 | hit only in `src/a.py`, `tests.file_regex` `\.test\.py$` | not blocked by the scan |
| ST-9 | hit in `other/a.test.py` while `skip_paths` is `["tests"]` | not blocked |
| ST-9 | hit only inside a folder listed in `exclude_dirs` | not blocked |
| ST-10 | green `run` on a dirty tree | no output, exit 0 |
| ST-11 | `run` `echo "FAIL a"; echo "ok b"; exit 1` | block, reason starts `specguard: tests failed`, ends with `FAIL a`, does not contain `ok b` |
| ST-11 | the command prints 30 lines `FAIL n` (n from 1) and exits 1 | reason holds `FAIL 6` to `FAIL 30`, not `FAIL 5` |
| ST-11 | the matching line is on stderr | it is in the reason |
| ST-11 | `run` `exit 1`, no output | block, reason has no lines after the first sentence |
| ST-11 | `summary_regex` `boom`, output `boom 1` and `FAIL 2` | reason holds `boom 1`, not `FAIL 2` |
| ST-11 | red, then a second Stop with `stop_hook_active` false | `run` executed a second time, block again |
| ST-12 | `run` `sleep 30`, `timeout` 1 | block, reason contains `stop.run timed out after 1s`, returned in under 6 seconds |
| ST-12 | `run` `sleep 30; true`, `timeout` 1 | the same, returned in under 6 seconds |
| ST-13 | red run | `log.jsonl` has `{"ev": "block", "rule": "stop-red"}` |
| ST-13 | skip-scan hit | `rule` `stop-skip-scan` |
| ST-13 | timeout | `rule` `stop-timeout` |
| ST-13 | tester notice | a line with `ev` `stop-inflight` |

## Retired rules

None.
