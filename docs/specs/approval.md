# Spec approval gate (rule 9) and the message rules of rule 4

In feature mode with approval on, and in hard mode, the executor may launch a tester on a spec only after the user approved that exact version of it, or when it is unchanged since HEAD. The gate refuses the launch and opens an approval request with a marker; the user answers an AskUserQuestion whose structure the hook checks, or a short text reply right after the marked message; a revoke phrase takes the approval back; an opt-out phrase turns approval off outside hard mode. The message part of rule 4 keeps the executor from steering the modes or approvals through the prompts it sends to agents.

**Tests** - `tests/test_spec_approval.py`

## Public API

Tests drive the hook as a process through `tests/helpers.py`, never by importing anything from `scripts/`.

- Build a project with `helpers.make_project(tmp, config=..., files=..., git=True, commit=...)`. The config is the `.claude/specguard.json` dict. Feature mode with approval on is `{"version": 1, "modes": {"default": "feature"}}`; approval off is `{"version": 1, "modes": {"default": "feature", "approval_default": false}}`; hard is `{"version": 1, "modes": {"default": "hard"}}`; simple is `{"version": 1, "modes": {"default": "simple"}}`. Specs live under `docs/specs`. A spec is "at HEAD" when the test committed it and did not touch it afterwards; an untracked or edited spec is "modified". `roles` is a dict of lists of agent type names, for example `{"roles": {"tester": ["qa"]}}`.
- Every call is `helpers.run_hook(event, payload, project_dir, state_home=state_home)` and returns `(exit_code, stdout_json, stderr)`. The exit code is always 0. Pass the same `state_home` to every call of one test, it holds the requests, approvals and launches. Sessions are separate by `session_id`; requests belong to the session that opened them, approvals belong to the project and count in every session.
- Launch a tester as the executor with `helpers.pre_tool_use(session_id, "Agent", {"subagent_type": "specguard:tester", "prompt": "Write the tests for docs/specs/x.md"})` through `run_hook("PreToolUse", ...)`. `stdout_json` is `None` when the launch is allowed. A refusal is `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}`. The refusal of this gate has a reason containing `spec not approved` and one marker `specguard-approve <spec>@<sha7>` per unapproved spec, where `<spec>` is the spec path relative to the repo (`docs/specs/x.md`) and `<sha7>` is the first seven hex characters of the SHA-256 of the spec file's bytes. A launch that names no spec is refused with a reason containing `name the spec file`.
- The executor's open requests are visible on its next `helpers.user_prompt_submit(session_id, "hello")`, whose printed `hookSpecificOutput.additionalContext` contains `Open approval requests - ` followed by the sorted spec paths joined by `, ` while any request is open, and does not contain `Open approval requests` when none is open.
- Ask the user with `helpers.pre_tool_use(session_id, "AskUserQuestion", {"questions": [{"question": "...", "options": [{"label": "✓ Approve"}, {"label": "Not yet"}]}]})` through `run_hook("PreToolUse", ...)`. Allowed is `None`; a refusal is a deny as above. The user's answer arrives as `helpers.post_tool_use(session_id, "AskUserQuestion", {"questions": [...same...], "answers": {"<exact question text>": "<chosen option label>"}}, {})` through `run_hook("PostToolUse", ...)`; the answers may be in `tool_input.answers` or, with `tool_input` holding only the questions, in the fourth argument as `{"answers": {...}}`. The printed output is `None` or `{"systemMessage": "..."}`; several lines are joined by a newline. Other specguard components may add their own lines to the same `systemMessage`, so tests check that the approval line is contained, never that the whole message is equal.
- The user's text reply is `helpers.user_prompt_submit(session_id, "approve", transcript_path=<path>)` through `run_hook("UserPromptSubmit", ...)`. The output has the same `systemMessage` field next to the `additionalContext` line described above. The file at `transcript_path` is JSON lines. The assistant's turns are lines of the form `{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "..."}]}}`; lines of other shapes (for example `{"type": "user", "message": {"role": "user", "content": "hi"}}`) may sit between them. The hook reads the last assistant line that has text blocks.
- Register a running agent for `SendMessage` with `run_hook("SubagentStart", helpers.subagent_start(session_id, "ag-1", "specguard:tester"), project_dir, state_home=state_home)`, then address it with `helpers.pre_tool_use(session_id, "SendMessage", {"to": "ag-1", "message": "..."})`. A scheduled prompt is `helpers.pre_tool_use(session_id, "CronCreate", {"cron": "*/5 * * * *", "prompt": "..."})` or `helpers.pre_tool_use(session_id, "ScheduleWakeup", {"delaySeconds": 60, "prompt": "..."})`. `SendMessage` carries its text in `message`, the other two in `prompt`.
- Only where the printed output cannot show the behaviour, read `<state_home>/specguard/<project dir with every "/" replaced by "-">/log.jsonl`, one JSON object per line with `ev`, and for approval events `spec` and `via`.

