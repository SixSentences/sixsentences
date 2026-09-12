"""Application settings.

Everything is overridable via environment variables prefixed with SIX_
(e.g. SIX_DATA_DIR, SIX_DATABASE_URL). Private workspace model traffic uses
an explicitly configured direct provider. OpenRouter is restricted to public-topic
search and never receives shared or private workspace content.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _load_local_env() -> None:
    """Expose non-SIX_ migration/test variables from a local ``.env`` file.

    pydantic-settings only maps SIX_-prefixed vars into Settings; the LLM
    registry retains explicit direct-provider adapters for controlled
    migrations and tests. Automatic deployment routing never reads or selects
    them. This loader is a no-op under pytest so tests never inherit real keys.
    """
    if "pytest" in sys.modules:
        return
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIX_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str = ""  # empty -> sqlite file under data_dir
    # PostgreSQL connection pools are process-local. Conservative defaults
    # keep aggregate connections bounded when several worker replicas spawn
    # short-lived job processes.
    database_pool_size: int = 3
    database_max_overflow: int = 2
    database_pool_recycle_seconds: int = 1800
    # Public abuse counters must be shared once more than one API process can
    # receive traffic. Development and isolated unit tests keep the lightweight
    # memory backend; deployment and the PostgreSQL RC explicitly use database.
    rate_limit_backend: Literal["memory", "database"] = "memory"
    openalex_api_key: str = ""
    openalex_mailto: str = ""
    # Optional GROBID service for structured PDF extraction (empty -> pypdf).
    grobid_url: str = ""
    # Web search normally reuses the OpenRouter key below and pins requests to
    # Perplexity Sonar. This optional compatibility field may hold a dedicated
    # OpenRouter key for isolated deployments/tests; legacy ``tvly-`` keys are
    # rejected so a former Tavily secret can never be sent to OpenRouter.
    websearch_api_key: str = ""
    # The separate acknowledgement keeps the feature fail-closed until its
    # processing purpose, sub-processor and transfer review are archived.
    websearch_data_processing_confirmed: bool = False
    # LaTeX engine invocation for the Writer ("main.tex" is appended); the
    # deployment stack points this at the tectonic container.
    tectonic_cmd: str = "tectonic"
    # OpenRouter is reserved for explicitly approved public search. Its key
    # never enables private workspace text, OCR, audio or visual generation.
    openrouter_api_key: str = ""
    # OpenRouter use is limited to the public-topic Sonar connector; retained
    # public-scoped adapters are not selected by workspace workflows. Private
    # text/image/audio/vision never uses this URL. Only credential-free HTTPS
    # origins without query state are accepted.
    openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL
    # Gemini API key shared by direct text, image, audio and live-voice paths.
    # The key alone never enables egress: EEA deployment use must first be tied
    # to a Cloud Project with an operator-reviewed processing basis.
    gemini_api_key: str = ""
    gemini_data_processing_confirmed: bool = False
    # Public spoken interviews retain an explicit release gate, independent
    # of private pilot testing. New sessions use the server-owned voice relay;
    # opening participation still requires processor and spoken UX approval.
    public_spoken_interviews_enabled: bool = False
    # Exact reviewed direct Google image model; no private OpenRouter fallback.
    figure_model: str = "gemini-3-pro-image"
    # Operator kill switch for the repository-to-visual workflow. Existing
    # analyses remain readable and deletable while new ingestion is disabled.
    repository_analysis_enabled: bool = True
    # Audio toolchain for interview transcription (must be on PATH or absolute).
    ffmpeg_cmd: str = "ffmpeg"
    ffprobe_cmd: str = "ffprobe"
    # Comma-separated allowed CORS origins for a browser frontend (empty -> none).
    cors_origins: str = ""
    # Hard per-run LLM budget in USD (safety net, not pacing: exhaustive runs
    # may take hours by design; when the budget trips, the run pauses honestly).
    llm_budget_usd: float = 10.0
    # Task routing override as JSON, e.g.
    # {"synthesis": "gemini:gemini-3.5-flash",
    #  "screening": ["gemini:gemini-3.5-flash", "gemini:gemini-3.1-pro-preview"]}
    # Empty -> explicitly configured Gemini for every workspace task. Explicit overrides
    # must also stay inside the private Gemini allowlist; old OR routing fails
    # closed rather than bypassing the content boundary.
    llm_routing: str = ""
    # Target recall for stopping certification (statistical enforcement lands
    # with the Chao estimator in H1; recorded in the audit trail today).
    target_recall: float = 0.95
    # Existing account login and password recovery remain available when closed.
    self_signup: bool = False
    # Public accounts must spend a one-time email token before login. Tests
    # may disable this explicitly; deployment keeps it on before signup opens.
    require_email_verification: bool = True
    # Operators can require their own published Terms and Privacy Notice.
    enforce_legal_acceptance: bool = False
    # Public OAuth web client id used to verify Google Identity Services ID
    # tokens. No client secret is required for this credential flow.
    google_oauth_client_id: str = ""
    # Application mail is handed to a local SMTP queue in deployment. The
    # queue relays through an operator-selected relay on 587 until direct SMTP is available.
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    mail_from: str = "SixSentences <noreply@localhost>"
    mail_reply_to: str = ""
    mail_message_id_domain: str = "localhost"
    app_url: str = "http://localhost:3000"
    site_url: str = "http://localhost:3000"
    privacy_notice_url: str = "http://localhost:3000/privacy"
    terms_url: str = "http://localhost:3000/terms"
    docs_url: str = "http://localhost:3000/docs"
    # Exact chrome.identity redirect URIs allowed for Browser Capture. Never
    # use a wildcard chromiumapp.org origin in deployment.
    browser_capture_redirect_uris: str = ""

    @field_validator("openrouter_base_url")
    @classmethod
    def _validate_openrouter_base_url(cls, value: str) -> str:
        normalized = str(value or "").strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("openrouter_base_url must be one credential-free HTTPS base URL")
        return normalized

    def openrouter_endpoint(self, path: str) -> str:
        """Resolve one OpenRouter endpoint from the deployment-wide base URL."""

        return f"{self.openrouter_base_url}/{path.lstrip('/')}"

    @property
    def websearch_openrouter_api_key(self) -> str:
        """Return a Sonar-capable OpenRouter key, never a legacy Tavily key."""

        dedicated = self.websearch_api_key.strip()
        if dedicated.casefold().startswith("tvly-"):
            # Older installations can still carry the retired Tavily secret.
            # Ignore it and use the reviewed shared OpenRouter route instead;
            # never send a Tavily credential to OpenRouter.
            return self.openrouter_api_key.strip()
        return dedicated or self.openrouter_api_key.strip()

    @property
    def websearch_enabled(self) -> bool:
        """Whether reviewed OpenRouter/Sonar grey-literature egress is enabled."""
        return bool(self.websearch_openrouter_api_key and self.websearch_data_processing_confirmed)

    @property
    def gemini_enabled(self) -> bool:
        """Whether direct Gemini egress is contractually enabled."""

        return bool(self.gemini_api_key.strip() and self.gemini_data_processing_confirmed)

    @property
    def gemini_egress_api_key(self) -> str:
        """Return the direct Gemini key only after the data-processing gate."""

        return self.gemini_api_key.strip() if self.gemini_enabled else ""

    @property
    def public_gemini_live_enabled(self) -> bool:
        """Whether public server-relayed Gemini Live interviews may run."""

        return bool(self.gemini_enabled and self.public_spoken_interviews_enabled)

    # URL-safe 32-byte Fernet key used only for third-party connector tokens.
    # Generate once, keep in the deployment secret file, never in Git.
    connector_encryption_key: str = ""
    # Optional signed desktop companion download metadata. The desktop sends
    # transcript text only; the API never receives or stores raw audio.
    live_companion_download_url: str = ""
    live_companion_minimum_version: str = ""
    # Append-only, content-free account-erasure journal. Multi-host deployments mount
    # this path from a dedicated volume that is never replaced by a product
    # database or user-file restore. Every record is chained and authenticated
    # with a distinct secret so an older backup cannot silently resurrect an
    # account that was deleted after the snapshot was taken.
    erasure_ledger_path: Path | None = None
    erasure_ledger_hmac_key: str = ""
    # Durable jobs stay inline for local development and tests. Multi-worker deployments
    # runs a separate database-backed worker so API restarts cannot lose
    # searches, renders, transcriptions, compiles, or extraction jobs.
    jobs_backend: str = "inline"  # inline | database
    jobs_poll_seconds: float = 1.0
    # Active run streams poll the append-only event log more frequently than
    # background workers. This keeps the first visible progress update below
    # one second without busy-looping idle workers.
    run_events_poll_seconds: float = 0.2
    jobs_max_attempts: int = 3
    jobs_retention_days: int = 14
    # Expired/revoked authentication rows remain briefly for incident
    # correlation, then are removed by the scheduled retention job.
    auth_token_retention_days: int = 30
    stream_ticket_retention_hours: int = 24
    # Larger deployments run disjoint worker lanes so a multi-hour research run
    # cannot hold chat, document, media or short LaTeX work in the same queue.
    # all | chat | research | documents | compile | media | notifications
    jobs_worker_lane: str = "all"
    jobs_worker_concurrency: int = 1
    # First TERM stops new claims and lets active tasks finish. After this
    # deadline their fenced leases are returned to the queue for another
    # replica. A second TERM forces that hand-off immediately.
    jobs_drain_seconds: int = 300
    # Container health checks need a replica-local heartbeat file. Containers can
    # points this at /tmp; an unset value preserves the local data-dir layout.
    jobs_heartbeat_dir: Path | None = None
    # A crashed replica stays visible long enough for monitoring to alert but
    # must not leave the lane degraded until the general job-retention window.
    jobs_replica_alert_seconds: int = 900
    release_git_revision: str = "unknown"
    # Public-launch evidence binds the API journey to the exact web clients
    # shipped beside this core revision, not merely to the backend image.
    # A claimed job remains owned while its worker renews this lease. Another
    # replica may reclaim it only after the lease expires. Keep this well above
    # the 15-second worker heartbeat interval.
    jobs_lease_seconds: int = 90
    # Prefer queued work from organizations with fewer active jobs in the same
    # lane. This prevents one large workspace from monopolizing every worker.
    jobs_fair_scheduling: bool = True
    # Refuse persistent writes before the host filesystem is completely full.
    # Operators should reserve storage headroom; local development keeps this at zero.
    storage_reserve_bytes: int = 0
    # Aggregate health warns when queued work waits longer than this.
    jobs_queue_warning_seconds: int = 900
    # Transactional monitor alerts, handed to the same private SMTP queue.
    # Keep the active and one rollback corpus generation by default.
    corpus_snapshots_to_keep: int = 2

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'six.db'}"

    @property
    def corpus_dir(self) -> Path:
        path = self.data_dir / "corpus"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def documents_dir(self) -> Path:
        """Local full-text document store (acquisition layer, H1)."""
        path = self.data_dir / "documents"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_erasure_ledger_path(self) -> Path:
        """Return the journal path; deployment must override this outside /data."""

        if self.erasure_ledger_path is not None:
            return self.erasure_ledger_path
        return self.data_dir / "privacy" / "erasure-ledger.jsonl"


@lru_cache
def get_settings() -> Settings:
    _load_local_env()
    return Settings()
