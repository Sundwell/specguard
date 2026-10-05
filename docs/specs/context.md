# Context the agent receives

What specguard tells the agent without being asked. The SessionStart intro and the mode rules, the status line added to every prompt of the executor, and the notes and summary handed to a tester or advocate when it starts. This spec covers only the text these events print, not switching modes or approval.

**Tests** - `tests/test_spec_context.py`

## Public API

Tests drive the hook as a process through `tests/helpers.py`, never by importing anything from `scripts/`.

- Build a project with `helpers.make_project(tmp, config=..., files=..., git=True, commit=True)`. The config is the `.claude/specguard.json` dict, for example `{"version": 1, "modes": {"default": "feature", "approval_default": false}}`. Notes files are ordinary files in the project (`files={".claude/specguard/tester-notes.md": "..."}`).
- Send an event with `helpers.run_hook(event, payload, project_dir, state_home=state_home)`. It returns `(exit_code, stdout_json, stderr)`. The exit code is always 0. `stdout_json` is `None` when nothing is printed, otherwise `{"hookSpecificOutput": {"hookEventName": <event>, "additionalContext": "<text>"}}`. All rules below speak of that `additionalContext` text.
- Events used - `helpers.session_start(session_id, source="startup")`, `helpers.user_prompt_submit(session_id, "hello")`, `helpers.subagent_start(session_id, agent_id, agent_type)`, `helpers.user_prompt_expansion(session_id, "specguard:mode", "visual+feature")` to switch the session mode. A role session is an event carrying `agent_type` (`helpers.session_start(sid, agent_type="specguard:tester")`). An event without `agent_type` is the executor.
- A tester launch that opens an approval request (for setup of CX-4) is `helpers.pre_tool_use(session_id, "Agent", {"subagent_type": "specguard:tester", "prompt": "Write tests for docs/specs/a.md"})` in a project with the config `{"version": 1, "modes": {"default": "feature", "approval_default": true}}` and an uncommitted `docs/specs/a.md`; the hook refuses it and opens the request.
- Pass the same `state_home` to every call of one test, it holds the session mode.
- Session modes are `simple`, `feature`, `hard`, `visual` (a single mode may be a config default) and the sets `visual+feature` and `visual+hard`, which a test reaches with the `specguard:mode` expansion.

## Rules

