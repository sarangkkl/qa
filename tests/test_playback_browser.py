"""Record with the real driver, replay with no model - against a tiny local site, in a real browser.

Opt-in (`NKQA_BROWSER_TESTS=1`): it launches Chromium, which is too slow for the pre-commit hook.
"""

import asyncio
import functools
import http.server
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.execution.driver import Driver
from nkqa.execution.playback import Recording, from_run, play
from nkqa.hitl import HumanInTheLoop
from nkqa.scenarios import Scenario, Step
from nkqa.vault import Vault

# pyright: reportUnknownMemberType=false

pytestmark = pytest.mark.skipif(not os.environ.get('NKQA_BROWSER_TESTS'), reason='set NKQA_BROWSER_TESTS=1')

LOGIN = """<html><body><h1>Sign in</h1>
<form action="dashboard.html" method="get">
<input id="email" name="email" placeholder="Email">
<button id="go" class="btn primary" type="submit">Sign in</button>
</form></body></html>"""
DASHBOARD = '<html><body><h1>Projects</h1><p>Welcome back</p></body></html>'


@pytest.fixture
def site(tmp_path: Path) -> Iterator[tuple[Path, str]]:
	root = tmp_path / 'site'
	root.mkdir()
	(root / 'login.html').write_text(LOGIN)
	(root / 'dashboard.html').write_text(DASHBOARD)
	handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
	server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
	threading.Thread(target=server.serve_forever, daemon=True).start()
	yield root, f'http://127.0.0.1:{server.server_address[1]}'
	server.shutdown()


async def index_of(driver: Driver, element_id: str) -> int:
	await driver.state()
	selector_map: dict[int, Any] = await driver._require().get_selector_map()  # pyright: ignore[reportPrivateUsage]
	return next(i for i, n in selector_map.items() if (n.attributes or {}).get('id') == element_id)


def test_a_recorded_run_replays_without_a_model(
	site: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	root, base = site
	ws = workspace_mod.create(tmp_path / 'ws', 'Local', base)
	s = Scenario(
		id='auth/sign-in',
		path=ws.scenarios_dir / 'auth' / 'sign-in.md',
		title='Sign in',
		steps=[Step('Open the login page', 'the sign in form shows'), Step('Sign in', 'the Projects page shows')],
	)
	scenarios_mod.approve(s, 'qa')

	# What Claude would do through the MCP tools, done directly.
	async def record() -> Recording:
		driver = Driver(ws.scenario_run_dir(s.id), {}, headless=True, scenario_id=s.id)
		await driver.start()
		try:
			await driver.act('navigate', {'url': f'{base}/login.html'})
			await driver.state()
			assert (await driver.check(1, 'text_visible', 'sign in'))[0]
			await driver.act('input', {'index': await index_of(driver, 'email'), 'text': 'qa-{{unique}}@x.test'})
			await driver.act('click', {'index': await index_of(driver, 'go')})
			await driver.state()
			assert (await driver.check(2, 'text_visible', 'Projects'))[0]
			assert (await driver.check(2, 'url_contains', 'dashboard'))[0]
			assert (await driver.check(2, 'url_contains', 'qa-{{unique}}'))[0]  # this run's stamp, every run
		finally:
			await driver.close()
		typed = next(r for r in driver.log.steps if r.action == 'input')
		assert typed.params['text'] == 'qa-{{unique}}@x.test' and typed.element  # the token is kept
		return Recording(
			scenario_id=s.id, approved_hash=s.approved_hash, base_url=base, steps=from_run(driver.log.steps)
		)

	def boom(*_a: Any, **_k: Any) -> Any:
		raise AssertionError('playback must never build a model')

	monkeypatch.setattr('nkqa.models.resolve_llm', boom)

	async def replay(recording: Recording, name: str) -> tuple[str, FakeChannel]:
		ch = FakeChannel()
		hitl = HumanInTheLoop(ws.permissions_file, ch, Vault(ws))
		verdict = await play(s, recording, hitl, ch, ws.runs_dir / name, base, headless=True)
		return verdict, ch

	async def scenario() -> None:
		recording = await record()
		assert (await replay(recording, 'replay-1'))[0] == 'pass'

		# A cosmetic change - the button's classes - still finds it.
		(root / 'login.html').write_text(LOGIN.replace('btn primary', 'btn secondary big'))
		assert (await replay(recording, 'replay-2'))[0] == 'pass'

		# Renamed in place: found by position, and the checks - the real assertions - still hold.
		renamed = LOGIN.replace('id="go" class="btn primary" type="submit">Sign in', 'type="submit">Next')
		(root / 'login.html').write_text(renamed)
		assert (await replay(recording, 'replay-3'))[0] == 'pass'

		# Gone: the replay fails at that step and says what it could not find.
		(root / 'login.html').write_text(
			LOGIN.replace('<button id="go" class="btn primary" type="submit">Sign in</button>', '')
		)
		verdict, ch = await replay(recording, 'replay-4')
		assert verdict == 'fail'
		assert any('could not find' in e.text for e in ch.events)
		results = (ws.runs_dir / 'replay-4' / 'results.md').read_text()
		assert '— FAIL —' in results and 'could not find' in results

	asyncio.run(scenario())
