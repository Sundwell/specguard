#!/usr/bin/env python3
# Role guard for sample-shops. The subagent "tester" (hook input agent_type) may not read or write code under
# src/ or scripts/, except the database schema. The subagent "devils-advocate" is read-only, writes only its report
# under .omc/research/spec-review/ and sees what the tester sees. Everyone else is the executor and may not write tests.
import json
import os
import re
import sys

data = json.load(sys.stdin)
tool = data.get('tool_name', '')
tool_input = data.get('tool_input') or {}
agent = data.get('agent_type')
role = agent if agent in ('tester', 'devils-advocate') else 'executor'
root = os.environ.get('CLAUDE_PROJECT_DIR') or data.get('cwd') or os.getcwd()
repo = os.path.join(root, 'sample-shops') + os.sep
reviews = os.path.join(root, '.omc', 'research', 'spec-review') + os.sep
answers = os.path.join(root, '.omc', 'research', 'test-review') + os.sep

TESTS = re.compile(r'(^|/)(test|e2e)/|\.(test|spec)\.ts$')
CODE = re.compile(r'(^|/)(src|scripts)(/|$)')
OPEN_TO_TESTER = 'apps/api/src/db/schema.ts'


def in_repo(path):
    if not path:
        return None
    full = os.path.normpath(os.path.join(data.get('cwd') or root, path))
    return full[len(repo):] if full.startswith(repo) else None


def deny(message):
    print(message, file=sys.stderr)
    sys.exit(2)


def full_path(path):
    return os.path.normpath(os.path.join(data.get('cwd') or root, path)) if path else ''


raw = tool_input.get('file_path') or tool_input.get('notebook_path') or tool_input.get('path')
path = in_repo(raw)

if role == 'executor':
    if tool in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit') and path is not None and TESTS.search(path):
        deny('The executor may not write tests in sample-shops. The tester subagent writes them from docs/specs. '
             'If a test looks wrong, say so in the report; the user decides.')
elif role == 'devils-advocate':
    if tool in ('Edit', 'MultiEdit', 'NotebookEdit', 'Bash'):
        deny('The devils-advocate is read-only. It writes only its report under .omc/research/spec-review/.')
    if tool == 'Write' and not full_path(raw).startswith(reviews):
        deny('The devils-advocate writes only its report under .omc/research/spec-review/.')
    if tool == 'Grep' and not raw:
        deny('The devils-advocate greps with an explicit path.')
    if tool in ('Read', 'Grep') and (full_path(raw) + os.sep).startswith(answers):
        deny('The devils-advocate works from the spec, not from earlier reviews of the tests.')
    if tool in ('Read', 'Grep') and path is not None and CODE.search(path) and path != OPEN_TO_TESTER:
        deny('The devils-advocate sees what the tester sees and does not read src/ or scripts/.')
    if tool == 'Grep' and (repo.startswith(full_path(raw) + os.sep) or path is not None and not (
            TESTS.search(path + '/') or path.startswith('docs/') or path == OPEN_TO_TESTER)):
        deny('The devils-advocate greps only inside test folders and docs/ of sample-shops, or outside sample-shops.')
else:
    if tool in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit') and path is not None \
            and CODE.search(path) and path != OPEN_TO_TESTER:
        deny('The tester may not touch src/ or scripts/ in sample-shops. Work from docs/specs; '
             'if something is missing there, report it as a spec gap.')
    if tool == 'Grep' and not (path is not None and (TESTS.search(path + '/') or path.startswith('docs/'))):
        deny('The tester greps only inside test folders and docs/ of sample-shops.')
    if tool == 'Bash':
        command = tool_input.get('command', '').replace(OPEN_TO_TESTER, '')
        if re.search(r'(^|[^A-Za-z0-9_.-])(src|scripts)([^A-Za-z0-9_.-]|$)', command):
            deny('The tester may not run commands that name src/ or scripts/.')

sys.exit(0)
