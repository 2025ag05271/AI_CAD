import os

# Keep test runs out of the real C:\Temp\AI_Logs files; must be set
# before catia_agent.config is first imported.
os.environ.setdefault("CATIA_AGENT_ENABLE_FILE_LOGGING", "false")

# Unit tests use fake tool definitions that are not in the pinned
# manifest; tests/test_mcp_guardrails.py exercises pinning explicitly.
os.environ.setdefault("CATIA_AGENT_MCP_TOOL_PINNING_MODE", "off")
