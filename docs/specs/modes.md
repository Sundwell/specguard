# Modes - switching by command and by start-of-message phrase

The session mode is set only by the user, either with the command `/specguard:mode` or with a phrase at the very start of his message, taken from the English, Russian and Ukrainian packs at once in every project. This spec covers both routes, the echo line, the state a switch leaves behind and the ban on agent-sent prompts that carry a mode phrase. Switching by the user's confirmation is a separate spec, `mode-confirm.md`.

**Tests** - `tests/test_spec_modes.py`

## Public API

Tests drive the hook as a process through `tests/helpers.py`, never by importing anything from `scripts/`. Every helper below is described in `docs/specs/core.md`.

- Build a project with `helpers.make_project(tmp, config={"version": 1, "modes": {"default": "simple", "approval_default": True}})`. `modes.default` holds exactly one of `simple`, `feature`, `hard`, `visual`. `modes.approval_default` (boolean, default true) is the approval flag a switch starts from.
- A phrase arrives as `helpers.run_hook("UserPromptSubmit", helpers.user_prompt_submit(session_id, prompt), project_dir, state_home=state_home)`. It returns `(exit_code, stdout_json, stderr)`; the exit code is always 0.
- A command arrives as `helpers.run_hook("UserPromptExpansion", helpers.user_prompt_expansion(session_id, "specguard:mode", "feature no-approval"), project_dir, state_home=state_home)`. The command name is `specguard:mode`; any other command name is not a mode command.
- A role session is a call with `agent_type="specguard:tester"` (or `"specguard:devils-advocate"`) passed to the builder.
- A switch prints `{"systemMessage": "<text>", "hookSpecificOutput": {"hookEventName": ..., "additionalContext": "<text>"}}` (the message may be alone for a refusal). The message is one line per fact and always English, starting with `specguard: `.
- The session's current state is read by sending an ordinary prompt such as `"hello"` with the same `session_id` and the same `state_home` and reading `additionalContext` of the output. It always ends with one line `Active specguard mode is <set>[, approval on|off].` where `<set>` is the modes joined by `+` in the order visual, feature, hard, simple, and the approval part appears only when the set holds `feature` or `hard`. This line is called the status line below.
- Pass the same `state_home` to every call of one test, it holds the session. Different `session_id` values are different sessions.
- Only where the printed output cannot show the behaviour, read `<state_home>/specguard/<project dir with every "/" replaced by "-">/log.jsonl`, one JSON object per line with the keys `ev`, `via` and, for a switch, `modes`.

## Rules

