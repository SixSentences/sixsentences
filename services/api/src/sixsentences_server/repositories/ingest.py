"""Bounded, non-executing ingestion for GitHub repository snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import httpx

from sixsentences_server.core.net import is_public_http_url
from sixsentences_server.core.uploads import (
    UnsafeArchiveError,
    open_safe_zip,
    read_zip_member,
)
from sixsentences_server.repositories.schemas import RepositoryCoverage

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_FILES = 5_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ANALYZED_FILES = 5_000
MAX_ANALYZED_BYTES = 32 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 1024 * 1024
MAX_PATH_LENGTH = 512
MAX_PATH_DEPTH = 32

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,198}[A-Za-z0-9._-])?$")
_SPECIFIC_STATIC_EXTENSIONS = {
    ".hcl",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".tf",
    ".ts",
    ".tsx",
    ".vue",
}
_KNOWN_BINARY_EXTENSIONS = {
    ".a",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".ttf",
    ".wasm",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
}
_LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".clj": "clojure",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".go": "go",
    ".h": "c",
    ".hcl": "hcl",
    ".hpp": "cpp",
    ".hs": "haskell",
    ".java": "java",
    ".jl": "julia",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".mjs": "javascript",
    ".php": "php",
    ".proto": "protobuf",
    ".py": "python",
    ".r": "r",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "shell",
    ".sol": "solidity",
    ".sql": "sql",
    ".svelte": "svelte",
    ".swift": "swift",
    ".tf": "terraform",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".zig": "zig",
}
_VENDOR_PARTS = {
    ".cache",
    ".git",
    ".gradle",
    ".idea",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
_LOCK_FILES = {
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
_NESTED_ARCHIVES = {
    ".7z",
    ".bz2",
    ".gz",
    ".jar",
    ".rar",
    ".tar",
    ".tgz",
    ".war",
    ".xz",
    ".zip",
}
_SECRET_FILENAMES = {
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "secrets.yml",
}
_SECRET_DIRECTORY_NAMES = {
    ".aws",
    ".credentials",
    ".secrets",
    ".ssh",
    "credential-config",
    "credential_config",
    "credentials",
    "private-config",
    "private-keys",
    "private_config",
    "private_keys",
    "secret-config",
    "secret_config",
    "secrets",
}
_SENSITIVE_PATH_CANARY = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|"
    r"(?:github_pat_|gh[opusr]_)[A-Za-z0-9._-]{12,}|sk-[A-Za-z0-9]{20,})"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
_SECRET_KEY = (
    r"(?<![A-Za-z0-9])(?:"
    r"api[_-]?(?:key|secret)|access[_-]?token|auth(?:entication)?[_-]?token|"
    r"authorization|aws[_-]?secret[_-]?access[_-]?key|client[_-]?secret|"
    r"credentials?|pass(?:word|wd|phrase)?|pwd|private[_-]?key|"
    r"secret(?:[_-]?access)?[_-]?key|"
    r"(?:signing|encryption|license|auth|cookie|client)[_-]?key|"
    r"secrets?|tokens?"
    r")(?![A-Za-z0-9])"
)
_SECRET_PATH_ASSIGNMENT = re.compile(rf"(?i){_SECRET_KEY}\s*[:=]\s*[^/]{{1,512}}")
_SECRET_ASSIGNMENT = re.compile(
    rf"(?im)((?:[\"']?){_SECRET_KEY}(?:[\"']?)\s*[:=]\s*)"
    r"[^\r\n]{1,512}"
)
_SECRET_CONTAINER = re.compile(
    rf"^(?P<indent>[ \t]*)(?:[\"']?){_SECRET_KEY}(?:[\"']?)\s*[:=]\s*"
    r"(?P<marker>\[\s*|\{\s*|[|>][+-]?\d*\s*|.*\\\s*)?$",
    re.IGNORECASE,
)
_SECRET_SECTION = re.compile(
    r"^(?P<indent>[ \t]*)\[(?:secrets?|credentials?|auth(?:entication)?)\]\s*$",
    re.IGNORECASE,
)
_AMBIGUOUS_SECRET_STRUCTURE = re.compile(
    rf"(?im)(?:"
    rf"^\s*\[{{1,2}}[^\]\r\n]{{0,160}}(?:secret|credential|auth)"
    rf"[^\]\r\n]{{0,80}}\]{{1,2}}(?:\s*[#;].*)?$|"
    rf"^\s*(?:(?:const|final|let|static|var)\s+)?[\"']?{_SECRET_KEY}[\"']?"
    rf"\s*[:=]\s*(?:$|[\[{{(`|>&*!]|[\"']{{3}}|"
    rf"[\"'][^\"'\r\n]{{0,128}}[\"']\s*\+|[\"'][^\"'\r\n]*$|"
    rf"%[qQwWxri]?[\(\[{{<]|[A-Za-z_][A-Za-z0-9_.]*\s*\())"
)
_AMBIGUOUS_SECRET_ACCESS = re.compile(
    rf"(?im)(?:"
    rf"^\s*[A-Za-z_$][A-Za-z0-9_$.'\"\[\]]{{0,160}}"
    rf"\[\s*[\"']{_SECRET_KEY}[\"']\s*\]\s*[:=]|"
    rf"^\s*[\"']?{_SECRET_KEY}[\"']?\s*/\*[^\r\n]{{0,160}}\*/\s*[:=]|"
    rf"^\s*(?:const\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s+)+\*?\s*"
    rf"{_SECRET_KEY}\s*[:=][^\r\n]*(?:\\|\+)\s*$)"
)
_ASCII_ESCAPE = re.compile(
    r"\\(?:"
    r"u(?P<unicode4>[0-9A-Fa-f]{4})|"
    r"x(?P<hex2>[0-9A-Fa-f]{2})|"
    r"U(?P<unicode8>[0-9A-Fa-f]{8})|"
    r"u\{(?P<unicode_braced>[0-9A-Fa-f]{1,6})\}|"
    r"(?P<octal>[0-7]{3})"
    r")"
)
_XML_SECRET_VALUE = re.compile(
    rf"(?is)(<(?P<secret_key>{_SECRET_KEY})>\s*)"
    r"(?P<secret_value>[^<]{4,512})(\s*</(?P=secret_key)>)"
)
_TOKEN_VALUE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|"
    r"(?:github_pat_|gh[opusr]_)[A-Za-z0-9._-]{12,}|"
    r"sk-[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|glpat-[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{24,})"
)
_JWT_VALUE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_CREDENTIALED_DATABASE_URL = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://)"
    r"[^\s/:@]{1,128}:[^\s/@]{1,256}@"
)
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{32,256}(?![A-Za-z0-9_+/=-])")
_LONG_HEX = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,128}(?![0-9A-Fa-f])")


class RepositoryIngestError(RuntimeError):
    """Safe ingestion failure with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str = "repository_ingest_failed") -> None:
        super().__init__(message)
        self.code = code


