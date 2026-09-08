"""The crawl, and the promise that it keeps what it learns.

A real 17-minute crawl over five screens once wrote nothing at all: the only appmap write
happened after the agent called `done`, and it never did. These pin the fix - a page is on
disk the moment it is understood, and a screen already documented is not mapped twice.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import appmap, crawler, workspace
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop
from nkqa.ui import Event
from nkqa.workspace import Workspace

APP = Config(base_url='https://shop.test')


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	return workspace.create(tmp_path)


def recorder(ws: Workspace, budget: int = 3, refresh: bool = False) -> crawler.PageRecorder:
	return crawler.PageRecorder(ws, FakeChannel(), APP, budget, refresh)


def test_build_task(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	(ws.appmap_dir / 'quirks.md').write_text('slow tables')
	task = crawler.build_task(Config(base_url='https://shop.test'), {'quirks.md': 'slow tables'}, 7, {})
	assert 'https://shop.test' in task
	assert 'budget: 7' in task.lower()
	assert 'slow tables' in task  # current appmap included for merge


def test_the_task_names_the_pages_not_to_revisit(tmp_path: Path) -> None:
	"""The budget is only spent on new ground if the agent is told what is old ground."""
	task = crawler.build_task(Config(base_url='https://shop.test'), {}, 7, {'/cart': 'pages/cart.md'})
	assert '/cart' in task and 'do NOT spend budget' in task


def test_crawl_requires_base_url(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	hitl = HumanInTheLoop(ws.permissions_file)
	assert asyncio.run(crawler.crawl(ws, Config(base_url=''), hitl, FakeChannel(), 5)) == 2
	assert not any(ws.runs_dir.glob('crawl--*'))


def test_a_page_is_on_disk_the_moment_it_is_recorded(ws: Workspace) -> None:
	r = recorder(ws)
	asyncio.run(r.page('/clients', 'Clients', 'A table of clients.'))

	written = ws.appmap_dir / 'pages' / 'clients.md'
	assert written.is_file(), 'the page must not wait for the end of the run'
	assert 'A table of clients.' in written.read_text()


def test_what_was_recorded_survives_an_agent_that_dies_mid_run(ws: Workspace) -> None:
	"""The whole point. Two screens understood, then the run falls over - keep the two.

	This is the exact shape of the failure that prompted the change: the run ends without the
	agent ever calling `done`, so the end-of-run structured output is None.
	"""
	r = recorder(ws)

	async def crawl_then_die() -> None:
		await r.page('/clients', 'Clients', 'A table of clients.')
		await r.page('/reviews', 'Reviews', 'Review queues.')
		raise RuntimeError('browser died')

	with pytest.raises(RuntimeError):
		asyncio.run(crawl_then_die())

	assert sorted(p.name for p in (ws.appmap_dir / 'pages').glob('*.md')) == ['clients.md', 'reviews.md']


def test_a_recorded_page_is_found_again_by_the_next_crawl(ws: Workspace) -> None:
	"""The Route line is load-bearing: record_page writes it, known_routes reads it back."""
	asyncio.run(recorder(ws).page('/pm-hub/501.D70162.00001', 'PM Hub detail', 'One project.'))

	assert appmap.known_routes(ws) == {'/pm-hub/:id': 'pages/pm-hub-detail.md'}
	assert recorder(ws).known == {'/pm-hub/:id': 'pages/pm-hub-detail.md'}


def test_a_page_already_documented_is_refused_not_overwritten(ws: Workspace) -> None:
	"""Human edits win: the map is a notebook people write in, not a scratch buffer."""
	(ws.appmap_dir / 'pages').mkdir()
	page = ws.appmap_dir / 'pages' / 'clients.md'
	page.write_text('# Clients\n\n**Route:** `/clients`\n\nWritten by a human who knew things.\n')

	said = asyncio.run(recorder(ws).page('/clients', 'Clients', 'Shallow agent prose.'))

	assert 'already mapped' in said
	assert 'Written by a human' in page.read_text(), 'a hand-written page must survive a crawl'


def test_refresh_re_maps_what_is_already_known(ws: Workspace) -> None:
	(ws.appmap_dir / 'pages').mkdir()
	(ws.appmap_dir / 'pages' / 'clients.md').write_text('# Clients\n\n**Route:** `/clients`\n\nStale.\n')

	said = asyncio.run(recorder(ws, refresh=True).page('/clients', 'Clients', 'Fresh.'))

	assert 'Recorded' in said
	assert 'Fresh.' in (ws.appmap_dir / 'pages' / 'clients.md').read_text()


def test_a_human_written_template_route_covers_its_instances(ws: Workspace) -> None:
	"""`/projects/{projectNumber}/{tab}` is how a person writes it; the crawl sees a real id."""
	(ws.appmap_dir / 'pages').mkdir()
	(ws.appmap_dir / 'pages' / 'project-detail.md').write_text(
		'# Project detail\n\n**Route:** `/projects/{projectNumber}/{tab}`\n\nTabs.\n'
	)

	said = asyncio.run(recorder(ws).page('/projects/501.D71289.00001/project-details', 'Project', 'x'))
	assert 'already mapped' in said


def test_the_budget_is_actually_enforced(ws: Workspace) -> None:
	"""It used to be advisory - a line in the prompt asking the model to police itself."""
	r = recorder(ws, budget=2)
	for route in ('/a', '/b'):
		asyncio.run(r.page(route, route, 'x'))

	said = asyncio.run(r.page('/c', 'C', 'x'))
	assert 'budget spent' in said
	assert not (ws.appmap_dir / 'pages' / 'c.md').exists()


def test_an_identity_provider_is_not_a_page_of_the_app(ws: Workspace) -> None:
	"""~8 of the last real crawl's 50 steps went on login.microsoftonline.com."""
	said = asyncio.run(
		recorder(ws).page('https://login.microsoftonline.com/109cec53/oauth2/v2.0/authorize', 'Sign in', 'x')
	)

	assert 'not part of this app' in said
	assert not list(ws.appmap_dir.rglob('pages/*.md')), 'the identity provider must not take a slot in the map'


