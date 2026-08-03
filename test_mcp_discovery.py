import asyncio
import json

from fastmcp import Client


MCP_SERVER_URL = "http://127.0.0.1:8001/mcp"


async def main() -> None:
    client = Client(MCP_SERVER_URL)

    async with client:
        tools = await client.list_tools()

        print(
            f"Connected to the CATIA MCP server."
        )

        print(
            f"Discovered {len(tools)} tools.\n"
        )

        for tool in tools:
            if hasattr(tool, "model_dump"):
                tool_data = tool.model_dump(
                    by_alias=True
                )
            else:
                tool_data = {
                    "name": getattr(
                        tool,
                        "name",
                        "unknown",
                    ),
                    "description": getattr(
                        tool,
                        "description",
                        None,
                    ),
                    "inputSchema": getattr(
                        tool,
                        "inputSchema",
                        {},
                    ),
                }

            print(
                json.dumps(
                    tool_data,
                    indent=2,
                    default=str,
                )
            )

            print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())