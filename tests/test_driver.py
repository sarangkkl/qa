"""The LLM-free driver: browser-use does the work, nkqa records it, the agent never sees a secret."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from browser_use import ActionResult

from nkqa.execution import driver as driver_mod
from nkqa.execution.driver import Driver, read_step_log


class FakeDom:
	def __init__(self, text: str):
		self.text = text

	def llm_representation(self) -> str:
		return self.text


class FakeTab:
	def __init__(self, target_id: str, url: str, title: str):
		self.target_id, self.url, self.title = target_id, url, title


class FakePageInfo:
	viewport_width, viewport_height, pixels_above, pixels_below = 1280, 800, 0, 1450


class FakeSummary:
	def __init__(self, url: str, dom: str):
		self.url = url
		self.title = 'Sign in'
		self.tabs = [FakeTab('ABCDEF1234', url, 'Sign in'), FakeTab('ABCDEF9999', 'https://shop.test/help', 'Help')]
		self.page_info = FakePageInfo()
		self.dom_state = FakeDom(dom)
		self.browser_errors: list[str] = []
		self.state_error: str | None = None


class FakeSession:
	"""Just enough of BrowserSession: state, url, screenshots, and a kill switch."""

	def __init__(self, **kwargs: Any):
		self.profile = kwargs.get('browser_profile')
		self.url = 'about:blank'
		self.dom = (
			'[1]<input placeholder=Email />\n[2]<input type=password value=hunter2 />\n[3]<button>Sign in</button>'
		)
		self.started = False
		self.killed = False
		self.shots = 0

	async def start(self) -> None:
		self.started = True

	async def kill(self) -> None:
		self.killed = True

	async def get_browser_state_summary(self, include_screenshot: bool = True) -> FakeSummary:
		return FakeSummary(self.url, self.dom)

	async def get_current_page_url(self) -> str:
		return self.url

	async def get_current_page_title(self) -> str:
		return 'Sign in'

	async def get_tabs(self) -> list[FakeTab]:
		return [FakeTab('ABCDEF1234', self.url, 'Sign in')]

	async def take_screenshot(self, path: str | None = None) -> bytes:
		self.shots += 1
		return b'\x89PNG' + bytes([self.shots])


class FakeRegistry:
	def __init__(self, session: FakeSession):
		self.session = session
		self.calls: list[tuple[str, dict[str, Any], Any]] = []

	async def execute_action(self, name: str, params: dict[str, Any], **kwargs: Any) -> Any:
		self.calls.append((name, params, kwargs.get('sensitive_data')))
		if name == 'navigate':
			self.session.url = params['url']
			return ActionResult(extracted_content=f'Navigated to {params["url"]}')
		if name == 'input':
			return ActionResult(extracted_content='Typed <password>' if 'secret' in params['text'] else 'Typed text')
		if name == 'boom':
			raise RuntimeError('Element index 9 does not exist')
		return None


class FakeTools:
	def __init__(self, registry: FakeRegistry):
		self.registry = registry


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Driver, FakeSession, FakeRegistry]:
	holder: dict[str, FakeSession] = {}

	def make_session(**kwargs: Any) -> FakeSession:
		holder['s'] = FakeSession(**kwargs)
		return holder['s']

	registry_holder: dict[str, FakeRegistry] = {}

	def make_tools() -> FakeTools:
		registry_holder['r'] = FakeRegistry(holder['s'])
		return FakeTools(registry_holder['r'])

	monkeypatch.setattr(driver_mod, 'BrowserSession', make_session)
	monkeypatch.setattr(driver_mod, 'Tools', make_tools)
	secrets: dict[str, str | dict[str, str]] = {'password': 'hunter2'}
	d = Driver(tmp_path / 'runs' / 'auth-login--20260910-120000', secrets, headless=True, scenario_id='auth/login')
	asyncio.run(d.start())
	return d, holder['s'], registry_holder['r']


def test_start_records_video_into_the_run_and_opens_the_log(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, session, _ = run
	assert session.started
	assert str(session.profile.record_video_dir).endswith('auth-login--20260910-120000/videos')  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
	log = read_step_log(d.run_dir)
	assert log is not None and log.scenario_id == 'auth/login' and log.steps == [] and not log.finished_at


def test_state_is_readable_and_redacted(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, _, _ = run
	text, shot = asyncio.run(d.state())
	assert 'URL: about:blank' in text and '[3]<button>Sign in</button>' in text
	assert '* [1234] Sign in' in text and '  [9999] Help' in text
	assert 'Viewport 1280x800' in text
	assert 'hunter2' not in text and '[secret]' in text  # the page echoed the password; the agent never sees it
	assert shot is None and d.fresh


def test_state_with_screenshot_returns_bytes_and_keeps_a_file(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, _, _ = run
	_, shot = asyncio.run(d.state(screenshot=True))
	assert shot is not None and shot.startswith(b'\x89PNG')
	assert (d.run_dir / 'steps' / 'step-001.png').read_bytes() == shot


def test_act_records_the_step_with_urls_and_a_screenshot(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, _, registry = run
	out = asyncio.run(d.act('navigate', {'url': 'https://shop.test/login'}))
	assert 'Navigated to https://shop.test/login' in out
	assert 'Now at: https://shop.test/login — Sign in' in out
	assert 'Call browser_state' in out
	assert not d.fresh

	log = json.loads((d.run_dir / 'steps.json').read_text())
	step = log['steps'][0]
	assert step['n'] == 1 and step['action'] == 'navigate' and step['params'] == {'url': 'https://shop.test/login'}
	assert step['url_before'] == 'about:blank' and step['url_after'] == 'https://shop.test/login'
	assert step['screenshot'] == 'steps/step-001.png' and (d.run_dir / 'steps' / 'step-001.png').is_file()
	assert registry.calls[0][2] is d.secrets  # the live dict, so a credential collected mid-run counts


def test_a_failed_action_is_a_step_with_an_error_not_a_crash(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, _, _ = run
	out = asyncio.run(d.act('boom', {}))
	assert 'RuntimeError: Element index 9 does not exist' in out
	assert read_step_log(d.run_dir).steps[0].error.startswith('RuntimeError')  # pyright: ignore[reportOptionalMemberAccess]


def test_placeholders_stay_literal_in_the_log_and_values_never_appear(
	run: tuple[Driver, FakeSession, FakeRegistry],
) -> None:
	d, _, _ = run
	asyncio.run(d.act('input', {'index': 2, 'text': '<secret>password</secret>'}))
	asyncio.run(d.act('input', {'index': 2, 'text': 'hunter2'}))  # the agent misbehaved and typed a real value
	raw = (d.run_dir / 'steps.json').read_text()
	assert '<secret>password</secret>' in raw
	assert 'hunter2' not in raw and '[secret]' in raw


def test_wait_is_capped_and_recorded(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, _, _ = run
	out = asyncio.run(d.wait(0))
	assert 'Waited 0s' in out
	assert read_step_log(d.run_dir).steps[0].action == 'wait'  # pyright: ignore[reportOptionalMemberAccess]
	assert driver_mod.MAX_WAIT == 30


def test_close_finalises_the_log_then_kills_the_browser(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, session, _ = run
	asyncio.run(d.act('navigate', {'url': 'https://shop.test/'}))
	asyncio.run(d.close(aborted=True))
	assert session.killed and d.session is None
	log = read_step_log(d.run_dir)
	assert log is not None and log.aborted and log.finished_at and len(log.steps) == 1
	with pytest.raises(RuntimeError):
		asyncio.run(d.state())


def test_tabs_lists_short_ids(run: tuple[Driver, FakeSession, FakeRegistry]) -> None:
	d, _, _ = run
	assert asyncio.run(d.tabs()) == '[1234] Sign in — about:blank'