def test_a_flow_is_recorded_but_never_clobbers_an_existing_one(ws: Workspace) -> None:
	r = recorder(ws)
	asyncio.run(r.flow('Login', 'Go to /login, sign in.'))
	assert (ws.appmap_dir / 'flows' / 'login.md').is_file()

	said = asyncio.run(recorder(ws).flow('Login', 'Something else.'))
	assert 'already exists' in said
	assert 'sign in' in (ws.appmap_dir / 'flows' / 'login.md').read_text()


def test_the_crawl_narrates_itself_like_every_other_run(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""A crawl ran for seventeen minutes showing nothing: it was the one runner that never
	entered `stream.forward`, so browser-use's narration went nowhere and the live pane stayed
	black. Without both of these there is no way to tell a working crawl from a stuck one.
	"""
	entered: list[str] = []
	steps_seen: list[int] = []

	@asynccontextmanager
	async def fake_forward(channel: Any, secrets: Any = None) -> AsyncGenerator[None]:
		entered.append('forward')
		yield

	@asynccontextmanager
	async def fake_screencast(agent: Any, channel: Any) -> AsyncGenerator[None]:
		entered.append('screencast')
		yield

	def fake_step_event(agent: Any, n: int, run_dir: Any = None) -> Event:
		steps_seen.append(n)
		return Event('step', f'step {n}', {'n': n})

	def no_llm(*_a: Any, **_k: Any) -> None:
		return None

	class OneStepAgent:
		def __class_getitem__(cls, _item: Any) -> type['OneStepAgent']:
			return cls

		def __init__(self, *_: Any, **__: Any) -> None:
			self.history = SimpleNamespace(history=[], structured_output=None)

		async def run(self, max_steps: int = 0, on_step_end: Any = None) -> Any:
			await on_step_end(self)
			return self.history

		def save_history(self, path: Path) -> None: ...
		def stop(self) -> None: ...

	monkeypatch.setattr(crawler, 'Agent', OneStepAgent)
	monkeypatch.setattr(crawler, 'resolve_llm', no_llm)
	monkeypatch.setattr(crawler.stream, 'forward', fake_forward)
	monkeypatch.setattr(crawler.screencast, 'stream', fake_screencast)
	monkeypatch.setattr(crawler.stream, 'step_event', fake_step_event)

	hitl = HumanInTheLoop(ws.permissions_file)
	ch = FakeChannel()
	asyncio.run(crawler.crawl(ws, APP, hitl, ch, 2))

	assert entered == ['forward', 'screencast'], 'a crawl must stream its narration and its browser'
	assert steps_seen == [1], 'and emit a step event per step, like run and replay do'