- **MO-1** The command `/specguard:mode <set> [no-approval]` switches the session to `<set>` when it is one of `simple`, `feature`, `hard`, `visual`, `visual+feature`, `visual+hard`. The output is a `systemMessage` that is exactly `specguard: mode <set>, approval on` or `specguard: mode <set>, approval off`. The status line of the next prompt shows the new set. Why - the command is the primary, language-neutral switch.
- **MO-2** Approval after a switch. Without `no-approval` the approval flag is `modes.approval_default` (true when the config has no such key), except in a set holding `hard`, where it is always on. With `no-approval` it is off. Why - a switch never carries approval over from the previous mode.
- **MO-3** `no-approval` together with a set holding `hard` is refused. The switch still happens, approval stays on, and the `systemMessage` is two lines, the `specguard: mode <set>, approval on` line and then `specguard: no-approval refused, hard mode always requires approval`. Why - hard mode cannot run without approval.
- **MO-4** A command that is not exactly one accepted set optionally followed by the single word `no-approval` changes nothing and prints a `systemMessage` beginning `specguard: mode not switched, usage /specguard:mode` that lists the accepted sets. That covers empty arguments, an unknown mode word, a pair that is not accepted (`simple+feature`, `feature+hard`, `hard+feature`), any second word other than `no-approval`, and a third word. Why - a typo must not silently change the mode.
- **MO-5** A `UserPromptExpansion` for another command name (`specguard:guide`), and any `UserPromptExpansion` from a role session, prints nothing and changes nothing. Why - only the user's own mode command counts, roles never switch.
- **MO-6** A phrase at the very start of a message switches the mode, with the same output and the same approval rules as the command (MO-1 to MO-3). A phrase is a trigger word, a mode word, optionally a join and a second mode word, optionally a filler and an opt-out, from any pack, in any mix. Triggers `go`, `го`, `режим`. Mode words - feature `feature spec`, `featurespec`, `feature`, `фичспек`, `фич спек`, `фича спек`, `фичаспек`, `фиче спек`, `фичеспек`, `фічспек`, `фіч спек`, `фіча спек`, `фічаспек`, `фіче спек`, `фічеспек`; hard `hard mode`, `hardmode`, `хардмод`, `хард мод`; visual `visual`, `визуал`, `візуал`; simple `simple`, `простой`, `простий`. Joins `and`, `и`, `і`, `+`, and a comma between two mode words. Filler after the mode `режим`. Opt-outs `no approval`, `без апрува`, `без аппрува`, `без апруфа`, `без апруву`. Why - the user dictates, in three languages, and the phrase is a convenience on top of the command.
- **MO-7** Letter case, `ё` versus `е`, the dashes U+2010, U+2011, U+2013, U+2014 and the plain hyphen, and a hyphen between two letters do not matter (`Го фич-спек` is `го фичспек`, `го хард-мод` is `го хардмод`), and a comma after the trigger, before a filler or before an opt-out is a separator. Any of `. , ! : ;` after the phrase ends it. A phrase after leading whitespace counts. Why - speech recognition puts punctuation and capitals at random.
- **MO-8** End of the phrase. A phrase must be followed by the end of the message, a line break, or one of `. , ! : ; -`. The exception is a phrase whose trigger is `go` or `го`, whose modes are only `feature`, `hard` or `visual`, and that has no opt-out - it may be followed by anything (`го фичспек сделай доменную часть` switches to feature). A `простой` or `simple` phrase, and any phrase opened by `режим`, must end (`го простой фикс` and `режим визуал выключи` switch nothing). Why - a mode word in ordinary talk must not switch, but a work order after the switch is normal.
- **MO-9** An opt-out counts only when it is followed by the end of the phrase (MO-8). `го фичспек без апрува тестера не запускай` and `Го фичспек, только без апрува` switch to feature with the default approval, `го фичспек без апрува.` and `Го фичспек, без апрува.` and `go feature spec, no approval` switch with approval off. Why - a garbled opt-out leaves approval on.
- **MO-10** Nothing but the start of the message counts. A message that starts with anything else than the trigger (a question word, `не`, a name, `Простой режим тут не подойдёт`, `Что будет в режиме простой?`, `не го простой`), and a phrase later in the message, switch nothing and print nothing but the status line context. Why - only an explicit phrase at the start is a switch.
- **MO-11** A message that would switch but ends with `?` switches nothing and prints the `systemMessage` `specguard: mode not switched, message ends with "?"`. A message that would not switch and ends with `?` prints no such message. Why - a question about a mode is not an order.
- **MO-12** A phrase for a pair that is not accepted (`го простой и фичспек`) changes nothing and prints a `systemMessage` beginning `specguard: mode not switched,` that contains `is not a valid combination` and names the accepted sets. Why - the user sees why it did not work.
- **MO-13** A message whose first character is not a letter (a `<task-notification>` payload, a raw `/specguard:mode feature` prompt, a digit, a quote), and every prompt of a role session, switch nothing even when the text after that would be a phrase. Why - harness-wrapped prompts and roles never switch.
- **MO-14** A switch replaces the whole previous set and the approval flag, in the session of the call only. After `го хардмод` and then `го простой` the status line reads `Active specguard mode is simple.` Another session id is unaffected. Why - the user says the mode he wants, there is no add or remove.
- **MO-15** On a switch the `additionalContext` carries the specguard intro line (it mentions `specguard:guide`) and the mode rules. A set holding `feature` carries `Feature mode rules.`, one holding `hard` carries `Hard mode adds.`, one holding `visual` carries `Visual mode rules.`, and `simple` carries no mode rules. A prompt that does not switch carries only the status line. Why - the agent needs the rules of the mode it is now in.
- **MO-16** Every switch is logged as `{"ev": "mode-switch", "modes": [...], "via": "command"}` or `"via": "phrase"`, with the modes sorted alphabetically. A refusal (MO-4, MO-11, MO-12) writes no `mode-switch` line. Why - the log is the only trace of who changed the mode.
- **MO-17** An agent-sent prompt may not carry a mode phrase. A `PreToolUse` for `SendMessage` (field `message`), `CronCreate` or `ScheduleWakeup` (field `prompt`) from the executor whose text would switch under MO-6 to MO-9 is denied with a reason beginning `specguard: `. Ordinary text passes. Why - the agent must not switch the mode by talking to itself.

