"""D0/S2 spike — does a PyInstaller onedir bundle of nkqa+browser-use actually run?

Not the real sidecar (no HTTP yet): the point is whether the heavy deps survive freezing
and whether a real browser still launches from inside the bundle.
"""

import asyncio
import json
import os
import sys


async def main() -> int:
	from browser_use.browser import BrowserProfile
	from browser_use.browser.session import BrowserSession

	from nkqa import workspace
	from nkqa.ui import TerminalChannel

	chrome = os.environ.get('SPIKE_CHROME', '')
	report = {'frozen': getattr(sys, 'frozen', False), 'nkqa_import': True, 'workspace_api': bool(workspace.slugify('A b'))}

	ch = TerminalChannel()
	await ch.log('bundle running; launching a browser...')
	profile = BrowserProfile(
		headless=True,
		**({'executable_path': chrome, 'args': ['--no-sandbox', '--disable-dev-shm-usage']} if chrome else {}),
	)
	session = BrowserSession(browser_profile=profile)
	await session.start()
	try:
		await session.navigate_to('http://127.0.0.1:8765/')
		cdp = await session.get_or_create_cdp_session()
		report['cdp_ok'] = bool(cdp.target_id)
		report['screenshot_chars'] = len(await session.take_screenshot() or '')
	finally:
		await session.kill()

	print('SPIKE_RESULT ' + json.dumps(report))
	return 0


sys.exit(asyncio.run(main()))
