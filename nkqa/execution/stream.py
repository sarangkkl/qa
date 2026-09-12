"""Make a browser run visible on a Channel.

browser-use narrates itself through `logging` and the occasional bare print. In a
terminal that narration *is* the show; on a socket it vanishes unless someone forwards
it. This module forwards it, and turns each finished agent step into a step Event
carrying the screenshot the UI needs.

For a TerminalChannel both are no-ops: browser-use already writes to that terminal, and
forwarding would double every line.

forward() swaps sys.stdout for the duration of a run, so it assumes one run at a time
per process - which is the sidecar's one-job-per-workspace rule anyway.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
import io
import logging
import shutil
import sys
from collections.abc import AsyncGenerator, Mapping
from pathlib import Path
from typing import Any, cast

from nkqa.ui import Channel, Event, TerminalChannel

BROWSER_USE_LOGGERS = ('browser_use', 'bubus', 'cdp_use')


class _QueueHandler(logging.Handler):
	def __init__(self, loop: asyncio.AbstractEventLoop, queue: 'asyncio.Queue[str | None]'):
		super().__init__()
		self.loop = loop
		self.queue = queue

	def emit(self, record: logging.LogRecord) -> None:
		with contextlib.suppress(Exception):
			self.loop.call_soon_threadsafe(self.queue.put_nowait, record.getMessage())


class _LineTee(io.TextIOBase):
	"""Collects writes and hands over whole lines; never writes to the real stdout."""

	def __init__(self, loop: asyncio.AbstractEventLoop, queue: 'asyncio.Queue[str | None]'):
		self.loop = loop
		self.queue = queue
		self.buffer_text = ''

	def write(self, s: str) -> int:
		self.buffer_text += s
		while '\n' in self.buffer_text:
			line, _, self.buffer_text = self.buffer_text.partition('\n')
			if line.strip():
				with contextlib.suppress(Exception):
					self.loop.call_soon_threadsafe(self.queue.put_nowait, line)
		return len(s)

	def flush(self) -> None:
		return None


def secret_values(secrets: Mapping[str, Any] | None) -> list[str]:
	"""Every credential value, flattened. browser-use also allows a per-domain nesting."""
	found: list[str] = []
	for value in (secrets or {}).values():
		if isinstance(value, str):
			found.append(value)
		elif isinstance(value, dict):
			found += [inner for inner in cast(dict[str, Any], value).values() if isinstance(inner, str)]
	return [v for v in found if v]


def redact(text: str, secrets: Mapping[str, Any] | None) -> str:
	"""Every live credential value replaced, for text that leaves the process any other way
	than the narration feed below - a page can echo a password back in its DOM or its URL."""
	for value in secret_values(secrets):
		text = text.replace(value, '[secret]')
	return text


async def _pump(ch: Channel, queue: 'asyncio.Queue[str | None]', secrets: Mapping[str, Any] | None) -> None:
	while True:
		line = await queue.get()
		if line is None:
			return
		# The <secret>key</secret> placeholder contract means a real credential should never be
		# in this narration. This is the backstop that makes "should never" into "cannot": these
		# lines are shown verbatim in the UI's terminal feed, where a leak is permanent and on
		# screen. Read live, not copied at entry - a credential collected mid-run counts too.
		if any(value in line for value in secret_values(secrets)):
			await ch.emit(Event('log', '[a line mentioning a credential was withheld]', {'source': 'browser-use'}))
			continue
		await ch.emit(Event('log', line, {'source': 'browser-use'}))


@contextlib.asynccontextmanager
async def forward(ch: Channel, secrets: Mapping[str, Any] | None = None) -> AsyncGenerator[None]:
	"""Route browser-use's own narration to the channel for the duration of a run."""
	if isinstance(ch, TerminalChannel):
		yield
		return

	loop = asyncio.get_running_loop()
	queue: asyncio.Queue[str | None] = asyncio.Queue()
	handler = _QueueHandler(loop, queue)
	loggers = [logging.getLogger(name) for name in BROWSER_USE_LOGGERS]
	for logger in loggers:
		logger.addHandler(handler)
	pump = asyncio.create_task(_pump(ch, queue, secrets))
	original_stdout = sys.stdout
	sys.stdout = _LineTee(loop, queue)  # type: ignore[assignment]
	try:
		yield
	finally:
		sys.stdout = original_stdout
		for logger in loggers:
			logger.removeHandler(handler)
		queue.put_nowait(None)
		with contextlib.suppress(Exception):
			await pump


STEP_SHOTS = 'steps'


def keep_shot(source: str, run_dir: Path, n: int) -> str:
	"""Copy a step screenshot into the run and return the URL the UI can fetch it from.

	browser-use writes step screenshots to a temp directory - verified on a real run:
	/var/folders/.../T/browser_use_agent_<id>/screenshots/step_1.png. That is outside the
	workspace, so `/artifacts` refuses to serve it, and it is a filesystem path rather than a
	URL anyway. The live pane put it straight into an <img src> and got a broken image on a
	black background, which is what "the live screen is not working" was.

	Copying also stops recorded evidence rotting: history.json references those same temp
	paths, and they do not survive a reboot.
	"""
	target = run_dir / STEP_SHOTS / f'step-{n:03d}{Path(source).suffix or ".png"}'
	target.parent.mkdir(parents=True, exist_ok=True)
	shutil.copyfile(source, target)
	# The shape `one_run()` already serves and `artifactUrl` already expects.
	return f'/artifacts/runs/{run_dir.name}/{STEP_SHOTS}/{target.name}'


def step_event(agent: Any, n: int, run_dir: Path | None = None) -> Event:
	"""The just-finished step, as the UI wants it: what it did, where, and a screenshot."""
	history = agent.history
	thoughts = history.model_thoughts()
	actions = history.action_names()
	shots = history.screenshot_paths()
	urls = history.urls()
	text = ''
	if thoughts:
		text = str(getattr(thoughts[-1], 'next_goal', '') or '').strip()
	if not text and actions:
		text = str(actions[-1])
	data: dict[str, Any] = {'n': n}
	if shots and shots[-1] and run_dir is not None:
		# Left out entirely when it cannot be copied, so the UI falls back to the step caption
		# rather than rendering a broken image it has no way to diagnose.
		with contextlib.suppress(OSError):
			data['screenshot'] = keep_shot(str(shots[-1]), run_dir, n)
	if urls and urls[-1]:
		data['url'] = str(urls[-1])
	if actions:
		data['action'] = str(actions[-1])
	return Event('step', text or f'step {n}', data)
