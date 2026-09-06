"""What a stopped run does, and - more importantly - what it does not do.

Pressing Stop used to fire a whole new LLM call (`auto_reflect`) and a synchronous gif
encode *after* the cancel, which is most of why stopping felt like it did nothing. These
pin the shape: evidence and the verdict still land, the expensive tail does not run.
"""

import asyncio
import contextlib
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import FakeChannel

from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.config import Config
from nkqa.execution import scenario_runner
from nkqa.hitl import HumanInTheLoop
from nkqa.stop import StopSignal
from nkqa.workspace import Workspace


class FakeHistory:
	# Non-empty: "some steps ran", which is what gates the post-run tail.
	history: ClassVar[list[object]] = [object()]
	structured_output = None


class FakeSettings:
	def __init__(self) -> None:
		self.generate_gif: str | bool = 'x.gif'


class CancellingAgent:
	"""An Agent whose run() is interrupted, like a real one being cancelled."""

	# The runner annotates a nested function `Agent[None, ScenarioResult]`, and that
	# annotation is evaluated at definition time, so the stand-in has to be subscriptable.
	def __class_getitem__(cls, _item: Any) -> type['CancellingAgent']:
		return cls

	def __init__(self, *_: Any, **kwargs: Any) -> None:
		self.settings = FakeSettings()
		self.history = FakeHistory()
		self.kwargs = kwargs
		self.saved = False

	async def run(self, **_: Any) -> Any:
		raise asyncio.CancelledError

	def save_history(self, path: Path) -> None:
		self.saved = True
		path.write_text('{}')

	def stop(self) -> None:
		return None


class NoMCP:
	def __init__(self, *_: Any) -> None: ...
	async def __aenter__(self) -> 'NoMCP':
		return self

	async def __aexit__(self, *_: Any) -> None: ...
	async def register_executor_tools(self, _tools: Any) -> list[str]:
		return []


@pytest.fixture
def approved(tmp_path: Path) -> tuple[Workspace, scenarios_mod.Scenario]:
	ws = workspace_mod.create(tmp_path)
	s = scenarios_mod.Scenario(
		id='auth/login',
		path=ws.scenarios_dir / 'auth' / 'login.md',
		title='Login works',
		steps=[scenarios_mod.Step('Open the login page.', 'the form shows')],
	)
	scenarios_mod.save(s)
	scenarios_mod.approve(s, 'tester')
	scenarios_mod.save(s)
	return ws, scenarios_mod.parse(s.path, ws.scenarios_dir)


def _no_servers(_config: Config) -> list[Any]:
	return []


def _no_llm(*_args: Any, **_kwargs: Any) -> None:
	return None


def _null(*_args: Any) -> AbstractContextManager[None]:
	return contextlib.nullcontext()


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(scenario_runner, 'Agent', CancellingAgent)
	monkeypatch.setattr(scenario_runner, 'MCPRuntime', NoMCP)
	monkeypatch.setattr(scenario_runner, 'executor_servers', _no_servers)
	monkeypatch.setattr(scenario_runner, 'resolve_llm', _no_llm)
	monkeypatch.setattr(scenario_runner.screencast, 'stream', _null)
	monkeypatch.setattr(scenario_runner.stream, 'forward', _null)


def test_a_stopped_run_does_not_fire_a_reflection_llm_call(
	approved: tuple[Workspace, scenarios_mod.Scenario], monkeypatch: pytest.MonkeyPatch
) -> None:
	ws, scenario = approved
	_patch(monkeypatch)

	import nkqa.reflector as reflector

	async def must_not_run(*_a: Any, **_k: Any) -> list[str]:
		raise AssertionError('a stopped run must not spend an LLM call reflecting')

	monkeypatch.setattr(reflector, 'auto_reflect', must_not_run)

	ch = FakeChannel()  # raises on an unexpected ask, so a stalled prompt fails the test too
	code = asyncio.run(
		scenario_runner.run_scenario(ws, Config(), HumanInTheLoop(ws.permissions_file, ch), ch, scenario)
	)

	assert code == 1
	# The verdict is not optional: unreached steps are `blocked`, which is the honest result.
	run_dir = next(d for d in ws.runs_dir.iterdir() if d.is_dir() and d.name.startswith('auth-login--'))
	assert (run_dir / 'results.md').is_file()
	assert (run_dir / 'history.json').is_file(), 'partial evidence must survive a stop'


def test_the_agent_is_built_with_nkqa_owning_the_signals(
	approved: tuple[Workspace, scenarios_mod.Scenario], monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Both halves of a stop depend on this construction.

	Leave `enable_signal_handler` on its default and browser-use grabs SIGINT/SIGTERM for
	the whole run: Ctrl+C then pauses the agent instead of stopping it, nothing resumes that
	pause, and its `unregister` strips uvicorn's handlers permanently on the way out.
	"""
	ws, scenario = approved
	_patch(monkeypatch)
	built: list[CancellingAgent] = []

	class Recording(CancellingAgent):
		def __init__(self, *a: Any, **kw: Any) -> None:
			super().__init__(*a, **kw)
			built.append(self)

	monkeypatch.setattr(scenario_runner, 'Agent', Recording)
	ch = FakeChannel()
	signal = StopSignal()
	asyncio.run(
		scenario_runner.run_scenario(ws, Config(), HumanInTheLoop(ws.permissions_file, ch), ch, scenario, None, signal)
	)

	assert built, 'the runner must have constructed an agent'
	kwargs = built[0].kwargs
	assert kwargs['enable_signal_handler'] is False
	assert kwargs['register_should_stop_callback'] == signal.should_stop
	# ...and the signal can reach it, which is the point of attaching.
	signal.stop()
	assert built[0].settings.generate_gif is False


def test_a_normal_run_still_reflects(
	approved: tuple[Workspace, scenarios_mod.Scenario], monkeypatch: pytest.MonkeyPatch
) -> None:
	"""The guard must be about cancellation, not about switching reflection off."""
	ws, scenario = approved
	_patch(monkeypatch)

	class FinishingAgent(CancellingAgent):
		async def run(self, **_: Any) -> Any:
			return FakeHistory()

	monkeypatch.setattr(scenario_runner, 'Agent', FinishingAgent)

	import nkqa.reflector as reflector

	called: list[bool] = []

	async def note(*_a: Any, **_k: Any) -> list[str]:
		called.append(True)
		return []

	monkeypatch.setattr(reflector, 'auto_reflect', note)

	ch = FakeChannel()
	asyncio.run(scenario_runner.run_scenario(ws, Config(), HumanInTheLoop(ws.permissions_file, ch), ch, scenario))
	assert called == [True]


def test_a_prearmed_stop_also_skips_reflection(
	approved: tuple[Workspace, scenarios_mod.Scenario], monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Stop can land after run() returns but before the tail - the flag covers that too."""
	ws, scenario = approved
	_patch(monkeypatch)

	class FinishingAgent(CancellingAgent):
		async def run(self, **_: Any) -> Any:
			return FakeHistory()

	monkeypatch.setattr(scenario_runner, 'Agent', FinishingAgent)

	import nkqa.reflector as reflector

	async def must_not_run(*_a: Any, **_k: Any) -> list[str]:
		raise AssertionError('a stopped run must not reflect')

	monkeypatch.setattr(reflector, 'auto_reflect', must_not_run)

	signal = StopSignal()
	signal.stop()
	ch = FakeChannel()
	asyncio.run(
		scenario_runner.run_scenario(ws, Config(), HumanInTheLoop(ws.permissions_file, ch), ch, scenario, None, signal)
	)
