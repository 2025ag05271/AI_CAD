import os

# Keep test runs out of the real C:\Temp\AI_Logs files; must be set
# before catia_agent.config is first imported.
os.environ.setdefault("CATIA_AGENT_ENABLE_FILE_LOGGING", "false")
