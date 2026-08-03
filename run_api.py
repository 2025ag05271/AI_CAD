import uvicorn

from catia_agent.config import settings


def main() -> None:
    uvicorn.run(
        "app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=(
            settings.environment == "development"
        ),
        access_log=False,
    )


if __name__ == "__main__":
    main()