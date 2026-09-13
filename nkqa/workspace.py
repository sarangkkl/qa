"""Workspace discovery and layout. All filesystem paths come from here."""

import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from nkqa.config import render_template

CONFIG_FILE = 'config.yaml'
VAULT_FILE = 'vault.yaml'
# The desktop app's identifier (desktop/src-tauri/tauri.conf.json) and its recents file
# (desktop/src-tauri/src/lib.rs). Read-only from here: the desktop owns it.
DESKTOP_ID = 'com.gauravsah.nkqa'
RECENTS_FILE = 'workspaces.json'

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
.nkqa/
"""


def slugify(text: str) -> str:
	"""Turn free text into a meaningful folder name: 'Login flow!' -> 'login-flow'."""
	slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
	return slug[:40].rstrip('-') or 'unnamed-test'


class Workspace:
	def __init__(self, root: Path):
		self.root = root.resolve()
		self.config_file = self.root / CONFIG_FILE
		self.vault_file = self.root / VAULT_FILE
		self.appmap_dir = self.root / 'appmap'
		self.scenarios_dir = self.root / 'scenarios'
		self.runs_dir = self.root / 'runs'
		self.chats_dir = self.root / 'chats'  # committed: the reasoning behind a scenario is reviewable
		self.permissions_file = self.root / 'qa_permissions.json'
		self.local_dir = self.root / '.nkqa'  # gitignored: machine-local, never shared

	def run_dir(self, name: str) -> Path:
		return self.runs_dir / slugify(name)

	def scenario_run_dir(self, scenario_id: str, now: datetime | None = None) -> Path:
		"""One timestamped dir per scenario run; `report.latest_run_dir` globs this exact shape."""
		return self.runs_dir / f'{scenario_id.replace("/", "-")}--{now or datetime.now():%Y%m%d-%H%M%S}'

	def identity(self) -> str:
		"""Stable id for this checkout, so two clones on one machine keep separate secrets.

		Lives in the gitignored .nkqa/, so cloning the repo never inherits someone else's
		keychain entries - it just reports the credentials as not yet set.
		"""
		id_file = self.local_dir / 'id'
		if id_file.is_file():
			existing = id_file.read_text(encoding='utf-8').strip()
			if existing:
				return existing
		self.local_dir.mkdir(parents=True, exist_ok=True)
		fresh = uuid.uuid4().hex
		id_file.write_text(fresh, encoding='utf-8')
		return fresh


def at(root: Path) -> Workspace | None:
	"""This exact directory, without the walk-up. What "is *this* folder a workspace?" means."""
	candidate = root.resolve()
	if (candidate / CONFIG_FILE).is_file() and (candidate / 'appmap').is_dir():
		return Workspace(candidate)
	return None


def find(start: Path | None = None) -> Workspace | None:
	"""Walk up from start (default cwd) looking for a workspace, like git finds .git."""
	current = (start or Path.cwd()).resolve()
	for candidate in (current, *current.parents):
		found = at(candidate)
		if found is not None:
			return found
	return None


def recents_file() -> Path:
	"""Where the desktop app keeps its recent-workspaces list (Tauri's app_data_dir per OS)."""
	if sys.platform == 'darwin':
		base = Path.home() / 'Library' / 'Application Support'
	elif sys.platform == 'win32':
		base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
	else:
		base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
	return base / DESKTOP_ID / RECENTS_FILE


def recent_workspaces(recents: Path | None = None) -> list[Workspace]:
	"""Workspaces the desktop app opened recently, most recent first. Only ones that still exist."""
	try:
		raw: object = json.loads((recents or recents_file()).read_text(encoding='utf-8'))
	except (OSError, json.JSONDecodeError):
		return []
	if not isinstance(raw, list):
		return []
	found: list[Workspace] = []
	for entry in raw:  # pyright: ignore[reportUnknownVariableType]
		if isinstance(entry, str) and (ws := at(Path(entry))) is not None:
			found.append(ws)
	return found


def create(root: Path, app_name: str = '', base_url: str = '') -> Workspace:
	"""Create the workspace layout in root. Idempotent; never overwrites existing files."""
	ws = Workspace(root)
	ws.appmap_dir.mkdir(parents=True, exist_ok=True)
	ws.scenarios_dir.mkdir(exist_ok=True)
	ws.runs_dir.mkdir(exist_ok=True)
	ws.chats_dir.mkdir(exist_ok=True)
	if not ws.config_file.exists():
		ws.config_file.write_text(render_template(app_name, base_url))
	if not ws.vault_file.exists():
		from nkqa.vault import VAULT_TEMPLATE

		ws.vault_file.write_text(VAULT_TEMPLATE)
	ws.local_dir.mkdir(exist_ok=True)
	overview = ws.appmap_dir / 'overview.md'
	if not overview.exists():
		overview.write_text(OVERVIEW_STUB)
	(ws.scenarios_dir / '.gitkeep').touch()
	(ws.runs_dir / '.gitkeep').touch()
	(ws.chats_dir / '.gitkeep').touch()
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
