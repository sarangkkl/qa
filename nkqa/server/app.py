"""The sidecar's HTTP + WebSocket surface.

Split: HTTP is state you can fetch (config, scenarios, runs, artifacts); the WebSocket is
things that happen (commands, their events, the prompts they raise). One connection is one
session, with its own HumanInTheLoop - so a credential typed in one window is reused for
that window and dies with it, exactly like the terminal session.

Commands are generated from shell.commands.REGISTRY, the same registry that generates the
slash parser and the chat agent's tool list, so a new command appears in all three at once.
"""

# FastAPI route functions are registered by their decorator, never called by name.
# pyright: reportUnusedFunction=false

import asyncio
import json
import re
import uuid
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope
from starlette.types import Send as AsgiSend

from nkqa import chats as chats_mod
from nkqa import config as config_mod
from nkqa import mcp_server, prompts
from nkqa import scenarios as scenarios_mod
from nkqa.execution.evidence import recorded_runs, recording_file, step_count
from nkqa.execution.report import last_verdict, latest_run_dir, read_results
from nkqa.hitl import HumanInTheLoop
from nkqa.server import auth, claude
from nkqa.server.channel import Relay, SocketChannel
from nkqa.server.jobs import JobRunner
from nkqa.shell.commands import REGISTRY, ShellContext, parse_slash
from nkqa.vault import Vault
from nkqa.workspace import Workspace

DRAFT_BATCH = 20  # drafts per chat turn: an imported sheet arrives in reviewable batches
MEDIA_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.webm', '.json', '.md', '.txt'}


def scenario_view(ws: Workspace, s: scenarios_mod.Scenario) -> dict[str, Any]:
	# last_run is what makes the verdict inspectable: latest_run_dir only matches a run that
	# wrote a results.md, so /artifacts/runs/<last_run>/results.md is a report the UI can fetch
	# without asking /runs/{name} first. Without it a scenario knew it had failed but not where.
	run_dir = latest_run_dir(ws.runs_dir, s.id)
	return {
		'id': s.id,
		'title': s.title,
		'state': s.runnable(),
		'ticket': s.ticket,
		'tags': s.tags,
		'approved_by': s.approved_by,
		'approved_at': s.approved_at,
		'last_verdict': last_verdict(ws.runs_dir, s.id),
		'last_run': run_dir.name if run_dir else '',
	}


def connector_view(spec: Any, jira_project: str) -> dict[str, Any]:
	"""What a configured MCP server looks like to the UI.

	Never includes `env`: those values are 'env:NAME' indirections whose whole point is
	that a resolved credential never leaves the process that launched the server.
	"""
	return {
		'name': spec.name,
		'command': f'{spec.command} {" ".join(spec.args)}'.strip(),
		'expose_to_executor': spec.expose_to_executor,
		'needs_env': sorted(spec.env),
		'project': jira_project if spec.name == 'jira' else '',
	}


def command_view(name: str) -> dict[str, Any]:
	cmd = REGISTRY[name]
	return {
		'name': cmd.name,
		'help': cmd.help,
		'human_only': cmd.human_only,
		'shell_only': cmd.shell_only,
		'instant': cmd.instant,
		'params': [
			{'name': p.name, 'help': p.help, 'type': p.type, 'flag': p.flag, 'required': p.required} for p in cmd.params
		],
	}


def coerce(name: str, args: dict[str, Any]) -> dict[str, Any]:
	by_name = {p.name: p for p in REGISTRY[name].params}
	out: dict[str, Any] = {}
	for key, value in args.items():
		param = by_name.get(key)
		if param is None:
			continue
		if param.type == 'integer':
			out[key] = int(value) if str(value).strip().lstrip('-').isdigit() else 0
		elif param.type == 'boolean':
			out[key] = str(value).strip().lower() in ('1', 'true', 'yes') if not isinstance(value, bool) else value
		else:
			out[key] = str(value)
	return out


