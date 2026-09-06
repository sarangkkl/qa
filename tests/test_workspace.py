from pathlib import Path

from nkqa import workspace


def test_slugify() -> None:
	assert workspace.slugify('Login flow!') == 'login-flow'
	assert workspace.slugify('  ') == 'unnamed-test'
	assert len(workspace.slugify('x' * 100)) <= 40


def test_create_and_find(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	assert ws.config_file.is_file()
	assert (ws.appmap_dir / 'overview.md').is_file()
	assert ws.scenarios_dir.is_dir() and ws.runs_dir.is_dir()

	nested = tmp_path / 'a' / 'b'
	nested.mkdir(parents=True)
	found = workspace.find(nested)
	assert found is not None and found.root == ws.root
	assert workspace.find(Path('/')) is None


def test_at_does_not_walk_up(tmp_path: Path) -> None:
	"""`find` adopts a parent workspace; `at` answers "is *this* folder one?" - which is what
	`--init` must ask, or initialising a subfolder would silently create nothing."""
	workspace.create(tmp_path)
	nested = tmp_path / 'sub'
	nested.mkdir()
	assert workspace.find(nested) is not None
	assert workspace.at(nested) is None


def test_create_writes_the_app_details(tmp_path: Path) -> None:
	from nkqa import config as config_mod

	ws = workspace.create(tmp_path, 'Sustain', 'https://dev.sustain.test')
	cfg = config_mod.load(ws.config_file)
	assert cfg.app_name == 'Sustain' and cfg.base_url == 'https://dev.sustain.test'


def test_create_survives_an_awkward_app_name(tmp_path: Path) -> None:
	"""A bare `name: Acme: The Shop` is not valid YAML, and `name: no` parses as False."""
	from nkqa import config as config_mod

	for name in ('Acme: The Shop', '#1 Store', 'no', 'a "quoted" thing'):
		root = tmp_path / workspace.slugify(name)
		ws = workspace.create(root, name, 'https://x.test')
		assert config_mod.load(ws.config_file).app_name == name


def test_create_without_details_is_the_untouched_template(tmp_path: Path) -> None:
	from nkqa import config as config_mod

	ws = workspace.create(tmp_path)
	assert ws.config_file.read_text() == config_mod.CONFIG_TEMPLATE


def test_create_is_idempotent(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	ws.config_file.write_text('app:\n  name: Edited\n')
	workspace.create(tmp_path)
	assert 'Edited' in ws.config_file.read_text()


def test_migrate_prototype(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	old = tmp_path / 'qa_output'
	(old / 'tests' / 'checkout').mkdir(parents=True)
	(old / 'tests' / 'checkout' / 'history.json').write_text('{}')
	(old / 'qa_permissions.json').write_text('["delete-test-user"]')

	moved = workspace.migrate_prototype(ws, old)
	assert moved == ['checkout']
	assert (ws.runs_dir / 'checkout' / 'history.json').is_file()
	assert ws.permissions_file.is_file()
	# second call is a no-op
	assert workspace.migrate_prototype(ws, old) == []
