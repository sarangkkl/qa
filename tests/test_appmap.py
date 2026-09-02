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