def _ordered(directory: Path, pattern: str) -> list[Path]:
	"""Files in step order. Plain sorting puts `..._10.txt` before `..._2.txt`, which reads as
	a shuffled transcript - so order on the trailing number when there is one."""
	if not directory.is_dir():
		return []

	def key(p: Path) -> tuple[int, str]:
		digits = re.findall(r'\d+', p.stem)
		return (int(digits[-1]) if digits else 0, p.name)

	return sorted(directory.glob(pattern), key=key)


def safe_artifact(ws: Workspace, relative: str) -> Path:
	"""Serve only what is actually inside the workspace, whatever the caller asks for."""
	target = (ws.root / relative).resolve()
	if not target.is_relative_to(ws.root.resolve()) or not target.is_file():
		raise HTTPException(status_code=404, detail='no such artifact')
	if target.suffix.lower() not in MEDIA_SUFFIXES:
		raise HTTPException(status_code=403, detail=f'{target.suffix} is not served')
	return target


class Guarded:
	"""/mcp is a raw ASGI app, so it gets the token and origin checks by hand. A class, not a
	closure: Starlette treats a plain function endpoint as a GET-only request handler."""

	def __init__(self, inner: ASGIApp):
		self.inner = inner

	async def __call__(self, scope: Scope, receive: Receive, send: AsgiSend) -> None:
		headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
		bearer = headers.get('authorization', '').removeprefix('Bearer ').strip()
		if not auth.token_ok(bearer) or not auth.origin_ok(headers.get('origin')):
			await PlainTextResponse('bad or missing token', status_code=401)(scope, receive, send)
			return
		await self.inner(scope, receive, send)


