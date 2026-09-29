# Mode switch by the user's confirmation

The user may ask for a mode in his own words anywhere in a message. The agent then asks with AskUserQuestion, the question carrying the marker `specguard-mode <set>`, and his click on the option marked with a check mark switches the mode. Without AskUserQuestion the agent ends a chat message with the same marker line and a short reply of one or two confirm words switches it. This spec covers the marker, the structure check on the question, the answer handling and the text fallback. Start-of-message phrases and the command are in `modes.md`.

**Tests** - `tests/test_spec_mode_confirm.py`

## Public API

Tests drive the hook as a process through `tests/helpers.py`, never by importing anything from `scripts/`. The helpers are described in `docs/specs/core.md` and `docs/specs/modes.md`.

- Build the project with `helpers.make_project(tmp, config={"version": 1, "modes": {"default": "simple", "approval_default": True}})` and pass the same `state_home` to every call of one test.
- The marker is the text `specguard-mode <set>` or `specguard-mode <set> no-approval`, where `<set>` is modes joined by `+` (`feature`, `visual+feature`). A marker may sit anywhere in a question or a chat message.
- The question is checked before it is asked with `helpers.run_hook("PreToolUse", helpers.pre_tool_use(session_id, "AskUserQuestion", {"questions": [{"question": "<text with marker>", "options": [{"label": "✓ Switch"}, {"label": "Stay"}]}]}), project_dir, state_home=state_home)`. An allowed call prints nothing (`stdout_json` is `None`). A refusal is `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<text>"}}`.
- The user's answer arrives with `helpers.run_hook("PostToolUse", helpers.post_tool_use(session_id, "AskUserQuestion", tool_input, tool_response), project_dir, state_home=state_home)`. `tool_input` is `{"questions": [...], "answers": {"<exact question text>": "<chosen option label>"}}`. The same `answers` map may instead sit in `tool_response`, next to or instead of the one in `tool_input`.
- A switch by answer prints `{"systemMessage": "specguard: mode <set>[, approval on|off] (confirmed by you)", ...}` (the approval part as in MC-4). When approval is refused it adds a second line. A declined question prints `{"systemMessage": "specguard: mode unchanged"}`.
- The text fallback: write a transcript file, JSON lines, one object per line, of the form `{"message": {"role": "assistant", "content": [{"type": "text", "text": "<chat text>"}]}}` (a user line is `{"message": {"role": "user", "content": "..."}}`), and send `helpers.user_prompt_submit(session_id, "yes", transcript_path=<that file>)` as `UserPromptSubmit` (the builder accepts `transcript_path`). The transcript is read at the moment of the reply and its last assistant text counts.
- To open a spec request or a visual gate without running the whole approval flow, write the session file `<state_home>/specguard/<key>/sessions/<session_id>.json` before the call, with `requests` (object, spec path to `{"sha256": "<64 hex>"}`) and `visual` (`{"pending": <n>}`). The spec marker of a request is `specguard-approve <spec>@<first 7 hex of its sha256>`.
- The session's mode is read by sending a plain prompt `"hello"` with the same `session_id` and reading the status line at the end of `additionalContext`, `Active specguard mode is <set>[, approval on|off].` (the approval part only when the set holds `feature` or `hard`).
- Only where the printed output cannot show the behaviour, read `<state_home>/specguard/<project dir with every "/" replaced by "-">/log.jsonl`, one JSON object per line with the keys `ev`, `via` and, for a switch, `modes`.

## Rules

