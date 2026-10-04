"""Drive the browser without a model: an external agent decides, nkqa executes and records.

This is what `kiwame mcp` runs a scenario with. browser-use still does the work - the same
`BrowserSession`, the same action registry, the same `<secret>key</secret>` substitution at
the DOM - but nothing here ever calls an LLM. Every action becomes a step in `steps.json`
with a screenshot, so a run driven from Claude Code leaves the same kind of evidence as one
driven by browser-use's own Agent.

Everything returned to the agent passes through `redact`: a page can echo a credential
back in its DOM, and the agent must never see one.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from browser_use import ActionResult, Tools
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.dom.views import DOMInteractedElement  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, Field

from nkqa.execution import checks
from nkqa.execution.evidence import STEP_LOG
from nkqa.execution.stream import STEP_SHOTS, redact

# The DOM text is capped like the chat context is: past this the agent should scroll, not read.
STATE_BUDGET = 40_000
MAX_WAIT = 30.0
# Typed as-is into a field, replaced by a per-run stamp: "QA project {{unique}}" can be created
# again on every replay without colliding with the last one. The recording keeps the token.
UNIQUE = '{{unique}}'


class StepRecord(BaseModel):
	n: int
	action: str
	params: dict[str, Any] = Field(default_factory=dict)
	url_before: str = ''
	url_after: str = ''
	title: str = ''
	result: str = ''
	error: str = ''
	screenshot: str = ''  # relative to the run dir, like `steps/step-003.png`
	at: str = ''
	# What an index pointed at, fingerprinted before acting: indices are only valid for one page
	# load, so this is how a replay finds the same element again.
	element: dict[str, Any] | None = None


class StepLog(BaseModel):
	driver: Literal['mcp'] = 'mcp'
	scenario_id: str = ''
	started_at: str = ''
	finished_at: str = ''
	aborted: bool = False
	replay: bool = False  # played back from a library recording, no model involved
	steps: list[StepRecord] = Field(default_factory=list)


def read_step_log(run_dir: Path) -> StepLog | None:
	f = run_dir / STEP_LOG
	if not f.is_file():
		return None
	try:
		return StepLog.model_validate_json(f.read_text(encoding='utf-8'))
	except ValueError:
		return None


def _now() -> str:
	return datetime.now().isoformat(timespec='seconds')


def with_unique(params: dict[str, Any], stamp: str) -> dict[str, Any]:
	return {k: v.replace(UNIQUE, stamp) if isinstance(v, str) else v for k, v in params.items()}


def describe(raw: Any) -> tuple[str, str]:
	"""(result, error) from whatever an action returned."""
	if isinstance(raw, ActionResult):
		return (raw.extracted_content or ('ok' if not raw.error else '')), (raw.error or '')
	if raw is None:
		return 'ok', ''
	return str(raw), ''


class Driver:
	"""One browser for one run. Actions are serialised; the agent calls one at a time anyway."""

	def __init__(
		self,
		run_dir: Path,
		secrets: dict[str, str | dict[str, str]],
		headless: bool,
		scenario_id: str = '',
		replay: bool = False,
	):
		self.run_dir = run_dir
		self.secrets = secrets  # the live dict `HumanInTheLoop` writes into; never copied
		self.headless = headless
		self.log = StepLog(scenario_id=scenario_id, started_at=_now(), replay=replay)
		self.unique = datetime.now().strftime('%m%d-%H%M%S')
		# Element indices come from the last state the agent saw. After any action they are
		# stale, and clicking a stale index is how a run clicks the wrong thing.
		self.fresh = False
		self.session: BrowserSession | None = None
		self.tools: Tools[None] | None = None
		self._lock = asyncio.Lock()

	async def start(self) -> None:
		self.run_dir.mkdir(parents=True, exist_ok=True)
		profile = BrowserProfile(headless=self.headless, record_video_dir=self.run_dir / 'videos', keep_alive=False)
		self.session = BrowserSession(browser_profile=profile)
		await self.session.start()
		self.tools = Tools()
		self._save()

	def _require(self) -> BrowserSession:
		if self.session is None:
			raise RuntimeError('the browser is not open')
		return self.session

	async def state(self, screenshot: bool = False) -> tuple[str, bytes | None]:
		session = self._require()
		async with self._lock:
			summary = await session.get_browser_state_summary(include_screenshot=False)
			lines = [f'URL: {summary.url}', f'Title: {summary.title}']
			if summary.tabs:
				lines.append('Tabs (* = current):')
				for tab in summary.tabs:
					mark = '*' if tab.url == summary.url else ' '
					lines.append(f'  {mark} [{tab.target_id[-4:]}] {tab.title or "(untitled)"} — {tab.url}')
			if summary.page_info:
				pi = summary.page_info
				size = f'Viewport {pi.viewport_width}x{pi.viewport_height}'
				lines.append(f'{size} · {pi.pixels_above}px above · {pi.pixels_below}px below')
			elements = summary.dom_state.llm_representation()
			if len(elements) > STATE_BUDGET:
				elements = elements[:STATE_BUDGET] + '\n… (truncated - scroll to see the rest)'
			lines += ['Interactive elements ([index]; * = new since the last state):', elements]
			if summary.browser_errors:
				lines.append('Browser errors: ' + '; '.join(summary.browser_errors))
			if summary.state_error:
				lines.append(f'State error: {summary.state_error}')
			shot: bytes | None = None
			n = len(self.log.steps) + 1
			saved = ''
			if screenshot:
				with contextlib.suppress(Exception):
					shot = await session.take_screenshot()
				if shot is not None:
					saved = self._keep(n, shot)
			self.fresh = True
			self._record(
				'state', {'screenshot': screenshot}, summary.url, summary.url, summary.title, 'state read', '', saved
			)
		return redact('\n'.join(lines), self.secrets), shot

	async def act(self, action: str, params: dict[str, Any]) -> str:
		session = self._require()
		assert self.tools is not None
		async with self._lock:
			url_before = await self._url()
			element = await self._fingerprint(params.get('index'))
			try:
				raw = await self.tools.registry.execute_action(
					action, with_unique(params, self.unique), browser_session=session, sensitive_data=self.secrets
				)
				result, error = describe(raw)
			except Exception as e:  # a failed click is a step that failed, not a crashed run
				result, error = '', f'{type(e).__name__}: {e}'
			url_after = await self._url()
			title = await self._title()
			self.fresh = False
			n = len(self.log.steps) + 1
			saved = ''
			with contextlib.suppress(Exception):
				saved = self._keep(n, await session.take_screenshot())
			self._record(action, params, url_before, url_after, title, result, error, saved, element)
		text = f'{result or error}\nNow at: {url_after} — {title}\nCall browser_state before the next click or type.'
		return redact(text, self.secrets)

	@property
	def failed(self) -> bool:
		"""Whether the last recorded step went wrong."""
		return bool(self.log.steps and self.log.steps[-1].error)

	async def check(
		self, step: int, kind: str, value: str, index: int | None = None, node: Any = None
	) -> tuple[bool, str]:
		"""Evaluate one assertion now and record it, so a replay can evaluate it again.

		The element kinds take an `index` from the last state (live) or an already-located
		`node` (replay); either way the element is fingerprinted into the record.
		"""
		if kind not in checks.KINDS:
			raise ValueError(f'Unknown check kind "{kind}". Use one of: {", ".join(checks.KINDS)}')
		session = self._require()
		async with self._lock:
			element: dict[str, Any] | None = None
			if kind in checks.ELEMENT_KINDS:
				if node is None and index is not None:
					node = (await session.get_selector_map()).get(index)
				if node is None and index is None:
					raise ValueError(f'{kind} needs the element index from browser_state.')
				if node is not None:
					element = self._clean_json(DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict())
			try:
				passed, seen = await checks.evaluate(session, kind, with_unique({'v': value}, self.unique)['v'], node)
			except Exception as e:  # a page that refuses to be read fails the check, not the run
				passed, seen = False, f'could not check: {type(e).__name__}: {e}'
			url = await self._url()
			self._record(
				'check',
				{'step': step, 'kind': kind, 'value': value},
				url,
				url,
				await self._title(),
				seen if passed else '',
				'' if passed else seen,
				'',
				element,
			)
		return passed, redact(seen, self.secrets)

	def checked_steps(self) -> set[int]:
		"""Scenario steps with at least one passing check."""
		return {int(r.params.get('step') or 0) for r in self.log.steps if r.action == 'check' and not r.error}

	async def wait(self, seconds: float) -> str:
		self._require()
		seconds = max(0.0, min(seconds, MAX_WAIT))
		await asyncio.sleep(seconds)
		url = await self._url()
		self._record('wait', {'seconds': seconds}, url, url, await self._title(), f'waited {seconds:g}s', '', '')
		return f'Waited {seconds:g}s. Call browser_state to see the page now.'

	async def tabs(self) -> str:
		session = self._require()
		found = await session.get_tabs()
		if not found:
			return 'No tabs open.'
		return redact(
			'\n'.join(f'[{t.target_id[-4:]}] {t.title or "(untitled)"} — {t.url}' for t in found), self.secrets
		)

	async def current_url(self) -> str:
		return await self._url()

	async def close(self, aborted: bool = False) -> None:
		"""Finalise the log first: the evidence must land even if the browser refuses to die."""
		self.log.finished_at = _now()
		self.log.aborted = aborted
		self._save()
		session, self.session = self.session, None
		if session is not None:
			with contextlib.suppress(Exception):
				await session.kill()  # BrowserStopEvent is what writes the video file

	# --- evidence ------------------------------------------------------------

	async def _url(self) -> str:
		with contextlib.suppress(Exception):
			return await self._require().get_current_page_url()
		return ''

	async def _title(self) -> str:
		with contextlib.suppress(Exception):
			return await self._require().get_current_page_title()
		return ''

	async def _fingerprint(self, index: Any) -> dict[str, Any] | None:
		"""The element an index points at in the state the agent last read - before acting, since
		a click can navigate away and take the element with it."""
		if not isinstance(index, int):
			return None
		with contextlib.suppress(Exception):
			node = (await self._require().get_selector_map()).get(index)
			if node is not None:
				return self._clean_json(DOMInteractedElement.load_from_enhanced_dom_tree(node).to_dict())
		return None

	def _clean_json(self, value: dict[str, Any]) -> dict[str, Any]:
		"""An input's attributes can carry what was typed into it: never record a secret."""
		return json.loads(redact(json.dumps(value, default=str), self.secrets))

	def _keep(self, n: int, png: bytes) -> str:
		target = self.run_dir / STEP_SHOTS / f'step-{n:03d}.png'
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_bytes(png)
		return f'{STEP_SHOTS}/{target.name}'

	def _record(
		self,
		action: str,
		params: dict[str, Any],
		before: str,
		after: str,
		title: str,
		result: str,
		error: str,
		shot: str,
		element: dict[str, Any] | None = None,
	) -> None:
		def clean(s: str) -> str:
			return redact(s, self.secrets)

		self.log.steps.append(
			StepRecord(
				n=len(self.log.steps) + 1,
				action=action,
				params=json.loads(clean(json.dumps(params))),
				url_before=clean(before),
				url_after=clean(after),
				title=clean(title),
				result=clean(result),
				error=clean(error),
				screenshot=shot,
				at=_now(),
				element=element,
			)
		)
		self._save()

	def _save(self) -> None:
		self.run_dir.mkdir(parents=True, exist_ok=True)
		(self.run_dir / STEP_LOG).write_text(self.log.model_dump_json(indent=1), encoding='utf-8')
