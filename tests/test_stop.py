"""Stopping a run: the signal that reaches the agent, and the Ctrl+C that works."""

import asyncio
from typing import Any

from nkqa.stop import StopSignal, interruptible


class FakeSettings:
	def __init__(self) -> None:
		self.generate_gif: str | bool = '/tmp/last_run.gif'


class FakeAgent:
	"""Stands in for browser_use.Agent: only the two things StopSignal touches."""

	def __init__(self) -> None:
		self.settings = FakeSettings()
		self.stopped = False

	def stop(self) -> None:
		self.stopped = True


def test_stop_reaches_the_agent_and_kills_the_gif() -> None:
	signal = StopSignal()
	agent = FakeAgent()
	signal.attach(agent)

	assert asyncio.run(signal.should_stop()) is False
	signal.stop()

	assert agent.stopped, 'the agent itself must be told, not just the task cancelled'
	# browser-use encodes the gif synchronously in run()'s finally - on the cancel path too -
	# so switching it off here is what stops a cancel taking seconds.
	assert agent.settings.generate_gif is False
	assert signal.stopped and asyncio.run(signal.should_stop()) is True


def test_attaching_after_a_stop_still_halts() -> None:
	"""The window between constructing an Agent and attaching it is small but real."""
	signal = StopSignal()
	signal.stop()
	agent = FakeAgent()
	signal.attach(agent)
	assert agent.stopped


def test_arm_clears_the_previous_run() -> None:
	"""Otherwise one stopped run would poison every job after it in the same session."""
	signal = StopSignal()
	signal.attach(FakeAgent())
	signal.stop()

	signal.arm()
	assert not signal.stopped and signal.reason == ''

	fresh = FakeAgent()
	signal.attach(fresh)
	assert not fresh.stopped


def test_a_stop_is_best_effort_and_never_raises() -> None:
	"""A stop that throws would be worse than a stop that is merely slower."""

	class Awkward:
		@property
		def settings(self) -> Any:
			raise RuntimeError('no settings here')

		def stop(self) -> None:
			raise RuntimeError('nor this')

	signal = StopSignal()
	signal.attach(Awkward())
	signal.stop()  # must not raise
	assert signal.stopped


def test_interruptible_returns_one_when_cancelled() -> None:
	async def scenario() -> int:
		signal = StopSignal()

		async def forever() -> int:
			await asyncio.sleep(30)
			return 0

		task = asyncio.ensure_future(interruptible(forever(), signal))
		await asyncio.sleep(0)
		task.cancel()
		return await task

	assert asyncio.run(scenario()) == 1


def test_interruptible_passes_a_normal_result_through() -> None:
	async def fine() -> int:
		return 0

	assert asyncio.run(interruptible(fine())) == 0
