"""Jira verbs over MCP - no HTTP code lives here.

Talks to whatever server is configured as mcp.jira (default: the official Atlassian
remote MCP, bridged to stdio by mcp-remote). Tool names are overridable via
mcp.jira.tools in config.yaml; the official server sometimes wants a cloudId, so
calls retry once with the first accessible cloud id.
"""

import json
import re
from pathlib import Path
from typing import Any, cast

from nkqa.config import Config, MCPServer
from nkqa.execution.report import RunRecord, StepVerdict
from nkqa.mcp import MCPRuntime
from nkqa.scenarios import Scenario

ISSUE_KEY_RE = re.compile(r'\b[A-Z][A-Z0-9]+-\d+\b')
UUID_RE = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')
DEFAULT_TOOLS = {
	'get_issue': 'getJiraIssue',
	'create_issue': 'createJiraIssue',
	'resources': 'getAccessibleAtlassianResources',
}
MAX_TICKET_CHARS = 8000


def issue_key(reference: str) -> str:
	"""'https://x.atlassian.net/browse/PNY-3689?focus=1' -> 'PNY-3689'. '' when there is none.

	People paste the URL, because that is what Jira gives them. Everything downstream needs the
	bare key: `fetch_issue` sends it as `issueIdOrKey` and then checks the key appears in the
	reply, so a URL fails twice over, and the Scenarios field's `.toUpperCase()` turned one into
	`HTTPS://.../BROWSE/PNY-3689`. Normalising here means the chat box, that field and the CLI
	all accept either form.
	"""
	found = ISSUE_KEY_RE.search(reference.strip().upper())
	return found.group(0) if found else ''


def jira_server(config: Config) -> MCPServer:
	spec = config.mcp_server('jira')
	if spec is None:
		raise RuntimeError(
			'No jira server in config.yaml. Add:\n'
			'mcp:\n  jira:\n    command: npx\n'
			"    args: ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']"
		)
	return spec


def _tool(spec: MCPServer, logical: str) -> str:
	return spec.tools.get(logical, DEFAULT_TOOLS[logical])


def _looks_like_cloud_id_error(text: str) -> bool:
	lowered = text.lower()
	return 'cloudid' in lowered and ('error' in lowered or 'required' in lowered or 'invalid' in lowered)


async def _cloud_id(rt: MCPRuntime, spec: MCPServer) -> str:
	text = await rt.call(spec.name, _tool(spec, 'resources'), {})
	match = UUID_RE.search(text)
	if not match:
		raise RuntimeError(f'Could not find a cloud id in {_tool(spec, "resources")} response: {text[:200]}')
	return match.group(0)


async def _call_with_cloud_retry(rt: MCPRuntime, spec: MCPServer, tool: str, args: dict[str, Any]) -> str:
	text = await rt.call(spec.name, tool, args)
	if _looks_like_cloud_id_error(text):
		text = await rt.call(spec.name, tool, {**args, 'cloudId': await _cloud_id(rt, spec)})
	return text


async def fetch_issue(rt: MCPRuntime, spec: MCPServer, key: str) -> str:
	"""The ticket as a text block for the planner context (raw tool result, truncated)."""
	text = await _call_with_cloud_retry(rt, spec, _tool(spec, 'get_issue'), {'issueIdOrKey': key})
	if key not in text:
		raise RuntimeError(f'Jira did not return {key}: {text[:300]}')
	return text[:MAX_TICKET_CHARS]


def _plain(value: Any) -> str:
	"""Jira descriptions come back as markdown text or as an ADF document. Flatten both."""
	if isinstance(value, str):
		return value
	if isinstance(value, dict):
		node = cast(dict[str, Any], value)
		text = str(node.get('text', ''))
		children: list[Any] = list(cast(list[Any], node.get('content', [])))
		return text + ''.join(_plain(c) for c in children) + ('\n' if node.get('type') == 'paragraph' else '')
	if isinstance(value, list):
		return ''.join(_plain(v) for v in cast(list[Any], value))
	return ''