class RepositoryScopeError(RepositoryIngestError):
    """The repository is valid but exceeds the beta analysis envelope."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "repository_scope_too_large",
        manifest: list[dict[str, Any]] | None = None,
        coverage: RepositoryCoverage | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.manifest = manifest or []
        self.coverage = coverage


@dataclass(frozen=True)
class GitHubRepository:
    owner: str
    name: str
    canonical_url: str


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    raw_sha256: str
    byte_size: int
    text: str
    redacted: bool
    language: str
    parser_mode: str


@dataclass(frozen=True)
class RepositoryArchive:
    archive_sha256: str
    manifest: list[dict[str, Any]]
    files: list[RepositoryFile]
    coverage: RepositoryCoverage


def parse_public_github_repository(value: str) -> GitHubRepository:
    """Accept exactly one credential-free GitHub repository root."""

    raw = value.strip()
    if len(raw) > 500:
        raise RepositoryIngestError("Repository URL is too long.", code="invalid_repository_url")
    try:
        parsed = urlsplit(raw)
        parsed_port = parsed.port
    except ValueError as exc:
        raise RepositoryIngestError(
            "Repository URL is invalid.", code="invalid_repository_url"
        ) from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RepositoryIngestError(
            "Use a credential-free https://github.com/owner/repository URL, "
            "query parameters or fragments.",
            code="invalid_repository_url",
        )
    if unquote(parsed.path) != parsed.path or "\\" in parsed.path:
        raise RepositoryIngestError(
            "Repository URL contains unsafe path encoding.",
            code="invalid_repository_url",
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise RepositoryIngestError(
            "Use the repository root, not a file, issue or branch URL.",
            code="invalid_repository_url",
        )
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not _OWNER.fullmatch(owner) or not _REPOSITORY.fullmatch(name) or name in {".", ".."}:
        raise RepositoryIngestError(
            "Repository owner or name is invalid.", code="invalid_repository_url"
        )
    return GitHubRepository(
        owner=owner,
        name=name,
        canonical_url=f"https://github.com/{owner}/{name}",
    )


def normalize_ref(value: str | None) -> str:
    resolved = (value or "").strip()
    if not resolved:
        return ""
    if (
        not _REF.fullmatch(resolved)
        or ".." in resolved.split("/")
        or "//" in resolved
        or resolved.startswith("-")
    ):
        raise RepositoryIngestError("Repository ref is invalid.", code="invalid_repository_ref")
    return resolved


def normalize_subpath(value: str | None) -> str:
    raw = (value or "").strip().strip("/")
    if not raw:
        return ""
    if (
        len(raw.encode("utf-8")) > MAX_PATH_LENGTH
        or "\\" in raw
        or unquote(raw) != raw
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in raw)
    ):
        raise RepositoryIngestError(
            "Repository subpath is invalid.", code="invalid_repository_subpath"
        )
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepositoryIngestError(
            "Repository subpath is invalid.", code="invalid_repository_subpath"
        )
    if len(path.parts) > MAX_PATH_DEPTH:
        raise RepositoryIngestError(
            "Repository subpath is too deep.", code="invalid_repository_subpath"
        )
    return path.as_posix()


def _bounded_get(
    url: str,
    *,
    max_bytes: int,
    accept: str,
    access_token: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> bytes:
    try:
        parsed = urlsplit(url)
        parsed_port = parsed.port
    except ValueError as exc:
        raise RepositoryIngestError(
            "GitHub endpoint is invalid.", code="github_unavailable"
        ) from exc
    host = (parsed.hostname or "").casefold()
    api_path = re.fullmatch(
        r"/repos/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}"
        r"(?:/commits/[A-Za-z0-9%._~-]{1,600})?",
        parsed.path,
    )
    codeload_path = re.fullmatch(
        r"/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}/zip/[0-9a-f]{40}",
        parsed.path,
    )
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port is not None
        or parsed.query
        or parsed.fragment
        or not (
            (host == "api.github.com" and api_path is not None)
            or (host == "codeload.github.com" and codeload_path is not None)
        )
    ):
        raise RepositoryIngestError(
            "GitHub endpoint is outside the fixed allowlist.", code="github_unavailable"
        )
    if access_token and host != "api.github.com":
        raise RepositoryIngestError(
            "GitHub credentials cannot be sent to this endpoint.",
            code="github_unavailable",
        )
    if cancel_check is not None and cancel_check():
        raise RepositoryIngestError(
            "Repository analysis was cancelled.", code="repository_analysis_cancelled"
        )
    if not is_public_http_url(url):
        raise RepositoryIngestError(
            "GitHub endpoint did not resolve publicly.", code="github_unavailable"
        )
    owned = http is None
    client = http or httpx.Client(follow_redirects=False, timeout=60, trust_env=False)
    try:
        headers = {
            "Accept": accept,
            "User-Agent": "SixSentences/0.1 repository-analysis",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request = client.build_request("GET", url, headers=headers)
        request.headers.pop("cookie", None)
        response = client.send(
            request,
            stream=True,
            follow_redirects=False,
            auth=None,
        )
        try:
            if 300 <= response.status_code < 400:
                raise RepositoryIngestError(
                    "GitHub returned an unexpected redirect.",
                    code="github_redirect_refused",
                )
            if access_token and response.status_code in {401, 403, 404}:
                raise RepositoryIngestError(
                    "GitHub could not authorize access to this repository.",
                    code="github_connection_failed",
                )
            if response.status_code == 404:
                raise RepositoryIngestError(
                    "The repository or ref was not found.", code="repository_not_found"
                )
            if response.status_code == 403:
                raise RepositoryIngestError(
                    "GitHub temporarily refused this public repository request.",
                    code="github_rate_limited",
                )
            if response.status_code >= 400:
                raise RepositoryIngestError(
                    "GitHub could not provide this public repository.",
                    code="github_unavailable",
                )
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise RepositoryScopeError(
                        "The complete repository archive exceeds the beta import limit; "
                        "selecting a subpath cannot reduce the codeload archive.",
                        code="repository_archive_too_large",
                    )
            return bytes(payload)
        finally:
            response.close()
    except RepositoryIngestError:
        raise
    except httpx.HTTPError as exc:
        raise RepositoryIngestError(
            "GitHub is temporarily unreachable.", code="github_unavailable"
        ) from exc
    finally:
        if owned:
            client.close()


def _github_json(
    repository: GitHubRepository,
    suffix: str,
    *,
    access_token: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository.owner}/{repository.name}{suffix}"
    payload = _bounded_get(
        url,
        max_bytes=1_000_000,
        accept="application/vnd.github+json",
        access_token=access_token,
        cancel_check=cancel_check,
        http=http,
    )
    try:
        data = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RepositoryIngestError(
            "GitHub returned an invalid repository record.",
            code="invalid_github_response",
        ) from exc
    if not isinstance(data, dict):
        raise RepositoryIngestError(
            "GitHub returned an invalid repository record.",
            code="invalid_github_response",
        )
    return data


def github_repository_record(
    repository: GitHubRepository,
    *,
    access_token: str,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return one authenticated private/internal repository identity record."""

    data = _github_json(
        repository,
        "",
        access_token=access_token,
        cancel_check=cancel_check,
        http=http,
    )
    repository_id = data.get("id")
    full_name = str(data.get("full_name") or "").strip()
    visibility = str(data.get("visibility") or "").strip().casefold()
    if (
        isinstance(repository_id, bool)
        or not isinstance(repository_id, int)
        or repository_id <= 0
        or repository_id >= 2**63
        or full_name.casefold() != f"{repository.owner}/{repository.name}".casefold()
        or visibility not in {"private", "internal"}
        or (visibility == "private" and data.get("private") is not True)
    ):
        raise RepositoryIngestError(
            "GitHub did not return the expected private repository.",
            code="github_connection_failed",
        )
    canonical = parse_public_github_repository(f"https://github.com/{full_name}")
    return {
        "id": repository_id,
        "full_name": f"{canonical.owner}/{canonical.name}",
        "owner": canonical.owner,
        "name": canonical.name,
        "repository_url": canonical.canonical_url,
        "visibility": visibility,
        "default_branch": str(data.get("default_branch") or ""),
    }