## Rules

- **AP-1** The gate applies to a tool call `Agent` made by the executor (no `agent_type`) whose `subagent_type` is a tester name (`specguard:tester`, `tester`, or a name from `roles.tester`), and only in hard mode or in feature mode with approval on. In simple mode, in feature mode with approval off, for another `subagent_type` (`general-purpose`, `specguard:devils-advocate`), and for a call made by a role (`agent_type` set), it refuses nothing. Why - the gate protects the step where tests get written from a spec.
- **AP-2** A spec reference in the launch prompt is a token ending in `.md` (surrounding quotes, backticks and trailing `,` `.` `)` `:` do not belong to it) that names an existing file under `docs/specs`, written as `docs/specs/x.md`, `./docs/specs/x.md`, the absolute path, or the bare `x.md` when that file exists there. `docs/specs/README.md`, an `.md` outside `docs/specs`, and a path under `docs/specs` that does not exist are not spec references. A prompt with no spec reference is refused with the reason `name the spec file` and opens no request. Why - the gate must know which version it is approving, and a marker for a file that is not there would be approvable nonsense.
- **AP-3** A launch passes when every spec it names is either byte for byte equal to its version at HEAD, or approved in its current form, meaning an approval was recorded (AP-9, AP-12, AP-14) for the SHA-256 of its current content. An allowed launch prints nothing. An approval belongs to the project, so it passes in every session, and it stops passing the moment the spec content changes. Why - the user approved a version, not a file name.
- **AP-4** A launch naming a spec that fails AP-3 is refused with a reason containing `spec not approved`, `AskUserQuestion`, and one marker per failing spec in the order the prompt names them. A spec that passes AP-3 in the same launch gets no marker. The reason tells the executor that a subagent must stop and pass the markers to the main session. Why - the executor needs everything to ask the user and one launch is one decision.
- **AP-5** The refusal opens an approval request for each failing spec, in the executor's session, holding the spec path and the content hash. It is visible as `Open approval requests` (Public API). Launching again on an unchanged spec refuses with the same marker and keeps a single request; launching after the spec changed refuses with a marker holding the new hash and replaces the old request for that spec. Why - the request is what the answer is matched against.
- **AP-6** An `AskUserQuestion` call carrying an `answers` field with content in its `tool_input` is refused, whatever the question says, with a reason containing `pre-filled answers`. Why - only the user may answer.
- **AP-7** An `AskUserQuestion` question whose text contains the marker of an open request (same spec, same hash) is checked, and is allowed only when it carries every open request's marker of the session, has exactly one option whose label contains `✓`, and has at least two options. A question that misses a marker of another open request is refused with a reason containing the missing marker; zero or two `✓` labels, or only one option, is refused with a reason containing `✓`. Every question of the call is checked, one broken question refuses the call. Why - the hook recognises approval by structure, so the structure has to be sound.
- **AP-8** A question that contains no marker of an open request is not checked by AP-7. A question with no marker at all, or with a marker whose spec has no open request or whose hash differs from the open one, is allowed whatever its options are. Why - only approval questions have a structure to protect.
- **AP-9** When the user's answer to a checked question (PostToolUse) is the label of the `✓` option, every open request whose marker is in that question text and whose spec still has the hashed content is approved and closed. The output is a `systemMessage` line `specguard: approved <spec> (<sha7>)` per approved spec. After it, a launch on that spec passes (AP-3). The answer is read from `tool_input.answers` or from `tool_response.answers`, keyed by the exact question text. Why - his `✓` is the approval.
- **AP-10** When the answer is any other label of the question, the matching requests are closed without approval, and the output has a line `specguard: request closed for <spec>`. A later launch on that spec is refused again with the same marker and opens a new request. Why - "not yet" must not approve and must not leave a stale request.
- **AP-11** When the spec content changed after the request was opened, an answer to the old marker changes nothing, the output has no approval line and no closing line, the old request stays open, and a launch on the edited spec is refused with the marker of the new hash. An answer that has no entry for the question text, a question without a marker, and an answer while no request is open, change nothing and print no approval line. Why - the approval must not travel to content the user did not see.
- **AP-12** One question that carries the markers of several open requests and is answered with its `✓` option approves all of them, one line each. Why - AP-7 forces one question for all open requests.
- **AP-13** Text approval. An executor prompt (UserPromptSubmit) counts as approval when, ignoring letter case and the punctuation marks `.` `,` `!` `;` `:`, it consists of one or two words and every word is an approval word. Approval words are the union of the packs - English `go`, `yes`, `approve`, `approved`, `ok`, `okay`; Russian `го`, `да`, `апрув`, `аппрув`, `апруф`, `утверждаю`, `одобряю`; Ukrainian `го`, `так`, `апрув`, `схвалюю`. A lone `yes`, `да` or `так` never counts and prints nothing. A prompt ending in `?`, a prompt whose first character is not a letter (`+`, `👍`, `<task-notification>`), a longer prompt (`approve the spec please`), and a prompt of a role session (`agent_type` set) are not approval. Why - text approval is a fallback that must not fire on ordinary speech.
- **AP-14** A counted text approval approves the open requests of the session whose marker appears in the text of the last assistant turn in the transcript at the prompt's `transcript_path`, and whose spec still has the hashed content. Each approved spec gets the line `specguard: approved <spec> (<sha7>)`, is closed, and passes AP-3 afterwards. Requests whose marker is not in that last assistant turn stay open and unapproved. A marker in an earlier assistant turn does not count, several text blocks of the last turn are read together, joined by a newline, so a marker cut across two blocks does not match, and a marker with another hash than the request's does not match. Why - the reply must be an answer to the marked question and to nothing older.
- **AP-15** A counted text approval that approves nothing says why, in a `systemMessage` line - `specguard: nothing to approve, no request is open` when the session has no open request, `specguard: approval not recorded, no marker in the last message` when the last assistant turn has no open marker or the spec content changed, and `specguard: could not read the transcript, approval not recorded` when the file is missing or has no assistant text. The exception is a message that is also a mode trigger word (`go`, `го`), which stays silent in all three cases. Why - he sees what counted, and a plain `go` is a mode phrase start, not noise to answer.
- **AP-16** Revoke. An executor prompt that opens with a revoke phrase (English `revoke approval`, Russian `отзываю апрув` or `отмени апрув`, Ukrainian `відкликаю апрув`; letter case ignored, more words or punctuation may follow, a trailing `?` makes it a question and it is ignored) removes every approval that this session recorded through AP-9 or AP-14 and that is still in force. An approval stays in force until it is revoked or replaced by a newer approval of the same spec, so editing the spec afterwards does not stop a revoke from printing its line. The output has a line `specguard: approval revoked - <spec> (<sha7>)` per revoked spec, `<sha7>` being the hash of the approved version, and a launch on that spec is then refused again (unless the spec is at HEAD). A second revoke, a revoke in a session that approved nothing, and a revoke of approvals recorded by another session print `specguard: nothing to revoke` and leave those approvals in force. Why - he must be able to take an approval back, and only the ones this session gave.
- **AP-17** Opt-out. An executor prompt that opens with an opt-out phrase (English `no approval`, Russian `без апрува`, `без аппрува`, `без апруфа`, Ukrainian `без апруву`; letter case and trailing punctuation ignored) in a session whose modes are not hard turns approval off for the rest of the session and prints `specguard: mode <modes>, approval off`, where `<modes>` is the session's mode set (`feature`). Afterwards AP-1 lets every tester launch through. In hard mode it prints `specguard: no-approval refused, hard mode always requires approval` and the gate stays. Why - approval is on by default and he switches it off in his own words, but hard mode forces it.
- **AP-18** Rule 4, launches of running roles. A `SendMessage` whose `to` is the id of an agent registered as a tester or an advocate (`specguard:tester`, `tester`, `specguard:devils-advocate`, `devils-advocate`, or a name from `roles.tester` or `roles.advocate`) is refused, in hard mode and in feature mode with approval on, with a reason containing `new Agent launch`. In simple mode, in feature mode with approval off, and to an agent of another type or an unknown id, it is allowed when its text passes AP-19. Why - a follow-up task sent by message would skip the gates that check the spec.
- **AP-19** Rule 4, messages carrying steering. A `SendMessage` (`message`), `CronCreate` (`prompt`) or `ScheduleWakeup` (`prompt`) whose text is a mode phrase at its start (`go feature spec`, `go hard mode`, `го хардмод`, `го фічспек`, also with text after it), an opt-out phrase at its start (`no approval`, `без апрува`), a revoke phrase at its start (`revoke approval`), or exactly a text approval per AP-13 (`approve`, `ok`, `yes ok`), is refused, in every mode, with a reason containing `may not carry mode or approval phrases`. Ordinary text (`Please continue with the tests`, `then go feature spec later`, `approve the spec please`, and a lone `yes`) is allowed. Why - the executor must not switch modes or fake the user's approval through a prompt it sends.

