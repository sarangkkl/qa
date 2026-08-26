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