def create_app(ws: Workspace, port: int = 0) -> FastAPI:
	# nkqa's MCP tools, served to the `claude` processes the chat spawns. In this process rather
	# than a `qa mcp` child, so their asks reach this window's modal instead of an OS dialog, the
	# browser they drive streams to the live pane, and one workspace has exactly one owner.
	relay = Relay()
	mcp_session = mcp_server.Session(dialogs=relay, live=relay)
	mcp_session.use(ws)
	mcp_session.draft_limit = DRAFT_BATCH
	mcp = mcp_server.build_server(mcp_session)
	mcp.streamable_http_app()  # creates the session manager the lifespan runs

	@asynccontextmanager
	async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
		async with mcp.session_manager.run():
			yield
		await mcp_session.close()

	app = FastAPI(title='nkqa sidecar', docs_url=None, redoc_url=None, lifespan=lifespan)
	app.router.routes.append(Route('/mcp', endpoint=Guarded(StreamableHTTPASGIApp(mcp.session_manager))))
	# The UI is always a different origin from this port - tauri://localhost in the app,
	# http://127.0.0.1:1420 while developing - so without this every fetch is blocked by
	# the browser before the token is even looked at. The allowlist is the same one auth
	# enforces; this only tells the browser what auth already decided.
	app.add_middleware(
		CORSMiddleware,
		allow_origins=sorted(auth.ALLOWED_ORIGINS),
		allow_methods=['GET', 'POST', 'OPTIONS'],
		allow_headers=['Authorization', 'Content-Type'],
		max_age=600,
	)
	app.state.workspace = ws
	app.state.jobs = JobRunner()
	app.state.relay = relay
	app.state.mcp_session = mcp_session
	app.state.port = port
	guard = [Depends(auth.require_token)]

	@app.get('/health', dependencies=guard)
	async def health(recheck: bool = False) -> dict[str, Any]:
		from importlib.metadata import version as pkg_version

		return {
			'nkqa': pkg_version('nkqa'),
			'browser_use': pkg_version('browser-use'),
			'workspace': str(ws.root),
			'claude': await claude.status(recheck),
			'busy': app.state.jobs.busy,
		}

	@app.post('/claude/login', dependencies=guard)
	async def claude_login() -> dict[str, bool]:
		await claude.login()
		return {'ok': True}

	@app.post('/open/{which}', dependencies=guard)
	def open_page(which: str) -> dict[str, bool]:
		"""Two fixed pages and nothing else: a URL from the webview is never opened as given."""
		url = claude.URLS.get(which)
		if url is None:
			raise HTTPException(status_code=404, detail=f'no page "{which}"')
		return {'ok': webbrowser.open(url)}

	@app.get('/workspace', dependencies=guard)
	def workspace_state() -> dict[str, Any]:
		from nkqa.models import CATALOGUE, PROVIDER_LABELS, default_tier_models

		cfg = config_mod.load(ws.config_file)
		return {
			'root': str(ws.root),
			'app_name': cfg.app_name,
			'base_url': cfg.base_url,
			'headless': cfg.headless,
			'models': cfg.models,
			'aliases': cfg.aliases,
			# The catalogue is what the settings page offers, not what it accepts: any id typed
			# in reaches the provider verbatim, so this list going stale costs a convenience,
			# never a capability.
			'providers': [
				{
					'name': name,
					'label': PROVIDER_LABELS.get(name, name),
					'models': entries,
					# Sent rather than inferred from the order of `models`: the settings page used to
					# re-derive this itself and could quietly disagree with what the CLI would pick.
					'defaults': default_tier_models(name),
				}
				for name, entries in CATALOGUE.items()
			],
			'appmap': sorted(str(f.relative_to(ws.appmap_dir)) for f in ws.appmap_dir.rglob('*.md')),
			'scenarios': [scenario_view(ws, s) for s in scenarios_mod.load_all(ws.scenarios_dir)],
			'runs': [d.name for d in reversed(recorded_runs(ws.runs_dir))],
			'connectors': [connector_view(spec, cfg.jira_project) for spec in cfg.mcp_servers],
			'commands': [command_view(name) for name in REGISTRY],
		}

	@app.get('/scenarios/{scenario_id:path}', dependencies=guard)
	def one_scenario(scenario_id: str) -> dict[str, Any]:
		s = scenarios_mod.find(ws.scenarios_dir, scenario_id)
		if s is None:
			raise HTTPException(status_code=404, detail=f'no scenario "{scenario_id}"')
		return {
			**scenario_view(ws, s),
			'body': s.path.read_text(encoding='utf-8'),
			# Parsed here so a chat card never has to re-implement the scenario format.
			'preconditions': s.preconditions,
			'steps': [{'action': st.action, 'expect': st.expect} for st in s.steps],
		}

	@app.get('/runs/{name}', dependencies=guard)
	def one_run(name: str) -> dict[str, Any]:
		run_dir = ws.runs_dir / name
		if not run_dir.is_dir():
			raise HTTPException(status_code=404, detail=f'no run "{name}"')
		record = read_results(run_dir)
		artifacts = {
			key: f'/artifacts/runs/{name}/{rel}'
			for key, rel in (('report', 'results.md'), ('gif', 'last_run.gif'), ('history', 'history.json'))
			if (run_dir / rel).is_file()
		}
		videos = sorted(p.name for p in (run_dir / 'videos').glob('*.mp4')) if (run_dir / 'videos').is_dir() else []
		return {
			'name': name,
			'steps': step_count(rec) if (rec := recording_file(run_dir)) is not None else 0,
			'result': record.model_dump() if record else None,
			'artifacts': artifacts,
			'videos': [f'/artifacts/runs/{name}/videos/{v}' for v in videos],
			# The report links these as `videos/` and `conversation/`, which are directories -
			# safe_artifact only serves files, so those links could never resolve. Listing the
			# files is what makes the evidence openable instead of merely mentioned.
			'shots': [f'/artifacts/runs/{name}/steps/{p.name}' for p in _ordered(run_dir / 'steps', '*.png')],
			'conversation': [
				f'/artifacts/runs/{name}/conversation/{p.name}' for p in _ordered(run_dir / 'conversation', '*.txt')
			],
		}

	@app.post('/imports', dependencies=guard)
	async def upload_import(request: Request, name: str) -> dict[str, Any]:
		"""A test case sheet, as the raw request body. Saved under imports/ and made readable."""
		from nkqa import imports

		try:
			path = imports.save_upload(ws, name, await request.body())
		except ValueError as e:
			raise HTTPException(status_code=400, detail=str(e)) from e
		try:
			sheets = imports.to_csv(path)
		except Exception as e:  # a corrupt or password-protected workbook: say so, keep nothing
			path.unlink(missing_ok=True)
			raise HTTPException(status_code=400, detail=f'Could not read {name}: {type(e).__name__}') from e
		return {
			'path': path.relative_to(ws.root).as_posix(),
			'sheets': [
				{'name': s.name, 'csv': s.csv.relative_to(ws.root).as_posix(), 'rows': s.rows, 'columns': s.columns}
				for s in sheets
			],
			'note': imports.attachment_note(ws, path, sheets),
		}

	@app.get('/library', dependencies=guard)
	def library_tests() -> dict[str, Any]:
		from nkqa import library

		return {'tests': library.entries(ws)}

	@app.get('/chats', dependencies=guard)
	def all_chats() -> dict[str, Any]:
		return {'chats': claude.list_sessions(ws.root, claude.read_titles(ws.chat_titles_file))}

	@app.get('/chats/{chat_id}', dependencies=guard)
	def one_chat(chat_id: str) -> dict[str, Any]:
		messages = claude.load_session(ws.root, chat_id)
		if messages is None:
			raise HTTPException(status_code=404, detail=f'no chat "{chat_id}"')
		chats = claude.list_sessions(ws.root, claude.read_titles(ws.chat_titles_file))
		title = next((c['title'] for c in chats if c['id'] == chat_id), '')
		return {'id': chat_id, 'title': title, 'messages': messages}

	@app.get('/artifacts/{relative:path}', dependencies=guard)
	def artifact(relative: str) -> FileResponse:
		return FileResponse(safe_artifact(ws, relative))

	@app.websocket('/session')
	async def session(socket: WebSocket) -> None:
		if not auth.token_ok(socket.query_params.get('token')) or not auth.origin_ok(socket.headers.get('origin')):
			await socket.close(code=4401)
			return
		await socket.accept()
		await _serve(socket, app)

	return app