## Examples

| Rule | Setup and event | Expected |
|---|---|---|
| AP-1 | feature approval on, `docs/specs/x.md` untracked, launch `specguard:tester` naming it | deny `spec not approved` |
| AP-1 | same, `subagent_type` `tester` | deny `spec not approved` |
| AP-1 | same, `roles.tester` `["qa"]`, `subagent_type` `qa` | deny `spec not approved` |
| AP-1 | same, `subagent_type` `general-purpose` | `None` |
| AP-1 | same, `subagent_type` `specguard:devils-advocate` | `None` |
| AP-1 | simple mode, launch tester naming a modified spec | `None` |
| AP-1 | feature approval off (`approval_default` false), same | `None` |
| AP-1 | hard mode, modified spec, tester launch | a deny (advocate gate or `spec not approved`), never `None` |
| AP-1 | feature approval on, the launch call carries `agent_type` `specguard:tester` | no `spec not approved` |
| AP-2 | modified spec, prompt names `docs/specs/x.md` | deny with marker for `docs/specs/x.md` |
| AP-2 | prompt names `./docs/specs/x.md` | deny with marker for `docs/specs/x.md` |
| AP-2 | prompt names the absolute path of `x.md` | deny with marker for `docs/specs/x.md` |
| AP-2 | prompt names bare `x.md` | deny with marker for `docs/specs/x.md` |
| AP-2 | prompt is "see `docs/specs/x.md`, then write" | deny with marker for `docs/specs/x.md` |
| AP-2 | prompt names only `docs/specs/README.md` | deny `name the spec file` |
| AP-2 | prompt names only `docs/notes.md` (exists, outside specs) | deny `name the spec file` |
| AP-2 | prompt names only `docs/specs/missing.md` (no such file) | deny `name the spec file`, next prompt line has no `Open approval requests` |
| AP-2 | prompt names no file | deny `name the spec file` |
| AP-3 | spec committed and untouched | `None` |
| AP-3 | spec committed, then one line edited, no approval | deny `spec not approved` |
| AP-3 | modified spec approved by `✓` answer, then launch | `None` |
| AP-3 | same, but launched from another `session_id` | `None` |
| AP-3 | approved, then spec edited, then launch | deny with marker holding the new hash |
| AP-3 | two specs, `a.md` at HEAD, `b.md` modified, one launch names both | deny, marker for `b.md`, none for `a.md` |
| AP-4 | two modified specs, prompt names `b.md` then `a.md` | reason has the `b.md` marker before the `a.md` marker |
| AP-4 | modified spec content `v1\n` | marker `specguard-approve docs/specs/x.md@` plus first 7 hex of `sha256("v1\n")` |
| AP-4 | any refusal | reason contains `AskUserQuestion` and `subagent` |
| AP-5 | launch refused, then `user_prompt_submit("hello")` | context has `Open approval requests - docs/specs/x.md` |
| AP-5 | two specs refused in one launch | context lists both, sorted |
| AP-5 | same launch twice, spec unchanged | same marker both times, one entry in the context |
| AP-5 | launch, edit spec, launch again | second marker differs, context lists the spec once |
| AP-5 | refusal by AP-2 (no spec) | context has no `Open approval requests` |
| AP-6 | `AskUserQuestion` with `answers` `{"q": "yes"}` and an unrelated question | deny `pre-filled answers` |
| AP-6 | approval question with `answers` filled | deny `pre-filled answers` |
| AP-7 | one open request, question with its marker, options `✓ Approve`, `Not yet` | `None` |
| AP-7 | same, no option has `✓` | deny mentioning `✓` |
| AP-7 | same, two options have `✓` | deny mentioning `✓` |
| AP-7 | same, only one option, the `✓` one | deny mentioning `✓` |
| AP-7 | two open requests, question carries only the first marker | deny naming the second marker |
| AP-7 | two open requests, question carries both markers, valid options | `None` |
| AP-7 | two questions in one call, the second carries the marker with no `✓` | deny |
| AP-8 | question "Which colour?" with options `Red`, `Blue`, no request open | `None` |
| AP-8 | question with a marker for a spec that has no open request, options without `✓` | `None` |
| AP-8 | question with the marker of the right spec and a wrong hash, no `✓` | `None` |
| AP-9 | open request, answer is the `✓` label, in `tool_input.answers` | line `specguard: approved docs/specs/x.md (<sha7>)`, next launch `None`, context has no open request |
| AP-9 | same, answer only in the fourth argument `{"answers": ...}` | same |
| AP-9 | answer is `✓ Approve` but question text differs from the asked one | no approval line, launch still refused |
| AP-10 | answer is `Not yet` | line `specguard: request closed for docs/specs/x.md`, context has no open request, next launch refused with the same marker |
| AP-11 | spec edited after the request, answer `✓` to the old marker | no approval line, context still lists the spec, launch refused with the new marker |
| AP-11 | answer map has no entry for the question | no approval line, launch refused |
| AP-11 | no request open, an answer to an unrelated question | no approval line |
| AP-12 | two open requests, one question with both markers, `✓` chosen | two approval lines, next launch naming both `None` |
| AP-13 | open request, last assistant turn has its marker, prompt `approve` | approval line |
| AP-13 | same, prompt `Approve.` | approval line |
| AP-13 | same, prompt `ok` | approval line |
| AP-13 | same, prompt `yes ok` | approval line |
| AP-13 | same, prompt `да апрув` | approval line |
| AP-13 | same, prompt `схвалюю` | approval line |
| AP-13 | same, prompt `yes` | no approval line, no message of this gate, request stays open |
| AP-13 | same, prompt `да` | same |
| AP-13 | same, prompt `так` | same |
| AP-13 | same, prompt `approve?` | no approval line |
| AP-13 | same, prompt `approve the spec please` | no approval line |
| AP-13 | same, prompt `+` | no approval line |
| AP-13 | same, prompt `approve`, call carries `agent_type` `specguard:tester` | no approval line |
| AP-14 | marker in the last assistant turn, spec unchanged | approved, next launch `None` |
| AP-14 | two open requests, only the first marker in the last turn | first approved, second still open and its launch refused |
| AP-14 | marker only in an earlier assistant turn, last turn is plain text | `approval not recorded, no marker in the last message`, launch refused |
| AP-14 | marker split over two text blocks of the last turn | approved |
| AP-14 | last turn holds the marker with an old hash | not approved |
| AP-14 | marker in last turn, spec edited after the request | not approved, launch refused with the new marker |
| AP-14 | one marker cut in the middle across two text blocks of the last turn | not approved |
| AP-16 | approved via `✓`, spec edited afterwards, prompt `revoke approval` | revoked line |
| AP-14 | a `user` line comes after the assistant line holding the marker | approved |
| AP-15 | no request open, prompt `approve` | line `specguard: nothing to approve, no request is open` |
| AP-15 | no request open, prompt `go` | no line of this gate |
| AP-15 | request open, transcript file missing, prompt `approve` | line `specguard: could not read the transcript, approval not recorded` |
| AP-15 | request open, transcript missing, prompt `го` | no line of this gate |
| AP-15 | request open, last turn without marker, prompt `ok` | line `specguard: approval not recorded, no marker in the last message` |
| AP-16 | approved via `✓`, prompt `revoke approval` | line `specguard: approval revoked - docs/specs/x.md (<sha7>)`, launch refused again |
| AP-16 | approved via text, prompt `Отзываю апрув` | revoked line |
| AP-16 | approved, prompt `відкликаю апрув, please` | revoked line |
| AP-16 | approved, prompt `revoke approval?` | no revoke line, launch still `None` |
| AP-16 | approved twice (two specs), one revoke | two revoked lines |
| AP-16 | revoke twice in a row | second prints `specguard: nothing to revoke` |
| AP-16 | approval recorded in session `s1`, revoke sent in `s2` | `specguard: nothing to revoke`, launch still `None` |
| AP-16 | spec at HEAD, revoke | launch still `None` |
| AP-17 | feature approval on, prompt `no approval` | `specguard: mode feature, approval off`, then a launch on a modified spec `None` |
| AP-17 | prompt `без апрува` | approval off message |
| AP-17 | prompt `Без апруву.` | approval off message |
| AP-17 | hard mode, prompt `no approval` | `specguard: no-approval refused, hard mode always requires approval`, launch still gated |
| AP-18 | feature approval on, tester registered as `ag-1`, `SendMessage` to `ag-1` | deny `new Agent launch` |
| AP-18 | hard mode, advocate registered, `SendMessage` to it | deny `new Agent launch` |
| AP-18 | feature approval off, same | `None` |
| AP-18 | simple mode, same | `None` |
| AP-18 | agent registered as `general-purpose` | `None` |
| AP-18 | `to` an id never registered | `None` |
| AP-19 | `SendMessage` text `go feature spec` | deny `may not carry mode or approval phrases` |
| AP-19 | `ScheduleWakeup` prompt `go hard mode and continue` | deny |
| AP-19 | `CronCreate` prompt `го хардмод` | deny |
| AP-19 | simple mode, `SendMessage` text `go feature spec` | deny |
| AP-19 | `SendMessage` text `no approval` | deny |
| AP-19 | `SendMessage` text `revoke approval` | deny |
| AP-19 | `SendMessage` text `approve` | deny |
| AP-19 | `CronCreate` prompt `yes ok` | deny |
| AP-19 | `SendMessage` text `Please continue with the tests` | `None` |
| AP-19 | `SendMessage` text `then go feature spec later` | `None` |
| AP-19 | `SendMessage` text `approve the spec please` | `None` |
| AP-19 | `SendMessage` text `yes` | `None` |
| AP-19 | `ScheduleWakeup` prompt `check the build` | `None` |

## Retired rules

None.
