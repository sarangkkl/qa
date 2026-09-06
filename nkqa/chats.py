"""Conversations, kept next to the project.

Phase 5 deliberately threw the routing agent's messages away each turn. The desktop needs
them to survive a restart, and the reasoning behind a scenario is worth reviewing in a PR -
so chats are plain JSON in the workspace, committed like everything else.

A turn records what the agent *did*, not only what it said: the command it ran and the exit
code land in the transcript. That is the part a QA lead screenshots.

Never write a credential here. Values live in HumanInTheLoop.secrets and nowhere else.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from nkqa.workspace import Workspace, slugify

Role = Literal['user', 'assistant', 'event']
CONTEXT_TURNS = 20  # what the router replays; the appmap is the durable memory, not this


@dataclass
class Turn:
	role: Role
	text: str = ''
	command: str = ''
	args: dict[str, str] = field(default_factory=dict[str, str])
	exit: int | None = None
	at: str = ''

	def __post_init__(self) -> None:
		self.at = self.at or datetime.now().isoformat(timespec='seconds')


@dataclass
class Chat:
	id: str
	title: str = ''
	created: str = ''
	updated: str = ''
	turns: list[Turn] = field(default_factory=list[Turn])

	def add(self, turn: Turn) -> Turn:
		self.turns.append(turn)
		self.updated = turn.at
		if not self.title and turn.role == 'user' and turn.text:
			self.title = turn.text[:60]
		return turn

	def recent(self, limit: int = CONTEXT_TURNS) -> list[Turn]:
		"""What to replay into the model. A 200-turn chat is not a haiku-sized prompt."""
		return self.turns[-limit:]

	def summary(self) -> dict[str, Any]:
		return {
			'id': self.id,
			'title': self.title or '(untitled)',
			'created': self.created,
			'updated': self.updated,
			'turns': len(self.turns),
		}


def _path(ws: Workspace, chat_id: str) -> Path:
	return ws.chats_dir / f'{chat_id}.json'


def new_chat(ws: Workspace, title: str = '') -> Chat:
	stamp = datetime.now()
	base = f'{stamp:%Y%m%d-%H%M%S}' + (f'-{slugify(title)}' if title else '')
	chat = Chat(
		id=base, title=title, created=stamp.isoformat(timespec='seconds'), updated=stamp.isoformat(timespec='seconds')
	)
	save(ws, chat)
	return chat


def load(ws: Workspace, chat_id: str) -> Chat | None:
	path = _path(ws, chat_id)
	if not path.is_file():
		return None
	try:
		loaded: Any = json.loads(path.read_text(encoding='utf-8'))
	except json.JSONDecodeError:
		return None
	raw = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}
	turns: list[Turn] = []
	raw_turns: list[Any] = raw.get('turns') or []
	for raw_turn in raw_turns:
		item = cast(dict[str, Any], raw_turn) if isinstance(raw_turn, dict) else {}
		raw_args: dict[str, Any] = item.get('args') or {}
		exit_code = item.get('exit')
		turns.append(
			Turn(
				role=cast(Role, item.get('role') or 'user'),
				text=str(item.get('text') or ''),
				args={str(k): str(v) for k, v in raw_args.items()},
				command=str(item.get('command') or ''),
				exit=int(exit_code) if isinstance(exit_code, int) else None,
				at=str(item.get('at') or ''),
			)
		)
	return Chat(
		id=str(raw.get('id') or chat_id),
		title=str(raw.get('title') or ''),
		created=str(raw.get('created') or ''),
		updated=str(raw.get('updated') or ''),
		turns=turns,
	)


def save(ws: Workspace, chat: Chat) -> Path:
	ws.chats_dir.mkdir(parents=True, exist_ok=True)
	path = _path(ws, chat.id)
	path.write_text(json.dumps(asdict(chat), indent=1), encoding='utf-8')
	return path


def list_chats(ws: Workspace) -> list[dict[str, Any]]:
	"""Newest first."""
	if not ws.chats_dir.is_dir():
		return []
	chats = [c for c in (load(ws, p.stem) for p in ws.chats_dir.glob('*.json')) if c is not None]
	return [c.summary() for c in sorted(chats, key=lambda c: c.updated, reverse=True)]


def delete(ws: Workspace, chat_id: str) -> bool:
	path = _path(ws, chat_id)
	if not path.is_file():
		return False
	path.unlink()
	return True