async def _send(socket: WebSocket, frame: dict[str, Any]) -> None:
	await socket.send_text(json.dumps(frame, default=str))


def build_context(ws: Workspace, channel: SocketChannel, hitl: HumanInTheLoop | None = None) -> ShellContext:
	"""One session's state. Extracted so the vault wiring below is testable."""
	return ShellContext(
		ws=ws,
		config=config_mod.load(ws.config_file),
		# The vault is not optional here: without it `_from_vault` short-circuits on every
		# call and each stored credential falls through to "type it again", origin binding
		# included - which is how the whole release chain was dead over the socket.
		hitl=hitl or HumanInTheLoop(ws.permissions_file, channel, Vault(ws)),
		channel=channel,
	)


def forget(hitl: HumanInTheLoop) -> None:
	"""A closed window ends the session: what was granted or typed in it goes too."""
	hitl.secrets.clear()
	hitl.session_grants.clear()
	hitl.session_credentials.clear()
	hitl.autonomy = 'ask'


async def _serve(socket: WebSocket, app: FastAPI) -> None:
	ws: Workspace = app.state.workspace
	jobs: JobRunner = app.state.jobs
	relay: Relay = app.state.relay

	async def send(frame: dict[str, Any]) -> None:
		await _send(socket, frame)

	channel = SocketChannel(send)
	# One HITL for this window and the Claude it talks to, so the autonomy picker and a
	# credential typed once cover the MCP tools too. The relay makes their asks this window's.
	ctx = build_context(ws, channel, app.state.mcp_session.require_hitl())
	relay.target = channel
	claude_turn = ClaudeTurn(ws, f'http://127.0.0.1:{app.state.port}/mcp', send, app.state.mcp_session)
	watchers: set[asyncio.Task[None]] = set()
	try:
		while True:
			frame = json.loads(await socket.receive_text())
			kind = frame.get('type')
			if kind == 'answer':
				channel.answer(str(frame.get('id', '')), str(frame.get('value', '')))
			elif kind == 'cancel':
				ok = jobs.cancel(str(frame.get('id', '')))
				if ok:
					# Only on a real cancel: abandoning resolves every pending ask with '',
					# which is a deny. A stale id must not silently deny the live job's prompt.
					channel.abandon()
				await send({'type': 'cancelled', 'job': frame.get('id', ''), 'ok': ok})
			elif kind in ('command', 'say'):
				task = await _launch(frame, ctx, channel, jobs, send, ws, claude_turn)
				if task is not None:
					watchers.add(task)
					task.add_done_callback(watchers.discard)
			else:
				await send({'type': 'error', 'message': f'unknown frame type {kind!r}'})
	except (WebSocketDisconnect, json.JSONDecodeError):
		pass
	finally:
		channel.abandon()  # a closed window must never leave a run waiting on an answer
		for task in watchers:
			task.cancel()
		if relay.target is channel:
			relay.target = None
		forget(ctx.hitl)


