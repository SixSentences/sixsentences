"""Safe, dependency-free citations for org-private Library paper metadata.

The Library stores richer, tenant-local metadata than the global ``WorkRow``.
These formatters deliberately consume that effective metadata snapshot instead
of looking up a global corpus record.  They are presentation helpers only: no
missing author, date, venue or identifier is guessed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

LibraryCitationFormat = Literal["apa", "mla", "chicago", "harvard", "bibtex", "ris"]

CITATION_FORMATS: tuple[LibraryCitationFormat, ...] = (
    "apa",
    "mla",
    "chicago",
    "harvard",
    "bibtex",
    "ris",
)
DOWNLOAD_MEDIA_TYPES: dict[LibraryCitationFormat, str] = {
    "apa": "text/plain; charset=utf-8",
    "mla": "text/plain; charset=utf-8",
    "chicago": "text/plain; charset=utf-8",
    "harvard": "text/plain; charset=utf-8",
    "bibtex": "application/x-bibtex; charset=utf-8",
    "ris": "application/x-research-info-systems; charset=utf-8",
}
DOWNLOAD_FILENAMES: dict[LibraryCitationFormat, str] = {
    "apa": "sixsentences-citations-apa.txt",
    "mla": "sixsentences-citations-mla.txt",
    "chicago": "sixsentences-citations-chicago.txt",
    "harvard": "sixsentences-citations-harvard.txt",
    "bibtex": "sixsentences-citations.bib",
    "ris": "sixsentences-citations.ris",
}

_SPACE = re.compile(r"\s+")
_NON_KEY = re.compile(r"[^a-z0-9]+")
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_PAGE_RANGE = re.compile(r"^\s*([^\s,;]+?)\s*[-–—]\s*([^\s,;]+)\s*$")
_INTERNAL_TITLE_SENTINELS = frozenset(
    {
        "captured paper",
        "captured paper citation",
        "untitled",
        "untitled paper",
        "untitled source",
        "uploaded document",
    }
)


def _plain(value: object | None) -> str:
    """Make one metadata value safe for a single citation/output line."""

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    without_controls = "".join(
        " " if char in "\r\n\t" else char
        for char in normalized
        if char in "\r\n\t" or unicodedata.category(char) not in {"Cc", "Cs"}
    )
    return _SPACE.sub(" ", without_controls).strip()


def _doi(value: object | None) -> str:
    doi = _plain(value)
    doi = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", doi, flags=re.I)
    return doi.rstrip(". ")


def _url(value: object | None) -> str:
    url = _plain(value)
    return url if re.match(r"^https?://", url, flags=re.I) else ""


def _split_author(name: str) -> tuple[str, str]:
    """Best-effort family/given split for the Library's free-form names."""

    clean = _plain(name)
    if not clean:
        return "", ""
    if "," in clean:
        family, given = clean.split(",", 1)
        return family.strip(), given.strip()
    parts = clean.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _initials(given: str) -> str:
    groups = []
    for token in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", given, flags=re.UNICODE):
        groups.append("-".join(f"{part[0].upper()}." for part in token.split("-") if part))
    return " ".join(groups)


def _given_family(name: str) -> str:
    family, given = _split_author(name)
    return " ".join(value for value in (given, family) if value)


def _inverted(name: str) -> str:
    family, given = _split_author(name)
    return f"{family}, {given}" if given else family


def _apa_name(name: str) -> str:
    family, given = _split_author(name)
    initials = _initials(given)
    return f"{family}, {initials}".rstrip(", ")


def _join_last(values: list[str], conjunction: str) -> str:
    values = [value for value in values if value]
    if len(values) < 2:
        return values[0] if values else ""
    if len(values) == 2:
        return f"{values[0]} {conjunction} {values[1]}"
    return f"{', '.join(values[:-1])}, {conjunction} {values[-1]}"


def _apa_authors(authors: tuple[str, ...]) -> str:
    names = [_apa_name(author) for author in authors if _plain(author)]
    if len(names) == 2:
        return f"{names[0]}, & {names[1]}"
    if len(names) <= 20:
        return _join_last(names, "&")
    return f"{', '.join(names[:19])}, … {names[-1]}"


