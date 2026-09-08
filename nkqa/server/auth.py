"""Who may talk to this sidecar.

A local port is reachable by every browser tab on the machine, so neither check here is
decoration: the token proves the caller is the app Tauri launched, and the Origin check
stops a web page that somehow learned the port from driving a QA run.
"""

import secrets

from fastapi import Header, HTTPException, Query

# Tauri's webview origins, plus the Vite dev server used while building the frontend.
ALLOWED_ORIGINS = frozenset(
	{
		'tauri://localhost',  # macOS / Linux
		'https://tauri.localhost',  # Windows
		'http://tauri.localhost',
		'http://localhost:1420',  # vite dev
		'http://127.0.0.1:1420',
	}
)

_token: str = ''


def new_token() -> str:
	global _token
	_token = secrets.token_hex(32)
	return _token


def set_token(value: str) -> None:
	global _token
	_token = value


def token_ok(candidate: str | None) -> bool:
	return bool(candidate) and bool(_token) and secrets.compare_digest(candidate or '', _token)


def origin_ok(origin: str | None) -> bool:
	"""No Origin at all is a non-browser caller (curl, websocat, tests) - allowed.

	A browser always sends one, so a page on some other origin is rejected by name.
	"""
	return origin is None or origin in ALLOWED_ORIGINS


def require_token(
	authorization: str | None = Header(default=None),
	token: str | None = Query(default=None),
	origin: str | None = Header(default=None),
) -> None:
	"""HTTP dependency. Accepts `Authorization: Bearer <token>` or `?token=`."""
	bearer = authorization.removeprefix('Bearer ').strip() if authorization else None
	if not token_ok(bearer or token):
		raise HTTPException(status_code=401, detail='bad or missing token')
	if not origin_ok(origin):
		raise HTTPException(status_code=403, detail='origin not allowed')
