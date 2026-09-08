"""D0/S1 spike — can we stream a live browser view out of browser-use via CDP?

Questions: is Page.startScreencast reachable through the CDP session browser-use already
holds; does browser-use's own screenshotting still work afterwards; what fps and byte
rate do we get. Throwaway - not part of the product.

Run: (cd /tmp/spikeweb && python3 -m http.server 8765 &) ; venv/bin/python spike_screencast.py
"""

import asyncio
import base64
import os
import time
from typing import Any

from browser_use.browser import BrowserProfile
from browser_use.browser.session import BrowserSession

URL = 'http://127.0.0.1:8765/'
CHROME = os.environ.get('SPIKE_CHROME', '')  # unset on a normal machine; set in this container
SECONDS = 6.0

FRAMES: list[tuple[float, int]] = []


async def main() -> None:
	profile = BrowserProfile(
		headless=True,
		**({'executable_path': CHROME, 'args': ['--no-sandbox', '--disable-dev-shm-usage']} if CHROME else {}),
	)
	session = BrowserSession(browser_profile=profile)
	await session.start()
	try:
		await session.navigate_to(URL)
		cdp = await session.get_or_create_cdp_session()
		print(f'cdp session ok · target={cdp.target_id[:12]} · connected={session.is_cdp_connected}')

		client = cdp.cdp_client
		started = time.monotonic()
		acks: list[asyncio.Task[Any]] = []

		def on_frame(event: Any, session_id: str | None = None) -> None:
			FRAMES.append((time.monotonic() - started, len(base64.b64decode(event['data']))))
			acks.append(
				asyncio.create_task(
					client.send.Page.screencastFrameAck(
						params={'sessionId': event['sessionId']}, session_id=session_id
					)
				)
			)

		client.register.Page.screencastFrame(on_frame)
		await client.send.Page.startScreencast(
			params={'format': 'jpeg', 'quality': 60, 'maxWidth': 1280, 'maxHeight': 800, 'everyNthFrame': 1},
			session_id=cdp.session_id,
		)
		print(f'screencast started; collecting for {SECONDS:.0f}s...')
		await asyncio.sleep(SECONDS)
		await client.send.Page.stopScreencast(session_id=cdp.session_id)
		await asyncio.gather(*acks, return_exceptions=True)

		shot = await session.take_screenshot()
		print(f'browser-use screenshot after screencast: {len(shot or "")} base64 chars (coexists: {bool(shot)})')
	finally:
		await session.kill()

	if not FRAMES:
		print('\nRESULT: NO FRAMES — Stage 2 is not reachable this way.')
		return
	span = (FRAMES[-1][0] - FRAMES[0][0]) or 1
	avg = sum(size for _, size in FRAMES) / len(FRAMES)
	fps = len(FRAMES) / span
	print(f'\nRESULT: {len(FRAMES)} frames in {span:.1f}s = {fps:.1f} fps')
	print(f'        avg frame {avg / 1024:.0f} KB → ~{avg * fps / 1024:.0f} KB/s over the socket')


asyncio.run(main())