class ClaudeTurn:
	"""What a `say` needs to become a `claude -p` turn."""

	def __init__(self, ws: Workspace, mcp_url: str, send: Any, mcp_session: Any = None):
		self.ws, self.mcp_url, self.send = ws, mcp_url, send
		self.mcp_session = mcp_session
		self.naming: set[asyncio.Task[None]] = set()

	async def start(self, text: str, chat_id: str, job_id: str) -> tuple[str, Any]:
		"""(session id, the job's coroutine). A new chat gets its id before Claude says a word,
		so the window can select it while the first reply is still streaming."""
		new = not claude.SESSION_ID.match(chat_id)
		if new:
			chat_id = str(uuid.uuid4())
			await self.send({'type': 'chat', 'id': chat_id, 'title': ''})

		async def work() -> int:
			if self.mcp_session is not None:
				self.mcp_session.drafted = 0  # a new message, a new batch
			cfg = config_mod.load(self.ws.config_file)
			brief = prompts.desktop_brief(cfg.app_name, cfg.base_url)
			code = await claude.turn(
				self.ws.root, text, chat_id, self.mcp_url, auth.current(), self.send, job_id, brief
			)
			if new and code == 0:
				# After the result, not before it: a title is not worth holding up the reply.
				task = asyncio.create_task(self.name(chat_id, text))
				self.naming.add(task)
				task.add_done_callback(self.naming.discard)
			return code

		return chat_id, work()

	async def name(self, chat_id: str, first_message: str) -> None:
		title = await claude.make_title(claude.readable(first_message))
		if title:
			claude.save_title(self.ws.chat_titles_file, chat_id, title)
			await self.send({'type': 'chat', 'id': chat_id, 'title': title})


async def _attach_chat(ctx: ShellContext, ws: Workspace, chat_id: str, send: Any) -> None:
	"""A `say` names its chat, or gets a new one - the client is told which either way."""
	if chat_id and ctx.chat is not None and ctx.chat.id == chat_id:
		return
	chat = chats_mod.load(ws, chat_id) if chat_id else None
	if chat is None:
		chat = chats_mod.new_chat(ws)
		await send({'type': 'chat', 'id': chat.id, 'title': chat.title})
	ctx.chat = chat


