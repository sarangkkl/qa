"""Scenario files: parse/serialize, content hash, lifecycle.

One file = one scenario under scenarios/<area>/<slug>.md; id is the relative path
without extension. Approval is bound to content_hash() - editing anything meaningful
after approval makes the scenario 'stale' and the runner refuses it.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml

Runnable = Literal['ok', 'draft', 'stale', 'deprecated']

FRONTMATTER_RE = re.compile(r'^---\n(.*?)\n---\n?(.*)$', re.DOTALL)
STEP_RE = re.compile(r'^(\d+)\.\s+(.*)$')
EXPECT_RE = re.compile(r'^\s*[-*]\s+\*\*Expect:\*\*\s*(.*)$')
BULLET_RE = re.compile(r'^\s*[-*]\s+(.*)$')


@dataclass
class Step:
	action: str
	expect: str = ''


@dataclass
class Scenario:
	id: str
	path: Path
	title: str = ''
	status: str = 'draft'
	ticket: str = ''
	tags: list[str] = field(default_factory=list[str])
	preconditions: list[str] = field(default_factory=list[str])
	steps: list[Step] = field(default_factory=list[Step])
	out_of_scope: list[str] = field(default_factory=list[str])
	approved_hash: str = ''
	approved_by: str = ''
	approved_at: str = ''

	def content_hash(self) -> str:
		"""Hash of everything approval-relevant; formatting and volatile keys excluded."""
		canonical = json.dumps(
			{
				'title': self.title,
				'ticket': self.ticket,
				'tags': self.tags,
				'preconditions': self.preconditions,
				'steps': [[s.action, s.expect] for s in self.steps],
				'out_of_scope': self.out_of_scope,
			},
			sort_keys=True,
		)
		return sha256(canonical.encode()).hexdigest()

	def runnable(self) -> Runnable:
		if self.status == 'deprecated':
			return 'deprecated'
		if self.status != 'approved':
			return 'draft'
		if self.approved_hash != self.content_hash():
			return 'stale'
		return 'ok'


def _sections(body: str) -> dict[str, list[str]]:
	sections: dict[str, list[str]] = {}
	current = ''
	for line in body.splitlines():
		if line.startswith('## '):
			current = line[3:].strip().lower()
			sections[current] = []
		elif current:
			sections[current].append(line)
	return sections


def parse(path: Path, scenarios_dir: Path) -> Scenario:
	text = path.read_text(encoding='utf-8')
	match = FRONTMATTER_RE.match(text)
	if not match:
		raise ValueError(f'{path}: missing --- frontmatter block')
	meta: dict[str, Any] = yaml.safe_load(match.group(1)) or {}
	sections = _sections(match.group(2))

	steps: list[Step] = []
	for line in sections.get('steps', []):
		if m := STEP_RE.match(line):
			steps.append(Step(action=m.group(2).strip()))
		elif (m := EXPECT_RE.match(line)) and steps:
			steps[-1].expect = (steps[-1].expect + ' ' + m.group(1).strip()).strip()
		elif line.strip() and steps:
			steps[-1].action += ' ' + line.strip()

	out_of_scope = [m.group(1).strip() for line in sections.get('out of scope', []) if (m := BULLET_RE.match(line))]

	tags_raw: list[Any] = meta.get('tags') or []
	preconditions_raw: list[Any] = meta.get('preconditions') or []
	sid = str(path.relative_to(scenarios_dir).with_suffix(''))
	return Scenario(
		id=sid,
		path=path,
		title=str(meta.get('title', sid)),
		status=str(meta.get('status', 'draft')),
		ticket=str(meta.get('ticket') or ''),
		tags=[str(t) for t in tags_raw],
		preconditions=[str(p) for p in preconditions_raw],
		steps=steps,
		out_of_scope=out_of_scope,
		approved_hash=str(meta.get('approved_hash') or ''),
		approved_by=str(meta.get('approved_by') or ''),
		approved_at=str(meta.get('approved_at') or ''),
	)


def serialize(s: Scenario) -> str:
	meta: dict[str, Any] = {'title': s.title, 'status': s.status}
	if s.ticket:
		meta['ticket'] = s.ticket
	if s.tags:
		meta['tags'] = s.tags
	if s.preconditions:
		meta['preconditions'] = s.preconditions
	if s.approved_hash:
		meta['approved_hash'] = s.approved_hash
		meta['approved_by'] = s.approved_by
		meta['approved_at'] = s.approved_at
	lines = ['---', yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip(), '---', '', '## Steps']
	for i, step in enumerate(s.steps, 1):
		lines.append(f'{i}. {step.action}')
		if step.expect:
			lines.append(f'   - **Expect:** {step.expect}')
	if s.out_of_scope:
		lines += ['', '## Out of scope']
		lines += [f'- {item}' for item in s.out_of_scope]
	return '\n'.join(lines) + '\n'


def save(s: Scenario) -> None:
	s.path.parent.mkdir(parents=True, exist_ok=True)
	s.path.write_text(serialize(s), encoding='utf-8')


def load_all(scenarios_dir: Path) -> list[Scenario]:
	if not scenarios_dir.is_dir():
		return []
	return [parse(p, scenarios_dir) for p in sorted(scenarios_dir.rglob('*.md'))]


def find(scenarios_dir: Path, scenario_id: str) -> Scenario | None:
	path = scenarios_dir / f'{scenario_id}.md'
	return parse(path, scenarios_dir) if path.is_file() else None


def approve(s: Scenario, approver: str) -> None:
	s.status = 'approved'
	s.approved_hash = s.content_hash()
	s.approved_by = approver
	s.approved_at = datetime.now().isoformat(timespec='seconds')
	save(s)


def git_identity(cwd: Path) -> str:
	try:
		name = subprocess.run(['git', 'config', 'user.name'], cwd=cwd, capture_output=True, text=True).stdout.strip()
		email = subprocess.run(['git', 'config', 'user.email'], cwd=cwd, capture_output=True, text=True).stdout.strip()
		return f'{name} <{email}>' if email else (name or 'unknown')
	except OSError:
		return 'unknown'
