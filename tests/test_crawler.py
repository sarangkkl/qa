import asyncio
from pathlib import Path

from nkqa import crawler, workspace
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop


def test_build_task(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	(ws.appmap_dir / 'quirks.md').write_text('slow tables')
	task = crawler.build_task(Config(base_url='https://shop.test'), {'quirks.md': 'slow tables'}, 7)
	assert 'https://shop.test' in task
	assert 'budget: 7' in task.lower()
	assert 'slow tables' in task  # current appmap included for merge


def test_crawl_requires_base_url(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	hitl = HumanInTheLoop(ws.permissions_file)
	assert asyncio.run(crawler.crawl(ws, Config(base_url=''), hitl, 5)) == 2
	assert not any(ws.runs_dir.glob('crawl--*'))
