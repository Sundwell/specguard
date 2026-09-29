# Hard-mode advocate gate (rule 8)

In hard mode the executor may launch a tester on a spec only after a devils-advocate has reviewed that spec, unless the spec is unchanged since HEAD or unchanged since the last tester launch that was allowed on it. This spec covers only that gate, not the approval gate that runs after it (rule 9).

**Tests** - `tests/test_spec_hardgate.py`

## Public API

Tests drive the hook as a process through `tests/helpers.py`, never by importing anything from `scripts/`.

- Build a project with `helpers.make_project(tmp, config=..., files=..., git=True, commit=True)`. The config is the `.claude/specguard.json` dict, for example `{"version": 1, "modes": {"default": "hard"}}`. Specs live under `specs_dir` (default `docs/specs`), advocate reports under `reports.advocate` (default `.specguard/reports/spec-review`, relative to the project root). A spec is "at HEAD" when the test committed it and did not touch it afterwards; a spec is "modified" when its working-tree content differs from HEAD or it is untracked. Tests may run `git` themselves to commit further versions.
- Launch a tester as the executor with `helpers.run_hook("PreToolUse", helpers.pre_tool_use(session_id, "Agent", {"subagent_type": "specguard:tester", "prompt": "Write the tests for docs/specs/x.md"}), project_dir, state_home=state_home)`. It returns `(exit_code, stdout_json, stderr)`. The exit code is always 0. `stdout_json` is `None` when the launch is allowed. A refusal is `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}`.
- This gate's refusal has a reason containing `hard mode requires the devils-advocate`. The approval gate that runs after it refuses with a reason containing `spec not approved` and a marker `specguard-approve <spec>@<sha7>`. So "rule 8 passed" is observed as a launch that is either allowed or refused with `spec not approved`, and "rule 8 refused" as the reason containing `hard mode requires the devils-advocate`. A modified spec that passes rule 8 is still refused by rule 9 in these tests, which is fine; no test needs an approval.
- Record an advocate report by sending, as the advocate, `helpers.pre_tool_use(session_id, "Write", {"file_path": ".specguard/reports/spec-review/<name>", "content": "notes"}, agent_type="specguard:devils-advocate", agent_id="adv-1")` through `run_hook("PreToolUse", ...)`. The output is `None`. The hook only records the write, it does not create the file. The same Write sent without `agent_type` is the executor writing, which is not an advocate report.
- Pass the same `state_home` to every call of one test, it holds the record of reports and allowed launches.
- Only where the printed output cannot show the behaviour, read `<state_home>/specguard/<project dir with every "/" replaced by "-">/log.jsonl`, one JSON object per line.

## Rules

- **HG-1** The gate applies only when the session mode set contains `hard`, to a tool call `Agent` by the executor whose `subagent_type` is a tester name (`specguard:tester` or `tester`, or a name from `roles.tester`) and whose prompt names at least one spec file under `specs_dir`. No other call is refused by this gate. Feature mode, another `subagent_type` (`general-purpose`, `specguard:devils-advocate`), a tool other than `Agent`, and a prompt that names no spec are never refused with the advocate reason. Why - the gate is a hard-mode addition on top of the approval gate, not a general one.
- **HG-2** A spec passes with no report when its working-tree content is byte for byte the version at HEAD. A modified or untracked spec with no advocate report is refused with the advocate reason. Why - a committed spec was already reviewed by the user's own flow, a fresh edit was not.
- **HG-3** The advocate reason contains the spec path as named under `specs_dir` and the report directory (`reports.advocate`), and does not contain a `specguard-approve` marker, and the refusal opens no approval request. The latter is observed on the executor's next prompt, `helpers.user_prompt_submit(session_id, "hello")`, whose printed `additionalContext` has no `Open approval requests`. Why - the advocate comes before the user's approval, the executor must not ask the user yet.
- **HG-4** An advocate report counts for a spec when an advocate session was recorded writing a file into the `reports.advocate` directory whose name is `<stem>.md`, or `<stem>-<YYYY-MM-DD>` followed by anything and `.md`, where `<stem>` is the spec's file name without `.md`. A different stem that merely starts with the same characters (`domain-order-total-2026-09-29.md` for the stem `domain-order`), a name with no date and no exact stem (`domain-order-draft.md`), a file that is not `.md`, and a file put on disk by the test without a recorded advocate Write do not count. A report written by the executor (a Write with no `agent_type`) does not count. The bare advocate names `devils-advocate` and names from `roles.advocate` count as advocates. Why - only the advocate's own report for that spec proves a review.
- **HG-5** Report timing. Until the spec has had an allowed tester launch, a report recorded at any time counts. After an allowed launch on the spec, only a report recorded after that launch counts. Why - an edit after the last launch needs a fresh review, an older report does not cover it.
- **HG-6** A spec whose content is byte for byte the content of its last allowed launch passes with no report even when it differs from HEAD. Why - a follow-up launch on an unchanged spec must not force a new advocate run.
- **HG-7** A launch that names several specs is refused when any one of them fails the gate, and the reason names every spec that lacks a report and no spec that passed. Why - one launch, one decision, and the executor must know which specs to send to the advocate.
- **HG-8** When the spec cannot be compared with git (the project is not a git repository, or has no commit), the launch is refused with a reason containing `git`, also when a valid advocate report exists. Why - hard mode fails closed.
- **HG-9** The gate's refusal goes before the approval gate's. A modified spec with no report gets the advocate reason, not `spec not approved`. A modified spec with a valid report gets `spec not approved`. Why - the order is advocate, then the user.
- **HG-10** The report directory is the configured `reports.advocate`. With `reports.advocate` set to another folder, a report written there counts and a report written into the default folder does not (that advocate Write is itself refused by the role guard, so a test does not expect an empty output for it, only the advocate reason on the next launch). The `roles.tester` and `roles.advocate` keys are lists of agent type names, for example `"roles": {"tester": ["qa"]}`, and a name from `roles.tester` is gated like `specguard:tester`. Why - the project chooses where reports live.
- **HG-11** Every refusal by this gate is logged as a line with `ev` `deny`, `rule` `8` and `spec` holding the refused spec paths, in the order the prompt names them, joined by a comma. Why - the log is the only trace of a gate decision.

