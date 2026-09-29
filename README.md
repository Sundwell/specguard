# specguard

A Claude Code plugin for role-separated TDD. A blind `specguard:tester` subagent writes tests from a spec without reading the implementation, the executor may not write tests itself while a gated mode is on, a spec needs the user's approval before a tester can run on it, a Stop hook gates the executor's exit on a green test run, and every session picks its own mode. specguard has no runtime dependency on any other plugin and reads its own config, `.claude/specguard.json`, per project.

## 1. What it is

specguard reproduces the role-guard-plus-Stop-gate setup that came out of hand-rolled per-project hooks, as a proper plugin with per-session modes and a mechanical approval step on top. The tester agent (`specguard:tester`, sonnet) only reads the spec, existing tests and, when configured, a generated API report, then writes tests against the named "Public API"; a hook denies it any Read, Edit, Write, Grep, Glob or Bash reach into the paths the project config marks hidden. The devils-advocate agent (`specguard:devils-advocate`, opus) reviews a draft spec before the user sees it and is read-only except for its own report file. The executor writes the implementation and, in feature and hard mode, may not touch files under the configured test paths, a spec must be approved (unchanged since HEAD, or explicitly approved) before the executor can launch a tester on it, and the Stop hook runs the project's test command and blocks a red exit once. Without a `.claude/specguard.json` in the project the plugin does nothing at all, every hook event exits with no output. Every session start also gets a one-line pointer to the model-invocable `specguard:guide` skill, which is where an agent that does not know the plugin should look, rather than searching the disk or the plugin cache for it; that same skill also covers which mode to recommend and how to draft the config for a project that has none yet.

## 2. Modes

A session mode lives outside any repo, in specguard's own state directory, and starts at `modes.default` from the config (`simple` unless the project sets otherwise). The accepted combinations are `simple`, `feature`, `hard`, `visual`, `visual+feature` and `visual+hard`; anything else is refused.

| Mode | What it enforces |
|---|---|
| simple | The role locks for tester and advocate, the config and settings protection, the no-child-claude rule, and the mode switch itself; nothing else. No executor test lock, no spec approval, no Stop gate unless the project's `stop.modes` names simple explicitly. |
| feature | Everything simple enforces, plus the executor test lock (no Edit, Write, MultiEdit or NotebookEdit under a test path) and the spec approval gate before a `specguard:tester` launch. Approval is on by default (`modes.approval_default`) and can be turned off for the session with a `no-approval` switch or an opt-out phrase. The Stop gate runs by default (`stop.modes` defaults to `["feature", "hard"]`). |
| hard | Everything feature enforces, with approval forced on (a `no-approval` request is refused) and the devils-advocate gate added, a spec needs an advocate report matching its stem before the tester can run on it unless the spec is already at HEAD or was already approved unchanged. |
| visual | Only the UI edit gate, the first executor edit under a path listed in `visual.ui_paths` each session is denied until the user gives his go; it carries no test lock or approval gate of its own. |
| visual+feature, visual+hard | The visual gate on top of everything feature or hard enforces. |

## 3. Switching modes

The primary way is the command `/specguard:mode <mode>[+<mode>] [no-approval]`, for example `/specguard:mode visual+feature` or `/specguard:mode feature no-approval`. The skill that backs it sets `disable-model-invocation: true`, and a PreToolUse rule separately denies any `Skill` call naming `specguard:mode` or `mode`, so the model cannot run the switch on its own in either path. The command is read from the `UserPromptExpansion` event, not from the raw slash text.

A voice phrase at the very start of a message is a convenience on top, recognised from English, Russian and Ukrainian word packs at once, in every project, regardless of the project's own language. A trailing question mark on the message never switches the mode, it only asks about it. Some real examples from the packs.

- English - `go feature spec`, `go hard mode`, `go visual`, `go simple`, `no approval`.
- Russian - `го фичспек`, `го хардмод`, `го визуал и фичспек`, `го фичспек без апрува`.
- Ukrainian - `го фічспек`, `го хардмод`, `го візуал`, `го простий`, `без апруву`.

