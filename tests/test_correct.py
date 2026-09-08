"""Correcting the app map from a sentence.

The map had three writers and every one took a path, a run name or a page budget. When the
agent believed something false about the app, there was no way to tell it so - you opened
the file yourself. This is the door ARCHITECTURE's "humans correct it and the human edit
wins" always implied.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import appmap, workspace
from nkqa import correct as correct_mod
from nkqa.appmap import AppmapUpdate, FileUpdate
from nkqa.config import Config
from nkqa.workspace import Workspace

CLIENTS = '# Clients\n\n**Route:** `/clients`\n\nClient rows are not clickable links.\n'
FIXED = '# Clients\n\n**Route:** `/clients`\n\nClient rows link to /clients/<id>.\n'


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	w = workspace.create(tmp_path)
	(w.appmap_dir / 'pages').mkdir()
	(w.appmap_dir / 'pages' / 'clients.md').write_text(CLIENTS)
	return w


def stub(monkeypatch: pytest.MonkeyPatch, update: AppmapUpdate) -> list[str]:
	"""Returns the list the prompt is captured into, so tests can assert what the model saw."""
	seen: list[str] = []

	class StubLLM:
		async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
			seen.extend(str(m.content) for m in messages)

			class R:
				completion = update

			return R()

	def fake(cfg: Config, role: str, override: str | None = None) -> StubLLM:
		return StubLLM()

	monkeypatch.setattr(correct_mod, 'resolve_llm', fake)
	return seen


def test_a_sentence_changes_the_file(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='pages/clients.md', content=FIXED)]))
	ch = FakeChannel()

	code = asyncio.run(correct_mod.correct(ws, Config(), ch, 'client rows are clickable, they open /clients/<id>'))

	assert code == 0
	assert 'link to /clients/<id>' in (ws.appmap_dir / 'pages' / 'clients.md').read_text()


def test_the_change_is_shown_before_it_is_made(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""'What did that just do to my notebook' is the question every automatic edit raises."""
	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='pages/clients.md', content=FIXED)]))
	ch = FakeChannel()

	asyncio.run(correct_mod.correct(ws, Config(), ch, 'rows are clickable'))

	assert '-Client rows are not clickable links.' in ch.out
	assert '+Client rows link to /clients/<id>.' in ch.out


def test_the_model_is_given_the_map_and_the_correction(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	seen = stub(monkeypatch, AppmapUpdate(files=[]))
	asyncio.run(correct_mod.correct(ws, Config(), FakeChannel(), 'rows are clickable'))

	prompt = '\n'.join(seen)
	assert 'not clickable links' in prompt, 'it must see what it currently believes'
	assert '### Correction\nrows are clickable' in prompt


def test_a_correction_already_reflected_writes_nothing(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	stub(monkeypatch, AppmapUpdate(files=[], notes='the map already says this'))
	ch = FakeChannel()

	assert asyncio.run(correct_mod.correct(ws, Config(), ch, 'rows are clickable')) == 0
	assert (ws.appmap_dir / 'pages' / 'clients.md').read_text() == CLIENTS
	assert 'already says this' in ch.out


def test_the_correction_is_committed_so_it_can_be_reverted(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""git diff is the review surface for every appmap writer; this one is no exception."""
	subprocess.run(['git', 'init', '-q'], cwd=ws.root, check=True)
	subprocess.run(['git', 'add', '-A'], cwd=ws.root, check=True)
	subprocess.run(
		['git', '-c', 'user.email=t@e.c', '-c', 'user.name=T', 'commit', '-q', '-m', 'base'], cwd=ws.root, check=True
	)
	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='pages/clients.md', content=FIXED)]))

	asyncio.run(correct_mod.correct(ws, Config(), FakeChannel(), 'rows are clickable'))

	log = subprocess.run(['git', 'log', '--oneline', '-1'], cwd=ws.root, capture_output=True, text=True).stdout
	assert 'corrected by you' in log


def test_an_empty_correction_is_usage(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	stub(monkeypatch, AppmapUpdate(files=[]))
	assert asyncio.run(correct_mod.correct(ws, Config(), FakeChannel(), '   ')) == 2


def test_correcting_an_empty_map_says_so_instead_of_inventing_one(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	empty = workspace.create(tmp_path)
	(empty.appmap_dir / 'overview.md').unlink()
	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='pages/x.md', content='invented')]))
	ch = FakeChannel()

	assert asyncio.run(correct_mod.correct(empty, Config(), ch, 'the app has a cart')) == 2
	assert 'empty' in ch.out
	assert not appmap.read_all(empty), 'nothing should be written from a correction with no map to correct'


def test_the_write_is_still_sandboxed(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""It goes through appmap.apply, so a model naming ../../.ssh/id_rsa gets nowhere."""
	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='../escaped.md', content='nope')]))

	with pytest.raises(ValueError, match='refused'):
		asyncio.run(correct_mod.correct(ws, Config(), FakeChannel(), 'escape'))
	assert not (ws.root.parent / 'escaped.md').exists()