- **CX-1** Without `.claude/specguard.json` no event prints anything (`stdout_json` is `None` for SessionStart, UserPromptSubmit and SubagentStart). Why - a project switches specguard on by config alone.
- **CX-2** SessionStart for the executor prints an intro first, then the mode-confirmation rule, then the rules of the session's modes. The intro is `This project runs the specguard plugin (role-separated TDD). For how it works, load the specguard:guide skill; do not search the disk for it.` and the text starts with it. The mode-confirmation rule contains the marker `specguard-mode <set>`, the words `AskUserQuestion` and `do not ask him to rephrase`. In `simple` nothing follows the mode-confirmation rule. `hookEventName` is `SessionStart`. The rules follow the mode the session has, so after a switch to `hard` a SessionStart with `source` `resume` prints the hard rules. Why - the agent must know the plugin exists and how the modes behave from its first turn.
- **CX-3** The mode rules by mode set. For `feature` and `hard` the rules start with the line `Approval is on.` or `Approval is off.` (in `hard` always on, whatever `modes.approval_default` says; in `feature` off only when the session's approval is off), then a block that starts `Feature mode rules.` and contains `Write the spec with behaviour and rule IDs`, `Never paste implementation code into the tester's task` and `Talk to the user in the user's language.` For `hard` the block continues with a paragraph that starts `Hard mode adds.` and contains `devils-advocate`, `mandatory`, `manual mutation check` and `at least three`. `feature` has no `Hard mode adds.` For `visual` alone there is no `Approval is` line and the text is a block that starts `Visual mode rules.` and contains `DESIGN | NOW`, `DESIGN | BEFORE | NOW`, `deviations log` and `what I decided myself`. `visual+feature` is the approval line, then the feature block, then the visual block. `visual+hard` is the approval line, then the hard block, then the visual block. Why - each mode adds its own duties and the combinations add up.
- **CX-4** The per-prompt line. A UserPromptSubmit from the executor prints, for a prompt that switches nothing, `Active specguard mode is <modes>, approval <on|off>.` where `<modes>` is the session's mode set written with `+` in the canonical order `visual+feature`, `visual+hard` (a single mode is just its name), and the clause `, approval on|off` appears only when the set holds `feature` or `hard`. So `Active specguard mode is simple.`, `Active specguard mode is visual.`, `Active specguard mode is feature, approval off.`, `Active specguard mode is hard, approval on.` (hard is always on), `Active specguard mode is visual+feature, approval on.`. The text is that single line and nothing else. `hookEventName` is `UserPromptSubmit`. Why - the line is the agent's only cheap way to see the current mode.
- **CX-5** Open approval requests. When the session has open approval requests, the line ends with ` Open approval requests - <specs>.` with the spec paths sorted alphabetically and joined by `, `, for example `Active specguard mode is feature, approval on. Open approval requests - docs/specs/a.md.` With no open request the sentence is absent. Why - the agent must remember what it still has to ask.
- **CX-6** A role session (a UserPromptSubmit with `agent_type` `specguard:tester`, `tester`, `specguard:devils-advocate` or `devils-advocate`) gets no per-prompt line. Why - only the executor talks to the user.
- **CX-7** SubagentStart for any agent that is not a tester or advocate (for example `general-purpose`) prints the status line of CX-4, with the open requests of CX-5, followed by a space and `Only the main session can ask the user; report gate refusals to it.`, all on one line with no newline. `hookEventName` is `SubagentStart`. Why - a subagent cannot ask the user and must hand a refusal back.
- **CX-8** SubagentStart for a tester (`specguard:tester`, `tester`, or a name in `roles.tester`) or an advocate (`specguard:devils-advocate`, `devils-advocate`, or a name in `roles.advocate`), and SessionStart of a session whose `agent_type` is such a name, print the role context instead, with no intro and no mode rules. It is the project notes file, then a blank line, then a summary of nine lines in this order and with these prefixes. `Project root: ` (the absolute project directory), `Repo: ` (the config `repo`, default `.`), `Specs dir: ` (`specs_dir`), `Report dir: ` (`reports.tester` for a tester, `reports.advocate` for an advocate), `Allowed grep roots: ` (the `tests.paths` then `docs_dirs`, joined by `, `, for an advocate followed by the `tester_readable` files; `none configured` when the list is empty), `Hidden: ` (`hidden.segments`, `hidden.names`, `hidden.paths` in that order joined by `, `, or `none`), `Readable exceptions: ` (`tester_readable` joined by `, ` or `none`), `Hands-on tag: ` (`hands_on_tag`, default `[hands-on]`), `Test command: ` (`stop.run` or `none configured`). The notes file is `notes.tester` (default `.claude/specguard/tester-notes.md`) for a tester and `notes.advocate` (default `.claude/specguard/advocate-notes.md`) for an advocate, relative to the project root. `hookEventName` is the event that was sent. Why - a generic role cannot know the project's facts, the notes and the summary give them.
- **CX-9** Length cap. When the notes, the blank line and the summary together are longer than 9,000 characters, the notes are left out, the text is the nine summary lines followed by a line `Read <notes path> first.` with the notes path as configured or defaulted, and none of the notes text appears. A notes file of 8,000 characters in a project with short paths is included whole and the text has no `Read ` line. Why - a huge notes file must not flood the agent, it can read it itself.
- **CX-10** When the notes file is missing or empty, the text is the summary alone, with no `Read ... first.` line. Why - pointing the agent at a file that is not there is noise.
- **CX-11** For a mode set containing `visual`, the SessionStart text names the sheet command the plugin ships, `compare-sheet`, and has no `~/` anywhere in it when the project has no notes files. Why - other people install the plugin, so its instructions may only point at tools that come with it, never at a path in the author's home.

## Examples

| Rule | Setup and event | Expected |
|---|---|---|
| CX-1 | no config file, SessionStart | `None` |
| CX-1 | no config file, UserPromptSubmit | `None` |
| CX-1 | no config file, SubagentStart `general-purpose` | `None` |
| CX-2 | default `simple`, SessionStart | text is the intro followed by the mode-confirmation rule, contains `specguard-mode <set>`, has no `Approval is` and no `rules.` block |
| CX-2 | default `feature`, SessionStart | starts with the intro, then `Approval is`, intro comes before `Approval is` |
| CX-2 | default `feature`, then the expansion `visual+hard`, SessionStart `source` `resume` | contains `Hard mode adds.` and `Visual mode rules.` |
| CX-2 | default `feature`, `hookEventName` | `SessionStart` |
| CX-3 | default `feature`, `approval_default` false | contains `Approval is off.`, `Feature mode rules.`, not `Hard mode adds.` |
| CX-3 | default `feature`, `approval_default` true | contains `Approval is on.` |
| CX-3 | default `hard`, `approval_default` false | contains `Approval is on.`, `Feature mode rules.`, `Hard mode adds.`, `manual mutation check` |
| CX-3 | default `visual` | contains `Visual mode rules.`, `DESIGN | BEFORE | NOW`, not `Approval is`, not `Feature mode rules.` |
| CX-3 | expansion `visual+feature`, resume | `Approval is` before `Feature mode rules.` before `Visual mode rules.` |
| CX-3 | expansion `visual+hard`, resume | `Approval is on.` before `Hard mode adds.` before `Visual mode rules.` |
| CX-4 | default `simple`, prompt `hello` | text is exactly `Active specguard mode is simple.` |
| CX-4 | default `visual`, prompt `hello` | exactly `Active specguard mode is visual.` |
| CX-4 | default `feature`, `approval_default` false | exactly `Active specguard mode is feature, approval off.` |
| CX-4 | default `hard`, `approval_default` false | exactly `Active specguard mode is hard, approval on.` |
| CX-4 | `approval_default` true, expansion `visual+feature`, prompt `hello` | exactly `Active specguard mode is visual+feature, approval on.` |
| CX-4 | expansion `visual+hard` (config `approval_default` false), prompt `hello` | exactly `Active specguard mode is visual+hard, approval on.` |
| CX-5 | feature approval on, refused tester launch on `docs/specs/a.md`, prompt `hello` | text ends `Open approval requests - docs/specs/a.md.` |
| CX-5 | refused launches on `docs/specs/b.md` then `docs/specs/a.md` | ends `Open approval requests - docs/specs/a.md, docs/specs/b.md.` |
| CX-5 | no launch | no `Open approval requests` |
| CX-6 | prompt with `agent_type` `specguard:tester` | `None` |
| CX-6 | prompt with `agent_type` `devils-advocate` | `None` |
| CX-7 | feature approval off, SubagentStart `general-purpose` | exactly `Active specguard mode is feature, approval off. Only the main session can ask the user; report gate refusals to it.` |
| CX-7 | same with an open request on `docs/specs/a.md` | exactly `Active specguard mode is feature, approval on. Open approval requests - docs/specs/a.md. Only the main session can ask the user; report gate refusals to it.` (`approval_default` true, launch refused as in CX-5) |
| CX-8 | SubagentStart `specguard:tester`, notes file `Read the spec first.` | text starts `Read the spec first.`, then a blank line, then `Project root: <dir>` |
| CX-8 | notes for tester and advocate differ, SubagentStart `specguard:devils-advocate` | holds the advocate notes, not the tester notes, `Report dir: .specguard/reports/spec-review` |
| CX-8 | same for the tester | `Report dir: .specguard/reports/tester` |
| CX-8 | `tests.paths` `["tests"]`, `docs_dirs` default, `tester_readable` `["shared/types.ts"]` | tester `Allowed grep roots: tests, docs`, advocate `Allowed grep roots: tests, docs, shared/types.ts`, both `Readable exceptions: shared/types.ts` |
| CX-8 | empty config besides version | `Hidden: none`, `Readable exceptions: none`, `Test command: none configured`, `Hands-on tag: [hands-on]` |
| CX-8 | `hidden` segments `["src"]`, names `["secret"]`, paths `["lib/core"]`, `stop.run` `pytest -q` | `Hidden: src, secret, lib/core`, `Test command: pytest -q` |
| CX-8 | `roles.tester` `["qa"]`, SubagentStart `qa` | role context |
| CX-8 | SubagentStart `tester` (bare) | role context |
| CX-8 | SessionStart with `agent_type` `specguard:tester` | role context with `hookEventName` `SessionStart` |
| CX-8 | `notes.tester` `notes/t.md` holding text | the text is included |
| CX-9 | notes of 9,500 characters `x` | no run of 100 `x`, ends with `Read .claude/specguard/tester-notes.md first.` |
| CX-9 | notes of 8,000 characters | included whole, no `Read ` line |
| CX-10 | no notes file | text starts `Project root:`, no `Read ` line |
| CX-10 | empty notes file | same |
| CX-11 | default `visual`, SessionStart | contains `compare-sheet`, no `~/` |
| CX-11 | default `feature`, then the expansion `visual+feature`, SessionStart `source` `resume` | contains `compare-sheet`, no `~/` |

## Retired rules

None.
