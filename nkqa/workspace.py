"""Workspace discovery and layout. All filesystem paths come from here."""

import re
import shutil
from pathlib import Path

from nkqa.config import CONFIG_TEMPLATE

CONFIG_FILE = 'config.yaml'

OVERVIEW_STUB = """\
# App overview

<!-- The agent's index into everything it knows. Filled by hand, by runs, or by `qa init --crawl` (Phase 4). -->

## What this app does

## Roles & test users

## Environments

## Areas / main flows
"""

GITIGNORE = """\
.env
runs/*/videos/
"""


def slugify(text: str) -> str:
	"""Turn free text into a meaningful folder name: 'Login flow!' -> 'login-flow'."""
	slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
	return slug[:40].rstrip('-') or 'unnamed-test'


class Workspace:
	def __init__(self, root: Path):
		self.root = root.resolve()
		self.config_file = self.root / CONFIG_FILE
		self.appmap_dir = self.root / 'appmap'
		self.scenarios_dir = self.root / 'scenarios'
		self.runs_dir = self.root / 'runs'
		self.permissions_file = self.root / 'qa_permissions.json'

	def run_dir(self, name: str) -> Path:
		return self.runs_dir / slugify(name)


def find(start: Path | None = None) -> Workspace | None:
	"""Walk up from start (default cwd) looking for a workspace, like git finds .git."""
	current = (start or Path.cwd()).resolve()
	for candidate in (current, *current.parents):
		if (candidate / CONFIG_FILE).is_file() and (candidate / 'appmap').is_dir():
			return Workspace(candidate)
	return None


def create(root: Path) -> Workspace:
	"""Create the workspace layout in root. Idempotent; never overwrites existing files."""
	ws = Workspace(root)
	ws.appmap_dir.mkdir(parents=True, exist_ok=True)
	ws.scenarios_dir.mkdir(exist_ok=True)
	ws.runs_dir.mkdir(exist_ok=True)
	if not ws.config_file.exists():
		ws.config_file.write_text(CONFIG_TEMPLATE)
	overview = ws.appmap_dir / 'overview.md'
	if not overview.exists():
		overview.write_text(OVERVIEW_STUB)
	(ws.scenarios_dir / '.gitkeep').touch()
	(ws.runs_dir / '.gitkeep').touch()
	gitignore = ws.root / '.gitignore'
	if not gitignore.exists():
		gitignore.write_text(GITIGNORE)
	return ws


def prototype_recordings(source: Path) -> list[Path]:
	"""Old qa_output/tests/<name> dirs that hold a recording."""
	tests_dir = source / 'tests'
	if not tests_dir.is_dir():
		return []
	return sorted(d for d in tests_dir.iterdir() if (d / 'history.json').is_file())


def migrate_prototype(ws: Workspace, source: Path) -> list[str]:
	"""Move recordings from the old qa_output layout into runs/. Returns moved names."""
	moved: list[str] = []
	for test_dir in prototype_recordings(source):
		target = ws.runs_dir / test_dir.name
		if target.exists():
			continue
		shutil.move(str(test_dir), str(target))
		moved.append(test_dir.name)
	old_permissions = source / 'qa_permissions.json'
	if old_permissions.is_file() and not ws.permissions_file.exists():
		shutil.move(str(old_permissions), str(ws.permissions_file))
	return moved