def _mla_authors(authors: tuple[str, ...]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return _inverted(authors[0])
    if len(authors) == 2:
        return f"{_inverted(authors[0])}, and {_given_family(authors[1])}"
    return f"{_inverted(authors[0])}, et al."


def _chicago_authors(authors: tuple[str, ...]) -> str:
    if not authors:
        return ""
    if len(authors) > 6:
        shown = [
            _inverted(authors[0]),
            *(_given_family(author) for author in authors[1:3]),
        ]
        return f"{', '.join(shown)}, et al."
    if len(authors) == 2:
        return f"{_inverted(authors[0])}, and {_given_family(authors[1])}"
    return _join_last(
        [_inverted(authors[0]), *(_given_family(author) for author in authors[1:])],
        "and",
    )


def _harvard_authors(authors: tuple[str, ...]) -> str:
    names = [_apa_name(author) for author in authors]
    return _join_last(names, "and")


@dataclass(frozen=True)
class LibraryCitationRecord:
    """One effective, already-authorized Library paper snapshot."""

    document_id: int
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    published_at: str = ""
    container_title: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    publisher_place: str = ""
    edition: str = ""
    doi: str = ""
    url: str = ""
    language: str = ""
    issn: str = ""
    isbn: str = ""
    accessed_at: str = ""
    item_type: str = ""
    citation_key: str = ""

    @property
    def missing_fields(self) -> list[str]:
        """Fields fundamental to a useful reference, without inventing values."""

        missing = [
            field
            for field, value in (
                ("title", self.title),
                ("authors", self.authors),
                ("year", self.year),
            )
            if not value
        ]
        kind = self.item_type.casefold()
        if "book" in kind and "chapter" not in kind:
            if not self.publisher:
                missing.append("publisher")
        elif (
            not any(value in kind for value in ("preprint", "thesis", "report", "dataset"))
            and not self.container_title
        ):
            missing.append("container_title")
        return missing


def record_from_metadata(
    document_id: int,
    metadata: Mapping[str, Any],
    *,
    internal_titles: Collection[str] = (),
) -> LibraryCitationRecord:
    """Convert an effective Library metadata mapping into a sanitized record."""

    raw_year = metadata.get("year")
    year = int(raw_year) if isinstance(raw_year, int) and 1000 <= raw_year <= 3000 else None
    title = _plain(metadata.get("title"))
    if title.casefold() in {
        *_INTERNAL_TITLE_SENTINELS,
        *(_plain(value).casefold() for value in internal_titles),
    }:
        title = ""
    authors = tuple(
        author for value in (metadata.get("authors") or []) if (author := _plain(value))
    )
    return LibraryCitationRecord(
        document_id=document_id,
        title=title,
        authors=authors,
        year=year,
        published_at=_plain(metadata.get("published_at")),
        container_title=_plain(metadata.get("container_title")),
        volume=_plain(metadata.get("volume")),
        issue=_plain(metadata.get("issue")),
        pages=_plain(metadata.get("pages")),
        publisher=_plain(metadata.get("publisher")),
        publisher_place=_plain(metadata.get("publisher_place")),
        edition=_plain(metadata.get("edition")),
        doi=_doi(metadata.get("doi")),
        url=_url(metadata.get("canonical_url") or metadata.get("source_url")),
        language=_plain(metadata.get("language")),
        issn=_plain(metadata.get("issn")),
        isbn=_plain(metadata.get("isbn")),
        accessed_at=_plain(metadata.get("accessed_at")),
        item_type=_plain(metadata.get("item_type")),
        citation_key=_plain(metadata.get("citation_key")),
    )


def _year(record: LibraryCitationRecord) -> str:
    return str(record.year) if record.year else "n.d."


def _doi_url(record: LibraryCitationRecord) -> str:
    return f"https://doi.org/{record.doi}" if record.doi else ""


def _terminal(text: str) -> str:
    return text if not text or text.endswith((".", "?", "!")) else f"{text}."


def _apa(record: LibraryCitationRecord) -> str:
    authors = _apa_authors(record.authors)
    if authors:
        parts = [f"{authors} ({_year(record)})."]
        if record.title:
            parts.append(_terminal(record.title))
    else:
        parts = []
        if record.title:
            parts.append(_terminal(record.title))
        parts.append(f"({_year(record)}).")
    publication = record.container_title
    if record.volume:
        publication += (", " if publication else "") + record.volume
    if record.issue:
        publication += f"({record.issue})"
    if record.pages:
        publication += (", " if publication else "") + record.pages
    if publication:
        parts.append(_terminal(publication))
    elif record.publisher:
        parts.append(_terminal(record.publisher))
    if locator := (_doi_url(record) or record.url):
        parts.append(locator)
    return " ".join(part for part in parts if part)


def _mla(record: LibraryCitationRecord) -> str:
    parts: list[str] = []
    if authors := _mla_authors(record.authors):
        parts.append(_terminal(authors))
    if record.title:
        parts.append(f"“{_terminal(record.title)}”")
    if record.container_title:
        parts.append(f"{record.container_title},")
    if record.volume:
        parts.append(f"vol. {record.volume},")
    if record.issue:
        parts.append(f"no. {record.issue},")
    if record.year:
        parts.append(f"{record.year},")
    if record.pages:
        parts.append(f"pp. {record.pages}.")
    elif record.publisher:
        parts.append(_terminal(record.publisher))
    if locator := (_doi_url(record) or record.url):
        parts.append(_terminal(locator))
    return " ".join(parts).replace(", .", ".")


def _chicago(record: LibraryCitationRecord) -> str:
    """Chicago author-date bibliography entry (plain-text presentation)."""

    parts: list[str] = []
    if authors := _chicago_authors(record.authors):
        parts.append(_terminal(authors))
        parts.append(_terminal(_year(record)))
        if record.title:
            parts.append(f"“{_terminal(record.title)}”")
    else:
        if record.title:
            parts.append(f"“{_terminal(record.title)}”")
        parts.append(_terminal(_year(record)))
    publication = record.container_title
    if record.volume:
        publication += (" " if publication else "") + record.volume
    if record.issue:
        publication += f" ({record.issue})"
    if record.pages:
        publication += f": {record.pages}"
    if publication:
        parts.append(_terminal(publication))
    elif record.publisher:
        location = f"{record.publisher_place}: " if record.publisher_place else ""
        parts.append(_terminal(f"{location}{record.publisher}"))
    if locator := (_doi_url(record) or record.url):
        parts.append(_terminal(locator))
    return " ".join(parts)


def _harvard(record: LibraryCitationRecord) -> str:
    """Harvard author-date reference with all recorded authors."""

    authors = _harvard_authors(record.authors)
    if authors:
        parts = [f"{authors} ({_year(record)})"]
        if record.title:
            parts.append(f"‘{record.title}’,")
    elif record.title:
        parts = [f"‘{record.title}’ ({_year(record)}),"]
    else:
        parts = [f"({_year(record)})"]
    if record.container_title:
        parts.append(f"{record.container_title},")
    if record.volume:
        parts.append(f"vol. {record.volume},")
    if record.issue:
        parts.append(f"no. {record.issue},")
    if record.pages:
        parts.append(f"pp. {record.pages}.")
    elif record.publisher:
        parts.append(_terminal(record.publisher))
    if locator := (_doi_url(record) or record.url):
        access = f" (Accessed: {record.accessed_at})" if record.url and record.accessed_at else ""
        parts.append(_terminal(f"Available at: {locator}{access}"))
    return " ".join(parts).replace(", .", ".")


_BIBTEX_ESCAPES = {
    "\\": r"{\textbackslash{}}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"{\textasciitilde{}}",
    "^": r"{\textasciicircum{}}",
}


def _bibtex_escape(value: str) -> str:
    return "".join(_BIBTEX_ESCAPES.get(char, char) for char in _plain(value))


def _citation_key(record: LibraryCitationRecord) -> str:
    requested = _NON_KEY.sub(
        "",
        unicodedata.normalize("NFKD", record.citation_key)
        .encode("ascii", "ignore")
        .decode()
        .lower(),
    )
    if requested:
        return requested
    family = _split_author(record.authors[0])[0] if record.authors else ""
    words = [
        word
        for word in _WORDS.findall(record.title)
        if len(unicodedata.normalize("NFKD", word).encode("ascii", "ignore")) > 2
    ]
    stem = f"{family}{record.year or ''}{words[0] if words else ''}"
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    return _NON_KEY.sub("", ascii_stem.lower()) or f"paper{record.document_id}"


def _bibtex_type(record: LibraryCitationRecord) -> str:
    kind = record.item_type.casefold()
    if "book" in kind and "chapter" not in kind:
        return "book"
    if any(value in kind for value in ("conference", "proceeding")):
        return "inproceedings"
    if "thesis" in kind:
        return "phdthesis"
    return "article"


def _bibtex(record: LibraryCitationRecord) -> str:
    fields: list[tuple[str, str]] = []
    if record.title:
        fields.append(("title", record.title))
    if record.authors:
        fields.append(("author", " and ".join(record.authors)))
    if record.year:
        fields.append(("year", str(record.year)))
    if record.container_title:
        field = "booktitle" if _bibtex_type(record) == "inproceedings" else "journal"
        fields.append((field, record.container_title))
    for key, value in (
        ("volume", record.volume),
        ("number", record.issue),
        ("pages", record.pages),
        ("publisher", record.publisher),
        ("address", record.publisher_place),
        ("edition", record.edition),
        ("doi", record.doi),
        ("url", _doi_url(record) or record.url),
        ("language", record.language),
        ("issn", record.issn),
        ("isbn", record.isbn),
    ):
        if value:
            fields.append((key, value))
    body = ",\n".join(f"  {key} = {{{_bibtex_escape(value)}}}" for key, value in fields)
    return f"@{_bibtex_type(record)}{{{_citation_key(record)},\n{body}\n}}"


def _ris_type(record: LibraryCitationRecord) -> str:
    kind = record.item_type.casefold()
    if "book" in kind and "chapter" not in kind:
        return "BOOK"
    if "chapter" in kind:
        return "CHAP"
    if any(value in kind for value in ("conference", "proceeding")):
        return "CPAPER"
    if "thesis" in kind:
        return "THES"
    return "JOUR"


def _ris(record: LibraryCitationRecord) -> str:
    lines = [f"TY  - {_ris_type(record)}"]
    for author in record.authors:
        lines.append(f"AU  - {_inverted(author)}")
    if record.title:
        lines.append(f"TI  - {record.title}")
    if record.container_title:
        lines.append(f"T2  - {record.container_title}")
    if record.year:
        lines.append(f"PY  - {record.year}")
    if record.volume:
        lines.append(f"VL  - {record.volume}")
    if record.issue:
        lines.append(f"IS  - {record.issue}")
    if record.pages:
        match = _PAGE_RANGE.match(record.pages)
        if match:
            lines.extend((f"SP  - {match.group(1)}", f"EP  - {match.group(2)}"))
        else:
            lines.append(f"SP  - {record.pages}")
    if record.publisher:
        lines.append(f"PB  - {record.publisher}")
    if record.doi:
        lines.append(f"DO  - {record.doi}")
    if locator := (_doi_url(record) or record.url):
        lines.append(f"UR  - {locator}")
    if record.language:
        lines.append(f"LA  - {record.language}")
    if serial := (record.issn or record.isbn):
        lines.append(f"SN  - {serial}")
    lines.append("ER  - ")
    return "\n".join(lines)


def render_citation(record: LibraryCitationRecord, fmt: LibraryCitationFormat) -> str:
    """Render one safe copy/download value in the requested format."""

    renderers = {
        "apa": _apa,
        "mla": _mla,
        "chicago": _chicago,
        "harvard": _harvard,
        "bibtex": _bibtex,
        "ris": _ris,
    }
    return renderers[fmt](record)


def render_citation_export(
    records: list[LibraryCitationRecord],
    fmt: LibraryCitationFormat,
) -> str:
    """Render a deterministic batch; every download is UTF-8 text."""

    export_records = records
    if fmt == "bibtex":
        used: set[str] = set()
        export_records = []
        for record in records:
            stem = _citation_key(record)
            key = stem
            duplicate_number = 0
            while key in used:
                duplicate_number += 1
                key = f"{stem}{_alphabetic_suffix(duplicate_number)}"
            used.add(key)
            export_records.append(replace(record, citation_key=key))
    separator = "\n\n"
    return separator.join(render_citation(record, fmt) for record in export_records) + "\n"


def _alphabetic_suffix(number: int) -> str:
    """Return 1 -> a, 26 -> z, 27 -> aa without punctuation overflow."""

    suffix = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        suffix = chr(ord("a") + remainder) + suffix
    return suffix