def summarize_issue(raw: str) -> str:
	"""The fields a QA cares about, or the raw text when it does not parse.

	getJiraIssue returns the whole REST payload - avatar URLs, self links, expand strings.
	Handing that to the model burns the context budget on noise and puts a wall of JSON in the
	chat where a person has to read it. Falls back to raw rather than losing anything.
	"""
	start = raw.find('{')
	if start < 0:
		return raw
	try:
		issue = cast(dict[str, Any], json.loads(raw[start:]))
		fields = cast(dict[str, Any], issue.get('fields') or {})
	except (ValueError, AttributeError):
		return raw
	if not fields:
		return raw

	def named(key: str) -> str:
		value = fields.get(key)
		return str(cast(dict[str, Any], value).get('name', '')) if isinstance(value, dict) else ''

	head = f'{issue.get("key", "")}  {fields.get("summary", "")}'.strip()
	meta = ' · '.join(p for p in (named('issuetype'), named('status'), named('priority')) if p)
	body = _plain(fields.get('description')).strip() or '(no description)'
	return '\n'.join([head, *([meta] if meta else []), '', body])


async def read_issue(config: Config, ch: Any, key: str) -> tuple[str, str]:
	"""(ticket text, error). Never raises - the one place Jira failures become a message.

	This used to be inline in the planner with no try/except at all, so a Jira hiccup escaped
	the handler, killed the task that sends the `result` frame, and left the job at `working…`
	for good.
	"""
	try:
		spec = jira_server(config)
	except RuntimeError as e:
		return '', f'❌ {e}'

	await ch.log(f'🎫 Fetching {key} via MCP server "{spec.name}"...')
	try:
		async with MCPRuntime([spec]) as rt:
			return summarize_issue(await fetch_issue(rt, spec, key)), ''
	except Exception as e:
		hint = ''
		# browser-use's MCP client gives connect a hard 10s, which a cold `npx -y mcp-remote`
		# or an expired token both blow through, and the two look identical from here. `qa auth`
		# spawns the same command with 300s and a browser, which is the way out of both.
		if 'Failed to connect' in str(e) or isinstance(e, TimeoutError):
			hint = '\n   Jira did not answer within 10s. Sign in (or warm it up) with:  /auth jira'
		return '', f'❌ Could not read {key} ({type(e).__name__}: {str(e)[:200]}){hint}'


async def create_bug(rt: MCPRuntime, spec: MCPServer, project: str, summary: str, description: str) -> str:
	"""Create a Bug issue; returns the new issue key."""
	args: dict[str, Any] = {
		'projectKey': project,
		'issueTypeName': 'Bug',
		'summary': summary,
		'description': description,
	}
	text = await _call_with_cloud_retry(rt, spec, _tool(spec, 'create_issue'), args)
	keys = [k for k in ISSUE_KEY_RE.findall(text) if k.startswith(f'{project}-')] or ISSUE_KEY_RE.findall(text)
	if not keys:
		raise RuntimeError(f'Bug creation returned no issue key: {text[:300]}')
	return keys[0]


def failed_steps(record: RunRecord) -> list[StepVerdict]:
	if record.result is None:
		return []
	return [v for v in record.result.steps if v.verdict in ('fail', 'blocked')]


def compose_bug(
	scenario: Scenario, record: RunRecord, failure: StepVerdict, run_dir: Path, base_url: str
) -> tuple[str, str]:
	"""Bug (summary, description) from one failed step of a run."""
	step = scenario.steps[failure.step - 1] if failure.step <= len(scenario.steps) else None
	action = step.action if step else f'step {failure.step}'
	summary = f'[{scenario.id}] step {failure.step} {failure.verdict}s: {action}'[:200]

	repro = [f'{i}. {s.action}' for i, s in enumerate(scenario.steps[: failure.step], 1)]
	lines = [
		f'Found by QA scenario *{scenario.title}* ({scenario.id}, approved hash {record.approved_hash[:12]}).',
		f'Application: {base_url or "see workspace config"}',
		'',
		'Steps to reproduce:',
		*repro,
		'',
		f'Expected: {step.expect if step and step.expect else "see scenario step"}',
		f'Actual: {failure.note or failure.verdict}',
		'',
		f'Evidence (local run dir): {run_dir}',
	]
	if scenario.ticket:
		lines.append(f'Related ticket: {scenario.ticket}')
	return summary, '\n'.join(lines)
