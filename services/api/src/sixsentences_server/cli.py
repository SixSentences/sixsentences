"""Administrative commands for a community deployment."""

from __future__ import annotations

import argparse
import json
from importlib.resources import files
from pathlib import Path

import uvicorn
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from sixsentences_server.config import get_settings
from sixsentences_server.database import create_database


def _alembic_config() -> Config:
    service_root = Path(__file__).resolve().parents[2]
    source_migrations = service_root / "alembic"
    if source_migrations.is_dir():
        config = Config(service_root / "alembic.ini")
        migrations = source_migrations
    else:
        config = Config()
        migrations = Path(str(files("sixsentences_server").joinpath("_alembic")))
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return config


def main() -> None:
    """Run the API, migrate its clean schema, or check the database connection."""

    parser = argparse.ArgumentParser(description="Operate the SixSentences community API")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="run the ASGI server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--workers", default=1, type=int)
    subcommands.add_parser("migrate", help="upgrade the community schema to the latest revision")
    subcommands.add_parser("check", help="check configuration and database connectivity")
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run(
            "sixsentences_server.app:app",
            host=args.host,
            port=args.port,
            workers=args.workers,
        )
        return
    if args.command == "migrate":
        settings = get_settings()
        if settings.sqlite_path is not None:
            settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        command.upgrade(_alembic_config(), "head")
        return

    settings = get_settings()
    database = create_database(settings)
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        database.dispose()
    print(json.dumps({"database": "reachable", "environment": settings.environment}))


if __name__ == "__main__":
    main()
