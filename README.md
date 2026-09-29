# specguard

A Claude Code plugin that guards role-limited subagents (tester, devils-advocate), gates the executor's Stop with a test run, and adds per-session modes with a mechanical spec approval step, on top of `.claude/specguard.json`.

Not finished - this is the Phase 1a skeleton only (manifest, hooks, entrypoint, config, state, path classification, and stubs for every later module); the guard rules, modes, approval, Stop gate, context and the two branch gates are not implemented yet.