def resolve_github_ref(
    repository: GitHubRepository,
    ref: str | None,
    *,
    access_token: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> tuple[str, str]:
    """Resolve an omitted/default or explicit ref to one immutable commit."""

    actual_ref = normalize_ref(ref)
    if not actual_ref:
        repository_record = _github_json(
            repository,
            "",
            access_token=access_token,
            cancel_check=cancel_check,
            http=http,
        )
        actual_ref = normalize_ref(str(repository_record.get("default_branch") or ""))
        if not actual_ref:
            raise RepositoryIngestError(
                "GitHub returned no valid default branch.",
                code="invalid_github_response",
            )
    safe_ref = quote(actual_ref, safe="")
    data = _github_json(
        repository,
        f"/commits/{safe_ref}",
        access_token=access_token,
        cancel_check=cancel_check,
        http=http,
    )
    try:
        commit_sha = str(data["sha"]).casefold()
    except (KeyError, TypeError, ValueError) as exc:
        raise RepositoryIngestError(
            "GitHub returned an invalid commit record.", code="invalid_github_response"
        ) from exc
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise RepositoryIngestError(
            "GitHub returned an invalid commit identifier.",
            code="invalid_github_response",
        )
    return actual_ref, commit_sha


def resolve_github_commit(
    repository: GitHubRepository,
    ref: str,
    *,
    access_token: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> str:
    """Backward-compatible single-value resolver for explicit refs."""

    return resolve_github_ref(
        repository,
        ref,
        access_token=access_token,
        cancel_check=cancel_check,
        http=http,
    )[1]


def _validated_private_archive_location(
    location: str,
    *,
    repository: GitHubRepository,
    commit_sha: str,
) -> str:
    """Validate GitHub's short-lived archive redirect without disclosing it."""

    if not location or len(location) > 4_096 or any(ord(char) < 32 for char in location):
        raise RepositoryIngestError(
            "GitHub returned an invalid archive redirect.",
            code="github_redirect_refused",
        )
    try:
        parsed = urlsplit(location)
        port = parsed.port
    except ValueError as exc:
        raise RepositoryIngestError(
            "GitHub returned an invalid archive redirect.",
            code="github_redirect_refused",
        ) from exc
    expected_paths = {
        f"/{repository.owner}/{repository.name}/zip/{commit_sha}".casefold(),
        f"/{repository.owner}/{repository.name}/legacy.zip/{commit_sha}".casefold(),
    }
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "codeload.github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or parsed.path.casefold() not in expected_paths
        or len(parsed.query) > 2_048
    ):
        raise RepositoryIngestError(
            "GitHub returned an archive redirect outside the fixed allowlist.",
            code="github_redirect_refused",
        )
    if parsed.query:
        try:
            pairs = parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=20,
            )
        except ValueError as exc:
            raise RepositoryIngestError(
                "GitHub returned an invalid archive redirect.",
                code="github_redirect_refused",
            ) from exc
        if not pairs:
            raise RepositoryIngestError(
                "GitHub returned an invalid archive redirect.",
                code="github_redirect_refused",
            )
        for key, value in pairs:
            normalized_key = key.casefold().replace("-", "_")
            if (
                not key
                or len(key) > 120
                or len(value) > 1_024
                or any(ord(char) < 32 for char in key + value)
                or normalized_key in {"location", "next", "redirect", "redirect_uri", "url"}
                or "://" in value
                or value.startswith("//")
            ):
                raise RepositoryIngestError(
                    "GitHub returned an invalid archive redirect.",
                    code="github_redirect_refused",
                )
    return location