## Examples

| Rule | Input | Expected |
|---|---|---|
| MO-1 | command `feature` | message `specguard: mode feature, approval on`; status line `Active specguard mode is feature, approval on.` |
| MO-1 | command `simple` | message `specguard: mode simple, approval on` |
| MO-1 | command `visual+feature` | message `specguard: mode visual+feature, approval on` |
| MO-1 | command `visual+hard` | message `specguard: mode visual+hard, approval on` |
| MO-1 | command `hard` | message `specguard: mode hard, approval on` |
| MO-2 | config `approval_default` false, command `feature` | message `specguard: mode feature, approval off` |
| MO-2 | config `approval_default` false, command `hard` | approval on |
| MO-2 | command `feature no-approval` | message `specguard: mode feature, approval off`; status line `..., approval off.` |
| MO-2 | command `visual+feature no-approval` | approval off |
| MO-2 | `feature no-approval`, then command `feature` | status line approval on |
| MO-3 | command `hard no-approval` | two-line message, approval on |
| MO-3 | command `visual+hard no-approval` | two-line message, approval on |
| MO-4 | command with empty arguments | `specguard: mode not switched, usage /specguard:mode`, mode unchanged |
| MO-4 | command `turbo` | the same, mode unchanged |
| MO-4 | command `simple+feature` | the same |
| MO-4 | command `feature+hard` | the same |
| MO-4 | command `feature yes-approval` | the same |
| MO-4 | command `feature no-approval now` | the same |
| MO-5 | command name `specguard:guide`, args `hard` | no output, mode unchanged |
| MO-5 | command `hard` with `agent_type` `specguard:tester` | no output, mode unchanged |
| MO-6 | `go feature spec` | feature, approval on |
| MO-6 | `го фичспек` | feature |
| MO-6 | `го фічспек` | feature |
| MO-6 | `режим фичспек` | feature |
| MO-6 | `go hard mode` | hard |
| MO-6 | `go hardmode` | hard |
| MO-6 | `го хард мод` | hard |
| MO-6 | `го хардмод` (ru and uk share it) | hard |
| MO-6 | `go visual` | visual |
| MO-6 | `го візуал` | visual |
| MO-6 | `go simple` | simple |
| MO-6 | `го простий` | simple |
| MO-6 | `го простой` | simple |
| MO-6 | `го визуал и фичспек` | visual+feature |
| MO-6 | `го візуал і фічспек` | visual+feature |
| MO-6 | `go visual and feature spec` | visual+feature |
| MO-6 | `го визуал + хардмод` | visual+hard |
| MO-6 | `Го визуал, фичспек.` | visual+feature |
| MO-6 | `go visual і фічспек` (mixed packs) | visual+feature |
| MO-6 | `го хардмод режим` | hard |
| MO-6 | `го простой режим` | simple |
| MO-6 | `го фичспек без апруву` | feature, approval off |
| MO-6 | `go feature spec no approval` | feature, approval off |
| MO-7 | `ГО ФИЧСПЕК` | feature |
| MO-7 | `Го фич-спек, сделай доменную часть.` | feature |
| MO-7 | `го хард-мод` | hard |
| MO-7 | `го фич–спек` (en dash) | feature |
| MO-7 | `Го, фичспек.` | feature |
| MO-7 | `  go feature spec` | feature |
| MO-7 | `Go, hard mode!` | hard |
| MO-8 | `го фичспек сделай доменную часть` | feature |
| MO-8 | `go visual and start with the header` | visual (the word after `and` is not a mode, the tail is free), approval default |
| MO-8 | `Го простой - поправь футер.` | simple |
| MO-8 | `Го простой. Поправь футер` | simple |
| MO-8 | `go simple, fix the footer` | simple |
| MO-8 | `го простой фикс` | no change |
| MO-8 | `Го, там простой фикс` | no change |
| MO-8 | `режим визуал выключи` | no change |
| MO-8 | `go simple fix the footer` | no change |
| MO-8 | `Го простой\nпоправь футер` | simple |
| MO-9 | `го фичспек без апрува.` | feature, approval off |
| MO-9 | `Го фичспек, без апрува.` | feature, approval off |
| MO-9 | `го фичспек без апрува, сделай X` | feature, approval off |
| MO-9 | `го фичспек без апрува тестера не запускай` | feature, approval on |
| MO-9 | `Го фичспек, только без апрува тестера не запускай` | feature, approval on |
| MO-9 | `go feature spec no approval for the tester` | feature, approval on |
| MO-10 | `Простой режим тут не подойдёт` | no change, no `systemMessage` |
| MO-10 | `Что будет в режиме простой?` | no change, no `systemMessage` |
| MO-10 | `не го простой` | no change |
| MO-10 | `Как работает хардмод режим?` | no change |
| MO-10 | `Почему тестер ушёл без апрува?` | no change |
| MO-10 | `please go feature spec` | no change |
| MO-10 | `Ок. Го фичспек` | no change |
| MO-10 | `simple` | no change |
| MO-11 | `го хардмод?` | message `specguard: mode not switched, message ends with "?"`, mode unchanged |
| MO-11 | `Го фичспек. Что скажешь?` | the same |
| MO-11 | `Го фичспек, сделай доменную часть?` | the same |
| MO-11 | `go simple?` | the same |
| MO-11 | `Как ты?` | no message |
| MO-12 | `го простой и фичспек` | `not switched`, `is not a valid combination`, mode unchanged |
| MO-12 | `go feature and hard mode` | the same |
| MO-13 | `<task-notification>го хардмод</task-notification>` | no switch |
| MO-13 | `/specguard:mode feature` as a prompt | no switch |
| MO-13 | `1. go feature spec` | no switch |
| MO-13 | `"го хардмод"` | no switch |
| MO-13 | `го хардмод` with `agent_type` `specguard:tester` | no output, mode unchanged |
| MO-13 | `go simple` with `agent_type` `specguard:devils-advocate` | no output, mode unchanged |
| MO-14 | `го хардмод`, then `го простой` | status line `Active specguard mode is simple.` |
| MO-14 | `го визуал и фичспек`, then `го фичспек` | `Active specguard mode is feature, approval on.` |
| MO-14 | session `s1` set to hard, prompt from session `s2` | `s2` shows the default |
| MO-15 | `го хардмод` | context has `Hard mode adds.` and `Feature mode rules.` and `specguard:guide` |
| MO-15 | `го фичспек` | context has `Feature mode rules.`, not `Hard mode adds.` |
| MO-15 | `го визуал` | context has `Visual mode rules.`, not `Feature mode rules.` |
| MO-15 | `го простой` | context has `specguard:guide`, no `mode rules.` |
| MO-15 | `го визуал и фичспек` | both `Visual mode rules.` and `Feature mode rules.` |
| MO-15 | `hello` | context is only the status line, no `mode rules.` |
| MO-16 | a phrase switch to feature | log line `ev` `mode-switch`, `via` `phrase`, `modes` `["feature"]` |
| MO-16 | a command switch to visual+feature | `via` `command`, `modes` `["feature", "visual"]` |
| MO-16 | a refused command | no `mode-switch` line |
| MO-17 | `SendMessage` `message` `го хардмод` | deny, reason begins `specguard: ` |
| MO-17 | `CronCreate` `prompt` `go feature spec` | deny |
| MO-17 | `ScheduleWakeup` `prompt` `го визуал и фичспек` | deny |
| MO-17 | `SendMessage` `message` `please run the tests` | no output |
| MO-17 | `SendMessage` `message` `го фичспек сделай X` | deny |

## Retired rules

None.
