# Specs

One spec per piece of behaviour, named `<stem>.md`. The tester writes tests from the spec alone, so everything it needs must be in the file.

## Format

- **Tests** - the test file the tester writes, for example `tests/test_stop.py`.
- **Public API** - the names the tests may import and the import path. Nothing outside this section is visible to the tester.
- **Rules** - one per line with a stable ID (`ST-1`, `ST-2`), stating the observable behaviour and, after it, a short why. Every rule must be breakable through the Public API.
- **Examples** - a table of concrete input and expected output per rule ID. Each row becomes a parameterised test row, so a row that no wrong implementation would fail does not belong.
- **Retired rules** - a rule ID that already has tests is never renumbered or deleted. Move it here with the reason it was retired.

## Conventions

- Rules and example values are concrete, not "should handle errors".
- Rule IDs are permanent once a test exists.
- Prose is one paragraph per line, no hard wraps.
