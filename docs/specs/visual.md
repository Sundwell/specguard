# Visual-mode gate

**Tests** - `tests/test_spec_visual.py`

The gate makes the executor wait for the user's go before the first UI edit of a session. It is active only while the session's mode set contains `visual` (`visual`, `visual+feature`, `visual+hard`) and the project config has a `visual` object. The user's go is recorded only by the hook, from his `✓` answer to a marked AskUserQuestion or from his short approval message right after the marked request; nothing the agent can write opens it.

## Public API

Everything is driven through `tests/helpers.py` (`sys.path` gets `tests/`; import `helpers`). One hook process per event, with `helpers.run_hook(event, payload, project_dir, state_home=<one temp dir shared by the whole test>)`, which returns `(exit_code, stdout_json_or_None, stderr)`. The exit code is always 0. Build the project with `helpers.make_project(tmp, config=..., files=..., git=True, commit=True)`.

Config used by the tests, in `.claude/specguard.json` - `{"version": 1, "modes": {"default": "visual"}, "visual": {"ui_paths": ["app/"], "deviations_log": "docs/design-deviations.md", "notes": ".claude/specguard/visual-notes.md"}}`. Variants change `modes.default` (`feature`, `simple`, `hard`) or drop the `visual` key. `visual+feature` is reached by a mode switch (below), since `modes.default` accepts only a single mode.

| Action | Payload and call | Observable output |
|---|---|---|
| Executor edit | `helpers.pre_tool_use(sid, tool, tool_input)` sent as event `PreToolUse`, tool one of `Edit`, `Write`, `MultiEdit`, `NotebookEdit` (path in `file_path`, or `notebook_path` for `NotebookEdit`) | Denied - stdout JSON `hookSpecificOutput.permissionDecision == "deny"` and `permissionDecisionReason` containing the marker `specguard-approve visual@<n>`. Allowed - stdout is `None` (nothing printed) |
| Role edit | the same payload with keyword `agent_type="specguard:tester"` or `"specguard:devils-advocate"` | see VG-9 |
| Answer by click | `helpers.post_tool_use(sid, "AskUserQuestion", tool_input, tool_input)` sent as event `PostToolUse`, where `tool_input = {"questions": [{"question": Q, "options": [{"label": "✓ Approve"}, {"label": "No"}]}], "answers": {Q: chosen_label}}` and `Q` contains the marker text | Approved - stdout JSON `systemMessage` containing `visual gate open`. Not approved - `systemMessage` absent or without `visual gate open` |
| Answer by text | `helpers.user_prompt_submit(sid, prompt, transcript_path=T)` sent as event `UserPromptSubmit`, where `T` is a file you write holding one JSON line `{"message": {"role": "assistant", "content": [{"type": "text", "text": TEXT}]}}` | Approved - `systemMessage` containing `visual gate open`. Otherwise it does not contain that text |
| Mode switch | `helpers.user_prompt_expansion(sid, "specguard:mode", "visual")` (or `"visual+feature"`, `"feature"`) sent as event `UserPromptExpansion` | a `specguard:` line; effect seen on the next edit |

The marker is read from the first denial, never hard-coded, except for the numbers `visual@1` and `visual@2`, which VG-2 and VG-7 fix. Where the printed output cannot show the behaviour, read `<state_home>/specguard/<abs project dir with "/" replaced by "-">/log.jsonl`, one JSON object per line with keys `ev`, `sid`, `role`, `rule`, and for approvals `via`.

## Rules

