# guard-shared

The PreToolUse rules that hold for every role - protected state and plugin paths (rule 1), config and settings files (rule 2), no child Claude Code (rule 3) and the launch shape of the tester and the devils-advocate plus the mode-skill deny (rule 4). The role-specific rules 5 to 7 are in `guard-roles.md`.

## Tests

`tests/test_spec_guard_shared.py`

## Public API

Everything is driven through `tests/helpers.py` and the hook entrypoint. Nothing else is visible.

- `helpers.make_project(tmp, config=<dict>)` writes `.claude/specguard.json` into `tmp` and returns it.
- `helpers.pre_tool_use(session_id, tool_name, tool_input, agent_type=<str or omitted>)` builds the event payload. An omitted `agent_type` is the executor. `specguard:tester` and the bare `tester` are the tester, `specguard:devils-advocate` and the bare `devils-advocate` are the advocate.
- `helpers.run_hook("PreToolUse", payload, project_dir, state_home=<dir>)` returns `(exit_code, stdout_json, stderr)`. The exit code is always 0.
  - Allow prints nothing, `stdout_json` is `None`.
  - Deny gives `stdout_json["hookSpecificOutput"]["permissionDecision"] == "deny"` and a non-empty `permissionDecisionReason`.
  - Ask gives the same shape with `"ask"`.
- Which rule fired is not visible in the output. Where a row names a rule, read the last line of `<state_home>/specguard/<key>/log.jsonl`, where `<key>` is the absolute project path with every `/` replaced by `-`. The line is JSON with `"ev"` set to `"deny"` or `"ask"`, `"rule"` set to the rule number as a string (`"1"`), `"role"` (`executor`, `tester`, `advocate`) and `"tool"`.
- The plugin root the guard protects is `helpers.REPO_ROOT` (the default of `run_hook`) or the directory passed as `plugin_root=`. The state directory is `<state_home>/specguard/<key>`.
- Fixture config `BASE` (the modes default is `simple`, so no test lock or approval gate interferes)

```json
{"version": 1, "repo": ".", "modes": {"default": "simple"},
 "hidden": {"segments": ["src"]}, "tests": {"segments": ["test"]}}
```

Rows use the executor unless a role is named. "allow" means empty output. "deny 1" means a deny whose log line has rule "1".

## Rules

### Rule 1, protected paths

- **GS-1** An Edit, Write, MultiEdit or NotebookEdit whose target lies in the state directory or under the plugin root is denied (rule 1) for every role, by absolute or relative path, including `..` spellings that resolve there. Read, Grep and Glob on the same paths are not touched by rule 1. Why: an agent that can write its own state or the hook code can switch every gate off.
- **GS-2** A Bash command that contains the text of the state directory, the plugin root, `~/.local/state/specguard`, `$HOME/.local/state/specguard`, `${HOME}/.local/state/specguard`, `$XDG_STATE_HOME/specguard`, `${XDG_STATE_HOME}/specguard`, `$CLAUDE_PLUGIN_ROOT` or `${CLAUDE_PLUGIN_ROOT}` is denied (rule 1) for every role, read-only commands included. Why: the shell can spell one path many ways, so the guard looks for every spelling instead of resolving.
- **GS-3** One command shape is exempt from GS-2 - a single simple command, without any shell operator, of the form `<words> <path ending in specguard_hook.py> --status` or `... --check-config`, where the flag is the last word. It is allowed. Appending any other word after the flag, or joining a second command with `;`, `&&`, `||`, `|` or a newline, brings the deny back. Why: the README tells an agent to inspect the plugin this way when `claude` itself is refused.

### Rule 2, config and settings

