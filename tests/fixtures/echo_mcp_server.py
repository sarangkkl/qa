"""Tiny stdio MCP server used by tests. Real protocol, no network."""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP('echo')


@server.tool()
def echo(text: str) -> str:
	return f'echo: {text}'


@server.tool()
def read_secret() -> str:
	return f'secret={os.environ.get("FIXTURE_SECRET", "missing")}'


if __name__ == '__main__':
	server.run()