- **VG-1** In a mode set containing `visual`, an executor `Edit`, `Write`, `MultiEdit` or `NotebookEdit` on a file under a `visual.ui_paths` entry is denied while the gate is closed. The gate starts closed in every session. This is the whole point of the mode - no UI edit lands before his go.
- **VG-2** The denial carries the marker `specguard-approve visual@<n>`. The first denial of a session is `visual@1`. The number identifies the request, so an old go cannot approve a new request.
- **VG-3** A `ui_paths` entry matches a file whose repo-relative path equals the entry or sits below it, at a path-segment boundary, with leading or trailing slashes on the entry ignored (`app/`, `/app`, `app` are the same). `app` covers `app/button.tsx` and `app/a/b/c.vue` but not `application/x.tsx`, `apps/x.tsx` or `src/app/x.tsx`. A relative `file_path` is resolved against the payload `cwd`. A path outside the project, or under no entry, is not gated. An empty `ui_paths` or a blank entry gates nothing. So the gate stays out of the way of code that is not UI.
- **VG-4** Only the four write tools are gated. `Read`, `Grep`, `Glob` and `Bash` calls on a UI path pass this gate. The plan puts the gate on file edits only, Bash writes are a known residual.
- **VG-5** The gate is active only when the session's mode set contains `visual`. In `simple`, `feature` and `hard` mode a UI edit is not denied by it, and a project config without a `visual` key gates nothing even in visual mode. The gate must not tax sessions that did not ask for it.
- **VG-6** While the request is open, every further denial repeats the same marker number; the number changes only after a switch into visual (VG-10). Two denials for one request must not send the agent chasing two markers.
- **VG-7** After a new denial cycle the marker number grows by one, within the same session. The first cycle is `visual@1`, the cycle after a new switch is `visual@2`.
- **VG-8** An `AskUserQuestion` answer opens the gate only when the question text carries the open marker and the chosen label is the option whose label contains `✓`. Then the hook prints `specguard: visual gate open`, and every later edit on any UI path in that session passes with no output. Any other chosen label, a question without the open marker, or a marker of a different number leaves the gate closed and the next edit is denied with the same marker as before.
- **VG-9** A text approval opens the gate when all of these hold - the prompt starts with a letter, does not end with `?`, has one or two words, every word is an approval word from the language packs (`approve`, `approved`, `ok`, `okay`, `go` in English; `апрув`, `го`, `одобряю` in Russian; `схвалюю`, `так` in Ukrainian), a lone `yes`, `да` or `так` never counts, and the last assistant message in the transcript at `transcript_path` contains the open marker. Then the hook prints `specguard: visual gate open`. If the last assistant message lacks the marker, the transcript cannot be read, or no request is open, the gate stays closed. The text path is the fallback for a session without a clickable question and must count only right after the request.
- **VG-10** A switch into a mode set containing `visual`, by `/specguard:mode` or a start-of-message phrase, closes an open gate and drops any open request, even when the mode set is the same as before. The edit after the switch is denied with the next marker number (VG-7). Switching to a set without `visual` does not touch the gate, but switching back into a set with `visual` closes it again. Each visual stretch needs its own go.
- **VG-11** The gate applies only to the executor. An edit sent with `agent_type` `specguard:tester` on a UI path is not denied with a visual marker, and `specguard:devils-advocate` is denied by its own read-only rule, not with a visual marker. The visual gate is not a role lock.
- **VG-12** Sessions are independent. A go given in session `s1` does not open session `s2`, and each session numbers its markers from 1.
- **VG-13** The log records the gate. A denial writes an event with `ev` `deny`, `rule` `V`. A go by click writes `ev` `approval`, `rule` `V`, `via` `ask`; a go by text writes the same with `via` `text`; a switch into visual writes `ev` `visual-close`, `rule` `V`. So a later reader can tell who opened and closed the gate.
- **VG-14** The denial reason names the sheet command the plugin ships, `compare-sheet`, and has no `~/` in it. Other people install the plugin, so the reason may only point at tools that come with it, never at a path in the author's home.

## Examples

Project `app/button.tsx`, `app/a/b/c.vue`, `apps/x.tsx`, `application/x.tsx`, `src/app/x.tsx`, `docs/note.md` exist, config as above unless the row says otherwise. "Edit" means an executor `Edit` unless another tool is named.

