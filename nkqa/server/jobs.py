"""One job at a time, per process.

Not a limitation to route around: a run drives a real browser, holds the HITL prompts,
and stream.forward() swaps sys.stdout for its duration. Two at once in one process would
interleave all three.

The runners deliberately swallow CancelledError so partial evidence is saved, so a
cancelled job returns an exit code like any other. Cancellation is therefore tracked
here, not inferred from an exception.
"""

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from nkqa.stop import StopSignal


@dataclass
class Job:
	id: str
	name: str
	task: asyncio.Task[int]
	cancelled: bool = False
	stop: StopSignal | None = None


@dataclass
class JobRunner:
	current: Job | None = field(default=None)

	@property
	def busy(self) -> bool:
		return self.current is not None and not self.current.task.done()

	def start(self, job_id: str, name: str, coro: Coroutine[Any, Any, int], stop: StopSignal | None = None) -> Job:
		if self.busy:
			coro.close()
			raise RuntimeError(f'busy: "{self.current.name}" is still running')  # pyright: ignore[reportOptionalMemberAccess]
		if stop is not None:
			stop.arm()  # a previous run's stop must not kill this one
		job = Job(id=job_id, name=name, task=asyncio.create_task(coro), stop=stop)
		self.current = job
		return job

	def cancel(self, job_id: str) -> bool:
		"""Both halves, always. Cancelling the task alone lands wherever the run happens to
		be awaiting and leaves browser-use to decide what that means; the stop signal reaches
		the agent itself but is only polled at step boundaries. Neither is enough alone."""
		job = self.current
		if job is None or job.id != job_id or job.task.done():
			return False
		job.cancelled = True
		if job.stop is not None:
			job.stop.stop()
		job.task.cancel()
		return True

	async def wait(self, job: Job) -> tuple[int, bool]:
		"""(exit code, cancelled). A cancelled job that saved evidence still has a code."""
		try:
			return await job.task, job.cancelled
		except asyncio.CancelledError:
			return 1, True
		finally:
			if self.current is job:
				self.current = None