Every switch, by command or by phrase, prints a line starting with `specguard:`, for example `specguard: mode feature, approval on`. Every line the hook prints itself is in English regardless of the project's language; a deny reason goes to the agent, which is expected to talk to the user in the user's own language.

## 4. Spec approval

In hard mode, or in feature mode with approval on, an executor's `Agent` launch of `specguard:tester` (or the bare `tester` name) must name spec files under `specs_dir` in its prompt. A spec passes when its current content matches the blob already at the repository's HEAD, or when its sha256 is already recorded in the state directory as approved. Otherwise the launch is denied, a request is opened, and the deny reason carries one marker per unapproved spec in the form `specguard-approve <spec>@<sha7>`.

The primary path is `AskUserQuestion`. A PreToolUse check requires the question text to carry every open marker, exactly one option label to carry a `✓`, at least one further option besides it, and no pre-filled `answers` field from the model. `PostToolUse` then records approval for a marked spec, still unchanged, when the user's chosen option is the one marked `✓`; any other choice closes that request instead.

The text fallback is a whole message of one or two words drawn from the approval words of any of the three packs, and it only counts for the requests whose marker sat in the assistant's last message before that prompt, read from the tail of the transcript file. A lone word from the trigger words that doubles as an approval word (`го`, `да`, `так`) never counts by itself, this is `approval_single_exclude` in the packs. A revoke phrase at the start of a message (`revoke approval`, `отзываю апрув`, `відкликаю апрув`) removes the specs from the last recorded approval round. Opting out is the `no-approval` switch or an opt-out phrase (`no approval`, `без апрува`, `без апруву`); it is refused in hard mode. Every recorded outcome is echoed back, for example `specguard: approved domain-phone.md (3f2a91c)` or `specguard: request closed for domain-phone.md`.

## 5. Config

`.claude/specguard.json` turns the plugin on for a project. With no such file, every hook event exits silently and nothing is enforced. `version` is the only required key and must be `1`.

| Key | Default |
|---|---|
| `repo` | `.` |
| `roles.tester` | `["specguard:tester", "tester"]` |
| `roles.advocate` | `["specguard:devils-advocate", "devils-advocate"]` |
| `hidden.segments`, `hidden.names`, `hidden.paths` | all empty |
| `tester_readable` | empty |
| `tests.segments`, `tests.paths`, `tests.file_regex` | all empty |
| `docs_dirs` | `["docs"]` |
| `specs_dir` | `docs/specs` |
| `notes.tester` | `.claude/specguard/tester-notes.md` |
| `notes.advocate` | `.claude/specguard/advocate-notes.md` |
| `reports.tester` | `.specguard/reports/tester` |
| `reports.advocate` | `.specguard/reports/spec-review` |
| `reports.advocate_hidden` | empty |
| `plan_file` | not set |
| `money_topics` | empty |
| `hands_on_tag` | `[hands-on]` |
| `modes.default` | `simple` |
| `modes.approval_default` | `true` |
| `stop` | not set (no Stop gate runs at all) |
| `stop.modes` | `["feature", "hard"]` |
| `stop.skip_preset` | `none` |
| `stop.summary_regex` | `FAIL\|Error\|failed` |
| `stop.timeout` | `300`, refused above `1800` |
| `api_report` | not set |
| `visual` | not set (no visual gate) |

A minimal config that turns the plugin on in feature mode, with the Stop gate wired to a real test command.

```json
{
  "version": 1,
  "modes": { "default": "feature" },
  "specs_dir": "docs/specs",
  "stop": {
    "dirty_pathspec": ["."],
    "fresh_paths": ["src"],
    "fresh_globs": ["*.ts"],
    "exclude_dirs": ["node_modules", ".git"],
    "run": "npm test"
  }
}
```

## 6. What is mechanical and what is instruction-only