| Rule | Input | Expected |
|---|---|---|
| VG-1 | first Edit of `app/button.tsx` in session s1 | deny with a marker |
| VG-1 | Write of a new file `app/new.tsx` | deny |
| VG-1 | MultiEdit on `app/button.tsx` | deny |
| VG-1 | NotebookEdit with `notebook_path` `app/n.ipynb` | deny |
| VG-2 | first denial in s1 | reason contains `specguard-approve visual@1` |
| VG-3 | Edit `app/a/b/c.vue` | deny |
| VG-3 | Edit `application/x.tsx` | no output |
| VG-3 | Edit `src/app/x.tsx` | no output |
| VG-3 | Edit `docs/note.md` | no output |
| VG-3 | Edit with relative `file_path` `app/button.tsx` and `cwd` the project dir | deny |
| VG-3 | `ui_paths` `["app"]` instead of `["app/"]`, Edit `app/button.tsx` | deny |
| VG-3 | `ui_paths` `["/app/"]`, Edit `app/button.tsx` | deny |
| VG-3 | `ui_paths` `[]`, Edit `app/button.tsx` | no output |
| VG-3 | `ui_paths` `[""]`, Edit `app/button.tsx` | no output |
| VG-3 | Edit of an absolute path in another directory outside the project | no output |
| VG-4 | Read `app/button.tsx` | no output |
| VG-4 | Bash `ls app` | no output |
| VG-5 | `modes.default` `feature` (approval off), Edit `app/button.tsx` | no output |
| VG-5 | `modes.default` `simple`, Edit `app/button.tsx` | no output |
| VG-5 | `modes.default` `hard`, Edit `app/button.tsx` | no output |
| VG-5 | `modes.default` `visual` but no `visual` key in config, Edit `app/button.tsx` | no output |
| VG-5 | `modes.default` `feature`, switch `visual+feature`, Edit `app/button.tsx` | deny with a visual marker |
| VG-6 | two Edits in a row on `app/button.tsx`, no answer between | both denials carry `visual@1` |
| VG-6 | Edit `app/button.tsx`, then Edit `app/a/b/c.vue` | both carry the same marker |
| VG-7 | deny, click `✓`, switch `visual`, Edit | reason contains `visual@2` |
| VG-8 | deny, click answer `✓ Approve`, Edit | `visual gate open` printed, then Edit gives no output |
| VG-8 | after the go, Edit `app/a/b/c.vue` and Write `app/new.tsx` | no output for both |
| VG-8 | deny, click `No`, Edit | deny with the same marker, no `visual gate open` |
| VG-8 | deny, question without the marker text, chosen `✓ Approve`, Edit | deny |
| VG-8 | deny (`visual@1`), question carrying `specguard-approve visual@7`, chosen `✓ Approve`, Edit | deny with `visual@1` |
| VG-8 | deny, question with marker, options `A` and `B` with no `✓`, chosen `A`, Edit | deny |
| VG-9 | deny, assistant text `Please confirm specguard-approve visual@1`, prompt `approve` | `visual gate open`, then Edit no output |
| VG-9 | same, prompt `апрув` | `visual gate open` |
| VG-9 | same, prompt `схвалюю` | `visual gate open` |
| VG-9 | same, prompt `ok go` | `visual gate open` |
| VG-9 | same, prompt `yes` | not opened, Edit denied |
| VG-9 | same, prompt `да` | not opened |
| VG-9 | same, prompt `так` | not opened |
| VG-9 | same, prompt `approve?` | not opened |
| VG-9 | same, prompt `approve please now` | not opened |
| VG-9 | same, prompt `approve the change` | not opened |
| VG-9 | assistant text without the marker, prompt `approve` | not opened, Edit denied |
| VG-9 | assistant text carrying `specguard-approve visual@9`, prompt `approve` | not opened |
| VG-9 | transcript path that does not exist, prompt `approve` | not opened |
| VG-9 | no Edit yet (no request open), prompt `approve` | not opened; the next Edit is denied |
| VG-9 | prompt `approve` sent with agent_type `specguard:tester` | not opened |
| VG-10 | go given, switch `visual`, Edit | deny |
| VG-10 | go given, switch `feature`, then switch `visual+feature`, Edit | deny |
| VG-10 | go given, switch `feature`, Edit `app/button.tsx` | no output |
| VG-10 | deny (request open), switch `visual`, Edit | deny with `visual@2` |
| VG-11 | Edit `app/button.tsx` with `agent_type` `specguard:tester` | no output |
| VG-11 | Edit `app/button.tsx` with `agent_type` `specguard:devils-advocate` | deny whose reason has no `specguard-approve visual` |
| VG-12 | go in s1, Edit in s2 | s2 denied with `visual@1` |
| VG-13 | deny, click `✓ Approve`, read log | lines `deny` with `rule` `V`, then `approval` with `rule` `V` and `via` `ask` |
| VG-13 | deny, text approval, read log | `approval` with `via` `text` |
| VG-13 | go given, switch `visual`, read log | a `visual-close` line with `rule` `V` |
| VG-14 | first Edit of `app/button.tsx` in s1 | reason contains `compare-sheet` and no `~/` |

## Differences between the code and the intent

- A text approval also reaches the spec-approval prompt handler, so the printed message can carry "nothing to approve, no request is open" next to `visual gate open`. The spec asserts only that `visual gate open` is present or absent. That line comes from `approval.py`.
- `ui_paths` are matched against the path relative to the config `repo` folder, not the project root. The plan does not say which, and no rule covers a nested `repo`.
- A non-`✓` answer leaves the request open with the same marker (VG-8). The plan is silent on closing it.
- `visual.py` matches the intent on every rule. No code difference found in the file.

Details and the run status are in `.specguard/dogfood/visual.md`.

## Retired rules

None.
