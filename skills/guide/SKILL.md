---
name: guide
description: "What specguard is and how it works in this project - the tester and devils-advocate agents, modes and how the user switches them, the spec approval flow, the Stop test gate, what to do when a specguard hook refuses a call, and how to set specguard up in a new project. Use when the user asks about specguard, its modes, approval or the tester, or when a tool call is refused with a specguard reason."
---

# specguard: role-separated TDD for this project

specguard is a Claude Code plugin, not a search target. Its only configuration is `.claude/specguard.json` in the project root; the plugin needs nothing else. Never search the disk, the plugin cache or `~/.claude` for it, read this skill instead.

## Roles

You are the executor. You write the implementation and the spec, and you launch the other two roles as plain subagents - never with `name`, `team_name` or `isolation`, or the launch loses its role and is denied.

- `specguard:tester` writes tests from a spec without reading the implementation; a hook blocks its reach into the paths this project marks hidden.
- `specguard:devils-advocate` reviews a draft spec before the user sees it, read-only except for its own report file.

## Modes

A session starts in `simple` mode unless the project sets another default. The accepted combinations are `simple`, `feature`, `hard`, `visual`, `visual+feature` and `visual+hard`.

- `simple` - only the role locks and the mode switch itself; no test lock, no approval, no Stop gate unless the project wires simple into it explicitly.
- `feature` - you may not write tests yourself (only the tester does), and a spec needs approval before you can launch a tester on it; the Stop gate runs by default.
- `hard` - everything feature enforces, approval forced on, and the devils-advocate must review a spec before the tester runs on it.
- `visual` - the first UI edit of the session under a configured UI path is denied until the user gives his go.
- `visual+feature`, `visual+hard` - the visual gate on top of feature or hard.

The user switches modes with `/specguard:mode <mode>[+<mode>] [no-approval]` or a phrase at the start of his message (English, Russian or Ukrainian, for example "go feature spec" or "го хардмод"). You cannot switch modes yourself - the mode-switch skill refuses a model-invoked call, and a PreToolUse rule denies it too. Do not try.

When you tell the user how to switch, quote only a phrase from this table or the command; any other wording, such as "го симпл", switches nothing.

| Mode | Command | Russian | Ukrainian | English |
|---|---|---|---|---|
| simple | `/specguard:mode simple` | го простой | го простий | go simple |
| feature | `/specguard:mode feature` | го фичспек | го фічспек | go feature spec |
| feature, approval off | `/specguard:mode feature no-approval` | го фичспек без апрува | го фічспек без апруву | go feature spec no approval |
| hard | `/specguard:mode hard` | го хардмод | го хардмод | go hard mode |
| visual | `/specguard:mode visual` | го визуал | го візуал | go visual |
| visual and feature | `/specguard:mode visual+feature` | го визуал и фичспек | го візуал і фічспек | go visual and feature spec |

## Choosing a mode

When the user asks what to work in, or hands you a task without naming a mode, recommend one, name the exact command or phrase, and never switch it yourself.

- `simple` for a small change with no behaviour change, or code you are not writing tests for. Example, fixing a typo in a button label or a docs edit.
- `feature` for new or changed behaviour worth a test; this is the ordinary default for application logic. The devils-advocate joins only when the spec touches a money topic named in the config's `money_topics`. Example, adding a filter to a list endpoint.
- `hard` when a silent error would cost money or data and the change is risky, such as payments, orders, stock, prices, migrations or auth. The devils-advocate reviews every spec, approval cannot be turned off, and a manual mutation check closes it out. Example, changing how a discount is applied at checkout.
- `visual` for UI work driven from a design, and `visual+feature` when that UI also carries logic worth a test. Example, `visual` for a restyled card, `visual+feature` for a new filter panel with its own state.

When you are unsure between two modes, say which two and why in one line, then let the user pick, for example "`/specguard:mode feature` or `go hard mode` - I would lean feature unless this touches pricing."

## The approval flow, step by step

In feature mode with approval on, or in hard mode, a tester launch that names a spec is gated.

1. Write the spec, then launch `specguard:tester` naming the spec path in the prompt.
2. The gate refuses the launch and gives you back markers, one per unapproved spec, in the form `specguard-approve <spec>@<sha7>`.
3. Show the user the spec's rules (and the advocate table, in hard mode) and ask with AskUserQuestion. The question text must carry every marker, exactly one option label carries a `✓`, and there is at least one other option besides it.
4. After his `✓`, relaunch the tester on the same spec.