- **GS-4** An Edit, Write, MultiEdit or NotebookEdit whose target is a file named `specguard.json`, `settings.json` or `settings.local.json` directly inside a directory named `.claude` (in the project or anywhere else, for example the home directory) is an ask (rule 2) for the executor and a deny (rule 2) for the tester and the advocate. A file of the same name outside a `.claude` directory, or another file in `.claude`, is not touched by rule 2. Read, Grep and Glob are not touched by rule 2. Why: config and settings decide which gates exist, so a change needs the user.
- **GS-5** A Bash command that names `specguard.json`, `settings.json` or `settings.local.json` as a whole word is checked. A word is whole when the characters before and after it are not letters, digits, `_`, `.` or `-` (so `my-settings.json`, `settings.json.bak` and `settings.jsonl` are not the name). For the tester and the advocate the result is always deny (rule 2), even for a read-only command. For the executor the command is split into simple commands on `;`, `&&`, `||`, `|`, a newline and `$(`, and `bash -c '...'` or `sh -c '...'` strings are opened and split the same way. If every simple command that names one of the files is read-only the call is allowed, otherwise it is an ask (rule 2). Simple commands that do not name a file are not judged by rule 2.
- **GS-6** Read-only means the command starts with one of `cat less head tail grep rg ls stat wc diff file echo printf sort uniq cut tr pwd cd basename dirname realpath readlink tree du true nl column comm md5sum sha256sum`, or `jq` without `-i`, or `find` without `-exec`, `-execdir`, `-delete`, `-ok` or any `-fprint*` option, or `sed` with `-n` (alone or inside a flag group such as `-ne`), without `-i` or `--in-place` and without a `w` or `W` write command, or `git` with the subcommand `status`, `log`, `diff`, `show`, `blame`, `grep` or `ls-files`. The program may be given by path (`/bin/cat`). For rule 2 a `&` that stands right before a redirection (`&>`) is part of the redirection, not a command separator, so `cat .claude/settings.json &> all.txt` is one simple command with a write and is an ask (rule 2). Any output redirection that is not `2>&1`, `>&2`, `1>&2` or a redirection to `/dev/null` (`>`, `>>`, `2>`, `&>` with another target), and any `tee`, make it not read-only. Why: a whitelist of reading programs keeps a routine `cat .claude/specguard.json` silent without letting a write through.

### Rule 3, no child Claude

- **GS-7** A Bash command whose command word is `claude` or ends in `/claude` is denied (rule 3) for every role, including the executor. The command word is the first word of a simple command after `;`, `&&`, `||`, `|`, `&`, a newline or `$(`, skipping leading `NAME=value` assignments and the prefix words `env`, `exec`, `nohup`, `nice`, `setsid`, `sudo`, `command`, `time`, `xargs` and `timeout <seconds>`. The same check runs inside `bash -c '...'`, `sh -c '...'` and `zsh -c '...'` strings. Any command containing `@anthropic-ai/claude-code` is denied (rule 3). Why: a child Claude Code would run outside the mode and approval rules.
- **GS-8** `claude` as an argument, part of a longer word or a different command word is not a child Claude and is not denied by rule 3 - `echo claude`, `grep claude README.md`, `ls claude-notes`, `cat claude.md`, `omc ask claude`, `git log --grep claude`, `python3 run.py claude`. Why: only starting the program is dangerous, and `omc ask claude` is a documented allowed path.

### Rule 4, launch shape and the mode skill

- **GS-9** An `Agent` call whose `subagent_type` is one of `specguard:tester`, `tester`, `specguard:devils-advocate`, `devils-advocate` and that carries a non-empty `name`, `team_name` or `isolation` is denied (rule 4). The same call without those fields, or with them empty, is allowed. An `Agent` call with any other `subagent_type` is allowed whatever fields it carries. Why: a named, team or isolated launch loses its role, and every call inside it would run as an unguarded executor.
- **GS-10** A `Skill` call whose `skill` is `specguard:mode` or `mode` is denied (rule 4) in every mode. Any other skill name, for example `specguard:guide`, is allowed. Why: the mode switch belongs to the user and the model must not run it.

## Examples

