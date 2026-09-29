# Project notes for the tester - specguard

specguard is a Claude Code plugin. Its behaviour is what the hook entrypoint prints for a hook event, so tests drive the real entrypoint as a black box. You never read `scripts/`; the spec's "Public API" section and `tests/helpers.py` are all you need.

- Harness - `tests/helpers.py` (read it first). `make_project(tmp, config=..., files=..., git=..., commit=...)` builds a fixture project, `run_hook(event, payload, project_dir, state_home=...)` runs the entrypoint in a subprocess and returns exit code, parsed stdout JSON or None, and stderr. Payload builders - `pre_tool_use`, `post_tool_use`, `user_prompt_submit`, `user_prompt_expansion`, `session_start`, `subagent_start`, `stop`. Always pass a temp `state_home`, never the real state dir.
- Read the existing tests in `tests/` for conventions, but write your own from the spec; do not copy their cases.
- Your file is `tests/test_spec_<spec stem>.py`, one per spec, stdlib `unittest` only, directly in `tests/` (no subfolders).
- Run one file with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_spec_<stem> -v` from the repo root, the whole suite with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`.
- Phrase packs in `phrases/*.json` are readable data, not implementation.
- Rules that depend on git need a fixture repo made with `git=True, commit=True`.
- The report is in Russian with English identifiers.
