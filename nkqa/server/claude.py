"""Claude Code as the desktop's brain: every chat turn is `claude -p`, on the user's own login.

The sidecar hosts nkqa's MCP tools at /mcp and points each `claude` process at them, so nkqa
itself never calls a model. Sessions and their titles are Claude Code's own, read back from
~/.claude/projects - resuming a desktop chat in the terminal (or the other way) just works.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast

Send = Callable[[dict[str, Any]], Awaitable[None]]

URLS = {
	'install': 'https://claude.com/product/claude-code',
	'upgrade': 'https://claude.ai/upgrade',
}
PAID_PLANS = frozenset({'pro', 'max', 'team', 'enterprise'})
# Read the app's own source to understand it, drive nkqa - and nothing that writes or runs.
ALLOWED = 'mcp__nkqa__* Read Glob Grep'
# Approval is the human's button on the card, never something Claude does from chat text.
DENIED = 'Bash Edit Write NotebookEdit WebFetch WebSearch mcp__nkqa__approve_scenario'
# Project and local settings only: the user's own plugins, hooks and output styles are for
# their coding sessions, and leaked into the QA agent's persona when they were loaded.
SETTING_SOURCES = 'project,local'
SESSION_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
STATUS_TTL = 60.0

# --- finding it ------------------------------------------------------------------------


def find_claude() -> str:
	"""A Finder-launched app gets launchd's PATH, not the shell's, so `which` alone misses
	every usual install location on a Mac."""
	found = shutil.which('claude')
	if found:
		return found
	home = Path.home()
	folders = [home / '.local/bin', home / '.claude/local', Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
	if os.environ.get('APPDATA'):
		folders.append(Path(os.environ['APPDATA']) / 'npm')  # npm's global bin on Windows
	for folder in folders:
		for name in ('claude', 'claude.exe', 'claude.cmd'):
			if (folder / name).is_file():
				return str(folder / name)
	shell = os.environ.get('SHELL', '')
	if shell and sys.platform != 'win32':
		with contextlib.suppress(OSError, subprocess.TimeoutExpired):
			out = subprocess.run([shell, '-lc', 'command -v claude'], capture_output=True, text=True, timeout=10)
			lines = out.stdout.strip().splitlines()
			if lines and Path(lines[-1]).is_file():
				return lines[-1]
	return ''


def launcher(claude: str) -> list[str]:
	"""How to start `claude`: usually the path itself.

	An npm install on Windows is a `claude.cmd` shim, and everything handed to a .cmd goes through
	cmd.exe - which cuts an argument at the first newline and the whole line at 8191 characters,
	i.e. our system-prompt brief. So run the shim's target with node directly instead.
	"""
	path = Path(claude)
	if path.suffix.lower() not in ('.cmd', '.bat'):
		return [claude]
	cli = path.parent / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'cli.js'
	node = shutil.which('node') or next(
		(str(path.parent / n) for n in ('node.exe', 'node') if (path.parent / n).is_file()), ''
	)
	return [node, str(cli)] if cli.is_file() and node else [claude]


def child_env(claude: str) -> dict[str, str]:
	"""An npm-installed `claude` is `#!/usr/bin/env node`: node must be on PATH too.

	ANTHROPIC_API_KEY is dropped. This process picked it up from a `.env` - the workspace's, or
	any above the working directory, which browser-use loads at import - where it was put for
	nkqa's own model calls. Claude Code prefers that variable to the user's login, so passing it
	on would quietly bill someone's API key instead of their subscription.
	"""
	extra = [str(Path(claude).parent), '/opt/homebrew/bin', '/usr/local/bin']
	env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
	return {**env, 'PATH': os.pathsep.join([*extra, os.environ.get('PATH', '')])}


# --- is it usable ----------------------------------------------------------------------


def ready(logged_in: bool, auth_method: str, provider: str, plan: str) -> bool:
	"""Signed in, and paying one way or another: a Claude plan, API-key billing, or a cloud
	provider's own credentials. A free claude.ai account cannot run Claude Code."""
	if provider and provider != 'firstParty':
		return True
	return logged_in and (auth_method != 'claude.ai' or plan in PAID_PLANS)


async def _output(claude: str, *args: str) -> str:
	proc = await asyncio.create_subprocess_exec(
		*launcher(claude),
		*args,
		stdin=asyncio.subprocess.DEVNULL,
		stdout=asyncio.subprocess.PIPE,
		stderr=asyncio.subprocess.DEVNULL,
		env=child_env(claude),
	)
	try:
		out, _ = await asyncio.wait_for(proc.communicate(), 20)
	except TimeoutError:
		proc.kill()
		return ''
	return out.decode(errors='replace').strip()


_cache: tuple[float, dict[str, Any]] | None = None


