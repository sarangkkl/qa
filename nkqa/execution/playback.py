"""Replay a library recording with no model: the regression run.

A recording is what Claude did in a run that passed - its actions, the element each one hit,
and the checks that proved each expectation. Playback repeats the actions, finds each element
again by its fingerprint (never by its old index), and re-evaluates every check. It stops at
the first thing that does not hold, because what comes after a failure is not evidence of
anything. It never calls a model: a failure is for a human to look at.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field

from nkqa.execution import checks, screencast
from nkqa.execution.driver import Driver, StepRecord
from nkqa.execution.report import ScenarioResult, StepVerdict, write_results
from nkqa.hitl import HumanInTheLoop
from nkqa.scenarios import Scenario
from nkqa.ui import Channel
from nkqa.vault import normalize

WAIT_FOR = 10.0  # an element or a check gets this long to appear: SPAs render after load
POLL = 0.5
REPLAYED = ('navigate', 'click', 'input', 'scroll', 'send_keys', 'go_back', 'switch', 'close', 'wait', 'check')
SECRET = re.compile(r'<secret>(.*?)</secret>')
EVIDENCE = [
	'- [Step log (steps.json)](steps.json)',
	'- [Screenshots](steps/)',
	'- [Videos](videos/)',
]


class Recording(BaseModel):
	"""A library test: what to repeat, and the approval it was recorded against."""

	scenario_id: str
	approved_hash: str
	base_url: str = ''  # what navigate URLs were recorded under, so they can move with it
	recorded_from: str = ''  # the run it came from
	saved_at: str = ''
	steps: list[StepRecord] = Field(default_factory=list)


def from_run(steps: list[StepRecord]) -> list[StepRecord]:
	"""What is worth repeating: actions and checks that worked. State reads are Claude looking,
	and a step that errored was one Claude recovered from - replaying it would fail for nothing."""
	return [s for s in steps if s.action in REPLAYED and not s.error]


def owners(steps: list[StepRecord], last: int) -> list[int]:
	"""Which scenario step each recorded step belongs to: the step of the next check at or after
	it - the actions before a check are the ones that set it up. Trailing actions: the last step."""
	out = [last] * len(steps)
	owner = last
	for i in range(len(steps) - 1, -1, -1):
		if steps[i].action == 'check':
			owner = int(steps[i].params.get('step') or owner)
		out[i] = owner
	return out


def rebase(url: str, recorded: str, current: str) -> str:
	if recorded and current and url.startswith(recorded.rstrip('/')):
		return current.rstrip('/') + url[len(recorded.rstrip('/')) :]
	return url


class Playback:
	def __init__(self, driver: Driver, hitl: HumanInTheLoop, ch: Channel, recording: Recording, base_url: str):
		self.driver, self.hitl, self.ch = driver, hitl, ch
		self.recording, self.base_url = recording, base_url

	async def _fresh_map(self) -> dict[int, Any]:
		session = self.driver._require()  # pyright: ignore[reportPrivateUsage]
		summary = await session.get_browser_state_summary(include_screenshot=False)
		return dict(summary.dom_state.selector_map)

	async def _find(self, fingerprint: dict[str, Any] | None) -> tuple[int | None, Any, str]:
		"""(index, node, how) - polled, because the page may still be rendering."""
		if not fingerprint:
			return None, None, ''
		deadline = time.monotonic() + WAIT_FOR
		while True:
			selector_map = await self._fresh_map()
			index, how = checks.locate(fingerprint, selector_map)
			if index is not None or time.monotonic() > deadline:
				return index, selector_map.get(index) if index is not None else None, how
			await asyncio.sleep(POLL)

	async def _secrets(self, params: dict[str, Any]) -> str:
		"""Release what a typed placeholder needs, for this page - the same gate a live run uses."""
		for name in SECRET.findall(str(params.get('text', ''))):
			decision = await self.hitl.release_credential(name, await self.driver.current_url())
			if not self.hitl.has_secret(normalize(name)):
				return f'credential "{name}" was not released ({decision.content.splitlines()[0][:120]})'
		return ''

	async def step(self, rec: StepRecord) -> str:
		"""Repeat one recorded step. '' when it held, otherwise what went wrong."""
		params = dict(rec.params)
		if rec.action == 'check':
			return await self._check(rec)
		if rec.action == 'wait':
			await self.driver.wait(float(params.get('seconds') or 0))
			return ''
		if rec.action == 'navigate':
			params['url'] = rebase(str(params.get('url', '')), self.recording.base_url, self.base_url)
		if rec.action in ('switch', 'close'):
			return await self._tab(rec)
		if 'index' in params and params['index'] is not None:
			index, _node, _how = await self._find(rec.element)
			if index is None:
				tried = 'hash, stable hash, xpath, name, attributes'
				return f'could not find {checks.describe_element(rec.element)} (tried {tried})'
			params['index'] = index
		if rec.action == 'input':
			problem = await self._secrets(params)
			if problem:
				return problem
		await self.driver.act(rec.action, params)
		return self.driver.log.steps[-1].error if self.driver.failed else ''

	async def _check(self, rec: StepRecord) -> str:
		step, kind, value = int(rec.params.get('step') or 0), str(rec.params['kind']), str(rec.params.get('value', ''))
		session = self.driver._require()  # pyright: ignore[reportPrivateUsage]
		node: Any = None
		deadline = time.monotonic() + WAIT_FOR
		while True:  # the page may still be getting there
			if kind in checks.ELEMENT_KINDS:
				_index, node, _how = await self._find(rec.element)
			passed, _seen = await checks.evaluate(session, kind, value, node)
			if passed or time.monotonic() > deadline:
				break
			await asyncio.sleep(POLL)
		passed, seen = await self.driver.check(step, kind, value, node=node)
		return '' if passed else f'check failed ({kind} "{value}"): {seen}'

	async def _tab(self, rec: StepRecord) -> str:
		"""Tab ids differ every run: find the tab by the URL it had when this was recorded."""
		session = self.driver._require()  # pyright: ignore[reportPrivateUsage]
		wanted = rec.url_after if rec.action == 'switch' else rec.url_before
		for tab in await session.get_tabs():
			if tab.url == wanted:
				await self.driver.act(rec.action, {'tab_id': tab.target_id[-4:]})
				return self.driver.log.steps[-1].error if self.driver.failed else ''
		return f'no open tab at {wanted}'


def verdicts(scenario: Scenario, failed_at: int | None, reason: str, owner_of: list[int]) -> list[StepVerdict]:
	"""Every scenario step gets one: passed before the failure, failed where it broke, not run after."""
	if failed_at is None:
		return [StepVerdict(step=i, verdict='pass', note='replayed') for i in range(1, len(scenario.steps) + 1)]
	broken = owner_of[failed_at]
	out: list[StepVerdict] = []
	for i in range(1, len(scenario.steps) + 1):
		if i < broken:
			out.append(StepVerdict(step=i, verdict='pass', note='replayed'))
		elif i == broken:
			out.append(StepVerdict(step=i, verdict='fail', note=reason))
		else:
			out.append(StepVerdict(step=i, verdict='blocked', note=f'not run: the replay stopped at step {broken}'))
	return out


async def play(
	scenario: Scenario,
	recording: Recording,
	hitl: HumanInTheLoop,
	ch: Channel,
	run_dir: Path,
	base_url: str,
	headless: bool,
) -> str:
	"""Replay one recording into `run_dir`. Returns the verdict: pass, fail or blocked."""
	driver = Driver(run_dir, hitl.secrets, headless, scenario.id, replay=True)
	hitl.scenario_id = scenario.id
	steps = recording.steps
	owner_of = owners(steps, len(scenario.steps))
	failed_at: int | None = None
	reason = ''
	playback = Playback(driver, hitl, ch, recording, base_url)
	await ch.log(f'\n▶️  Replaying {scenario.id} — {len(steps)} recorded steps, no model')
	try:
		await driver.start()
		# The same live view a Claude run gets: frames in the window, and a filmstrip of steps.
		async with screencast.stream(SimpleNamespace(browser_session=driver.session), ch):
			for i, rec in enumerate(steps):
				try:
					problem = await playback.step(rec)
				except Exception as e:  # a step that crashes is a step that failed, not a crashed suite
					problem = f'{type(e).__name__}: {e}'
				label = f'{rec.action} {checks.describe_element(rec.element) if rec.element else ""}'.strip()
				shot = driver.log.steps[-1].screenshot if driver.log.steps else ''
				data: dict[str, Any] = {'n': i + 1, 'ok': not problem}
				if shot:
					data['screenshot'] = f'/artifacts/runs/{run_dir.name}/{shot}'
				await ch.step(f'step {owner_of[i]} · {label}: {"❌ " + problem if problem else "✅"}', **data)
				if problem:
					failed_at, reason = i, problem
					break
	finally:
		await driver.close(aborted=failed_at is not None)
	summary = f'Replayed without a model. {"Stopped: " + reason if reason else "Every recorded step and check held."}'
	verdict = write_results(
		run_dir,
		scenario,
		ScenarioResult(steps=verdicts(scenario, failed_at, reason, owner_of), summary=summary),
		EVIDENCE,
	)
	await ch.verdict(f'{scenario.id}: {verdict.upper()}', run=run_dir.name, verdict=verdict)
	return verdict
