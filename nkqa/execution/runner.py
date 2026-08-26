"""Freeform AI-driven recording - the prototype's record flow.

Phase 2 turns `qa run` into scenario execution; this becomes `qa explore`.
"""

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
from nkqa.hitl import HumanInTheLoop
from nkqa.models import resolve_llm
from nkqa.prompts import QA_RULES
from nkqa.workspace import Workspace, slugify


async def greet(ws: Workspace, url_arg: str, focus_arg: str, name_arg: str) -> tuple[str, str, str]:
	"""Greet the developer like a colleague and collect the job details."""
	print()
	print('👋 Hey! QA here. Ready when you are.')
	url = url_arg
	while not url:
		url = (await asyncio.to_thread(input, '   Which app should I test today? (paste the link): ')).strip()
	if not url.startswith(('http://', 'https://')):
		url = 'https://' + url
	focus = (
		focus_arg
		or (await asyncio.to_thread(input, '   Anything specific to focus on? (Enter = full smoke test): ')).strip()
	)
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
		typed = (await asyncio.to_thread(input, f'   Name this test (Enter = "{suggestion}"): ')).strip()
		name = slugify(typed) if typed else suggestion
		if (ws.run_dir(name) / 'history.json').exists():
			answer = (
				(await asyncio.to_thread(input, f'   Test "{name}" already exists. Overwrite it? [y/N]: '))
				.strip()
				.lower()
			)
			if answer != 'y':
				name = ''  # ask again

	print(f'   Got it - test "{name}" on {url}, focus: {focus}. I\'ll ask if I need anything. 🎬 Recording video.\n')
	return url, focus, name


async def run_freeform(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	url_arg: str,
	focus_arg: str,
	name_arg: str,
	model_override: str | None = None,
) -> int:
	url, focus, name = await greet(ws, url_arg, focus_arg, name_arg)
	run_dir = ws.run_dir(name)
	run_dir.mkdir(parents=True, exist_ok=True)

	from nkqa.mcp import MCPRuntime, executor_servers

	tools = hitl.build_tools()
	async with MCPRuntime(executor_servers(config)) as mcp_runtime:
		extra_tools = await mcp_runtime.register_executor_tools(tools)
		if extra_tools:
			print(f'🔧 Extra tools from MCP: {", ".join(extra_tools)}')
		return await _record(ws, config, hitl, url, focus, name, run_dir, tools, model_override)


async def _record(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	url: str,
	focus: str,
	name: str,
	run_dir: 'Path',
	tools: 'Tools[None]',
	model_override: str | None,
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
	)

	async def checkpoint(active_agent: Agent[None, BaseModel]) -> None:
		"""Save the recording after every step, so even a hard kill (double Ctrl+C) loses nothing."""
		with contextlib.suppress(Exception):
			active_agent.save_history(run_dir / 'history.json')

	run_error: BaseException | None = None
	try:
		history = await agent.run(max_steps=config.max_steps, on_step_end=checkpoint)
		print('\n=== FINAL RESULT ===')
		print(history.final_result())
	except (KeyboardInterrupt, asyncio.CancelledError) as e:
		run_error = e
		print('\n🛑 Run interrupted - saving the partial recording so nothing is lost...')
	except Exception as e:
		run_error = e
		print(f'\n💥 Run crashed ({type(e).__name__}: {e}) - saving the partial recording...')
	finally:
		# Even a dirty exit keeps its artifacts: partial history, gif, transcript, video.
		if agent.history.history:
			agent.save_history(run_dir / 'history.json')  # structured record, secrets redacted
			if not (run_dir / 'last_run.gif').exists():
				try:
					from browser_use.agent.gif import create_history_gif

					create_history_gif(
						task=agent.task, history=agent.history, output_path=str(run_dir / 'last_run.gif')
					)
				except Exception:
					pass  # gif needs at least one screenshot; skip quietly
			print(f'\n📼 Transcript: {run_dir / "conversation"}/')
			print(f'📄 Recording:  {run_dir / "history.json"}')
			print(f'▶️  Replay it anytime WITHOUT the LLM:  qa replay {name}')
			print('🎬 The video file is finalized only now - open it AFTER this message, not mid-run.')
		else:
			print('\nNo step completed, so there is nothing to save for this test.')

	if run_error is None:
		try:
			from browser_use.agent.variable_detector import detect_variables_in_history

			detected = detect_variables_in_history(agent.history)
			if detected:
				print('🔁 Values you can change on replay:')
				for var_name, var in detected.items():
					print(f'   --var {var_name}=...   (recorded: {var.original_value[:40]})')
		except Exception:
			pass
		return 0
	if isinstance(run_error, KeyboardInterrupt):
		return 0
	return 1
