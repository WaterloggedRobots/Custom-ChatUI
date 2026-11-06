"""A stand-in for REA's MCP server, for test_rea.py: a few of its tool names, one that crashes.

Run by the tests as `python fake_rea_server.py`; it speaks MCP over stdio like `npx rea-agents mcp`."""

import os
import sys
import time

from mcp.server.mcpserver import MCPServer

server = MCPServer("rea")


@server.tool()
def binary_overview() -> str:
    """Return the bound document and counts."""
    return f"overview of pid {os.getpid()}"


@server.tool()
def search_strings(pattern: str, case_sensitive: bool = False) -> str:
    """Search strings."""
    return f"strings matching {pattern!r} (case_sensitive={case_sensitive}) " + "x" * int(os.environ.get("FAKE_REA_PAD", "0"))


@server.tool()
def open_binary(path: str) -> str:
    """Open a local executable."""
    return f"opened {path}"


@server.tool()
def gdb_console(command: str) -> str:
    """Never exposed by the bridge."""
    return "gdb ran " + command


@server.tool()
def trace_feature(query: str) -> str:
    """Crashes the server when asked to (query 'crash'), or takes a while (query 'slow')."""
    if query == "crash":
        os._exit(3)
    if query == "slow":
        time.sleep(30)
    return f"traced {query}"


if __name__ == "__main__":
    print("fake REA starting", file=sys.stderr)
    server.run()
