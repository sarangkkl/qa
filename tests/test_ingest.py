import asyncio
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import ingest, workspace
from nkqa.appmap import AppmapUpdate, FileUpdate
from nkqa.config import Config

PNG = bytes.fromhex('89504e470d0a1a0a0000000d494844520000000100000001080600000018dd8db0')  # tiny fake png header


def make_doc(tmp_path: Path) -> Path:
	(tmp_path / 'images').mkdir()
	(tmp_path / 'images' / 'login.png').write_bytes(PNG)
	doc = tmp_path / 'app.md'
	doc.write_text('# Shop\n\n### Login\n![](images/login.png)\nEmail + OTP.\n![](images/missing.png)\n')
	return doc


def test_collect_inputs_from_md(tmp_path: Path) -> None:
	doc = make_doc(tmp_path)
	ch = FakeChannel()
	text, images = asyncio.run(ingest.collect_inputs(ch, doc))
	assert 'Email + OTP.' in text
	assert [i.name for i in images] == ['login.png']  # missing one skipped with a warning
	assert 'missing.png' in ch.out


def test_collect_inputs_from_folder(tmp_path: Path) -> None:
	(tmp_path / 'shots').mkdir()
	(tmp_path / 'shots' / 'a.png').write_bytes(PNG)
	(tmp_path / 'shots' / 'notes.md').write_text('the dashboard is slow')
	text, images = asyncio.run(ingest.collect_inputs(FakeChannel(), tmp_path / 'shots'))
	assert 'slow' in text and [i.name for i in images] == ['a.png']


def test_build_messages_shape(tmp_path: Path) -> None:
	doc = make_doc(tmp_path)
	text, images = asyncio.run(ingest.collect_inputs(FakeChannel(), doc))
	messages = asyncio.run(ingest.build_messages(FakeChannel(), text, images, {'overview.md': '# existing'}))
	user = messages[1]
	parts: list[Any] = list(user.content)  # type: ignore[arg-type]
	kinds = [p.type for p in parts]
	assert kinds == ['text', 'text', 'text', 'image_url']  # existing appmap, doc text, image label, image
	assert parts[-1].image_url.url.startswith('data:image/png;base64,')
	assert parts[0].text.startswith('Existing appmap')


def test_learn_end_to_end_with_stub_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	ws = workspace.create(tmp_path)
	doc = make_doc(tmp_path)

	class StubResponse:
		completion = AppmapUpdate(
			files=[FileUpdate(file='pages/login.md', content='# Login\nEmail + OTP.')],
			notes='what happens after 3 wrong OTPs?',
		)

	class StubLLM:
		async def ainvoke(self, messages: Any, output_format: Any = None) -> StubResponse:
			return StubResponse()

	def stub_resolve(cfg: Config, role: str, override: str | None = None) -> StubLLM:
		return StubLLM()

	monkeypatch.setattr(ingest, 'resolve_llm', stub_resolve)
	assert asyncio.run(ingest.learn(ws, Config(), FakeChannel(), str(doc))) == 0
	assert (ws.appmap_dir / 'pages' / 'login.md').read_text() == '# Login\nEmail + OTP.\n'

	assert asyncio.run(ingest.learn(ws, Config(), FakeChannel(), str(tmp_path / 'nope.md'))) == 2
