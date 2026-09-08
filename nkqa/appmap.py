"""The appmap store: the QA notebook about the app, plain files in git.

Every automatic writer (ingest, reflector, crawler) emits the same AppmapUpdate and
goes through apply(): path-sandboxed to appmap/, full-file create/update only, never
delete, auto-committed when the workspace is a git repo. Review surface: git diff.
"""

import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from nkqa.workspace import Workspace

ALLOWED_SUFFIXES = ('.md', '.json')


class FileUpdate(BaseModel):
	file: str = Field(
		description="path relative to appmap/, e.g. 'pages/login.md' or 'flows/checkout.md'. "
		"Do NOT repeat the 'appmap/' prefix used in the headings you were shown."
	)
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


RUN_CONTEXT_FILES = ('overview.md', 'flows/login.md')


def context_for_run(ws: Workspace) -> str:
	"""What the executor should know before it touches the app: the overview, plus the
	login flow when one is documented. Deliberately not the whole appmap - the page docs
	are large and mostly irrelevant to any single scenario.
	"""
	parts: list[str] = []
	for name in RUN_CONTEXT_FILES:
		f = ws.appmap_dir / name
		if f.is_file():
			parts.append(f'--- appmap/{name} ---\n{f.read_text(encoding="utf-8").strip()}')
	return '\n\n'.join(parts)


# --- route identity ---------------------------------------------------------
#
# What counts as "the same screen". Without this a crawl of a dev environment holding 442
# projects would describe each one as its own page and never finish, and a login redirect
# would file a 2KB single-use OAuth URL as a page of the app.

_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_HEX = re.compile(r'^[0-9a-f]{8,}$', re.I)
_TEMPLATE = re.compile(r'^[{:<].*$')  # {projectNumber}, :id, <id> - a human already generalised it
ID_PLACEHOLDER = ':id'


def _is_id(segment: str) -> bool:
	if _TEMPLATE.match(segment):
		return True
	if segment.isdigit() or _UUID.match(segment) or _HEX.match(segment):
		return True
	# Codes like 501.D70162.00001: dotted and carrying digits. A plain word with a dot
	# (index.html, about.us) is a page name, not a record id.
	return '.' in segment and any(c.isdigit() for c in segment) and len(segment) > 6


def normalize_route(url: str) -> str:
	"""A URL or route -> the screen it is an instance of. '/pm-hub/501.D70162.00001' -> '/pm-hub/:id'.

	Query and fragment are dropped: they carry state, not identity, and they are where the
	enormous single-use OAuth redirect URLs live.
	"""
	path = urlsplit(url.strip()).path if '//' in url else url.strip().split('?', 1)[0].split('#', 1)[0]
	segments = [ID_PLACEHOLDER if _is_id(s) else s.lower() for s in path.split('/') if s]
	return '/' + '/'.join(segments)


def route_slug(route: str) -> str:
	"""'/pm-hub/:id' -> 'pm-hub-detail'. The file name a route's page doc lives under."""
	parts = [('detail' if s == ID_PLACEHOLDER else s) for s in route.split('/') if s]
	slug = re.sub(r'[^a-z0-9]+', '-', '-'.join(parts).lower()).strip('-')
	return slug or 'home'


# `**Route:** `/clients`` - the convention every hand-written page doc already follows. The
# first backticked token is the route; anything after it is prose ("reached from ...").
_ROUTE_LINE = re.compile(r'^\s*\*\*Route:\*\*\s*`([^`]+)`', re.M)


def route_of(text: str) -> str:
	"""The normalized route a page doc describes, or '' when it does not name one."""
	found = _ROUTE_LINE.search(text)
	return normalize_route(found.group(1)) if found else ''


def route_matches(pattern: str, route: str) -> bool:
	"""Does a documented route cover this one? `:id` is a wildcard on either side.

	Equality is not enough. A human writes `/projects/{projectNumber}/{tab}`, which normalizes
	to `/projects/:id/:id` and must still cover the crawler's `/projects/:id/project-details` -
	otherwise the crawl re-maps a page someone already documented by hand.
	"""
	left, right = [s for s in pattern.split('/') if s], [s for s in route.split('/') if s]
	if len(left) != len(right):
		return False
	return all(a == b or ID_PLACEHOLDER in (a, b) for a, b in zip(left, right, strict=True))


def covering_route(known: dict[str, str], route: str) -> str:
	"""The documented route covering this one, or ''. Exact match wins over a wildcard."""
	if route in known:
		return route
	return next((p for p in known if route_matches(p, route)), '')


def known_routes(ws: Workspace) -> dict[str, str]:
	"""Routes already documented -> the file documenting them. The crawl's skip list.

	Derived from the page docs themselves rather than a side manifest, so it cannot drift out
	of sync with what is actually written, and a page a human wrote by hand counts as known.
	"""
	pages = ws.appmap_dir / 'pages'
	if not pages.is_dir():
		return {}
	found: dict[str, str] = {}
	for f in sorted(pages.glob('*.md')):
		route = route_of(f.read_text(encoding='utf-8'))
		if route:
			found.setdefault(route, f'pages/{f.name}')
	return found


CHAT_CONTEXT_BUDGET = 40_000  # characters, ~10k tokens on the cheap chat model


def context_for_chat(ws: Workspace, budget: int = CHAT_CONTEXT_BUDGET) -> str:
	"""The whole notebook, for answering questions about the app.

	The overview goes first and is never dropped - it is the index. Flows before pages,
	because a flow explains behaviour and a page lists elements. Past the budget the rest is
	named but not included, so the model can say "I have a page doc for that but have not
	read it" instead of silently believing the app has nothing else.
	"""
	files = read_all(ws)
	order = sorted(files, key=lambda n: (n != 'overview.md', not n.startswith('flows/'), n))
	parts: list[str] = []
	omitted: list[str] = []
	used = 0
	for name in order:
		body = files[name].strip()
		if not body:
			continue
		chunk = f'--- appmap/{name} ---\n{body}'
		if used and used + len(chunk) > budget:
			omitted.append(name)
			continue
		parts.append(chunk)
		used += len(chunk)
	if omitted:
		parts.append(f'--- not loaded (context budget) ---\n{", ".join(omitted)}')
	return '\n\n'.join(parts)


def strip_appmap_prefix(rel: str) -> str:
	"""'appmap/overview.md' -> 'overview.md'. Paths are relative to appmap/, not to the root.

	Every writer shows the model its current knowledge under `--- appmap/<name> ---` headings,
	so the model naturally hands that same string back as the file to write - and joining it to
	appmap/ again produced appmap/appmap/overview.md. The sandbox allowed it (still inside the
	directory), so it failed silently and nested one level deeper on every single write, while
	the real overview.md kept the stub and nothing ever read the corrections.

	Only that prefix is removed. The root marker of an absolute path is deliberately left in
	place: dropping it here would turn '/etc/evil.md' into a relative path and walk it straight
	past the sandbox check in _safe_target.
	"""
	parts = PurePosixPath(rel.strip().replace('\\', '/')).parts
	while parts and parts[0] == 'appmap':
		parts = parts[1:]
	return str(PurePosixPath(*parts)) if parts else ''


def _safe_target(ws: Workspace, rel: str) -> Path:
	p = Path(strip_appmap_prefix(rel))
	if not p.name or p.is_absolute() or '..' in p.parts or p.suffix not in ALLOWED_SUFFIXES:
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
		# The normalized path, not what the model said: callers render this and diff against it.
		written.append(str(target.relative_to(ws.appmap_dir)))
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
