"""Sidecar entrypoint: bind a loopback port, print the handshake, serve.

Tauri spawns `nkqa-server --workspace <path>` and reads exactly one line of JSON from
stdout to learn where to connect and with what token. Nothing else is ever written to
stdout - a stray print here would corrupt the handshake, which is why the product's
narration goes through the Channel instead.
"""

import argparse
import contextlib
import json
import os
import signal
import socket
import sys
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from nkqa import workspace as workspace_mod
from nkqa.server import auth
from nkqa.server.app import create_app

HOST = '127.0.0.1'  # never 0.0.0.0: this port must not leave the machine
BACKLOG = 128


def bind_port(preferred: int = 0) -> tuple[socket.socket, int]:
	"""Bind AND listen before the handshake is printed.

	Binding alone does not queue connections: a client that reads the handshake and
	connects immediately would get ECONNREFUSED in the window before uvicorn calls
	listen(). Listening here makes the kernel hold those connections until uvicorn
	accepts them, so "ready" on stdout really means ready.
	"""
	sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	sock.bind((HOST, preferred))
	sock.listen(BACKLOG)
	return sock, sock.getsockname()[1]


def exit_when_parent_does() -> None:
	"""Shut down when whoever spawned us goes away.

	The desktop shell kills its sidecars on close, but a crash or a force-quit runs no
	cleanup, and an abandoned server would sit there holding a browser forever. Our stdin is
	a pipe from the parent, so it hits EOF the moment that process dies - that is the signal.
	Opt-in (--exit-with-parent), because a sidecar started from a terminal with stdin closed
	would otherwise exit on the spot.
	"""

	def watch() -> None:
		with contextlib.suppress(Exception):
			sys.stdin.buffer.read()  # blocks until the parent's end of the pipe closes
		os.kill(os.getpid(), signal.SIGTERM)  # uvicorn's own handler, so shutdown stays clean

	threading.Thread(target=watch, daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog='nkqa-server', description=__doc__)
	parser.add_argument('--workspace', default='.', help='workspace directory')
	parser.add_argument('--port', type=int, default=0, help='0 (default) picks a free port')
	parser.add_argument('--log-level', default='warning', help='uvicorn log level')
	parser.add_argument('--init', action='store_true', help='create the workspace layout if it is not there yet')
	# Only meaningful with --init. Never put a secret on argv: it is world-readable via `ps`.
	parser.add_argument('--app-name', default='', help='with --init: app name for config.yaml')
	parser.add_argument('--base-url', default='', help='with --init: base URL for config.yaml')
	parser.add_argument(
		'--exit-with-parent',
		action='store_true',
		help='shut down when stdin closes, i.e. when the process that spawned us dies',
	)
	return parser


def create_or_fail(root: Path, app_name: str, base_url: str) -> str:
	"""Create a workspace at exactly `root`. Returns '' on success, else the error to report.

	`workspace.at`, not `find`: `find` walks up, so initialising a folder inside an existing
	workspace would quietly adopt the parent and create nothing. Refuses $HOME and the
	filesystem root, because those are the two places a mis-click really costs something -
	everywhere else, including a populated app repo, is the expected case.
	"""
	if root == Path.home() or root == Path(root.anchor):
		return f'refusing to create a workspace directly in {root}. Pick or make a project folder.'
	try:
		workspace_mod.create(root, app_name, base_url)
	except OSError as e:
		return f'cannot create a workspace in {root}: {e}'
	return ''


def main() -> None:
	args = build_parser().parse_args()
	root = Path(args.workspace).expanduser().resolve()

	if args.init and workspace_mod.at(root) is None:
		problem = create_or_fail(root, args.app_name, args.base_url)
		if problem:
			print(json.dumps({'ready': False, 'error': problem}), flush=True)
			sys.exit(2)

	ws = workspace_mod.find(root)
	if ws is None:
		print(json.dumps({'ready': False, 'error': f'no QA workspace at {root}'}), flush=True)
		sys.exit(2)

	# Per-workspace env, loaded in this process only - which is why there is one process
	# per open workspace: load_dotenv mutates the global environment.
	load_dotenv(ws.root / '.env')

	if args.exit_with_parent:
		exit_when_parent_does()

	sock, port = bind_port(args.port)
	print(json.dumps({'ready': True, 'port': port, 'token': auth.new_token(), 'workspace': str(ws.root)}), flush=True)

	uvicorn.run(create_app(ws), fd=sock.fileno(), log_level=args.log_level, access_log=False)


if __name__ == '__main__':
	main()