A plain-text fallback exists for when AskUserQuestion is not available - end your message with the markers instead, and a short reply from the user in his own approval words counts. If you are a subagent and hit this refusal, you cannot ask the user yourself; pass the markers verbatim back to the main session and stop.

## Hard mode and visual mode specifics

In hard mode, the devils-advocate report comes first, named `<stem>-<YYYY-MM-DD>.md` in the advocate report folder the config names. The hard gate passes without a fresh report only when the spec's content already sits at HEAD, or its sha256 matches the one recorded at the last tester launch that was itself allowed on it; otherwise it stays denied until an advocate report matching the spec's stem was written after that launch.

In visual mode, before the first UI edit build a checklist and a DESIGN | NOW sheet and wait for the user's go, the same AskUserQuestion shape as above but with a single `specguard-approve visual@<n>` marker.

## The Stop gate

When your turn ends, in feature and hard mode (by default) the Stop hook runs the project's test command. A red run blocks the stop once with the failing summary; fix the code, never bend the test, or say in your report which spec rules are red and why. While `specguard:tester` is still running in the background, Stop only tells you so and lets your turn end - it is not another block.

## When a call comes back refused

| Reason you see | What it means | What to do |
|---|---|---|
| Names specguard's state dir or the plugin root | Rule 1, protected for every role | Do not touch either path from any tool |
| Names `.claude/specguard.json` or a `settings*.json` | Rule 2, config and settings are gated | Confirm with the user before this edit; a read-only Bash command may go through silently |
| Starts a child `claude` process | Rule 3, no nested Claude Code | Ask the user to run it in his own terminal |
| A named, team or isolated tester/advocate launch | Rule 4, launch shape | Launch it as a plain subagent instead |
| A tester or advocate call reaches hidden code | Rules 5-6, role reach | Work only from the spec and the paths open to that role |
| `In <mode> mode the executor does not write tests` | Rule 7, executor test lock | Let the tester write it; escalate if a test looks wrong |
| "spec not approved in its current form" | Rule 9, spec approval gate | Run the approval flow above |
| "hard mode requires the devils-advocate" | Hard gate, rule 8 | Launch `specguard:devils-advocate` on the spec first |
| "his go is needed before the first UI edit" | Visual gate | Build the DESIGN \| NOW checklist and ask |
| "tests failed" / "stop.run timed out" | Stop gate | Fix the code, or report which rules are red |

## Where session state already is

Every one of your prompts already carries a line from specguard naming the session state - "Active specguard mode is `<modes>`, approval on/off.", with "Open approval requests - `<specs>`." appended when any are pending. Read that line before looking anywhere else; it is already in front of you on every turn.

`python3 <plugin root>/scripts/specguard_hook.py --status` is for the user, run from his own terminal outside a specguard project, not for you to run from Bash; it prints the same session state plus approved specs and the log tail. Running `claude` itself from Bash inside this project is refused regardless (rule 3).

## Setting up a project

If there is no `.claude/specguard.json` yet, every specguard hook is silent and there is no session intro line, so this skill is the only place you see any of this. Installing the plugin itself is the user's own step, run from his terminal, not something you do - `claude plugin marketplace add <path>` then `claude plugin install specguard@specguard --scope local`. Your part starts after that.

1. Inspect the repo. Find where the implementation code lives, the test folders and the test file naming pattern, the command that runs the tests, whether a specs folder already exists, and whether the code sits under a nested repo folder rather than the project root.
2. Draft `.claude/specguard.json` from the real keys in `config.py`, keeping it to what this project actually needs. A typical minimal draft looks like this.

```json
{
  "version": 1,
  "repo": ".",
  "hidden": { "segments": ["src"] },
  "tests": { "segments": ["test"], "file_regex": "\\.(test|spec)\\.ts$" },
  "docs_dirs": ["docs"],
  "specs_dir": "docs/specs",
  "modes": { "default": "feature" },
  "stop": {
    "dirty_pathspec": ["."],
    "fresh_paths": ["src"],
    "fresh_globs": ["*.ts"],
    "exclude_dirs": ["node_modules", ".git"],
    "run": "npm test"
  }
}
```

3. Show the draft to the user with one line of reasoning per key, for example why `repo` points at a nested folder, why `hidden.segments` names the implementation folder the tester should not read, why `stop.run` is the project's real test command. Write the file only after his go, never before it.
4. Offer, as optional extras, a tester notes file and an advocate notes file under `.claude/specguard/` for project facts a generic role cannot know, and a specs folder with a short README if the project has none yet.
5. After writing the config, tell the user to restart the session with `claude --continue` so the SessionStart rules load fresh.
