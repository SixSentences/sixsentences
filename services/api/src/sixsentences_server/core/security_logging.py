"""Last-line redaction for operational logs.

Application code should never log credentials or tenant content. This filter
also removes common credential shapes from messages and formatting arguments
before a handler serializes them, protecting against an unexpected exception
or dependency message that contains a URL query value or authorization header.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_SENSITIVE_KEYS = {
    "access_token",
    "answer",
    "answers",
    "api_key",
    "authorization",
    "code",
    "content",
    "content_base64",
    "cookie",
    "credential",
    "email",
    "messages",
    "password",
    "prompt",
    "question",
    "respondent_label",
    "secret",
    "set_cookie",
    "source_image_base64",
    "ticket",
    "token",
}

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "REDACTED"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=-]+"), "REDACTED"),
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{12,}\b"), "REDACTED"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "REDACTED"),
    (re.compile(r"\bsix_(?:ss|sk|ev|mc|st)_[A-Za-z0-9_-]{8,}\b"), "REDACTED"),
    (
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9_-]{12,}\b"),
        "REDACTED",
    ),
    (re.compile(r"\bwhsec_[A-Za-z0-9_-]{12,}\b"), "REDACTED"),
    (
        re.compile(r"\b(?:gh[opusr]|github_pat)_[A-Za-z0-9._-]{12,}\b"),
        "REDACTED",
    ),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"), "REDACTED"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "REDACTED",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "REDACTED",
    ),
    (
        re.compile(
            r"(?i)([?&](?:"
            r"token|access_token|ticket|api_key|secret|password|email|code|"
            r"answer|answers|respondent_label|prompt|question|content|messages|note"
            r")=)"
            r"[^&#\s]+"
        ),
        r"\1REDACTED",
    ),
    (
        re.compile(
            r"(?i)(\b(?:authorization|cookie|set-cookie|api[-_ ]?key|password|"
            r"passwort|secret|token)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^,;\s}]+)"
        ),
        r"\1REDACTED",
    ),
    (
        re.compile(
            r"(?i)(\b(?:password|passwort|api[-_ ]?key|secret|token)\s+"
            r"(?:is|ist|lautet)\s+)(\S+)"
        ),
        r"\1REDACTED",
    ),
    (
        re.compile(r"(?i)(/(?:public/(?:runs|talk|surveys)|verify|reset)/)[A-Za-z0-9_-]{12,}"),
        r"\1REDACTED",
    ),
    (
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "REDACTED_EMAIL",
    ),
)
_FACTORY_INSTALLED = False


def redact_log_value(value: str) -> str:
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _redact_argument(value: Any) -> Any:
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: details redacted"
    if isinstance(value, str):
        return redact_log_value(value)
    if isinstance(value, tuple):
        return tuple(_redact_argument(item) for item in value)
    if isinstance(value, list):
        return [_redact_argument(item) for item in value]
    if isinstance(value, set):
        return {_redact_argument(item) for item in value}
    if isinstance(value, dict):
        return {
            key: (
                "REDACTED"
                if str(key).lower().replace("-", "_") in _SENSITIVE_KEYS
                else _redact_argument(item)
            )
            for key, item in value.items()
        }
    return value


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_log_value(record.msg)
        record.args = _redact_argument(record.args)
        # Python's default formatter renders ``exc_info`` after formatting the
        # message. Provider exceptions commonly contain response bodies, so a
        # message-only filter would still leak prompts or credentials through
        # the traceback footer. Preserve the exception type and stack location,
        # but never its runtime detail.
        if record.exc_info is not None:
            exception_type = record.exc_info[0]
            name = getattr(exception_type, "__name__", "Exception")
            record.exc_text = f"{name}: details redacted"

        # Structured formatters may serialize ``extra`` fields directly. Apply
        # the same last-line policy to all non-standard values as well.
        for key, value in tuple(record.__dict__.items()):
            normalized = key.lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                record.__dict__[key] = "REDACTED"
            elif isinstance(value, (str, tuple, list, set, dict)):
                record.__dict__[key] = _redact_argument(value)
        return True


def install_sensitive_log_filter() -> None:
    """Install once for existing and future loggers/handlers."""

    global _FACTORY_INSTALLED
    # HTTPX emits successful request URLs at INFO. Private GitHub repository
    # identities and short-lived codeload query capabilities are themselves
    # sensitive, so suppress these dependency access logs in both API and
    # worker processes instead of relying on generic token redaction.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    root = logging.getLogger()
    if not any(isinstance(item, SensitiveDataFilter) for item in root.filters):
        root.addFilter(SensitiveDataFilter())
    for handler in root.handlers:
        if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
            handler.addFilter(SensitiveDataFilter())
    if not _FACTORY_INSTALLED:
        factory = logging.getLogRecordFactory()

        def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = factory(*args, **kwargs)
            SensitiveDataFilter().filter(record)
            return record

        logging.setLogRecordFactory(redacting_factory)
        _FACTORY_INSTALLED = True