- **MC-1** A question carrying a mode marker is allowed to be asked only when it has at least two options and exactly one option label contains `✓`. With no `✓` option, with two or more, or with a single option, the `PreToolUse` is denied with a reason beginning `specguard: ` that mentions `✓`. A question without any marker is not checked by this rule. Why - the hook recognises the switching option by structure, not by words.
- **MC-2** An `AskUserQuestion` whose input carries an `answers` field is denied with a reason beginning `specguard: ` (`AskUserQuestion may not carry pre-filled answers`), marker or not. Why - the agent must not answer for the user.
- **MC-3** A question may not carry a mode marker together with a spec approval marker of an open request (`specguard-approve <spec>@<first 7 hex of sha256>`) or the pending visual marker (`specguard-approve visual@<n>`). It is denied with a reason that contains `may not mix`. Why - one click must mean one thing.
- **MC-4** When the user's answer to a marked question is the label of the `✓` option, the mode switches to the marker's set. The output is the `systemMessage` `specguard: mode <set>, approval on (confirmed by you)` or `specguard: mode <set>, approval off (confirmed by you)` for a set holding `feature` or `hard`, the same wording as the phrase and command paths plus the suffix, and `specguard: mode <set> (confirmed by you)` with no approval word for a set holding neither (`simple`, `visual`), and the status line of the next prompt shows the new set. The approval flag is `modes.approval_default` (on when the config has no such key), always on when the set holds `hard`, and off when the marker ends with `no-approval` (refused with the extra line `specguard: no-approval refused, hard mode always requires approval` in a set holding `hard`). Why - his click is the switch, with the same approval rules as a phrase.
- **MC-5** The switch by answer also delivers the mode rules to the agent. The output's `hookSpecificOutput` has `hookEventName` `PostToolUse` and an `additionalContext` that holds the same rule blocks as a phrase switch (`Feature mode rules.` for a set holding `feature`, `Hard mode adds.` for `hard`, `Visual mode rules.` for `visual`, no mode rules for `simple`). Why - the agent needs the rules of the mode it is now in, and the next prompt line only names the mode.
- **MC-6** Any other answer to a marked question (the label of a non-`✓` option, free text, a label that matches no option) changes nothing and prints `{"systemMessage": "specguard: mode unchanged"}`. Why - declining is not a switch.
- **MC-7** A `✓` answer to a marker whose set is not an accepted one (`simple+feature`, `feature+hard`, `hard+feature`, `turbo`) changes nothing and prints a `systemMessage` beginning `specguard: mode not switched,` that contains `is not a valid combination`. Why - the user sees why nothing happened.
- **MC-8** Only the answer to the question that carries the marker counts. A `PostToolUse` with no `answers` (in both `tool_input` and `tool_response`) or with an empty map, an `answers` map keyed by other text than the question, and a `✓` answer to a question without a marker, all print nothing and change nothing. Why - unrelated questions never switch.
- **MC-9** A `PostToolUse` from a role session (`agent_type` `specguard:tester` or `specguard:devils-advocate`) switches nothing and prints nothing, even with a valid marker and a `✓` answer. Why - roles never set the mode.
- **MC-10** Text fallback. A user message of one or two words, every word being a confirm word, switches the mode when the last assistant text of the transcript contains a mode marker. The `systemMessage` is exactly the one line of MC-4 for the set, `specguard: mode <set>, approval on|off (confirmed by you)` for a set holding `feature` or `hard` and `specguard: mode <set> (confirmed by you)` otherwise (no other line, also when the word is an approval word such as `ok` and no spec request is open), and `additionalContext` holds, the intro, the mode rules as in MC-5 and the status line. Confirm words, whole word after lower-casing and dropping `. , ! : ;` around it - `yes`, `y`, `ok`, `okay`, `+`, `go`, `approve`, `да`, `так`, `ок`, `ага`, `угу`, `давай`, `го`, `апрув`, and the two-word `так точно`. Two confirm words together also count (`да го`, `yes ok`). Why - a lone yes-word is enough when the last thing the agent said was a mode question, unlike a spec approval.
- **MC-11** The text fallback needs the marker in the last assistant text only. It switches nothing and prints nothing when the message is three or more words, holds a word that is not a confirm word (`да нет`, `не го`, `погоди, го`, `yes please`), contains `?` (`да?`), starts with `<` (a `<task-notification>` payload), comes from a role session, when the last assistant text has no marker even though an earlier assistant text had one, when the transcript is missing, unreadable or has no assistant line, and when the `transcript_path` field is absent. Why - only a fresh, unanswered question can be confirmed.
- **MC-12** The text fallback applies the same set rules as the answer path. A marker with `no-approval` switches with approval off, in a set holding `hard` with the refusal line and approval on. A marker with a set that is not accepted prints the `not switched ... is not a valid combination` message and changes nothing. Why - the click and the word mean the same.
- **MC-13** The last assistant text is the text blocks of the last assistant line of the transcript, joined by a line break; a marker in any of them counts, and lines of other roles are skipped. Why - the marker may sit in the second text block of a message that also called a tool.
- **MC-14** Each confirmed switch is logged as `{"ev": "mode-switch", "via": "ask"}` (answer) or `"via": "text"` (text fallback) with the sorted `modes`; a declined question is logged as `{"ev": "mode-unchanged", "via": "ask"}`. Why - the log is the trace of who changed the mode and how.