## Examples

| Rule | Setup and event | Expected |
|---|---|---|
| HG-1 | feature mode, modified spec, no report, launch `specguard:tester` | no advocate reason (allowed or `spec not approved`) |
| HG-1 | hard mode, modified spec, no report, `subagent_type` `general-purpose` naming the spec | not refused with the advocate reason |
| HG-1 | hard mode, modified spec, no report, `subagent_type` `tester` | advocate reason |
| HG-1 | hard mode, `Bash` call naming the spec | not refused with the advocate reason |
| HG-1 | hard mode, prompt names no spec | not refused with the advocate reason |
| HG-2 | hard mode, spec committed and untouched, no report | allowed, output `None` |
| HG-2 | spec edited (one line changed), no report | advocate reason |
| HG-2 | new untracked spec, no report | advocate reason |
| HG-3 | modified spec `docs/specs/domain-order.md`, no report | reason contains `docs/specs/domain-order.md`, `.specguard/reports/spec-review`, not `specguard-approve` |
| HG-4 | advocate wrote `domain-order-2026-09-29.md`, spec `domain-order.md` modified | rule 8 passes |
| HG-4 | advocate wrote `domain-order.md` | passes |
| HG-4 | advocate wrote `domain-order-2026-09-29-r2.md` | passes |
| HG-4 | advocate `devils-advocate` (bare name) wrote `domain-order-2026-09-29.md` | passes |
| HG-4 | advocate wrote `domain-order-total-2026-09-29.md` | advocate reason |
| HG-4 | advocate wrote `domain-order-draft.md` | advocate reason |
| HG-4 | advocate wrote `domain-order-2026-09-29.txt` | advocate reason |
| HG-4 | the executor sent a Write to `domain-order-2026-09-29.md` | advocate reason |
| HG-4 | the test wrote the report file straight to disk, no advocate Write | advocate reason |
| HG-5 | report recorded, then spec edited, never launched | passes |
| HG-5 | spec at HEAD launched (allowed), spec edited, report recorded before the launch | advocate reason |
| HG-5 | same, report recorded after the launch | passes |
| HG-6 | v1 committed, launched (allowed), v2 committed, working tree restored to v1 | passes, no advocate reason |
| HG-6 | same but working tree set to a third content v3 | advocate reason |
| HG-7 | `a.md` at HEAD and `b.md` modified without report, one launch naming both | advocate reason naming `b.md` and not `a.md` |
| HG-7 | both modified without report | reason names both |
| HG-7 | both modified, report only for `a` | reason names `b.md`, not `a.md` |
| HG-8 | project with no git repository, spec present, report recorded | reason contains `git` |
| HG-9 | modified spec, no report | reason contains `hard mode requires the devils-advocate`, not `spec not approved` |
| HG-9 | modified spec, report recorded | reason contains `spec not approved` |
| HG-10 | `reports.advocate` `out/adv`, report Write into `out/adv/domain-order-2026-09-29.md` | passes |
| HG-10 | same config, report Write into the default folder | advocate reason |
| HG-11 | refused launch | log line with `ev` `deny`, `rule` `8`, `spec` `docs/specs/domain-order.md` |
| HG-11 | refusal naming two specs | `spec` `docs/specs/a.md,docs/specs/b.md` |

## Retired rules

None.
