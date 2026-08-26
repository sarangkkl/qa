"""The appmap store: the QA notebook about the app, plain files in git.

Every automatic writer (ingest, reflector, crawler) emits the same AppmapUpdate and
goes through apply(): path-sandboxed to appmap/, full-file create/update only, never
delete, auto-committed when the workspace is a git repo. Review surface: git diff.
"""

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from nkqa.workspace import Workspace

ALLOWED_SUFFIXES = ('.md', '.json')


class FileUpdate(BaseModel):
	file: str = Field(description="path relative to appmap/, e.g. 'pages/login.md' or 'flows/checkout.md'")
	content: str = Field(description='the complete new file content (full replacement)')


class AppmapUpdate(BaseModel):
	files: list[FileUpdate]
	notes: str = Field(default='', description='open questions or uncertainties for the human')


def read_all(ws: Workspace) -> dict[str, str]:
	"""Current appmap content, rel path -> text."""
	if not ws.appmap_dir.is_dir():
		return {}
	return {
		str(f.relative_to(ws.appmap_dir)): f.read_text(encoding='utf-8')
		for f in sorted(ws.appmap_dir.rglob('*'))
		if f.is_file() and f.suffix in ALLOWED_SUFFIXES
	}


def _safe_target(ws: Workspace, rel: str) -> Path:
	p = Path(rel)
	if p.is_absolute() or '..' in p.parts or p.suffix not in ALLOWED_SUFFIXES:
		raise ValueError(f'appmap write refused for path: {rel!r}')
	target = (ws.appmap_dir / p).resolve()
	if not target.is_relative_to(ws.appmap_dir.resolve()):
		raise ValueError(f'appmap write refused for path: {rel!r}')
	return target


def apply(ws: Workspace, update: AppmapUpdate, commit_message: str) -> list[str]:
	"""Write the update (sandboxed) and auto-commit when the workspace is a git repo."""
	written: list[str] = []
	for fu in update.files:
		target = _safe_target(ws, fu.file)
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_text(fu.content.rstrip() + '\n', encoding='utf-8')
		written.append(fu.file)
	if written:
		commit(ws, commit_message)
	return written


def in_git_repo(ws: Workspace) -> bool:
	try:
		result = subprocess.run(
			['git', 'rev-parse', '--is-inside-work-tree'], cwd=ws.root, capture_output=True, text=True
		)
		return result.stdout.strip() == 'true'
	except OSError:
		return False


def commit(ws: Workspace, message: str) -> bool:
	"""Commit appmap/ changes only. No-op (False) outside a git repo or with nothing to commit."""
	if not in_git_repo(ws):
		return False
	subprocess.run(['git', 'add', 'appmap'], cwd=ws.root, capture_output=True)
	result = subprocess.run(
		['git', 'commit', '-q', '-m', message, '--', 'appmap'], cwd=ws.root, capture_output=True, text=True
	)
	return result.returncode == 0
