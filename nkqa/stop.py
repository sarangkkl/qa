"""Stopping a run, and meaning it.

Cancelling the asyncio task is not enough on its own: the task is almost always parked
inside `Agent.run()`, and browser-use decides for itself what to do with a `CancelledError`.
So a stop is two things fired together - tell the agent to stop *and* cancel the task:

- `StopSignal.should_stop` is browser-use's own embedding hook. It is polled at step
  boundaries only, never during an LLM call, so on its own it can take up to a minute.
- `task.cancel()` unwinds the current await immediately, but lands wherever it lands.

Neither is sufficient; together they are. `stop()` also switches the summary GIF off, because
browser-use encodes it synchronously in `run()`'s `finally` - on the cancel path too - and
that encode is one of the reasons stopping used to feel like it did nothing.

This module deliberately imports nothing from browser-use: it is pulled in by CLI-adjacent
code where import cost is startup cost, and the agent is typed loosely for the same reason.
"""

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Coroutine
from typing import Any


class StopSignal:
	"""One run's stop state. Lives on ShellContext, so it is per-session like the HITL."""

	def __init__(self) -> None:
		self._stopped = False
		self._agents: list[Any] = []
		self.reason = ''

	@property
	def stopped(self) -> bool:
		return self._stopped

	def arm(self) -> None:
		"""A new job starts clean: the previous run's stop must not kill this one."""
		self._stopped = False
		self._agents = []
		self.reason = ''

	def attach(self, agent: Any) -> None:
		"""The runner hands over its Agent, which is otherwise a local nothing can reach."""
		self._agents.append(agent)
		if self._stopped:
			self._halt(agent)  # stopped between construction and attach: honour it anyway

	def stop(self, reason: str = 'stopped by you') -> None:
		self._stopped = True
		self.reason = reason
		for agent in self._agents:
			self._halt(agent)

	@staticmethod
	def _halt(agent: Any) -> None:
		# Best effort by design: this reaches into browser-use, and a stop that raises would
		# be worse than a stop that is merely slower.
		with contextlib.suppress(Exception):
			agent.settings.generate_gif = False  # skip the synchronous encode in run()'s finally
		with contextlib.suppress(Exception):
			agent.stop()

	async def should_stop(self) -> bool:
		"""Passed to browser-use as `register_should_stop_callback`."""
		return self._stopped


async def interruptible(coro: Coroutine[Any, Any, int], stop: StopSignal | None = None) -> int:
	"""Run a command with a Ctrl+C that works.

	browser-use installs its own SIGINT handler unless told not to (`enable_signal_handler`),
	and its handler pauses the agent rather than stopping it - a pause nothing ever resumes.
	Every Agent nkqa builds now disables that, which puts SIGINT back in our hands and makes
	this the one place that defines what Ctrl+C means:

	first  - stop the run, keep the evidence
	second - leave now

	The second exit is code 1, not 130: CI is pinned on 0/1/2.
	"""
	task = asyncio.ensure_future(coro)
	loop = asyncio.get_running_loop()
	hits = 0

	def interrupt() -> None:
		nonlocal hits
		hits += 1
		if hits == 1:
			print('\n⏹  stopping - saving evidence. Ctrl+C again to quit now.', file=sys.stderr, flush=True)
			if stop is not None:
				stop.stop()
			task.cancel()
			return
		print('\n⏹  quitting.', file=sys.stderr, flush=True)
		os._exit(1)

	installed = False
	with contextlib.suppress(NotImplementedError, RuntimeError):  # e.g. a non-main thread
		loop.add_signal_handler(signal.SIGINT, interrupt)
		installed = True
	try:
		return await task
	except asyncio.CancelledError:
		return 1
	except KeyboardInterrupt:  # the handler could not be installed; behave the same way
		if stop is not None:
			stop.stop()
		return 1
	finally:
		if installed:
			with contextlib.suppress(Exception):
				loop.remove_signal_handler(signal.SIGINT)
