#!/usr/bin/env python3
"""PostToolUse hook: auto-fix style and type-check any .py file Claude edits.

Exit 2 feeds remaining (unfixable) errors back to Claude in the same turn.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = ('venv/', 'kt/', 'qa_output/', 'qa_test.py')


def main() -> int:
	payload = json.load(sys.stdin)
	file_path = payload.get('tool_input', {}).get('file_path', '')
	if not file_path.endswith('.py'):
		return 0
	try:
		rel = str(Path(file_path).resolve().relative_to(ROOT))
	except ValueError:
		return 0  # outside the repo
	if any(rel.startswith(s) for s in SKIP):
		return 0

	py = ROOT / 'venv' / 'bin'
	errors: list[str] = []
	subprocess.run([py / 'ruff', 'format', '-q', file_path], cwd=ROOT, capture_output=True)
	fix = subprocess.run([py / 'ruff', 'check', '--fix', file_path], cwd=ROOT, capture_output=True, text=True)
	if fix.returncode != 0:
		errors.append(fix.stdout + fix.stderr)
	types = subprocess.run([py / 'pyright', file_path], cwd=ROOT, capture_output=True, text=True)
	if types.returncode != 0:
		errors.append(types.stdout + types.stderr)

	if errors:
		print(f'check_edit: issues remain in {rel} after auto-fix:\n' + '\n'.join(errors), file=sys.stderr)
		return 2
	return 0


if __name__ == '__main__':
	sys.exit(main())