async def status(recheck: bool = False) -> dict[str, Any]:
	"""What the desktop needs to decide between chat and the install/sign-in/upgrade screen.

	Email and org ids from `auth status` stay here: the UI needs the verdict, not the account.
	"""
	global _cache
	if _cache is not None and not recheck and time.monotonic() - _cache[0] < STATUS_TTL:
		return _cache[1]
	claude = find_claude()
	info: dict[str, Any] = {'path': claude, 'version': '', 'logged_in': False, 'auth_method': '', 'plan': ''}
	if claude:
		info['version'] = (await _output(claude, '--version')).split(' ')[0]
		raw = await _output(claude, 'auth', 'status', '--json')
		try:
			parsed: Any = json.loads(raw)
		except json.JSONDecodeError:
			parsed = None
		if isinstance(parsed, dict):
			auth = cast(dict[str, Any], parsed)
			info['logged_in'] = bool(auth.get('loggedIn'))
			info['auth_method'] = str(auth.get('authMethod') or '')
			info['plan'] = str(auth.get('subscriptionType') or '')
			provider = str(auth.get('apiProvider') or '')
			info['ready'] = ready(info['logged_in'], info['auth_method'], provider, info['plan'])
		else:
			# ponytail: an older CLI without `auth status` - let the first turn find out.
			info['ready'] = True
	else:
		info['ready'] = False
	_cache = (time.monotonic(), info)
	return info


async def login() -> None:
	"""Opens the browser sign-in. Not awaited: it finishes when the human does."""
	claude = find_claude()
	if not claude:
		return
	await asyncio.create_subprocess_exec(
		*launcher(claude),
		'auth',
		'login',
		stdin=asyncio.subprocess.DEVNULL,
		stdout=asyncio.subprocess.DEVNULL,
		stderr=asyncio.subprocess.DEVNULL,
		env=child_env(claude),
	)


# --- sessions --------------------------------------------------------------------------


def project_dir(root: Path) -> Path:
	"""Where Claude Code keeps sessions for a working directory: every non-alphanumeric → '-'.

	ponytail: very long paths get a hashed name in Claude Code; those workspaces list nothing.
	"""
	return Path.home() / '.claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(root))


def _records(path: Path, only: tuple[str, ...] = ()) -> Iterator[dict[str, Any]]:
	"""The session file is undocumented and grows new record types: skip what does not parse."""
	with path.open(encoding='utf-8', errors='replace') as f:
		for line in f:
			if only and not any(marker in line for marker in only):
				continue
			with contextlib.suppress(json.JSONDecodeError):
				rec: Any = json.loads(line)
				if isinstance(rec, dict):
					yield cast(dict[str, Any], rec)


def _user_text(rec: dict[str, Any]) -> str:
	message: Any = rec.get('message')
	content: Any = cast(dict[str, Any], message).get('content') if isinstance(message, dict) else None
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		for block in cast(list[Any], content):
			if isinstance(block, dict) and cast(dict[str, Any], block).get('type') == 'text':
				return str(cast(dict[str, Any], block).get('text') or '')
	return ''


def _spoken(rec: dict[str, Any]) -> bool:
	"""A message a person would recognise as the conversation, not bookkeeping around it."""
	if rec.get('type') not in ('user', 'assistant') or rec.get('isSidechain') or rec.get('isMeta'):
		return False
	if rec.get('isCompactSummary'):
		return False
	return not (rec.get('type') == 'user' and _user_text(rec).lstrip().startswith('<'))


ATTACHED = re.compile(r'^\[Attached: (\S+)[^\]]*\]\s*')


def readable(message: str) -> str:
	"""A message as a person would name it: an attached sheet is "Import cases.xlsx", not the
	[Attached: ...] note in front of it that tells Claude where the CSV is."""
	match = ATTACHED.match(message)
	if not match:
		return message
	name = Path(match.group(1)).name
	rest = message[match.end() :].strip()
	return f'Import {name}' if not rest or rest == 'Import these test cases.' else f'{rest} ({name})'


def list_sessions(root: Path, titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
	"""Newest first. Title: one the human set, else Claude's own, else the one the desktop asked
	Claude for (`claude -p` writes none itself), else the first thing said."""
	folder = project_dir(root)
	if not folder.is_dir():
		return []
	sessions: list[dict[str, Any]] = []
	for path in folder.glob('*.jsonl'):
		if not SESSION_ID.match(path.stem):
			continue
		custom = ai = first = ''
		turns = 0
		for rec in _records(path, ('custom-title', 'ai-title', '"user"')):
			kind = rec.get('type')
			if kind == 'custom-title':
				custom = str(rec.get('customTitle') or custom)
			elif kind == 'ai-title':
				ai = str(rec.get('aiTitle') or ai)
			elif _spoken(rec) and _user_text(rec).strip():
				turns += 1
				first = first or readable(_user_text(rec).strip())
		if not turns:
			continue  # tool results only, or a turn that died before it said anything
		updated = path.stat().st_mtime
		sessions.append(
			{
				'id': path.stem,
				'title': custom or ai or (titles or {}).get(path.stem) or first[:60],
				'updated': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(updated)),
				'turns': turns,
			}
		)
	return sorted(sessions, key=lambda s: str(s['updated']), reverse=True)


