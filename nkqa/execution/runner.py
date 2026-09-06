"""Freeform AI-driven recording - `qa explore`."""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
from pathlib import Path
from urllib.parse import urlparse

from browser_use import Agent, Tools
from browser_use.browser import BrowserProfile
from pydantic import BaseModel

from nkqa.config import Config
from nkqa.execution import screencast, stream
from nkqa.hitl import HumanInTheLoop
from nkqa.models import resolve_llm
from nkqa.prompts import QA_RULES
from nkqa.stop import StopSignal
from nkqa.ui import Channel
from nkqa.workspace import Workspace, slugify


async def greet(ws: Workspace, ch: Channel, url_arg: str, focus_arg: str, name_arg: str) -> tuple[str, str, str]:
	"""Greet the developer like a colleague and collect the job details."""
	await ch.log()
	await ch.log('👋 Hey! QA here. Ready when you are.')
	url = url_arg
	while not url:
		url = await ch.ask_text('   Which app should I test today? (paste the link): ')
	if not url.startswith(('http://', 'https://')):
		url = 'https://' + url
	focus = focus_arg or await ch.ask_text('   Anything specific to focus on? (Enter = full smoke test): ')
	focus = focus or 'all main user flows'

	name = slugify(name_arg) if name_arg else ''
	while not name:
		parsed = urlparse(url)
		# suggest from the first few words of the focus, so long sentences don't become ugly slugs
		suggestion = (
			slugify(' '.join(focus.split()[:4]))
			if focus != 'all main user flows'
			else slugify(f'{parsed.hostname or "site"} {parsed.path} smoke')
		)
		typed = await ch.ask_text(f'   Name this test (Enter = "{suggestion}"): ')
		name = slugify(typed) if typed else suggestion
		exists = (ws.run_dir(name) / 'history.json').exists()
		if exists and not await ch.confirm(f'   Test "{name}" already exists. Overwrite it? [y/N]: '):
			name = ''  # ask again

	await ch.log(
		f'   Got it - test "{name}" on {url}, focus: {focus}. I\'ll ask if I need anything. 🎬 Recording video.\n'
	)
	return url, focus, name


async def run_freeform(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	url_arg: str,
	focus_arg: str,
	name_arg: str,
	model_override: str | None = None,
	stop: StopSignal | None = None,
) -> int:
	stop = stop or StopSignal()
	url, focus, name = await greet(ws, ch, url_arg, focus_arg, name_arg)
	run_dir = ws.run_dir(name)
	run_dir.mkdir(parents=True, exist_ok=True)

	from nkqa.mcp import MCPRuntime, executor_servers

	tools = hitl.build_tools()
	async with MCPRuntime(executor_servers(config)) as mcp_runtime:
		extra_tools = await mcp_runtime.register_executor_tools(tools)
		if extra_tools:
			await ch.log(f'🔧 Extra tools from MCP: {", ".join(extra_tools)}')
		return await _record(ws, config, hitl, ch, url, focus, name, run_dir, tools, model_override, stop)


async def _record(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	url: str,
	focus: str,
	name: str,
	run_dir: 'Path',
	tools: 'Tools[None]',
	model_override: str | None,
	stop: StopSignal,
) -> int:
	agent: Agent[None, BaseModel] = Agent(
		task=f'Test the web application at {url}. Focus on: {focus}.',
		llm=resolve_llm(config, 'executor', model_override),
		tools=tools,
		extend_system_message=QA_RULES,
		sensitive_data=hitl.secrets,  # same dict ask_credential writes into
		fallback_llm=resolve_llm(config, 'fallback'),  # cross-provider failover
		browser_profile=BrowserProfile(
			headless=config.headless,
			record_video_dir=run_dir / 'videos',  # full session .mp4
		),
		generate_gif=str(run_dir / 'last_run.gif'),  # step-by-step summary gif
		save_conversation_path=run_dir / 'conversation',  # full LLM transcript, one file per step
		calculate_cost=True,
		file_system_path=str(run_dir),
		# nkqa owns SIGINT/SIGTERM in every surface: browser-use's own handler pauses the
		# agent instead of stopping it, and nothing ever resumes that pause.
		enable_signal_handler=False,
		register_should_stop_callback=stop.should_stop,
	)
	stop.attach(agent)

	steps = 0

	async def checkpoint(active_agent: Agent[None, BaseModel]) -> None:
		"""Save the recording after every step, so even a hard kill (double Ctrl+C) loses nothing."""
		nonlocal steps
		steps += 1
		with contextlib.suppress(Exception):
			active_agent.save_history(run_dir / 'history.json')
		with contextlib.suppress(Exception):
			await ch.emit(stream.step_event(active_agent, steps))

	run_error: BaseException | None = None
	try:
		async with stream.forward(ch), screencast.stream(agent, ch):
			history = await agent.run(max_steps=config.max_steps, on_step_end=checkpoint)
		await ch.log('\n=== FINAL RESULT ===')
		await ch.log(str(history.final_result()))
	except (KeyboardInterrupt, asyncio.CancelledError) as e:
		run_error = e
		await ch.log('\n🛑 Run interrupted - saving the partial recording so nothing is lost...')
	except Exception as e:
		run_error = e
		await ch.log(f'\n💥 Run crashed ({type(e).__name__}: {e}) - saving the partial recording...')
	finally:
		# A stopped run skips everything expensive: the gif encode is synchronous and the
		# reflection below is a whole new LLM call. Both used to run *after* you pressed
		# Stop, which is most of why stopping felt like it did nothing.
		cancelled = isinstance(run_error, (asyncio.CancelledError, KeyboardInterrupt)) or stop.stopped
		# Even a dirty exit keeps its artifacts: partial history, transcript, video.
		if agent.history.history:
			agent.save_history(run_dir / 'history.json')  # structured record, secrets redacted
			if not cancelled and not (run_dir / 'last_run.gif').exists():
				try:
					from browser_use.agent.gif import create_history_gif

					create_history_gif(
						task=agent.task, history=agent.history, output_path=str(run_dir / 'last_run.gif')
					)
				except Exception:
					pass  # gif needs at least one screenshot; skip quietly
			await ch.artifact(
				f'\n📼 Transcript: {run_dir / "conversation"}/', conversation=str(run_dir / 'conversation')
			)
			await ch.artifact(f'📄 Recording:  {run_dir / "history.json"}', history=str(run_dir / 'history.json'))
			await ch.log(f'▶️  Replay it anytime WITHOUT the LLM:  qa replay {name}')
			await ch.log('🎬 The video file is finalized only now - open it AFTER this message, not mid-run.')
		else:
			await ch.log('\nNo step completed, so there is nothing to save for this test.')

	if agent.history.history and not cancelled:
		from nkqa.reflector import auto_reflect

		await auto_reflect(ws, config, ch, run_dir)

	if run_error is None:
		try:
			from browser_use.agent.variable_detector import detect_variables_in_history

			detected = detect_variables_in_history(agent.history)
			if detected:
				await ch.log('🔁 Values you can change on replay:')
				for var_name, var in detected.items():
					await ch.log(f'   --var {var_name}=...   (recorded: {var.original_value[:40]})')
		except Exception:
			pass
		return 0
	if isinstance(run_error, KeyboardInterrupt):
		return 0
	return 1
