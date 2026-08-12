"""MCP client test — exercise the server through the protocol, not by direct calls.

Uses the SDK's in-memory transport, so this verifies schema generation,
tool dispatch and result shapes without launching a subprocess.
"""
import asyncio
import json

from mcp.client import Client

import server


async def main() -> None:
    async with Client(server.mcp) as client:

        result = await client.list_tools()
        tools = getattr(result, "tools", result)

        print(f"=== {len(tools)} tools registered ===\n")
        for t in tools:
            first_line = (t.description or "").strip().splitlines()[0]
            print(f"{t.name}")
            print(f"  description: {first_line}")
            print(f"  input_schema: {json.dumps(t.input_schema, indent=2)}")
            print()

        print("=== call_tool('search_pages', keyword='ski touring', limit=3) ===")
        out = await client.call_tool("search_pages", {"keyword": "ski touring", "limit": 3})
        for block in getattr(out, "content", []):
            print(getattr(block, "text", block))


if __name__ == "__main__":
    asyncio.run(main())