def load_session(root: Path, session_id: str) -> list[dict[str, Any]] | None:
	"""The conversation in the same shape `claude -p` streams, so one renderer serves both."""
	if not SESSION_ID.match(session_id):
		return None
	path = project_dir(root) / f'{session_id}.jsonl'
	if not path.is_file():
		return None
	return [{'type': rec['type'], 'message': rec.get('message')} for rec in _records(path) if _spoken(rec)]


def read_titles(path: Path) -> dict[str, str]:
	try:
		raw: Any = json.loads(path.read_text(encoding='utf-8'))
	except (OSError, json.JSONDecodeError):
		return {}
	return {str(k): str(v) for k, v in cast(dict[str, Any], raw).items()} if isinstance(raw, dict) else {}


def save_title(path: Path, session_id: str, title: str) -> None:
	titles = read_titles(path)
	titles[session_id] = title
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(titles, indent=1), encoding='utf-8')


TITLE_PROMPT = (
	'Write a title of 3 to 6 words for a conversation that opens with the message below. '
	'Reply with the title only: no quotes, no trailing punctuation.\n\nMessage:\n'
)


async def make_title(first_message: str) -> str:
	"""A cheap Haiku call on the user's own login, no tools, not saved as a session."""
	claude = find_claude()
	if not claude:
		return ''
	out = await _output(
		claude,
		'-p',
		TITLE_PROMPT + first_message[:2000],
		'--model',
		'haiku',
		'--strict-mcp-config',
		'--no-session-persistence',
		'--tools',
		'',
	)
	lines = [line.strip().strip('"*#').strip() for line in out.splitlines() if line.strip()]
	return lines[0][:80] if lines else ''


# --- one turn --------------------------------------------------------------------------


def command(claude: str, mcp_url: str, token: str, session_id: str, resume: bool, brief: str) -> list[str]:
	config = {'mcpServers': {'nkqa': {'type': 'http', 'url': mcp_url, 'headers': {'Authorization': f'Bearer {token}'}}}}
	return [
		*launcher(claude),
		'-p',
		'--output-format',
		'stream-json',
		'--verbose',
		'--mcp-config',
		json.dumps(config),
		'--strict-mcp-config',
		'--allowedTools',
		ALLOWED,
		'--disallowedTools',
		DENIED,
		'--permission-mode',
		'dontAsk',
		'--setting-sources',
		SETTING_SOURCES,
		'--append-system-prompt',
		brief,
		*(['--resume', session_id] if resume else ['--session-id', session_id]),
	]


async def turn(
	root: Path, prompt: str, session_id: str, mcp_url: str, token: str, send: Send, job_id: str, brief: str
) -> int:
	"""One message in, Claude's stream out as `claude` frames. Cancelling kills the process."""
	claude = find_claude()
	if not claude:
		await send({'type': 'event', 'job': job_id, 'kind': 'log', 'text': 'Claude Code is not installed.', 'data': {}})
		return 2
	resume = (project_dir(root) / f'{session_id}.jsonl').is_file()
	proc = await asyncio.create_subprocess_exec(
		*command(claude, mcp_url, token, session_id, resume, brief),
		cwd=root,
		stdin=asyncio.subprocess.PIPE,
		stdout=asyncio.subprocess.PIPE,
		stderr=asyncio.subprocess.PIPE,
		env=child_env(claude),
		limit=16 * 1024 * 1024,  # one stream line carries a whole tool result, screenshots included
	)
	assert proc.stdin and proc.stdout and proc.stderr
	# stdin, not argv: a message that starts with '-' would otherwise be read as a flag.
	proc.stdin.write(prompt.encode())
	proc.stdin.close()
	errors = asyncio.create_task(proc.stderr.read())
	try:
		async for raw in proc.stdout:
			try:
				msg: Any = json.loads(raw)
			except json.JSONDecodeError:
				continue
			await send({'type': 'claude', 'job': job_id, 'msg': msg})
		code = await proc.wait()
	finally:
		if proc.returncode is None:
			proc.kill()
			await proc.wait()
	stderr = (await errors).decode(errors='replace').strip()
	if code and stderr:
		await send({'type': 'event', 'job': job_id, 'kind': 'log', 'text': stderr[-2000:], 'data': {}})
	return code
