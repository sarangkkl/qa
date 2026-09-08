import asyncio
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import revise as revise_mod
from nkqa import scenarios, workspace
from nkqa.config import Config
from nkqa.revise import RevisedScenario, RevisedStep
from nkqa.scenarios import Scenario, Step


def approved_scenario(tmp_path: Path) -> Scenario:
	s = Scenario(
		id='auth/login',
		path=tmp_path / 'scenarios' / 'auth' / 'login.md',
		title='Login works',
		steps=[Step('Open the login page.', 'form visible'), Step('Sign in.', 'dashboard shown')],
	)
	scenarios.save(s)
	scenarios.approve(s, 'T <t@e.c>')
	return scenarios.parse(s.path, tmp_path / 'scenarios')


def stub(monkeypatch: pytest.MonkeyPatch, revised: RevisedScenario) -> None:
	class StubLLM:
		async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
			class R:
				completion = revised

			return R()

	def fake(cfg: Config, role: str, override: str | None = None) -> StubLLM:
		return StubLLM()

	monkeypatch.setattr(revise_mod, 'resolve_llm', fake)


def test_revise_invalidates_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	ws = workspace.create(tmp_path)
	s = approved_scenario(tmp_path)
	assert s.runnable() == 'ok'

	stub(
		monkeypatch,
		RevisedScenario(
			title='Login works',
			steps=[
				RevisedStep(action='Open the login page.', expect='form visible'),
				RevisedStep(action='Sign in.', expect='dashboard shows the user name'),
			],
			changes='tightened step 2',
		),
	)
	ch = FakeChannel()
	assert asyncio.run(revise_mod.revise(ws, Config(), ch, 'auth/login', 'make step 2 stricter')) == 0

	after = scenarios.parse(s.path, ws.scenarios_dir)
	assert after.steps[1].expect == 'dashboard shows the user name'
	assert after.runnable() == 'stale'  # approval no longer matches the content
	assert 'tightened step 2' in ch.out and 'Approval invalidated' in ch.out


def test_revise_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	ws = workspace.create(tmp_path)
	approved_scenario(tmp_path)
	stub(monkeypatch, RevisedScenario(title='x', steps=[RevisedStep(action='y')]))
	assert asyncio.run(revise_mod.revise(ws, Config(), FakeChannel(), 'no/such', 'do it')) == 2
	assert asyncio.run(revise_mod.revise(ws, Config(), FakeChannel(), 'auth/login', '   ')) == 2