| Rule | Role | Tool | Input | Result |
|---|---|---|---|---|
| GS-1 | executor | Write | `<state_dir>/sessions/s1.json` | deny 1 |
| GS-1 | tester | Edit | `<plugin_root>/scripts/specguard/guard.py` | deny 1 |
| GS-1 | executor | Write | `<state_dir>/x/../sessions/s1.json` (a `..` spelling that resolves into the state dir) | deny 1 |
| GS-1 | executor | Read | `<plugin_root>/README.md` | allow |
| GS-1 | executor | Write | `notes/plan.md` (ordinary file) | allow |
| GS-2 | executor | Bash | `cat <state_dir>/log.jsonl` | deny 1 |
| GS-2 | executor | Bash | `ls ~/.local/state/specguard` | deny 1 |
| GS-2 | executor | Bash | `rm -rf "$XDG_STATE_HOME/specguard"` | deny 1 |
| GS-2 | executor | Bash | `cat ${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json` | deny 1 |
| GS-2 | tester | Bash | `cat <plugin_root>/README.md` | deny 1 |
| GS-2 | executor | Bash | `ls notes` | allow |
| GS-3 | executor | Bash | `python3 <plugin_root>/scripts/specguard_hook.py --status` | allow |
| GS-3 | executor | Bash | `python3 <plugin_root>/scripts/specguard_hook.py --check-config` | allow |
| GS-3 | executor | Bash | `python3 <plugin_root>/scripts/specguard_hook.py --status && cat <state_dir>/log.jsonl` | deny 1 |
| GS-3 | executor | Bash | `python3 <plugin_root>/scripts/specguard_hook.py --status extra` | deny 1 |
| GS-3 | executor | Bash | `python3 <plugin_root>/scripts/specguard_hook.py --status; ls` | deny 1 |
| GS-3 | executor | Bash | `python3 <plugin_root>/scripts/specguard_hook.py` | deny 1 |
| GS-4 | executor | Write | `.claude/specguard.json` | ask 2 |
| GS-4 | executor | Edit | `.claude/settings.json` | ask 2 |
| GS-4 | executor | MultiEdit | `.claude/settings.local.json` | ask 2 |
| GS-4 | executor | Write | `<home>/.claude/settings.json` (absolute) | ask 2 |
| GS-4 | tester | Write | `.claude/specguard.json` | deny 2 |
| GS-4 | advocate | Edit | `.claude/settings.json` | deny 2 |
| GS-4 | executor | Write | `config/specguard.json` (not in `.claude`) | allow |
| GS-4 | executor | Write | `.claude/notes.md` | allow |
| GS-4 | executor | Write | `settings.json` in the project root | allow |
| GS-4 | executor | Read | `.claude/specguard.json` | allow |
| GS-5 | executor | Bash | `cat .claude/specguard.json` | allow |
| GS-5 | executor | Bash | `grep -n hidden .claude/settings.json` | allow |
| GS-5 | executor | Bash | `git diff .claude/settings.json` | allow |
| GS-5 | executor | Bash | `cat .claude/specguard.json 2>&1` | allow |
| GS-5 | executor | Bash | `cat .claude/settings.json 2>/dev/null` | allow |
| GS-5 | executor | Bash | `bash -c "cat .claude/specguard.json"` | allow |
| GS-5 | executor | Bash | `cat .claude/specguard.json; rm -rf build` | allow |
| GS-5 | executor | Bash | `echo x > .claude/specguard.json` | ask 2 |
| GS-5 | executor | Bash | `echo x >> .claude/settings.local.json` | ask 2 |
| GS-5 | executor | Bash | `sed -i s/a/b/ .claude/settings.json` | ask 2 |
| GS-5 | executor | Bash | `cp backup.json .claude/specguard.json` | ask 2 |
| GS-5 | executor | Bash | `echo x \| tee .claude/specguard.json` | ask 2 |
| GS-5 | executor | Bash | `cat .claude/specguard.json && rm .claude/settings.json` | ask 2 |
| GS-5 | executor | Bash | `bash -c "echo x > .claude/specguard.json"` | ask 2 |
| GS-5 | executor | Bash | `git checkout .claude/settings.json` | ask 2 |
| GS-5 | executor | Bash | `cat .claude/specguard.json > /tmp/copy.json` | ask 2 |
| GS-5 | tester | Bash | `cat .claude/specguard.json` | deny 2 |
| GS-5 | advocate | Bash | `grep hidden .claude/settings.json` | deny 2 |
| GS-5 | executor | Bash | `cat .claude/my-settings.json` | allow |
| GS-5 | executor | Bash | `cat .claude/settings.json.bak` | allow |
| GS-5 | executor | Bash | `cat .claude/settings.jsonl` | allow |
| GS-6 | executor | Bash | `sed -n 1,5p .claude/settings.json` | allow |
| GS-6 | executor | Bash | `sed -ne 1,5p .claude/settings.json` | allow |
| GS-6 | executor | Bash | `sed s/a/b/ .claude/settings.json` (no `-n`) | ask 2 |
| GS-6 | executor | Bash | `sed -n 'w out.txt' .claude/settings.json` (write command) | ask 2 |
| GS-6 | executor | Bash | `jq . .claude/settings.json` | allow |
| GS-6 | executor | Bash | `jq -i . .claude/settings.json` | ask 2 |
| GS-6 | executor | Bash | `find .claude -name settings.json` | allow |
| GS-6 | executor | Bash | `find .claude -name settings.json -delete` | ask 2 |
| GS-6 | executor | Bash | `find .claude -name settings.json -exec rm {} ;` | ask 2 |
| GS-6 | executor | Bash | `/bin/cat .claude/specguard.json` | allow |
| GS-6 | executor | Bash | `git log -p .claude/settings.json` | allow |
| GS-6 | executor | Bash | `git restore .claude/settings.json` | ask 2 |
| GS-6 | executor | Bash | `awk 1 .claude/settings.json` (program not in the list) | ask 2 |
| GS-6 | executor | Bash | `grep ">" .claude/specguard.json` (quoted `>` is not a redirection) | allow |
| GS-7 | executor | Bash | `claude` | deny 3 |
| GS-7 | executor | Bash | `claude --version` | deny 3 |
| GS-7 | executor | Bash | `/usr/local/bin/claude -p hi` | deny 3 |
| GS-7 | executor | Bash | `FOO=1 claude -p hi` | deny 3 |
| GS-7 | executor | Bash | `env FOO=1 claude` | deny 3 |
| GS-7 | executor | Bash | `timeout 5 claude -p hi` | deny 3 |
| GS-7 | executor | Bash | `nohup claude &` | deny 3 |
| GS-7 | executor | Bash | `sudo claude` | deny 3 |
| GS-7 | executor | Bash | `echo hi && claude` | deny 3 |
| GS-7 | executor | Bash | `echo hi \| claude -p x` | deny 3 |
| GS-7 | executor | Bash | `bash -c "claude -p x"` | deny 3 |
| GS-7 | executor | Bash | `sh -c 'cd /tmp && claude'` | deny 3 |
| GS-7 | executor | Bash | `npx @anthropic-ai/claude-code` | deny 3 |
| GS-7 | tester | Bash | `claude plugin list` | deny 3 |
| GS-8 | executor | Bash | `echo claude` | allow |
| GS-8 | executor | Bash | `grep claude README.md` | allow |
| GS-8 | executor | Bash | `ls claude-notes` | allow |
| GS-8 | executor | Bash | `cat claude.md` | allow |
| GS-8 | executor | Bash | `omc ask claude` | allow |
| GS-8 | executor | Bash | `git log --grep claude` | allow |
| GS-8 | executor | Bash | `python3 run.py claude` | allow |
| GS-9 | executor | Agent | `subagent_type` `specguard:tester`, `name` "t1" | deny 4 |
| GS-9 | executor | Agent | `tester`, `team_name` "core" | deny 4 |
| GS-9 | executor | Agent | `specguard:devils-advocate`, `isolation` "worktree" | deny 4 |
| GS-9 | executor | Agent | `devils-advocate`, `name` "adv" | deny 4 |
| GS-9 | executor | Agent | `specguard:tester`, no extra fields | allow |
| GS-9 | executor | Agent | `specguard:tester`, `name` "" | allow |
| GS-9 | executor | Agent | `general-purpose`, `name` "helper" | allow |
| GS-10 | executor | Skill | `skill` `specguard:mode` | deny 4 |
| GS-10 | executor | Skill | `skill` `mode` | deny 4 |
| GS-10 | executor | Skill | `skill` `specguard:guide` | allow |

## Differences from the intent

- The plan lists more in rule 4 (SendMessage to a tester or advocate in gated modes, CronCreate, ScheduleWakeup and SendMessage carrying a mode phrase or an approval, AskUserQuestion with a broken approval structure). That part belongs to the approval module and is outside this spec.
- The plan's rule 1 says only "Bash naming either"; the `--status` and `--check-config` exemption (GS-3) comes from the README and is kept.

## Retired rules

None.
