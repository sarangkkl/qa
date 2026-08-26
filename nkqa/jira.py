"""Jira verbs over MCP - no HTTP code lives here.

Talks to whatever server is configured as mcp.jira (default: the official Atlassian
remote MCP, bridged to stdio by mcp-remote). Tool names are overridable via
mcp.jira.tools in config.yaml; the official server sometimes wants a cloudId, so
calls retry once with the first accessible cloud id.
"""

import re
from pathlib import Path
from typing import Any

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
