from catia_agent.config import settings


print(
    settings.model_dump_json(
        indent=2,
    )
)