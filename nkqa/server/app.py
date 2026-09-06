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
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from nkqa import chats as chats_mod
from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa.execution.evidence import recorded_runs, step_count
from nkqa.execution.report import last_verdict, read_results
from nkqa.hitl import HumanInTheLoop
from nkqa.server import auth
from nkqa.server.channel import SocketChannel
from nkqa.server.jobs import JobRunner
from nkqa.shell.commands import REGISTRY, ShellContext, parse_slash
from nkqa.vault import Vault
from nkqa.workspace import Workspace

MEDIA_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.webm', '.json', '.md', '.txt'}


def scenario_view(ws: Workspace, s: scenarios_mod.Scenario) -> dict[str, Any]:
	return {
		'id': s.id,
		'title': s.title,
		'state': s.runnable(),
		'ticket': s.ticket,
		'tags': s.tags,
		'approved_by': s.approved_by,
		'approved_at': s.approved_at,
		'last_verdict': last_verdict(ws.runs_dir, s.id),
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


def safe_artifact(ws: Workspace, relative: str) -> Path:
	"""Serve only what is actually inside the workspace, whatever the caller asks for."""
	target = (ws.root / relative).resolve()
	if not target.is_relative_to(ws.root.resolve()) or not target.is_file():
		raise HTTPException(status_code=404, detail='no such artifact')
	if target.suffix.lower() not in MEDIA_SUFFIXES:
		raise HTTPException(status_code=403, detail=f'{target.suffix} is not served')
	return target


def create_app(ws: Workspace) -> FastAPI:
	app = FastAPI(title='nkqa sidecar', docs_url=None, redoc_url=None)
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
	guard = [Depends(auth.require_token)]

	@app.get('/health', dependencies=guard)
	def health() -> dict[str, Any]:
		from importlib.metadata import version as pkg_version

		from nkqa.models import ROLES, describe_role

		cfg = config_mod.load(ws.config_file)
		roles: dict[str, dict[str, Any]] = {}
		for role in ROLES:
			name, provider, _keys, missing = describe_role(cfg, role)
			roles[role] = {'model': name, 'provider': provider, 'missing_keys': missing}
		return {
			'nkqa': pkg_version('nkqa'),
			'browser_use': pkg_version('browser-use'),
			'workspace': str(ws.root),
			'models_ok': all(not role_info['missing_keys'] for role_info in roles.values()),
			'roles': roles,
			'busy': app.state.jobs.busy,
		}

	@app.get('/workspace', dependencies=guard)
	def workspace_state() -> dict[str, Any]:
		from nkqa.models import CATALOGUE, PROVIDER_LABELS

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
				{'name': name, 'label': PROVIDER_LABELS.get(name, name), 'models': entries}
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
		return {**scenario_view(ws, s), 'body': s.path.read_text(encoding='utf-8')}

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
			'steps': step_count(run_dir / 'history.json') if (run_dir / 'history.json').is_file() else 0,
			'result': record.model_dump() if record else None,
			'artifacts': artifacts,
			'videos': [f'/artifacts/runs/{name}/videos/{v}' for v in videos],
		}

	@app.get('/chats', dependencies=guard)
	def all_chats() -> dict[str, Any]:
		return {'chats': chats_mod.list_chats(ws)}

	@app.get('/chats/{chat_id}', dependencies=guard)
	def one_chat(chat_id: str) -> dict[str, Any]:
		chat = chats_mod.load(ws, chat_id)
		if chat is None:
			raise HTTPException(status_code=404, detail=f'no chat "{chat_id}"')
		return asdict(chat)

	@app.get('/artifacts/{relative:path}', dependencies=guard)
	def artifact(relative: str) -> FileResponse:
		return FileResponse(safe_artifact(ws, relative))

	@app.websocket('/session')
	async def session(socket: WebSocket) -> None:
		if not auth.token_ok(socket.query_params.get('token')) or not auth.origin_ok(socket.headers.get('origin')):
			await socket.close(code=4401)
			return
		await socket.accept()
		await _serve(socket, ws, app.state.jobs)

	return app


async def _send(socket: WebSocket, frame: dict[str, Any]) -> None:
	await socket.send_text(json.dumps(frame, default=str))


def build_context(ws: Workspace, channel: SocketChannel) -> ShellContext:
	"""One session's state. Extracted so the vault wiring below is testable."""
	return ShellContext(
		ws=ws,
		config=config_mod.load(ws.config_file),
		# The vault is not optional here: without it `_from_vault` short-circuits on every
		# call and each stored credential falls through to "type it again", origin binding
		# included - which is how the whole release chain was dead over the socket.
		hitl=HumanInTheLoop(ws.permissions_file, channel, Vault(ws)),
		channel=channel,
	)


async def _serve(socket: WebSocket, ws: Workspace, jobs: JobRunner) -> None:
	async def send(frame: dict[str, Any]) -> None:
		await _send(socket, frame)

	channel = SocketChannel(send)
	ctx = build_context(ws, channel)
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
				task = await _launch(frame, ctx, channel, jobs, send, ws)
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
		ctx.hitl.secrets.clear()  # session secrets die with the session


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
) -> 'asyncio.Task[None] | None':
	job_id = str(frame.get('id', ''))
	line = ''

	if frame.get('type') == 'say':
		text = str(frame.get('text', '')).strip()
		if not text:
			await send({'type': 'result', 'job': job_id, 'code': 2, 'error': 'empty message'})
			return None
		await _attach_chat(ctx, ws, str(frame.get('chat', '')), send)
		from nkqa.shell.agent import route

		name, work = 'say', route(ctx, text)
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
		code, cancelled = await jobs.wait(job)
		if transcribe:
			from nkqa.shell.agent import record

			record(ctx, chats_mod.Turn(role='assistant', text='', command=transcribe, exit=code))
		await send({'type': 'result', 'job': job_id, 'code': code, 'cancelled': cancelled})

	return asyncio.create_task(watch())
