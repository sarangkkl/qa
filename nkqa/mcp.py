"""MCP runtime: config'd servers -> live clients, executor tool registration,
deterministic tool calls. Secret env values use 'env:NAME' indirection, resolved
only at launch so config.yaml never holds a credential.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import contextlib
import os
from types import TracebackType
from typing import Any

from browser_use import Tools
from browser_use.mcp.client import MCPClient

from nkqa.config import Config, MCPServer


def resolve_env(env: dict[str, str]) -> dict[str, str]:
	resolved: dict[str, str] = {}
	for key, value in env.items():
		if value.startswith('env:'):
			var = value[4:]
			if var not in os.environ:
				raise ValueError(f"MCP config wants {key} from ${var}, but it isn't set (add it to .env or the shell)")
			resolved[key] = os.environ[var]
		else:
			resolved[key] = value
	return resolved


def executor_servers(config: Config) -> list[MCPServer]:
	return [s for s in config.mcp_servers if s.expose_to_executor]


class MCPRuntime:
	"""Connects a set of MCP servers for the duration of one command."""

	def __init__(self, servers: list[MCPServer]):
		self.servers = servers
		self.clients: dict[str, MCPClient] = {}

	async def __aenter__(self) -> 'MCPRuntime':
		try:
			for spec in self.servers:
				client = MCPClient(spec.name, spec.command, spec.args, resolve_env(spec.env) or None)
				await client.connect()
				self.clients[spec.name] = client
		except BaseException:
			await self._disconnect_all()
			raise
		return self

	async def __aexit__(
		self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
	) -> None:
		await self._disconnect_all()

	async def _disconnect_all(self) -> None:
		for client in self.clients.values():
			with contextlib.suppress(Exception):
				await client.disconnect()
		self.clients.clear()

	async def register_executor_tools(self, tools: Tools[None]) -> list[str]:
		"""Register every exposed server's tools as agent actions, prefixed by server name."""
		registered: list[str] = []
		for spec in self.servers:
			if not spec.expose_to_executor:
				continue
			client = self.clients[spec.name]
			await client.register_to_tools(tools, prefix=f'{spec.name}_')
			registered += [f'{spec.name}_{t}' for t in client._tools]  # pyright: ignore[reportPrivateUsage]
		return registered

	async def call(self, server: str, tool: str, args: dict[str, Any]) -> str:
		"""Deterministic tool call (no agent, no LLM). Returns the formatted text result."""
		client = self.clients.get(server)
		if client is None:
			raise RuntimeError(f"MCP server '{server}' is not connected (is it in config.yaml mcp: ?)")
		if client.session is None:
			raise RuntimeError(f"MCP server '{server}' has no live session")
		result = await client.session.call_tool(tool, args)
		return client._format_mcp_result(result)  # pyright: ignore[reportPrivateUsage]

	def tool_names(self, server: str) -> list[str]:
		client = self.clients.get(server)
		return list(client._tools) if client else []  # pyright: ignore[reportPrivateUsage]
