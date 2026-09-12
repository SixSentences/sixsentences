"""Licensed, provenance-preserving imports of public LaTeX templates.

The importer deliberately supports a narrow set of sources:

* Overleaf gallery pages, using the gallery metadata and an official upstream
  project when one is linked;
* public GitHub repository roots;
* direct public ``.zip``, ``.tex`` or ``.latex`` downloads; and
* ordinary public pages that expose one of those direct downloads.

It never authenticates, replays cookies, bypasses a paywall, or follows an
unchecked redirect. Unknown licenses require an explicit rights confirmation
from the importing user and remain marked as unverified in provenance.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

from sixsentences_server.acquisition.fetch import HttpxFetcher
from sixsentences_server.acquisition.models import FetchedBlob
from sixsentences_server.writer.project_import import (
    LatexProjectImportError,
    import_latex_project,
)

MAX_TEMPLATE_BYTES = 25 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024


class TemplateImportError(ValueError):
    """A public template could not be inspected or safely imported."""


FetchPublic = Callable[[str, int], FetchedBlob | None]


@dataclass(frozen=True)
class RemoteTemplatePackage:
    """A validated remote project plus the provenance shown to the user."""

    source_url: str
    upstream_url: str
    provider: str
    title: str
    author: str
    license_name: str
    license_url: str
    license_status: str
    package_filename: str
    package: bytes
    main_content: str
    extra_files: tuple[tuple[str, str], ...]
    assets: tuple[tuple[str, bytes], ...]
    warnings: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.package).hexdigest()

    @property
    def file_count(self) -> int:
        return 1 + len(self.extra_files) + len(self.assets)

    @property
    def rights_confirmation_required(self) -> bool:
        return self.license_status != "open"

    def preview(self) -> dict[str, object]:
        """Return the serializable, non-content preview for the UI."""

        return {
            "source_url": self.source_url,
            "upstream_url": self.upstream_url,
            "provider": self.provider,
            "title": self.title,
            "author": self.author,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "license_status": self.license_status,
            "rights_confirmation_required": self.rights_confirmation_required,
            "file_count": self.file_count,
            "text_file_count": 1 + len(self.extra_files),
            "asset_count": len(self.assets),
            "package_bytes": len(self.package),
            "sha256": self.sha256,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _PageMetadata:
    title: str
    author: str = ""
    license_name: str = ""
    license_url: str = ""
    upstream_url: str = ""
    download_url: str = ""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        self._href = values.get("href", "")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = ""
            self._text = []


_DROP = re.compile(
    r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<(?:h1|title)\b[^>]*>(.*?)</(?:h1|title)>", re.I | re.S)
_OG_TITLE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:title|twitter:title)["\'][^>]+content=["\']([^"\']+)',
    re.I,
)
_GITHUB_REPO = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"
    r"(?=[/?#\"'<>\s]|$)",
    re.I,
)
_DANGEROUS_TEX = (
    (re.compile(r"\\(?:immediate\s*)?write18\b", re.I), r"\write18"),
    (re.compile(r"\\(?:openin|openout)\b", re.I), "raw file access"),
    (re.compile(r"\\directlua\b", re.I), r"\directlua"),
    (re.compile(r"\\usepackage(?:\[[^\]]*\])?\{shellesc\}", re.I), "shellesc"),
    (
        re.compile(r"\\(?:input|include)\s*\{\s*(?:/|\\|(?:\.\./)+)", re.I),
        "an external file path",
    ),
)


def _visible_text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", _DROP.sub(" ", markup)))).strip()


def _page_title(markup: str, fallback: str) -> str:
    match = _OG_TITLE.search(markup) or _TITLE.search(markup)
    if not match:
        return fallback
    return _visible_text(match.group(1)).split(" - Overleaf", 1)[0].strip()[:200] or fallback


def _between(text: str, start: str, end: str) -> str:
    match = re.search(
        rf"\b{re.escape(start)}\s*:?\s*(.*?)\s+\b{re.escape(end)}\s*:?",
        text,
        re.I,
    )
    return match.group(1).strip() if match else ""


def _license_details(name: str) -> tuple[str, str, str]:
    normalized = re.sub(r"\s+", " ", name).strip()
    lowered = normalized.lower()
    known: tuple[tuple[tuple[str, ...], str, str], ...] = (
        (
            ("cc by 4.0", "cc-by-4.0"),
            "Creative Commons CC BY 4.0",
            "https://creativecommons.org/licenses/by/4.0/",
        ),
        (
            ("cc0", "public domain"),
            "Creative Commons CC0 1.0",
            "https://creativecommons.org/publicdomain/zero/1.0/",
        ),
        (
            ("latex project public license", "lppl"),
            "LaTeX Project Public License",
            "https://www.latex-project.org/lppl/",
        ),
        (
            ("apache license 2", "apache-2.0"),
            "Apache License 2.0",
            "https://www.apache.org/licenses/LICENSE-2.0",
        ),
        (
            ("mit license", "permission is hereby granted, free of charge"),
            "MIT License",
            "https://opensource.org/license/mit",
        ),
        (
            ("bsd 3-clause", "bsd 2-clause"),
            "BSD License",
            "https://opensource.org/license/bsd-3-clause",
        ),
        (
            ("gnu general public license", "gpl-"),
            "GNU General Public License",
            "https://www.gnu.org/licenses/gpl-3.0.html",
        ),
    )
    for needles, canonical, url in known:
        if any(needle in lowered for needle in needles):
            return canonical, url, "open"
    return normalized or "License not stated", "", "unverified"


def _archive_license(files: tuple[tuple[str, str], ...], fallback: str) -> tuple[str, str, str]:
    if fallback:
        return _license_details(fallback)
    candidates = [
        content
        for path, content in files
        if PurePosixPath(path).name.lower()
        in {"license", "license.txt", "license.md", "copying", "readme", "readme.md"}
    ]
    return _license_details("\n".join(candidates)[:100_000])


def _validate_tex_sources(main_content: str, extra_files: tuple[tuple[str, str], ...]) -> None:
    for path, content in (("main.tex", main_content), *extra_files):
        if PurePosixPath(path).suffix.lower() not in {
            ".tex",
            ".latex",
            ".sty",
            ".cls",
            ".bst",
            ".bbx",
            ".cbx",
            ".lbx",
            ".def",
            ".cfg",
            ".clo",
            ".dtx",
            ".ins",
            ".ist",
        }:
            continue
        for pattern, label in _DANGEROUS_TEX:
            if pattern.search(content):
                raise TemplateImportError(
                    f"the template contains {label} in {path}; review and upload it manually"
                )


def _default_fetch(url: str, max_bytes: int) -> FetchedBlob | None:
    return HttpxFetcher(max_bytes=max_bytes).fetch(url)


def _decode_page(blob: FetchedBlob) -> str:
    try:
        return blob.content.decode("utf-8")
    except UnicodeDecodeError:
        return blob.content.decode("latin-1", errors="replace")


def _filename(url: str, content_type: str, fallback: str = "template.zip") -> str:
    name = PurePosixPath(unquote(urlparse(url).path)).name
    if name.lower().endswith((".zip", ".tex", ".latex")):
        return name
    if "tex" in content_type.lower():
        return "template.tex"
    if "zip" in content_type.lower() or blob_is_zip_name(name):
        return fallback
    return fallback


def blob_is_zip_name(name: str) -> bool:
    return name.lower().endswith((".zip", ".zipball"))


def _github_root(url: str) -> str:
    match = _GITHUB_REPO.search(url)
    if not match:
        return ""
    owner, repo = match.groups()
    return f"https://github.com/{owner}/{repo.removesuffix('.git')}"


def _github_package(repo_url: str, fetch: FetchPublic) -> tuple[FetchedBlob, str, str]:
    root = _github_root(repo_url)
    if not root:
        raise TemplateImportError("the GitHub link is not a repository root")
    page = fetch(root, MAX_PAGE_BYTES)
    markup = _decode_page(page) if page is not None else ""
    branch_match = re.search(r'"defaultBranch"\s*:\s*"([^"]+)"', markup)
    branches = [branch_match.group(1)] if branch_match else []
    branches.extend(branch for branch in ("main", "master") if branch not in branches)
    for branch in branches:
        archive_url = f"{root}/archive/refs/heads/{branch}.zip"
        archive = fetch(archive_url, MAX_TEMPLATE_BYTES)
        if archive is not None and archive.content.startswith(b"PK"):
            return (
                archive,
                archive_url,
                f"{PurePosixPath(urlparse(root).path).name}.zip",
            )
    raise TemplateImportError("the public GitHub project archive could not be downloaded")


def _overleaf_metadata(markup: str, url: str) -> _PageMetadata:
    plain = _visible_text(markup)
    title = _page_title(markup, "Imported Overleaf template")
    author = _between(plain, "Author", "Last Updated")[:200]
    license_name = _between(plain, "License", "Abstract")[:200]
    github = next(
        (
            match.group(0).rstrip(".,)")
            for match in _GITHUB_REPO.finditer(markup)
            if "overleaf" not in match.group(0).lower()
        ),
        "",
    )
    canonical, license_url, _status = _license_details(license_name)
    return _PageMetadata(
        title=title,
        author=author,
        license_name=canonical if license_name else "",
        license_url=license_url,
        upstream_url=_github_root(github),
    )


def _generic_page_metadata(markup: str, url: str) -> _PageMetadata:
    parser = _LinkParser()
    parser.feed(markup)
    candidates: list[str] = []
    github = ""
    for href, _label in parser.links:
        absolute = urljoin(url, href)
        if not github and _github_root(absolute):
            github = _github_root(absolute)
        if urlparse(absolute).path.lower().endswith((".zip", ".tex", ".latex")):
            candidates.append(absolute)
    return _PageMetadata(
        title=_page_title(markup, PurePosixPath(urlparse(url).path).stem or "Imported template"),
        upstream_url=github,
        download_url=candidates[0] if candidates else "",
    )


def inspect_remote_template(
    url: str,
    *,
    fetch_public: FetchPublic | None = None,
) -> RemoteTemplatePackage:
    """Fetch, validate and describe a public template project.

    DNS/redirect hardening lives in :class:`HttpxFetcher`; a custom fetcher is
    accepted only for deterministic tests.
    """

    source_url = url.strip()
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise TemplateImportError("enter a public HTTPS template link")
    fetch = fetch_public or _default_fetch
    provider = "Direct"
    metadata = _PageMetadata(title=PurePosixPath(unquote(parsed.path)).stem or "Imported template")
    package_blob: FetchedBlob | None = None
    package_url = source_url
    package_filename = _filename(source_url, "")

    if _github_root(source_url):
        provider = "GitHub"
        package_blob, package_url, package_filename = _github_package(source_url, fetch)
        metadata = _PageMetadata(
            title=PurePosixPath(urlparse(_github_root(source_url)).path).name.replace("-", " "),
            upstream_url=_github_root(source_url),
        )
    else:
        initial = fetch(source_url, MAX_TEMPLATE_BYTES)
        if initial is None:
            raise TemplateImportError("the public template link could not be loaded")
        content_type = initial.content_type.lower()
        is_zip = initial.content.startswith(b"PK")
        is_tex = source_url.lower().endswith((".tex", ".latex")) or (
            "tex" in content_type and not initial.content.lstrip().startswith(b"<")
        )
        if is_zip or is_tex:
            package_blob = initial
            package_filename = _filename(initial.final_url, content_type)
            package_url = initial.final_url
        elif "html" in content_type or initial.content.lstrip().startswith(b"<"):
            markup = _decode_page(initial)
            is_overleaf = parsed.hostname.lower().removeprefix("www.") == "overleaf.com"
            metadata = (
                _overleaf_metadata(markup, source_url)
                if is_overleaf
                else _generic_page_metadata(markup, source_url)
            )
            provider = "Overleaf" if is_overleaf else "Publisher"
            if metadata.upstream_url:
                package_blob, package_url, package_filename = _github_package(
                    metadata.upstream_url, fetch
                )
            elif metadata.download_url:
                package_blob = fetch(metadata.download_url, MAX_TEMPLATE_BYTES)
                package_url = metadata.download_url
                if package_blob is not None:
                    package_filename = _filename(package_blob.final_url, package_blob.content_type)
            elif is_overleaf:
                raise TemplateImportError(
                    "this Overleaf page has no public project archive; "
                    "use its official source link or upload a ZIP"
                )
            else:
                raise TemplateImportError(
                    "the page does not expose a public .zip or .tex template download"
                )
        else:
            raise TemplateImportError("the link is not a LaTeX project or template page")

    if package_blob is None:
        raise TemplateImportError("the template project could not be downloaded")
    try:
        _project_title, main, extra_files, assets = import_latex_project(
            package_blob.content, package_filename
        )
    except LatexProjectImportError as exc:
        raise TemplateImportError(str(exc)) from exc
    files_tuple = tuple(extra_files)
    assets_tuple = tuple(assets)
    _validate_tex_sources(main, files_tuple)
    license_name, detected_license_url, license_status = _archive_license(
        files_tuple, metadata.license_name
    )
    license_url = metadata.license_url or detected_license_url
    warnings = (
        "Review publisher and venue instructions before submission.",
        "Remote LaTeX is executable source; inspect unfamiliar class and style files.",
    )
    return RemoteTemplatePackage(
        source_url=source_url,
        upstream_url=metadata.upstream_url or package_url,
        provider=provider,
        title=metadata.title.strip()[:200] or "Imported template",
        author=metadata.author.strip()[:200],
        license_name=license_name,
        license_url=license_url,
        license_status=license_status,
        package_filename=package_filename,
        package=package_blob.content,
        main_content=main,
        extra_files=files_tuple,
        assets=assets_tuple,
        warnings=warnings,
    )
