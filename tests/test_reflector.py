import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import reflector, workspace
from nkqa.appmap import AppmapUpdate, FileUpdate
from nkqa.config import Config


def make_run(tmp_path: Path) -> Path:
	run_dir = tmp_path / 'runs' / 'demo--20260826-1200'
	run_dir.mkdir(parents=True)
	(run_dir / 'results.json').write_text(json.dumps({'scenario_id': 'a/b', 'verdict': 'fail', 'result': None}))
	(run_dir / 'history.json').write_text(
		json.dumps(
			{
				'history': [
					{'state': {'url': 'https://shop.test/login'}, 'result': [{'error': None}]},
					{'state': {'url': 'https://shop.test/cart'}, 'result': [{'error': 'Timeout waiting for #coupon'}]},
					{'state': {'url': 'https://shop.test/cart'}, 'result': []},
				]
			}
		)
	)
	return run_dir


def test_run_facts(tmp_path: Path) -> None:
	facts = reflector.run_facts(make_run(tmp_path))
	assert 'https://shop.test/login' in facts and 'https://shop.test/cart' in facts
	assert facts.count('shop.test/cart') == 1  # deduped
	assert 'Timeout waiting for #coupon' in facts
	assert '"verdict": "fail"' in facts
	assert 'Steps executed: 3' in facts


class StubLLM:
	def __init__(self, update: AppmapUpdate | Exception):
		self.update = update

	async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
		if isinstance(self.update, Exception):
			raise self.update

		class R:
			completion = self.update

		return R()


def stub(monkeypatch: pytest.MonkeyPatch, update: AppmapUpdate | Exception) -> None:
	def fake(cfg: Config, role: str, override: str | None = None) -> StubLLM:
		return StubLLM(update)

	monkeypatch.setattr(reflector, 'resolve_llm', fake)


def test_reflect_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	ws = workspace.create(tmp_path)
	run_dir = make_run(tmp_path)
	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='quirks.md', content='coupon field is slow')]))
	written = asyncio.run(reflector.reflect(ws, Config(), FakeChannel(), run_dir))
	assert written == ['quirks.md']
	assert 'slow' in (ws.appmap_dir / 'quirks.md').read_text()


def test_auto_reflect_never_raises_and_respects_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	ws = workspace.create(tmp_path)
	run_dir = make_run(tmp_path)

	stub(monkeypatch, RuntimeError('LLM exploded'))
	asyncio.run(reflector.auto_reflect(ws, Config(), FakeChannel(), run_dir))  # must not raise

	stub(monkeypatch, AppmapUpdate(files=[FileUpdate(file='quirks.md', content='x')]))
	asyncio.run(reflector.auto_reflect(ws, Config(auto_reflect=False), FakeChannel(), run_dir))
	assert not (ws.appmap_dir / 'quirks.md').exists()  # toggle off -> zero writes
