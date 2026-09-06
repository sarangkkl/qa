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
import sys
from collections.abc import AsyncGenerator
from typing import Any

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


async def _pump(ch: Channel, queue: 'asyncio.Queue[str | None]') -> None:
	while True:
		line = await queue.get()
		if line is None:
			return
		await ch.emit(Event('log', line, {'source': 'browser-use'}))


@contextlib.asynccontextmanager
async def forward(ch: Channel) -> AsyncGenerator[None]:
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
	pump = asyncio.create_task(_pump(ch, queue))
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


def step_event(agent: Any, n: int) -> Event:
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
	if shots and shots[-1]:
		data['screenshot'] = str(shots[-1])
	if urls and urls[-1]:
		data['url'] = str(urls[-1])
	if actions:
		data['action'] = str(actions[-1])
	return Event('step', text or f'step {n}', data)