| Behaviour | Enforcement | Residual |
|---|---|---|
| Tester does not read or edit implementation | Mechanical, file tools, Grep, Glob, a token-aware Bash scan | Bash reaching code without naming it; the executor pasting code into the task |
| Roles keep their role | Mechanical, named, team or isolated launches of tester or advocate are denied | A tester session started without `--agent` is an executor |
| Advocate read-only, writes only its report | Mechanical | Prompt contamination |
| Executor writes no tests (feature, hard) | Mechanical for file tools | Bash writes, `sed -i`, redirection |
| Green before stop (feature, hard) | Mechanical, blocks once on red | May stop red after one block, must say so |
| Spec approved before tests | Mechanical | A `✓` on the wrong option, since the agent writes the option labels; a commit makes a spec equal to HEAD; a settings hook approved unread |
| Advocate on every spec (hard) | Mechanical | Report quality |
| His go before the first UI edit (visual) | Mechanical | Checklist quality |
| Modes set only by him | Mechanical | A script or wrapper that starts `claude`; moving `.claude` aside |
| Config and settings protected from the executor (rule 2) | Mechanical, `ask`; a Bash command that is read-only start to finish (`cat`, `grep`, `git status/log/diff/...`, no redirection, no `tee`) is let through silently instead | A read-only program used for a side effect it was not meant for |
| Advocate choice in feature mode, parallel order, escalation, the mutation check, no code pasted into the tester task, the visual sheet and deviations log | Instruction-only | - |
| Every gate | As reliable as the hook process itself | A hook timeout fails open silently; verified on CLI 2.1.284 only |

## 7. Known limitations and interplay

specguard has no runtime dependency on oh-my-claudecode, but the two can meet in the same session. In feature and hard mode, OMC's own test-writing agents such as `test-engineer` and `qa-tester` are still executor-role to specguard and cannot write tests, only `specguard:tester` can. OMC's persistent-mode Stop hook and specguard's Stop hook can both block on the same Stop event, and whichever one blocks wins. `omc ask claude` is not refused by the no-child-claude rule, since the command word is `omc`, not `claude`. OMC setup steps that edit `.claude/settings.json` trigger specguard's own ask on that file. specguard is Claude Code only for now, a port to another harness is a separate later task.

`omitClaudeMd: true` on the devils-advocate agent holds for it as a launched subagent, but the same setting does not hold for a top-level `claude --agent` session. Running `claude --version` or any `claude plugin ...` subcommand from an agent's Bash is refused inside a specguard project, since the no-child-claude rule matches on the command word `claude` itself, not on which subcommand follows it; use `python3 <plugin>/scripts/specguard_hook.py --status` from outside the project instead. On CLI 2.1.284 the Grep and Glob tools can be absent from a subagent's tool list entirely, in which case its searches go through Bash instead, where the same token-aware hidden-path scan applies to Bash commands. The devils-advocate has no Bash tool at all, so when Grep is also absent from its tool list it can only Read the paths it is explicitly given.

## 8. Install, update, rollback

Install once for your user account. A project without `.claude/specguard.json` gets nothing from the plugin, every hook exits in about 20 ms with no output, so a project is switched on by its config alone.

```bash
claude plugin marketplace add /path/to/specguard
claude plugin install specguard@specguard --scope user
```

Then open Claude Code in a project and ask it to set specguard up; the `specguard:guide` skill tells it to inspect the repo, draft `.claude/specguard.json` and write it only after your go. Restart the session with `claude --continue` so the rules load. A project that already has its own guard hooks needs them removed first, or both will gate the same calls.

Update from a terminal outside a specguard project (rule 3 refuses a child `claude` process inside one), then restart open sessions with `claude --continue`.

```bash
claude plugin marketplace update specguard
claude plugin update specguard@specguard --scope user
```

Rollback of one project - delete `.claude/specguard.json` and `.claude/specguard/`, restore anything the switch replaced from its backup, restart open sessions. Rollback everywhere - `claude plugin uninstall specguard@specguard --scope user`. `--scope local` installs per project still work if you prefer them; do not combine them with the user install in the same project, or the hooks run twice.

`python3 <plugin>/scripts/specguard_hook.py --status` prints, for the project in `CLAUDE_PROJECT_DIR` or the current directory, every recorded session with its modes, approval flag and open requests, the approved specs with their short hashes, the last green timestamp, the tail of the event log, whether the config validates, and the plugin's own tree hash. `--check-config` only validates `.claude/specguard.json` against the schema and prints `OK` or the list of problems.

## 9. Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