## Examples

| Rule | Input | Expected |
|---|---|---|
| MC-1 | question `Switch? specguard-mode feature`, options `✓ Switch`, `Stay` | allowed, no output |
| MC-1 | same, options `Switch`, `Stay` | deny, reason mentions `✓` |
| MC-1 | same, options `✓ Yes`, `✓ Maybe`, `Stay` | deny |
| MC-1 | same, single option `✓ Switch` | deny |
| MC-1 | same, no options | deny |
| MC-1 | question `Do you like it?`, no marker, no `✓` option | allowed |
| MC-1 | marker `specguard-mode visual+feature no-approval`, options `✓ Go`, `No` | allowed |
| MC-2 | question with marker, `answers` set in the input | deny, `pre-filled answers` |
| MC-2 | question without marker, `answers` set in the input | deny |
| MC-3 | session file has request `docs/specs/x.md` with sha256 `a` times 64, question holds `specguard-approve docs/specs/x.md@aaaaaaa` and `specguard-mode feature` | deny, `may not mix` |
| MC-3 | session file holds visual pending `3`, question holds `specguard-approve visual@3` and `specguard-mode feature` | deny, `may not mix` |
| MC-3 | no open request, question holds `specguard-approve docs/specs/x.md@aaaaaaa` and `specguard-mode feature`, options ok | allowed (the spec marker is not an open one) |
| MC-4 | marker `feature`, answer `✓ Switch` | message exactly `specguard: mode feature, approval on (confirmed by you)`; status line `feature, approval on` |
| MC-4 | marker `hard`, answer the `✓` label | message exactly `specguard: mode hard, approval on (confirmed by you)`, status line `hard, approval on` |
| MC-4 | marker `feature no-approval` | message exactly `specguard: mode feature, approval off (confirmed by you)` |
| MC-4 | config `approval_default` false, marker `feature` | message exactly `specguard: mode feature, approval off (confirmed by you)` |
| MC-4 | marker `visual+feature`, the `✓` label | message exactly `specguard: mode visual+feature, approval on (confirmed by you)` |
| MC-4 | marker `hard no-approval` | first message line exactly `specguard: mode hard, approval on (confirmed by you)`, second line the refusal |
| MC-4 | marker `simple` from a `feature` session, the `✓` label | message exactly `specguard: mode simple (confirmed by you)` |
| MC-4 | marker `visual`, the `✓` label | message exactly `specguard: mode visual (confirmed by you)` |
| MC-4 | marker `visual+feature`, answer the `✓` label | status line `Active specguard mode is visual+feature, approval on.` |
| MC-4 | marker `simple` from a `feature` session | status line `Active specguard mode is simple.` |
| MC-4 | marker `feature no-approval` | status line `feature, approval off` |
| MC-4 | marker `hard no-approval` | two-line message with the refusal, status line `hard, approval on` |
| MC-4 | config `approval_default` false, marker `feature` | `approval off` |
| MC-4 | `answers` only in `tool_response` | the same switch |
| MC-5 | marker `hard`, `✓` answer | `hookEventName` `PostToolUse`, `additionalContext` has `Hard mode adds.` |
| MC-5 | marker `visual`, `✓` answer | `Visual mode rules.` |
| MC-5 | marker `simple`, `✓` answer | no `mode rules.` and no `Hard mode adds.` in the context |
| MC-6 | marker `feature`, answer `Stay` | `{"systemMessage": "specguard: mode unchanged"}`, mode as before |
| MC-6 | answer `something else entirely` | the same |
| MC-7 | marker `simple+feature`, `✓` answer | `is not a valid combination`, mode as before |
| MC-7 | marker `turbo`, `✓` answer | the same |
| MC-7 | marker `hard+feature`, `✓` answer | the same |
| MC-8 | no `answers` anywhere | no output |
| MC-8 | `answers` is `{}` | no output |
| MC-8 | `answers` keyed by another text | no output, no switch |
| MC-8 | question without marker, `✓` answered | no output, no switch |
| MC-9 | marker `hard`, `✓` answer, `agent_type` `specguard:tester` | no output, mode as before |
| MC-9 | the same with `specguard:devils-advocate` | no output |
| MC-10 | last assistant text `Switch to feature? specguard-mode feature`, reply `yes` | message exactly `specguard: mode feature, approval on (confirmed by you)`; context has `Feature mode rules.` |
| MC-10 | marker `hard`, reply `yes` | message exactly `specguard: mode hard, approval on (confirmed by you)` |
| MC-10 | marker `feature no-approval`, reply `ok` | message exactly `specguard: mode feature, approval off (confirmed by you)` |
| MC-10 | reply `y` | switch |
| MC-10 | reply `ok` | switch |
| MC-10 | reply `+` | switch |
| MC-10 | reply `да` | switch |
| MC-10 | reply `так` | switch |
| MC-10 | reply `ок` | switch |
| MC-10 | reply `го` | switch |
| MC-10 | reply `так точно` | switch |
| MC-10 | reply `Да.` | switch |
| MC-10 | reply `yes!` | switch |
| MC-10 | reply `да го` | switch |
| MC-10 | reply `yes ok` | switch |
| MC-10 | marker `simple`, reply `yes` | message exactly `specguard: mode simple (confirmed by you)` |
| MC-10 | marker `visual`, reply `yes` | message exactly `specguard: mode visual (confirmed by you)` |
| MC-10 | marker `visual+feature`, reply `давай` | status line `visual+feature` |
| MC-11 | reply `да нет` | no output, no switch |
| MC-11 | reply `не го` | no output |
| MC-11 | reply `yes please` | no output |
| MC-11 | reply `да, покажи правила сначала` | no output |
| MC-11 | reply `yes yes yes` | no output |
| MC-11 | reply `да?` | no output |
| MC-11 | reply `<task-notification>ok</task-notification>` | no output |
| MC-11 | reply `yes` with `agent_type` `specguard:tester` | no output |
| MC-11 | assistant texts: first with marker, second `Anything else?`, reply `yes` | no output, no switch |
| MC-11 | the transcript file does not exist | no output |
| MC-11 | transcript holds only a user line | no output |
| MC-11 | no `transcript_path` given | no output |
| MC-12 | marker `feature no-approval`, reply `yes` | message, status line `feature, approval off` |
| MC-12 | marker `hard no-approval`, reply `yes` | refusal line in the message, `hard, approval on` |
| MC-12 | marker `simple+hard`, reply `yes` | `is not a valid combination`, mode as before |
| MC-13 | last assistant line has two text blocks, the marker in the second, reply `yes` | switch |
| MC-13 | last assistant line has a text block and a `tool_use` block, marker in the text, reply `yes` | switch |
| MC-13 | assistant line with marker, then a user line, reply `yes` | switch (user lines are skipped) |
| MC-14 | answer switch | log line `mode-switch`, `via` `ask` |
| MC-14 | text switch | log line `mode-switch`, `via` `text` |
| MC-14 | declined question | log line `mode-unchanged`, `via` `ask` |

## Retired rules

None.
