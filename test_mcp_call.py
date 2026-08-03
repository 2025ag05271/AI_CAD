import asyncio

from fastmcp import Client


MCP_SERVER_URL = "http://127.0.0.1:8001/mcp"


async def main() -> None:
    client = Client(MCP_SERVER_URL)

    async with client:
        result = await client.call_tool(
            "open_cad_document",
            {
                "filePath": (
                    r"C:\TestData\Gearbox.pdf"
                )
            },
        )

        print("MCP tool result:")
        print(result)


if __name__ == "__main__":
    asyncio.run(main())