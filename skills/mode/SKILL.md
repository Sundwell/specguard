---
name: mode
description: Switch the specguard session mode and approval flag
disable-model-invocation: true
---

This skill only runs when the user types `/specguard:mode <mode>[+<mode>] [no-approval]` himself; the model must never invoke it. The hook reads the switch from the command event and replies with a `specguard:` line naming the mode and the approval flag. If you see this body, just confirm the requested mode and approval back to the user in one line; the hook has already recorded the switch or refused it.
