"""Database-backed worker for research, mail and local AI tasks."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.database import Database, create_database
from sixsentences_server.mailer import MailTransport, SMTPTransport
from sixsentences_server.models import Brainstorm, Job, OutboxEmail, ResearchRun
from sixsentences_server.providers import ProviderUnavailable, TextProvider, text_provider
from sixsentences_server.research import OpenAlexResearchExecutor, ResearchExecutor


class Worker:
    """Claim jobs transactionally and dispatch only reviewed community job kinds."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        research: ResearchExecutor | None = None,
        ai: TextProvider | None = None,
        mail: MailTransport | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.research = research or OpenAlexResearchExecutor(settings)
        self.ai = ai or text_provider(settings)
        self.mail = mail or SMTPTransport(settings)

    def _claim(self) -> str | None:
        now = datetime.now(UTC)
        stale = now - timedelta(minutes=15)
        with self.database.session() as session:
            session.execute(
                update(Job)
                .where(Job.status == "running", Job.locked_at < stale)
                .values(status="queued", locked_at=None, error="worker lease expired")
            )
            statement = (
                select(Job)
                .where(Job.status == "queued", Job.available_at <= now)
                .order_by(Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = session.scalar(statement)
            if job is None:
                return None
            job.status = "running"
            job.locked_at = now
            job.attempts += 1
            return job.public_id

    def run_once(self) -> bool:
        """Run at most one job and return whether work was claimed."""

        job_id = self._claim()
        if job_id is None:
            return False
        try:
            with self.database.session() as session:
                job = session.scalar(select(Job).where(Job.public_id == job_id))
                if job is None or job.status != "running":
                    return True
                self._dispatch(session, job)
                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.locked_at = None
                job.error = ""
        except Exception as exc:
            with self.database.session() as session:
                job = session.scalar(select(Job).where(Job.public_id == job_id))
                if job is not None:
                    error = f"{type(exc).__name__}: {exc}"[:2000]
                    job.error = error
                    job.locked_at = None
                    if job.attempts >= job.max_attempts:
                        job.status = "failed"
                        job.completed_at = datetime.now(UTC)
                        self._mark_terminal_failure(session, job, error)
                    else:
                        job.status = "queued"
                        job.available_at = datetime.now(UTC) + timedelta(
                            seconds=min(2**job.attempts, 60)
                        )
            return True
        return True

    def _mark_terminal_failure(self, session: Session, job: Job, error: str) -> None:
        """Reflect an exhausted job in its tenant-owned domain record."""

        if job.kind == "research.run":
            record = session.scalar(
                select(ResearchRun).where(
                    ResearchRun.public_id == str(job.payload.get("run_id")),
                    ResearchRun.org_id == job.org_id,
                )
            )
            if record is not None:
                record.status = "failed"
                record.error = error
                record.completed_at = datetime.now(UTC)
        elif job.kind == "brainstorm.generate":
            brainstorm = session.scalar(
                select(Brainstorm).where(
                    Brainstorm.public_id == str(job.payload.get("brainstorm_id")),
                    Brainstorm.org_id == job.org_id,
                )
            )
            if brainstorm is not None:
                brainstorm.status = "failed"
                brainstorm.result = {"error": error}
        elif job.kind == "mail.send":
            message = session.scalar(
                select(OutboxEmail).where(
                    OutboxEmail.public_id == str(job.payload.get("message_id")),
                    OutboxEmail.org_id == job.org_id,
                )
            )
            if message is not None:
                message.status = "failed"
                message.error = error

    def _dispatch(self, session: Session, job: Job) -> None:
        if job.kind == "research.run":
            self._research(session, job)
        elif job.kind == "mail.send":
            self._mail(session, job)
        elif job.kind == "brainstorm.generate":
            self._brainstorm(session, job)
        else:
            raise ValueError(f"unsupported job kind: {job.kind}")

    def _research(self, session: Session, job: Job) -> None:
        run = session.scalar(
            select(ResearchRun).where(
                ResearchRun.public_id == str(job.payload.get("run_id")),
                ResearchRun.org_id == job.org_id,
            )
        )
        if run is None:
            raise ValueError("research run not found in job tenant")
        run.status = "running"
        try:
            run.result = self.research.execute(job.payload)
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
            run.completed_at = datetime.now(UTC)
            raise
        run.status = "completed"
        run.completed_at = datetime.now(UTC)

    def _mail(self, session: Session, job: Job) -> None:
        message = session.scalar(
            select(OutboxEmail).where(
                OutboxEmail.public_id == str(job.payload.get("message_id")),
                OutboxEmail.org_id == job.org_id,
            )
        )
        if message is None:
            raise ValueError("outbox message not found in job tenant")
        message.attempts += 1
        try:
            self.mail.send(message)
        except Exception as exc:
            message.status = "failed"
            message.error = f"{type(exc).__name__}: {exc}"[:2000]
            raise
        message.status = "sent"
        message.error = ""

    def _brainstorm(self, session: Session, job: Job) -> None:
        record = session.scalar(
            select(Brainstorm).where(
                Brainstorm.public_id == str(job.payload.get("brainstorm_id")),
                Brainstorm.org_id == job.org_id,
            )
        )
        if record is None:
            raise ValueError("brainstorm not found in job tenant")
        record.status = "running"
        try:
            content = self.ai.complete(
                system=(
                    "Help a researcher explore alternatives. Separate observations, hypotheses, "
                    "counterarguments, evidence needs and next steps. Do not invent sources."
                ),
                messages=[{"role": "user", "content": record.prompt}],
            )
        except ProviderUnavailable:
            record.result = {
                "outline": [
                    "Clarify the research question and population.",
                    "List competing explanations.",
                    "Identify evidence that could distinguish them.",
                    "Record assumptions and possible falsifiers.",
                ],
                "generated_by": "deterministic_local_fallback",
            }
        else:
            record.result = {"text": content, "generated_by": "configured_ai_provider"}
        record.status = "completed"

    def run_forever(self) -> None:
        """Poll until interrupted; job failures remain durable and bounded."""

        while True:
            if not self.run_once():
                time.sleep(self.settings.worker_poll_seconds)


def main() -> None:
    """Worker command-line entry point."""

    parser = argparse.ArgumentParser(description="Run community background jobs")
    parser.add_argument("--once", action="store_true", help="process at most one queued job")
    args = parser.parse_args()
    settings = get_settings()
    database = create_database(settings)
    worker = Worker(database, settings)
    try:
        if args.once:
            print(json.dumps({"claimed": worker.run_once()}))
        else:
            worker.run_forever()
    finally:
        database.dispose()
