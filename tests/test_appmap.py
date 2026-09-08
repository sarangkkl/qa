import subprocess
from pathlib import Path

import pytest

from nkqa import appmap, config, models, workspace
from nkqa.appmap import AppmapUpdate, FileUpdate


def test_apply_writes_and_reads(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	update = AppmapUpdate(files=[FileUpdate(file='pages/login.md', content='# Login\nEmail + OTP.')])
	written = appmap.apply(ws, update, 'appmap: test')
	assert written == ['pages/login.md']
	content = appmap.read_all(ws)
	assert content['pages/login.md'] == '# Login\nEmail + OTP.\n'
	assert 'overview.md' in content


def test_apply_is_sandboxed(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	for bad in ('../escape.md', '/etc/evil.md', 'pages/../../up.md', 'x.py'):
		with pytest.raises(ValueError, match='refused'):
			appmap.apply(ws, AppmapUpdate(files=[FileUpdate(file=bad, content='x')]), 'nope')
	assert not (tmp_path / 'escape.md').exists()


def test_git_autocommit(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	assert appmap.in_git_repo(ws) is False
	assert appmap.commit(ws, 'x') is False  # no-op outside git

	subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
	subprocess.run(['git', 'config', 'user.email', 't@e.c'], cwd=tmp_path, check=True)
	subprocess.run(['git', 'config', 'user.name', 'T'], cwd=tmp_path, check=True)
	assert appmap.in_git_repo(ws) is True

	appmap.apply(ws, AppmapUpdate(files=[FileUpdate(file='quirks.md', content='slow table')]), 'appmap: learned')
	log = subprocess.run(['git', 'log', '--oneline', '--', 'appmap'], cwd=tmp_path, capture_output=True, text=True)
	assert 'appmap: learned' in log.stdout


def test_config_appmap_section(tmp_path: Path) -> None:
	assert config.load(None).auto_reflect is True
	f = tmp_path / 'config.yaml'
	f.write_text('appmap:\n  auto_reflect: false\n  crawl_pages: 3\n')
	cfg = config.load(f)
	assert cfg.auto_reflect is False and cfg.crawl_pages == 3


def test_describe_role(monkeypatch: pytest.MonkeyPatch) -> None:
	cfg = config.Config()
	monkeypatch.setenv('ANTHROPIC_API_KEY', 'x')
	name, provider, keys, missing = models.describe_role(cfg, 'executor')
	assert (name, provider, keys, missing) == ('anthropic_claude_haiku_4_5', 'anthropic', ['ANTHROPIC_API_KEY'], [])
	monkeypatch.delenv('ANTHROPIC_API_KEY')
	assert models.describe_role(cfg, 'executor')[3] == ['ANTHROPIC_API_KEY']

	cfg.models['executor'] = 'default'
	assert models.describe_role(cfg, 'executor')[1] == 'browser-use default'


def test_context_for_run_selects_overview_and_login(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	(ws.appmap_dir / 'flows').mkdir(parents=True, exist_ok=True)
	(ws.appmap_dir / 'overview.md').write_text('the app does things')
	(ws.appmap_dir / 'flows' / 'login.md').write_text('go to /login')
	(ws.appmap_dir / 'pages').mkdir(parents=True, exist_ok=True)
	(ws.appmap_dir / 'pages' / 'huge.md').write_text('x' * 5000)

	context = appmap.context_for_run(ws)
	assert 'the app does things' in context
	assert 'go to /login' in context
	assert 'x' * 5000 not in context  # page docs stay out of every run's prompt
	assert 'appmap/overview.md' in context  # provenance is visible to the model


def test_context_for_run_empty_without_appmap(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	(ws.appmap_dir / 'overview.md').unlink()
	assert appmap.context_for_run(ws) == ''  # nothing known yet: no context block at all


# --- route identity ---------------------------------------------------------


@pytest.mark.parametrize(
	('url', 'route'),
	[
		# Record ids collapse, so 442 projects are one screen and not 442 pages.
		('/orders/12345', '/orders/:id'),
		('/u/3f7a1b2c-1111-2222-3333-444455556666', '/u/:id'),
		('https://dev.sustain.slrconsulting.com/pm-hub/501.D70162.00001', '/pm-hub/:id'),
		# A human writes the placeholder by hand; it has to mean the same thing.
		('/projects/{projectNumber}/{tab}', '/projects/:id/:id'),
		# Query and fragment carry state, not identity - and are where the 2KB OAuth URLs live.
		('/clients?page=2&sort=name', '/clients'),
		('/clients#tab', '/clients'),
		('/clients/', '/clients'),
		('/CLIENTS', '/clients'),
		('https://shop.test/', '/'),
		# Words that merely contain a dot are page names, not ids.
		('/docs/index.html', '/docs/index.html'),
		('/about.us', '/about.us'),
	],
)
def test_normalize_route(url: str, route: str) -> None:
	assert appmap.normalize_route(url) == route


def test_the_whole_oauth_redirect_collapses_to_one_short_route() -> None:
	"""The real thing, 2KB of it. Eight of the last crawl's fifty steps happened in here."""
	url = (
		'https://login.microsoftonline.com/109cec53-a877-42eb-93e8-b9f5c282ba38/oauth2/v2.0/authorize'
		'?client_id=247fde2e-d234-47c8-bdfd-f167d6f2a1e1&scope=api%3A%2F%2F247fde2e%2FUserAccess'
		'&redirect_uri=https%3A%2F%2Fdev.sustain.slrconsulting.com%2F&state=' + 'x' * 1500
	)
	assert appmap.normalize_route(url) == '/:id/oauth2/v2.0/authorize'


@pytest.mark.parametrize(
	('route', 'slug'),
	[('/pm-hub/:id', 'pm-hub-detail'), ('/clients', 'clients'), ('/', 'home'), ('/a/b/c', 'a-b-c')],
)
def test_route_slug(route: str, slug: str) -> None:
	assert appmap.route_slug(route) == slug


def test_a_template_route_covers_its_instances() -> None:
	assert appmap.route_matches('/projects/:id/:id', '/projects/:id/project-details')
	assert appmap.route_matches('/orders/:id', '/orders/:id')
	assert not appmap.route_matches('/orders/:id', '/orders')  # different depth is a different screen
	assert not appmap.route_matches('/orders/:id', '/clients/:id')


def test_known_routes_reads_the_hand_written_convention(tmp_path: Path) -> None:
	"""Parsed from the page docs themselves, so the skip list cannot drift from what is written."""
	ws = workspace.create(tmp_path)
	pages = ws.appmap_dir / 'pages'
	pages.mkdir()
	# The three real shapes in this repo's own appmap, including trailing prose.
	(pages / 'clients.md').write_text('# Clients\n\n**Route:** `/clients`\n')
	(pages / 'delegations.md').write_text(
		'# Delegations\n\n**Route:** `/delegations` (reached from **Manage delegations**)\n'
	)
	(pages / 'project-detail.md').write_text('# Project detail\n\n**Route:** `/projects/{projectNumber}/{tab}`\n')
	# ...and one that names no route at all, which must not break the scan.
	(pages / 'project-tabs.md').write_text('# Project detail tabs\n\nEach is its own route.\n')

	assert appmap.known_routes(ws) == {
		'/clients': 'pages/clients.md',
		'/delegations': 'pages/delegations.md',
		'/projects/:id/:id': 'pages/project-detail.md',
	}


def test_chat_context_leads_with_the_overview_and_names_what_it_dropped(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	(ws.appmap_dir / 'overview.md').write_text('# The app\nWhat it does.')
	(ws.appmap_dir / 'flows').mkdir()
	(ws.appmap_dir / 'flows' / 'login.md').write_text('# Login\nGo to /login.')
	(ws.appmap_dir / 'pages').mkdir()
	(ws.appmap_dir / 'pages' / 'huge.md').write_text('# Huge\n' + 'x' * 9000)

	context = appmap.context_for_chat(ws, budget=500)
	assert context.index('overview.md') < context.index('flows/login.md'), 'the index comes first'
	assert 'Go to /login' in context, 'behaviour beats element lists when space is short'
	# Not silently absent: the model must be able to say "there is a page doc I have not read".
	assert 'not loaded' in context and 'pages/huge.md' in context
	assert 'x' * 9000 not in context


# --- the path the model hands back ------------------------------------------


@pytest.mark.parametrize(
	('given', 'want'),
	[
		('appmap/overview.md', 'overview.md'),
		('appmap/appmap/overview.md', 'overview.md'),  # already nested twice in a real workspace
		('appmap/pages/clients.md', 'pages/clients.md'),
		('overview.md', 'overview.md'),
		('pages/clients.md', 'pages/clients.md'),
		('  appmap/overview.md  ', 'overview.md'),
	],
)
def test_the_appmap_prefix_is_stripped(given: str, want: str) -> None:
	assert appmap.strip_appmap_prefix(given) == want


def test_a_model_echoing_the_label_writes_the_real_file(tmp_path: Path) -> None:
	"""Every writer shows the map as `--- appmap/overview.md ---`, so the model returns that.

	Joining it to appmap/ again nested one level deeper on every write. Two corrections in a
	real workspace produced appmap/overview.md, appmap/appmap/overview.md and
	appmap/appmap/appmap/overview.md - while the overview the runner actually reads still held
	the empty stub, so neither correction was ever used.
	"""
	ws = workspace.create(tmp_path)
	update = AppmapUpdate(files=[FileUpdate(file='appmap/overview.md', content='# Real\nLogin via /login.')])

	written = appmap.apply(ws, update, 'appmap: test')

	assert written == ['overview.md'], 'the caller must be told where it actually went'
	assert not (ws.appmap_dir / 'appmap').exists(), 'no nested appmap/ directory'
	assert 'Login via /login.' in (ws.appmap_dir / 'overview.md').read_text()


def test_stripping_the_prefix_does_not_open_the_sandbox(tmp_path: Path) -> None:
	"""Normalising a path must never turn an absolute one into a relative one.

	Filtering the root marker out of the parts makes '/etc/evil.md' look relative, and it then
	lands inside appmap/ instead of being refused.
	"""
	ws = workspace.create(tmp_path)
	for bad in ('/etc/evil.md', 'appmap/../../up.md', 'appmap/../../../etc/evil.md'):
		with pytest.raises(ValueError, match='refused'):
			appmap.apply(ws, AppmapUpdate(files=[FileUpdate(file=bad, content='x')]), 'nope')
	assert not (tmp_path / 'up.md').exists()
	assert not Path('/etc/evil.md').exists()

	# For contrast: a leading-slash *segment* after the prefix is not an escape - it normalises
	# to a relative path and lands inside appmap/, which is the whole point of the sandbox.
	appmap.apply(ws, AppmapUpdate(files=[FileUpdate(file='appmap//notes/x.md', content='ok')]), 'fine')
	assert (ws.appmap_dir / 'notes' / 'x.md').read_text() == 'ok\n'