async def _launch(
	frame: dict[str, Any],
	ctx: ShellContext,
	channel: SocketChannel,
	jobs: JobRunner,
	send: Any,
	ws: Workspace,
	claude_turn: 'ClaudeTurn | None' = None,
) -> 'asyncio.Task[None] | None':
	job_id = str(frame.get('id', ''))
	line = ''

	if frame.get('type') == 'say':
		text = str(frame.get('text', '')).strip()
		if not text:
			await send({'type': 'result', 'job': job_id, 'code': 2, 'error': 'empty message'})
			return None
		if claude_turn is None:
			await send({'type': 'result', 'job': job_id, 'code': 2, 'error': 'chat is not available here'})
			return None
		_chat, work = await claude_turn.start(text, str(frame.get('chat', '')), job_id)
		name = 'say'
	else:
		# Two wire forms. Buttons send name+args; a slash line typed in the chat box sends
		# `line` and is parsed by `parse_slash` - the terminal's own parser, so the grammar
		# (quoting, --flags, free-text `rest` params, the "did you mean" hint) is identical
		# and there is no second implementation to drift.
		line = str(frame.get('line', '')).strip()
		if line:
			# Typed into the chat box, so it belongs in the transcript like anything else
			# said there - otherwise reading a conversation back leaves unexplained gaps.
			if 'chat' in frame:
				from nkqa.shell.agent import record

				await _attach_chat(ctx, ws, str(frame.get('chat', '')), send)
				record(ctx, chats_mod.Turn(role='user', text=line))
			cmd, args, error = parse_slash(line)
			if cmd is None:
				await send({'type': 'result', 'job': job_id, 'code': 2, 'error': error})
				return None
			name = cmd.name
		else:
			name = str(frame.get('name', '')).lstrip('/')
			cmd = REGISTRY.get(name)
			args = coerce(name, dict(frame.get('args') or {})) if cmd else {}
		if cmd is None or cmd.shell_only:
			await send({'type': 'result', 'job': job_id, 'code': 2, 'error': f'no such command "{name}"'})
			return None

		# An instant command skips the runner entirely, because the runner is one-job-at-a-time
		# and a session control you cannot use during a run is a session control you cannot
		# use. It gets its own channel so it can never mis-tag the running job's events, and
		# it shares `hitl` by reference, which is the state it exists to change.
		if cmd.instant:
			sub = replace(ctx, channel=SocketChannel(send, job_id))
			await send({'type': 'started', 'job': job_id, 'name': name})
			code = await cmd.handler(sub, args)
			await send({'type': 'result', 'job': job_id, 'code': code, 'cancelled': False})
			return None

		work = cmd.handler(ctx, args)

	# Claimed only once the job is certainly starting. Setting it earlier meant a refused
	# command - unknown name, or "busy" during a run - retagged the *running* job's later
	# events with an id the UI never saw a `started` for, and its log went dark.
	try:
		job = jobs.start(job_id, name, work, ctx.stop)
	except RuntimeError as e:  # jobs.start() closed the coroutine it refused
		await send({'type': 'result', 'job': job_id, 'code': 2, 'error': str(e)})
		return None
	channel.job_id = job_id
	await send({'type': 'started', 'job': job_id, 'name': name})

	transcribe = name if line and 'chat' in frame else ''

	async def watch() -> None:
		error = ''
		cancelled = False
		code = 1
		try:
			code, cancelled = await jobs.wait(job)
		except Exception as e:
			# A handler that raises used to take this task down with it, so no `result` frame was
			# ever sent and the job sat at `working…` for good - no code, no error, no way to
			# tell. Every command reaches the UI through here, so the guard belongs here rather
			# than in whichever handler happened to throw.
			error = f'{type(e).__name__}: {e}'
			await send({'type': 'event', 'job': job_id, 'kind': 'log', 'text': f'💥 {error}', 'data': {}})
		if transcribe:
			from nkqa.shell.agent import record

			record(ctx, chats_mod.Turn(role='assistant', text='', command=transcribe, exit=code))
		result: dict[str, Any] = {'type': 'result', 'job': job_id, 'code': code, 'cancelled': cancelled}
		await send({**result, 'error': error} if error else result)

	return asyncio.create_task(watch())
