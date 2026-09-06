"""Execute one approved scenario with structured per-step verdicts.

The runner is the enforcement point: draft, stale, or deprecated scenarios are
refused here, no matter how they were invoked.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
from datetime import datetime
from pathlib import Path

from browser_use import Agent, Tools
from browser_use.browser import BrowserProfile

from nkqa import appmap
from nkqa.config import Config
from nkqa.execution import screencast, stream
from nkqa.execution.report import ScenarioResult, write_results
from nkqa.hitl import HumanInTheLoop
from nkqa.mcp import MCPRuntime, executor_servers
from nkqa.models import resolve_llm
from nkqa.prompts import QA_RULES
from nkqa.scenarios import Scenario
from nkqa.stop import StopSignal
from nkqa.ui import Channel
from nkqa.workspace import Workspace

REFUSALS = {
	'draft': 'is not approved yet. Review it, then:  qa approve {id}',
	'stale': 'was EDITED after approval - the approval is stale. Re-review, then:  qa approve {id}',
	'deprecated': 'is deprecated and will not run.',
}


def build_task(scenario: Scenario, base_url: str, app_context: str = '') -> str:
	lines = [
		f'Execute this approved QA test scenario against {base_url or "the application"}.',
		f'Scenario: {scenario.title}',
	]
	if app_context:
		lines += [
			'',
			'WHAT YOU ALREADY KNOW ABOUT THIS APP (reference, not steps to execute - but do',
			'follow the documented sign-in procedure if you are not already signed in):',
			app_context,
		]
	if scenario.preconditions:
		lines += ['', 'Preconditions (verify or establish these first):']
		lines += [f'- {p}' for p in scenario.preconditions]
	lines += ['', 'Steps (execute in order; verify every expectation):']
	for i, step in enumerate(scenario.steps, 1):
		lines.append(f'{i}. {step.action}' + (f' EXPECT: {step.expect}' if step.expect else ''))
	if scenario.out_of_scope:
		lines += ['', 'OUT OF SCOPE - do NOT do any of this:']
		lines += [f'- {item}' for item in scenario.out_of_scope]
	lines += [
		'',
		'When finished (or when you cannot continue), return the structured result with one',
		"verdict per step number: 'pass' if the action worked and the expectation held,",
		"'fail' if the app misbehaved (note MUST say expected vs actual), 'blocked' if you",
		'could not attempt it. A step you never reached is blocked, not failed.',
	]
	return '\n'.join(lines)


async def run_scenario(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	scenario: Scenario,
	model_override: str | None = None,
	stop: StopSignal | None = None,
) -> int:
	state = scenario.runnable()
	if state != 'ok':
		await ch.log(f'⛔ Scenario "{scenario.id}" {REFUSALS[state].format(id=scenario.id)}')
		return 2

	hitl.scenario_id = scenario.id  # vault grants can be scoped to one scenario
	run_dir = ws.runs_dir / f'{scenario.id.replace("/", "-")}--{datetime.now():%Y%m%d-%H%M%S}'
	run_dir.mkdir(parents=True, exist_ok=True)
	await ch.log(f'▶️  Running scenario "{scenario.title}" ({len(scenario.steps)} steps). 🎬 Recording video.\n')

	tools = hitl.build_tools()
	async with MCPRuntime(executor_servers(config)) as mcp_runtime:
		extra_tools = await mcp_runtime.register_executor_tools(tools)
		if extra_tools:
			await ch.log(f'🔧 Extra tools from MCP: {", ".join(extra_tools)}')
		return await _execute(ws, config, hitl, ch, scenario, run_dir, tools, model_override, stop or StopSignal())


async def _execute(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	scenario: Scenario,
	run_dir: 'Path',
	tools: 'Tools[None]',
	model_override: str | None,
	stop: StopSignal,
) -> int:
	agent: Agent[None, ScenarioResult] = Agent(
		task=build_task(scenario, config.base_url, appmap.context_for_run(ws)),
		llm=resolve_llm(config, 'executor', model_override),
		tools=tools,
		extend_system_message=QA_RULES,
		sensitive_data=hitl.secrets,
		fallback_llm=resolve_llm(config, 'fallback'),
		browser_profile=BrowserProfile(headless=config.headless, record_video_dir=run_dir / 'videos'),
		generate_gif=str(run_dir / 'last_run.gif'),
		save_conversation_path=run_dir / 'conversation',
		calculate_cost=True,
		file_system_path=str(run_dir),
		# nkqa owns SIGINT/SIGTERM in every surface; see nkqa/stop.py.
		enable_signal_handler=False,
		register_should_stop_callback=stop.should_stop,
		output_model_schema=ScenarioResult,
	)

	steps = 0

	async def checkpoint(active_agent: Agent[None, ScenarioResult]) -> None:
		nonlocal steps
		steps += 1
		with contextlib.suppress(Exception):
			active_agent.save_history(run_dir / 'history.json')
		with contextlib.suppress(Exception):
			await ch.emit(stream.step_event(active_agent, steps))

	stop.attach(agent)

	result: ScenarioResult | None = None
	cancelled = False
	try:
		async with stream.forward(ch), screencast.stream(agent, ch):
			history = await agent.run(max_steps=config.max_steps, on_step_end=checkpoint)
		result = history.structured_output
	except (KeyboardInterrupt, asyncio.CancelledError):
		cancelled = True
		await ch.log('\n🛑 Run interrupted - partial evidence is kept; unreached steps count as blocked.')
	except Exception as e:
		await ch.log(f'\n💥 Run crashed ({type(e).__name__}: {e}) - evidence kept; result is blocked.')
	finally:
		if agent.history.history:
			agent.save_history(run_dir / 'history.json')

	verdict = write_results(run_dir, scenario, result)
	icon = {'pass': '✅', 'fail': '❌', 'blocked': '🚧'}[verdict]
	await ch.verdict(
		f'\n{icon} {scenario.id}: {verdict.upper()}', scenario=scenario.id, verdict=verdict, run=run_dir.name
	)
	await ch.artifact(f'📄 Report:   {run_dir / "results.md"}', report=str(run_dir / 'results.md'))
	await ch.log(f'▶️  Replay:   qa replay {run_dir.name}')

	# A stopped run does not teach the appmap: reflection is a fresh LLM call, and half a
	# run is a misleading thing to learn from. write_results above still ran - the verdict
	# and the exit code are not optional.
	if not (cancelled or stop.stopped):
		from nkqa.reflector import auto_reflect

		await auto_reflect(ws, config, ch, run_dir)
	return 0 if verdict == 'pass' else 1
