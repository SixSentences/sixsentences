# Third-party notices

Direct dependencies are exactly pinned in `pyproject.toml`, and `uv.lock`
records artifact hashes for the resolved environment. Principal runtime
components include Alembic, FastAPI, HTTPX, Pillow, psycopg, Pydantic,
pydantic-settings, pypdf, SQLAlchemy, Uvicorn, WebSockets and the sibling
SixSentences engine. Their upstream license terms apply; redistributors must
include the license texts required by the artifacts they ship.

Optional document and audio helpers may invoke separately installed system
tools or services. Those tools and services are not bundled or relicensed by
this directory. An operator-selected AI, speech, SMTP, literature or document
processing service has its own terms and data-processing boundary.
