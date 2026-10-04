"""Does a freshly built server actually start? python3 scripts/smoke_server.py dist/nkqa-server/nkqa-server

Runs it the way the desktop does: `--standby`, then hands it a brand-new workspace (created via
`init`), waits for the handshake, asks /health, and closes stdin - after which it must exit by
itself. CI runs this on every platform, so a module PyInstaller missed on Windows or Linux fails
the build rather than someone's install. Stdlib only. Exit 0 on success.
"""

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

LOAD_TIMEOUT = 300  # a cold CI runner, first launch, Windows Defender scanning every file


def read_line(proc: subprocess.Popen[bytes], deadline: float) -> dict[str, object]:
	assert proc.stdout
	while time.time() < deadline:
		line = proc.stdout.readline()
		if not line:
			raise SystemExit(f'the server exited (code {proc.poll()}) before answering')
		if line.strip():
			return json.loads(line)
	raise SystemExit('timed out waiting for the server')


def main() -> int:
	if len(sys.argv) != 2:
		print(__doc__.strip().splitlines()[0])
		return 2
	server = Path(sys.argv[1]).resolve()
	workspace = Path(tempfile.mkdtemp(prefix='kiwame-smoke-')) / 'shop'
	started = time.time()
	proc = subprocess.Popen([str(server), '--standby', '--exit-with-parent'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
	assert proc.stdin
	try:
		standby = read_line(proc, started + LOAD_TIMEOUT)
		assert standby == {'standby': True}, standby
		print(f'standby loaded in {time.time() - started:.1f}s')
		request = {'workspace': str(workspace), 'init': {'app_name': 'Smoke Shop', 'base_url': 'http://127.0.0.1:9'}}
		proc.stdin.write(json.dumps(request).encode() + b'\n')
		proc.stdin.flush()
		handshake = read_line(proc, time.time() + 60)
		assert handshake.get('ready') is True, handshake
		health = urllib.request.Request(
			f'http://127.0.0.1:{handshake["port"]}/health', headers={'Authorization': f'Bearer {handshake["token"]}'}
		)
		with urllib.request.urlopen(health, timeout=30) as response:
			body = json.loads(response.read())
		print(f'ready; /health says workspace={body.get("workspace")} nkqa={body.get("nkqa")}')
		proc.stdin.close()
		proc.wait(timeout=30)
		print('exited when its parent went away - ok')
		return 0
	finally:
		if proc.poll() is None:
			proc.kill()


if __name__ == '__main__':
	sys.exit(main())