def _private_archive_location(
    repository: GitHubRepository,
    commit_sha: str,
    *,
    access_token: str,
    cancel_check: Callable[[], bool] | None,
    client: httpx.Client,
) -> str:
    if cancel_check is not None and cancel_check():
        raise RepositoryIngestError(
            "Repository analysis was cancelled.", code="repository_analysis_cancelled"
        )
    url = f"https://api.github.com/repos/{repository.owner}/{repository.name}/zipball/{commit_sha}"
    if not is_public_http_url(url):
        raise RepositoryIngestError(
            "GitHub endpoint did not resolve publicly.", code="github_unavailable"
        )
    request = client.build_request(
        "GET",
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "SixSentences/0.1 repository-analysis",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    request.headers.pop("cookie", None)
    try:
        response = client.send(
            request,
            stream=True,
            follow_redirects=False,
            auth=None,
        )
        try:
            if response.status_code in {401, 403, 404}:
                raise RepositoryIngestError(
                    "GitHub could not authorize repository contents access.",
                    code="github_connection_failed",
                )
            if response.status_code != 302:
                raise RepositoryIngestError(
                    "GitHub could not provide the private repository archive.",
                    code="github_connection_failed",
                )
            return _validated_private_archive_location(
                response.headers.get("location", ""),
                repository=repository,
                commit_sha=commit_sha,
            )
        finally:
            response.close()
    except RepositoryIngestError:
        raise
    except httpx.HTTPError as exc:
        raise RepositoryIngestError(
            "GitHub is temporarily unreachable.", code="github_unavailable"
        ) from exc


def probe_github_archive_access(
    repository: GitHubRepository,
    commit_sha: str,
    *,
    access_token: str,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> None:
    """Prove Contents:read via the archive endpoint without downloading it."""

    owned = http is None
    client = http or httpx.Client(follow_redirects=False, timeout=60, trust_env=False)
    try:
        _private_archive_location(
            repository,
            commit_sha,
            access_token=access_token,
            cancel_check=cancel_check,
            client=client,
        )
    finally:
        if owned:
            client.close()


def download_github_archive(
    repository: GitHubRepository,
    commit_sha: str,
    *,
    access_token: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    http: httpx.Client | None = None,
) -> bytes:
    """Download one immutable archive through a closed GitHub allowlist."""

    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise RepositoryIngestError("Commit identifier is invalid.", code="invalid_commit_sha")
    if access_token:
        owned = http is None
        client = http or httpx.Client(follow_redirects=False, timeout=60, trust_env=False)
        try:
            location = _private_archive_location(
                repository,
                commit_sha,
                access_token=access_token,
                cancel_check=cancel_check,
                client=client,
            )
            if cancel_check is not None and cancel_check():
                raise RepositoryIngestError(
                    "Repository analysis was cancelled.",
                    code="repository_analysis_cancelled",
                )
            if not is_public_http_url(location):
                raise RepositoryIngestError(
                    "GitHub archive endpoint did not resolve publicly.",
                    code="github_unavailable",
                )
            request = client.build_request(
                "GET",
                location,
                headers={
                    "Accept": "application/zip",
                    "User-Agent": "SixSentences/0.1 repository-analysis",
                },
            )
            # A caller-provided client may have default credentials or cookies.
            # Explicitly strip both before the temporary codeload request.
            request.headers.pop("authorization", None)
            request.headers.pop("cookie", None)
            try:
                response = client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                    auth=None,
                )
                try:
                    if 300 <= response.status_code < 400:
                        raise RepositoryIngestError(
                            "GitHub returned an unexpected archive redirect.",
                            code="github_redirect_refused",
                        )
                    if response.status_code != 200:
                        raise RepositoryIngestError(
                            "GitHub could not provide the private repository archive.",
                            code="github_connection_failed",
                        )
                    payload = bytearray()
                    for chunk in response.iter_bytes():
                        payload.extend(chunk)
                        if len(payload) > MAX_ARCHIVE_BYTES:
                            raise RepositoryScopeError(
                                "The complete repository archive exceeds the beta import limit; "
                                "selecting a subpath cannot reduce the codeload archive.",
                                code="repository_archive_too_large",
                            )
                    return bytes(payload)
                finally:
                    response.close()
            except RepositoryIngestError:
                raise
            except httpx.HTTPError as exc:
                raise RepositoryIngestError(
                    "GitHub is temporarily unreachable.", code="github_unavailable"
                ) from exc
        finally:
            if owned:
                client.close()
    url = f"https://codeload.github.com/{repository.owner}/{repository.name}/zip/{commit_sha}"
    return _bounded_get(
        url,
        max_bytes=MAX_ARCHIVE_BYTES,
        accept="application/zip",
        cancel_check=cancel_check,
        http=http,
    )


def _safe_archive_path(value: str) -> PurePosixPath:
    if (
        not value
        or len(value.encode("utf-8")) > MAX_PATH_LENGTH + 150
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or unquote(value) != value
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise RepositoryIngestError(
            "Repository archive contains an unsafe path.", code="unsafe_archive"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepositoryIngestError(
            "Repository archive contains path traversal.", code="unsafe_archive"
        )
    if len(path.parts) > MAX_PATH_DEPTH + 1:
        raise RepositoryScopeError("Repository archive paths exceed the supported depth.")
    return path


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def _filename_carries_secret(path: str) -> bool:
    if _SENSITIVE_PATH_CANARY.search(path) or _SECRET_PATH_ASSIGNMENT.search(path):
        return True
    parts = PurePosixPath(path).parts
    if any(part.casefold() in _SECRET_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    for part in parts:
        stem = PurePosixPath(part).stem
        if len(stem) >= 28 and re.fullmatch(r"[A-Za-z0-9_=-]+", stem) and _entropy(stem) >= 4.2:
            return True
    return False


def _redacted_path(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"[redacted-secret-path-{digest}]"


def _known_secret_path(path: PurePosixPath) -> bool:
    lower = path.name.casefold()
    suffix = path.suffix.casefold()
    return (
        lower in _SECRET_FILENAMES
        or lower.startswith(".env")
        or suffix in {".key", ".p12", ".pfx", ".pem"}
        or any(part.casefold() in {".aws", ".ssh"} for part in path.parts)
    )


def _decode_ascii_escape_for_scan(match: re.Match[str]) -> str:
    """Decode only bounded ASCII escapes in the non-persisted DLP scan view."""

    octal = match.group("octal")
    encoded = next(
        (
            value
            for name in ("unicode4", "hex2", "unicode8", "unicode_braced")
            if (value := match.group(name)) is not None
        ),
        octal or "",
    )
    codepoint = int(encoded, 8 if octal is not None else 16)
    return chr(codepoint) if codepoint < 128 else match.group(0)


def _redact_content(value: str) -> tuple[str, bool, bool]:
    """Return secret-screened text, whether redacted, and hard-secret status."""

    canonical = unicodedata.normalize(
        "NFC",
        "".join(
            character
            if character in "\n\r\t" or unicodedata.category(character) not in {"Cc", "Cf"}
            else ""
            for character in value
        ),
    )
    scan_view = _ASCII_ESCAPE.sub(_decode_ascii_escape_for_scan, canonical)
    encoded_sensitive_key = scan_view != canonical and (
        _SECRET_ASSIGNMENT.search(scan_view) is not None
        or _AMBIGUOUS_SECRET_STRUCTURE.search(scan_view) is not None
        or _AMBIGUOUS_SECRET_ACCESS.search(scan_view) is not None
    )
    if (
        _PRIVATE_KEY.search(canonical)
        or _AMBIGUOUS_SECRET_STRUCTURE.search(canonical)
        or _AMBIGUOUS_SECRET_ACCESS.search(canonical)
        or encoded_sensitive_key
    ):
        return "", False, True
    canonical = _XML_SECRET_VALUE.sub(
        lambda match: (
            match.group(1)
            + "[REDACTED]"
            + "".join(character for character in match.group("secret_value") if character in "\r\n")
            + match.group(4)
        ),
        canonical,
    )
    redacted_lines: list[str] = []
    secret_container_indent: int | None = None
    secret_container_same_indent = False
    for line in canonical.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        container = _SECRET_CONTAINER.match(content)
        secret_section = _SECRET_SECTION.match(content)
        if container:
            secret_container_indent = len(container.group("indent").expandtabs(4))
            secret_container_same_indent = (
                (container.group("marker") or "").lstrip().startswith(("[", "{"))
            )
        elif secret_section:
            secret_container_indent = len(secret_section.group("indent").expandtabs(4))
            secret_container_same_indent = True
        elif secret_container_indent is not None and content.strip():
            indentation = len(content) - len(content.lstrip(" \t"))
            list_item = re.match(r"^(\s*-\s*)(.+?)(\s*,?\s*)$", content)
            json_item = re.match(r"^(\s*)([\"'])(.+?)\2(\s*,?\s*)$", content)
            nested_mapping = re.match(r"^(\s*[^:#=\r\n]{1,100}[:=]\s*)(.+?)\s*$", content)
            if re.match(r"^\s*\[[^\]]+\]\s*$", content) or content.strip() in {
                "]",
                "}",
                "],",
                "},",
            }:
                secret_container_indent = None
                secret_container_same_indent = False
            elif list_item and indentation >= secret_container_indent:
                line = f"{list_item.group(1)}[REDACTED]{list_item.group(3)}{newline}"
            elif json_item and indentation >= secret_container_indent:
                line = f"{json_item.group(1)}[REDACTED]{json_item.group(4)}{newline}"
            elif nested_mapping and (
                indentation > secret_container_indent
                or (secret_container_same_indent and indentation == secret_container_indent)
            ):
                line = f"{nested_mapping.group(1)}[REDACTED]{newline}"
            elif indentation > secret_container_indent:
                line = f"{content[:indentation]}[REDACTED]{newline}"
            elif indentation <= secret_container_indent and content.strip() not in {
                "]",
                "}",
            }:
                secret_container_indent = None
        redacted = _TOKEN_VALUE.sub("[REDACTED]", line)
        redacted = _JWT_VALUE.sub("[REDACTED]", redacted)
        redacted = _CREDENTIALED_DATABASE_URL.sub(r"\1[REDACTED]@", redacted)
        redacted = _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + "[REDACTED]", redacted)

        def redact_long_hex(match: re.Match[str]) -> str:
            token = match.group(0)
            return "[REDACTED]" if len(set(token.casefold())) >= 8 else token

        redacted = _LONG_HEX.sub(redact_long_hex, redacted)

        def redact_long_token(match: re.Match[str]) -> str:
            token = match.group(0)
            return "[REDACTED]" if _entropy(token) >= 4.0 else token

        redacted = _LONG_TOKEN.sub(redact_long_token, redacted)
        redacted_lines.append(redacted)
    redacted = "".join(redacted_lines)
    return redacted, redacted != value, False


def _looks_binary(payload: bytes) -> bool:
    if b"\x00" in payload[:8_192]:
        return True
    sample = payload[:8_192]
    if not sample:
        return False
    controls = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    if controls / len(sample) > 0.08:
        return True
    # Random/compressed blobs can contain no NULs and very few ASCII controls.
    # High byte entropy plus invalid UTF-8 is a conservative binary signal.
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        if _entropy(sample.decode("latin-1")) > 7.25:
            return True
        decoded = sample.decode("latin-1")
        printable = sum(character.isprintable() or character in "\n\r\t" for character in decoded)
        if printable / len(decoded) < 0.9:
            return True
    return False


def _decode_source_text(payload: bytes) -> str | None:
    """Decode bounded source text without silently treating opaque bytes as code."""

    if _looks_binary(payload):
        return None
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Latin-1 is lossless and permits older source/config files. The
        # binary classifier above requires a high printable ratio first.
        return payload.decode("latin-1")


def _language_and_parser_mode(path: PurePosixPath) -> tuple[str, str]:
    suffix = path.suffix.casefold()
    name = path.name.casefold()
    language = _LANGUAGE_BY_SUFFIX.get(suffix)
    if language is None:
        if name in {"dockerfile", "makefile", "justfile"}:
            language = name
        elif suffix:
            language = f"unknown/{suffix.removeprefix('.')}"
        else:
            language = "unknown/extensionless"
    mode = "specific_static" if suffix in _SPECIFIC_STATIC_EXTENSIONS else "generic_static"
    return language, mode


def _eligibility_reason(path: PurePosixPath, byte_size: int) -> str:
    lower_parts = {part.casefold() for part in path.parts}
    lower_name = path.name.casefold()
    if lower_parts.intersection(_VENDOR_PARTS):
        return "vendor_or_generated"
    if lower_name in _LOCK_FILES or lower_name.endswith((".min.js", ".min.css", ".map")):
        return "generated_or_lockfile"
    if path.suffix.casefold() in _NESTED_ARCHIVES:
        return "nested_archive"
    if _known_secret_path(path):
        return "secret_path"
    if byte_size <= 0:
        return "empty"
    if byte_size > MAX_SOURCE_FILE_BYTES:
        return "file_too_large"
    if path.suffix.casefold() in _KNOWN_BINARY_EXTENSIONS:
        return "unsupported_or_binary"
    return ""


def inspect_repository_archive(blob: bytes, *, subpath: str = "") -> RepositoryArchive:
    """Inventory every member and retain only bounded secret-screened source text."""

    if len(blob) > MAX_ARCHIVE_BYTES:
        raise RepositoryScopeError(
            "The complete repository archive exceeds the safe beta import limits; "
            "selecting a subpath cannot reduce the codeload archive.",
            code="repository_archive_too_large",
        )
    resolved_subpath = normalize_subpath(subpath)
    try:
        archive = open_safe_zip(
            blob,
            max_files=MAX_ARCHIVE_FILES,
            max_uncompressed_bytes=MAX_ARCHIVE_UNCOMPRESSED_BYTES,
            max_member_bytes=MAX_ARCHIVE_MEMBER_BYTES,
            max_compression_ratio=100,
        )
    except UnsafeArchiveError as exc:
        message = str(exc)
        if (
            "limit" in message
            or "more than" in message
            or "expands" in message
            or "ratio" in message
        ):
            raise RepositoryScopeError(
                "The complete repository archive exceeds the safe beta import limits; "
                "selecting a subpath cannot reduce the codeload archive.",
                code="repository_archive_too_large",
            ) from exc
        raise RepositoryIngestError(
            "The repository archive is unsafe or unreadable.", code="unsafe_archive"
        ) from exc

    try:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        paths = [_safe_archive_path(item.filename) for item in entries]
        roots = {path.parts[0] for path in paths}
        if len(roots) != 1:
            raise RepositoryIngestError(
                "Repository archive has an unexpected root layout.",
                code="unsafe_archive",
            )
        ordered_entries = sorted(
            zip(entries, paths, strict=True),
            key=lambda pair: unicodedata.normalize(
                "NFC", PurePosixPath(*pair[1].parts[1:]).as_posix()
            ).casefold(),
        )
        manifest: list[dict[str, Any]] = []
        files: list[RepositoryFile] = []
        exclusions: Counter[str] = Counter()
        eligible_bytes = 0
        in_scope_count = 0
        normalized_paths: set[str] = set()
        for item, archive_path in ordered_entries:
            unix_mode = item.external_attr >> 16
            file_type = stat.S_IFMT(unix_mode)
            if stat.S_ISLNK(unix_mode) or (file_type and not stat.S_ISREG(unix_mode)):
                raise RepositoryIngestError(
                    "Repository archive contains a special filesystem entry.",
                    code="unsafe_archive",
                )
            relative = PurePosixPath(*archive_path.parts[1:])
            if not relative.parts:
                continue
            logical_path = relative.as_posix()
            if len(logical_path.encode("utf-8")) > MAX_PATH_LENGTH or any(
                unicodedata.category(character) in {"Cc", "Cf"} for character in logical_path
            ):
                raise RepositoryIngestError(
                    "Repository archive contains an unsafe or overlong path.",
                    code="unsafe_archive",
                )
            collision_key = unicodedata.normalize("NFC", logical_path).casefold()
            if collision_key in normalized_paths:
                raise RepositoryIngestError(
                    "Repository archive contains colliding file paths.",
                    code="unsafe_archive",
                )
            normalized_paths.add(collision_key)
            path_carries_secret = _filename_carries_secret(logical_path)
            display_path = _redacted_path(logical_path) if path_carries_secret else logical_path
            in_scope = not resolved_subpath or (
                logical_path == resolved_subpath or logical_path.startswith(resolved_subpath + "/")
            )
            entry_record: dict[str, Any] = {
                "path": display_path,
                "byte_size": item.file_size,
                "in_scope": in_scope,
                "eligible": False,
                "excluded_reason": "outside_subpath" if not in_scope else "",
            }
            if not in_scope:
                manifest.append(entry_record)
                continue
            if resolved_subpath:
                logical_path = logical_path[len(resolved_subpath) :].lstrip("/")
                if not logical_path:
                    continue
                path_carries_secret = path_carries_secret or _filename_carries_secret(logical_path)
                entry_record["path"] = (
                    _redacted_path(logical_path) if path_carries_secret else logical_path
                )
            in_scope_count += 1
            path = PurePosixPath(logical_path)
            reason = (
                "secret_path" if path_carries_secret else _eligibility_reason(path, item.file_size)
            )
            if reason:
                entry_record["excluded_reason"] = reason
                exclusions[reason] += 1
                manifest.append(entry_record)
                continue
            try:
                payload = read_zip_member(archive, item, max_bytes=MAX_SOURCE_FILE_BYTES)
            except UnsafeArchiveError as exc:
                raise RepositoryScopeError(
                    "A repository file exceeds the safe analysis limit.",
                    manifest=manifest,
                ) from exc
            if len(payload) != item.file_size:
                raise RepositoryIngestError(
                    "Repository archive member size metadata is inconsistent.",
                    code="unsafe_archive",
                )
            entry_record["byte_size"] = len(payload)
            raw_sha256 = hashlib.sha256(payload).hexdigest()
            entry_record["sha256"] = raw_sha256
            text = _decode_source_text(payload)
            if text is None:
                entry_record["excluded_reason"] = "unsupported_or_binary"
                exclusions["unsupported_or_binary"] += 1
                manifest.append(entry_record)
                continue
            if text.startswith("version https://git-lfs.github.com/spec/v1"):
                entry_record["excluded_reason"] = "git_lfs_pointer_not_fetched"
                exclusions["git_lfs_pointer_not_fetched"] += 1
                manifest.append(entry_record)
                continue
            screened, redacted, hard_secret = _redact_content(text)
            if hard_secret:
                entry_record["excluded_reason"] = "secret_content"
                exclusions["secret_content"] += 1
                manifest.append(entry_record)
                continue
            if path.name.casefold() == ".gitmodules":
                entry_record["declared_submodules_not_fetched"] = len(
                    re.findall(r"(?im)^\s*\[submodule\s+", screened)
                )
            entry_record["eligible"] = True
            entry_record["redacted"] = redacted
            entry_record["sha256"] = raw_sha256
            language, parser_mode = _language_and_parser_mode(path)
            entry_record["language"] = language
            entry_record["parser_mode"] = parser_mode
            next_eligible_bytes = eligible_bytes + len(screened.encode("utf-8"))
            if len(files) + 1 > MAX_ANALYZED_FILES or next_eligible_bytes > MAX_ANALYZED_BYTES:
                manifest.append(entry_record)
                coverage = RepositoryCoverage(
                    archive_entries=len(entries),
                    files_in_scope=in_scope_count,
                    eligible_files=len(files) + 1,
                    analyzed_files=0,
                    excluded_files=sum(exclusions.values()),
                    eligible_bytes=next_eligible_bytes,
                    analyzed_bytes=0,
                    excluded_by_reason=dict(sorted(exclusions.items())),
                    complete=False,
                )
                raise RepositoryScopeError(
                    "This repository scope is too large for the beta analyzer. Choose a subpath.",
                    manifest=manifest,
                    coverage=coverage,
                )
            manifest.append(entry_record)
            eligible_bytes = next_eligible_bytes
            files.append(
                RepositoryFile(
                    path=logical_path,
                    raw_sha256=raw_sha256,
                    byte_size=len(payload),
                    text=screened,
                    redacted=redacted,
                    language=language,
                    parser_mode=parser_mode,
                )
            )
        coverage = RepositoryCoverage(
            archive_entries=len(entries),
            files_in_scope=in_scope_count,
            eligible_files=len(files),
            analyzed_files=0,
            excluded_files=sum(exclusions.values()),
            eligible_bytes=eligible_bytes,
            analyzed_bytes=0,
            excluded_by_reason=dict(sorted(exclusions.items())),
            complete=False,
        )
        if not files:
            raise RepositoryIngestError(
                "No supported, secret-screened source files were found in this scope.",
                code="no_analyzable_files",
            )
        if len(files) > MAX_ANALYZED_FILES or eligible_bytes > MAX_ANALYZED_BYTES:
            raise RepositoryScopeError(
                "This repository scope is too large for the beta analyzer. Choose a subpath.",
                manifest=manifest,
                coverage=coverage,
            )
        return RepositoryArchive(
            archive_sha256=hashlib.sha256(blob).hexdigest(),
            manifest=manifest,
            files=files,
            coverage=coverage,
        )
    finally:
        archive.close()
