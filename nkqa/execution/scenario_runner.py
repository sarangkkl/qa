"""Execute one approved scenario with structured per-step verdicts.

The runner is the enforcement point: draft, stale, or deprecated scenarios are
refused here, no matter how they were invoked.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
from datetime import datetime

from browser_use import Agent
from browser_use.browser import BrowserProfile

from nkqa.config import Config
from nkqa.execution.report import ScenarioResult, write_results
from nkqa.hitl import HumanInTheLoop
from nkqa.models import resolve_llm
from nkqa.prompts import QA_RULES
from nkqa.scenarios import Scenario
from nkqa.workspace import Workspace

REFUSALS = {
	'draft': 'is not approved yet. Review it, then:  qa approve {id}',
	'stale': 'was EDITED after approval - the approval is stale. Re-review, then:  qa approve {id}',
	'deprecated': 'is deprecated and will not run.',
}


def build_task(scenario: Scenario, base_url: str) -> str:
	lines = [
		f'Execute this approved QA test scenario against {base_url or "the application"}.',
		f'Scenario: {scenario.title}',
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
	scenario: Scenario,
	model_override: str | None = None,
) -> int:
	state = scenario.runnable()
	if state != 'ok':
		print(f'⛔ Scenario "{scenario.id}" {REFUSALS[state].format(id=scenario.id)}')
		return 2

	run_dir = ws.runs_dir / f'{scenario.id.replace("/", "-")}--{datetime.now():%Y%m%d-%H%M%S}'
	run_dir.mkdir(parents=True, exist_ok=True)
	print(f'▶️  Running scenario "{scenario.title}" ({len(scenario.steps)} steps). 🎬 Recording video.\n')

	agent: Agent[None, ScenarioResult] = Agent(
		task=build_task(scenario, config.base_url),
		llm=resolve_llm(config, 'executor', model_override),
		tools=hitl.build_tools(),
		extend_system_message=QA_RULES,
		sensitive_data=hitl.secrets,
		fallback_llm=resolve_llm(config, 'fallback'),
		browser_profile=BrowserProfile(headless=config.headless, record_video_dir=run_dir / 'videos'),
		generate_gif=str(run_dir / 'last_run.gif'),
		save_conversation_path=run_dir / 'conversation',
		calculate_cost=True,
		file_system_path=str(run_dir),
		output_model_schema=ScenarioResult,
	)

	async def checkpoint(active_agent: Agent[None, ScenarioResult]) -> None:
		with contextlib.suppress(Exception):
			active_agent.save_history(run_dir / 'history.json')

	result: ScenarioResult | None = None
	try:
		history = await agent.run(max_steps=config.max_steps, on_step_end=checkpoint)
		result = history.structured_output
	except (KeyboardInterrupt, asyncio.CancelledError):
		print('\n🛑 Run interrupted - partial evidence is kept; unreached steps count as blocked.')
	except Exception as e:
		print(f'\n💥 Run crashed ({type(e).__name__}: {e}) - evidence kept; result is blocked.')
	finally:
		if agent.history.history:
			agent.save_history(run_dir / 'history.json')

	verdict = write_results(run_dir, scenario, result)
	icon = {'pass': '✅', 'fail': '❌', 'blocked': '🚧'}[verdict]
	print(f'\n{icon} {scenario.id}: {verdict.upper()}')
	print(f'📄 Report:   {run_dir / "results.md"}')
	print(f'▶️  Replay:   qa replay {run_dir.name}')
	return 0 if verdict == 'pass' else 1
