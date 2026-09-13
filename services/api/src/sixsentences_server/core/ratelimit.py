"""Fixed-window abuse controls for public and machine endpoints.

The memory backend keeps local development cheap. The database backend uses
one atomic counter shared by every API replica and stores only a short-lived
SHA-256 pseudonym of the caller key, never a raw IP address or credential.
"""

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from sixsentences_server.config import get_settings
from sixsentences_server.core.db import RateLimitBucket, db_session


class RateLimiterUnavailable(RuntimeError):
    """The shared abuse-control store could not make a safe decision."""


class RateLimiterLike(Protocol):
    max_hits: int
    window_seconds: float

    def allow(self, key: str) -> bool: ...


@dataclass
class RateLimiter:
    max_hits: int  # allowed hits per window
    window_seconds: float
    # key -> (window_start, count); pruned lazily so it cannot grow unbounded
    _buckets: dict[str, tuple[float, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _clock: object = time.monotonic  # injectable for tests

    def allow(self, key: str) -> bool:
        """True if this hit is within budget; False when the window is full."""
        now = float(self._clock())  # type: ignore[operator]
        with self._lock:
            if len(self._buckets) > 4096:  # bound memory under a spray of keys
                self._prune(now)
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self.window_seconds:
                self._buckets[key] = (now, 1)
                return True
            if count >= self.max_hits:
                return False
            self._buckets[key] = (start, count + 1)
            return True

    def _prune(self, now: float) -> None:
        expired = [
            k for k, (start, _) in self._buckets.items() if now - start >= self.window_seconds
        ]
        for key in expired:
            del self._buckets[key]


@dataclass
class DatabaseRateLimiter:
    """Atomic fixed-window limiter shared through the product database."""

    scope: str
    max_hits: int
    window_seconds: float
    retention_windows: int = 3
    _clock: object = time.time  # wall clock is comparable across hosts
    _last_cleanup_at: float = field(default=0.0, init=False)
    _cleanup_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if not self.scope or len(self.scope) > 64:
            raise ValueError("rate-limit scope must contain 1 to 64 characters")
        if self.max_hits < 1:
            raise ValueError("max_hits must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.retention_windows < 1:
            raise ValueError("retention_windows must be positive")

    def allow(self, key: str) -> bool:
        """Atomically consume one shared budget unit, failing closed on DB errors."""

        now = float(self._clock())  # type: ignore[operator]
        window_id = int(now // self.window_seconds)
        key_hash = hashlib.sha256(
            f"sixsentences-rate-limit-v1\x00{self.scope}\x00{key}".encode()
        ).hexdigest()
        cleanup_due = self._claim_cleanup(now)
        try:
            with db_session() as session:
                if cleanup_due:
                    session.execute(
                        delete(RateLimitBucket).where(
                            RateLimitBucket.scope == self.scope,
                            RateLimitBucket.window_id < window_id - self.retention_windows,
                        )
                    )
                values = {
                    "scope": self.scope,
                    "key_hash": key_hash,
                    "window_id": window_id,
                    "count": 1,
                }
                dialect = session.get_bind().dialect.name
                statement: Any
                if dialect == "postgresql":
                    statement = postgresql_insert(RateLimitBucket).values(**values)
                elif dialect == "sqlite":
                    statement = sqlite_insert(RateLimitBucket).values(**values)
                else:  # production and supported development DBs are explicit
                    raise RateLimiterUnavailable(
                        f"unsupported shared rate-limit database: {dialect}"
                    )
                statement = statement.on_conflict_do_update(
                    index_elements=["scope", "key_hash", "window_id"],
                    set_={"count": RateLimitBucket.count + 1},
                ).returning(RateLimitBucket.count)
                count = int(session.execute(statement).scalar_one())
        except RateLimiterUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise RateLimiterUnavailable("shared rate-limit storage is unavailable") from exc
        return count <= self.max_hits

    def _claim_cleanup(self, now: float) -> bool:
        cleanup_interval = max(60.0, self.window_seconds)
        with self._cleanup_lock:
            if now - self._last_cleanup_at < cleanup_interval:
                return False
            self._last_cleanup_at = now
            return True


def build_rate_limiter(
    scope: str,
    *,
    max_hits: int,
    window_seconds: float,
) -> RateLimiterLike:
    """Build the configured limiter without contacting its backing store."""

    if get_settings().rate_limit_backend == "database":
        return DatabaseRateLimiter(
            scope=scope,
            max_hits=max_hits,
            window_seconds=window_seconds,
        )
    return RateLimiter(max_hits=max_hits, window_seconds=window_seconds)
