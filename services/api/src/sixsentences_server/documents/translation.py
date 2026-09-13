"""Durable full-document translation state and translated PDF rendering.

The original PDF is immutable. A translation stores one UTF-8 text file per
source page, a tenant-scoped progress manifest, and a clean reading-edition
PDF. Page files make retries resumable and ensure a provider outage never
throws away already completed work.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pypdf

from sixsentences_server.writer.service import compile_document

LANGUAGE_NAMES: dict[str, str] = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "sv": "Swedish",
    "tr": "Turkish",
}

LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "de": "Deutsch",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "nl": "Nederlands",
    "pl": "Polski",
    "pt": "Português",
    "sv": "Svenska",
    "tr": "Türkçe",
}

_RENDER_COPY: dict[str, dict[str, str]] = {
    "de": {
        "edition": "Übersetzte Leseausgabe",
        "source_page": "Quellseite",
        "contents": "Inhalt",
        "no_text": (
            "Auf dieser Quellseite war kein extrahierbarer Text vorhanden. "
            "Abbildungen und gescannte Inhalte bleiben im Original-PDF verfügbar."
        ),
        "notice": (
            "Diese Ausgabe übersetzt den vollständigen extrahierbaren Text des "
            "Quell-PDFs. Sie dient als Lesehilfe und ersetzt nicht die "
            "Originalpublikation. Zitate, Gleichungen, Zahlen und Fachbegriffe werden "
            "so originalgetreu wie möglich beibehalten. Zitieren und prüfen Sie stets "
            "anhand des Originals. Abbildungen und das ursprüngliche Seitenlayout "
            "bleiben im Original-PDF."
        ),
        "text_layer": "Hinweis zur Textebene.",
        "missing_pages": (
            "Die Quellseiten {pages} enthielten keinen extrahierbaren Text. "
            "Ihre Abbildungen oder gescannten Inhalte bleiben im Original-PDF verfügbar."
        ),
    },
    "en": {
        "edition": "Translated reading edition",
        "source_page": "Source page",
        "contents": "Contents",
        "no_text": (
            "No extractable text was present on this source page. Figures and scanned "
            "content remain available in the original PDF."
        ),
        "notice": (
            "This edition translates the complete extractable text of the source PDF. "
            "It is provided for reading support and does not replace the original "
            "publication. Citations, equations, numbers and technical terms are retained "
            "as faithfully as possible. Always cite and verify against the original "
            "document. Figures and the original page layout remain in the original PDF."
        ),
        "text_layer": "Text-layer notice.",
        "missing_pages": (
            "Source pages {pages} contained no extractable text. Their figures or "
            "scanned content remain available in the original PDF."
        ),
    },
}

TRANSLATION_VERSION = 2
MAX_TRANSLATION_PAGES = 400


class TranslationRenderError(RuntimeError):
    """The translated reading-edition PDF could not be rendered."""


@dataclass(frozen=True)
class TranslationPaths:
    """All files belonging to one document-language translation."""

    root: Path
    manifest: Path
    pdf: Path

    def page(self, page_number: int, language: str) -> Path:
        """Return the resumable page-text cache path."""

        return self.root / f"v{TRANSLATION_VERSION}-{page_number}-{language}.txt"


def translation_paths(
    documents_dir: Path,
    *,
    checksum: str,
    org_id: int,
    document_id: int,
    language: str,
) -> TranslationPaths:
    """Resolve cache paths without accepting user-controlled path fragments."""

    if language not in LANGUAGE_NAMES:
        raise ValueError("unsupported translation language")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("invalid document checksum")
    root = documents_dir / "translations" / checksum[:2] / checksum
    key = f"{org_id}-{document_id}-{language}"
    return TranslationPaths(
        root=root,
        manifest=root / f"{key}.json",
        pdf=root / f"v{TRANSLATION_VERSION}-{language}-reading-edition.pdf",
    )


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for progress payloads."""

    return datetime.now(UTC).isoformat()


def pdf_page_count(content: bytes) -> int:
    """Return the exact source-page count, or zero for an unreadable PDF."""

    try:
        return len(pypdf.PdfReader(BytesIO(content)).pages)
    except Exception:
        return 0


def read_manifest(path: Path) -> dict[str, Any] | None:
    """Read a translation manifest; damaged partial files are ignored safely."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a manifest so polling never observes partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "α": r"\ensuremath{\alpha}",
    "β": r"\ensuremath{\beta}",
    "γ": r"\ensuremath{\gamma}",
    "δ": r"\ensuremath{\delta}",
    "ε": r"\ensuremath{\varepsilon}",
    "λ": r"\ensuremath{\lambda}",
    "μ": r"\ensuremath{\mu}",
    "π": r"\ensuremath{\pi}",
    "ρ": r"\ensuremath{\rho}",
    "σ": r"\ensuremath{\sigma}",
    "τ": r"\ensuremath{\tau}",
    "φ": r"\ensuremath{\phi}",
    "χ": r"\ensuremath{\chi}",
    "ω": r"\ensuremath{\omega}",
    "Δ": r"\ensuremath{\Delta}",
    "Σ": r"\ensuremath{\Sigma}",
    "Ω": r"\ensuremath{\Omega}",
    "≤": r"\ensuremath{\leq}",
    "≥": r"\ensuremath{\geq}",
    "≠": r"\ensuremath{\neq}",
    "±": r"\ensuremath{\pm}",
    "×": r"\ensuremath{\times}",
    "∞": r"\ensuremath{\infty}",
    "∑": r"\ensuremath{\sum}",
    "√": r"\ensuremath{\surd}",
    "→": r"\ensuremath{\rightarrow}",
    "←": r"\ensuremath{\leftarrow}",
}


def latex_escape(value: str) -> str:
    """Escape untrusted translated text for literal LaTeX rendering."""

    return "".join(_LATEX_ESCAPES.get(character, character) for character in value)


def _copy(language: str) -> dict[str, str]:
    """Return localized renderer copy, falling back to neutral English."""

    return _RENDER_COPY.get(language, _RENDER_COPY["en"])


def _page_body(text: str, *, language: str) -> str:
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        return rf"\textit{{{latex_escape(_copy(language)['no_text'])}}}"
    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    if not blocks:
        blocks = [cleaned]
    rendered: list[str] = []
    for block in blocks:
        # Provider line wraps are presentation details, not new paragraphs.
        normalized = " ".join(line.strip() for line in block.splitlines() if line.strip())
        rendered.append(latex_escape(normalized))
    return "\n\n".join(rendered)


def translated_latex(
    *,
    title: str,
    language: str,
    language_name: str,
    translated_pages: list[str],
    pages_without_text: list[int],
) -> str:
    """Build a restrained, Unicode-safe LaTeX reading edition."""

    copy = _copy(language)
    safe_title = latex_escape(title.strip() or "Untitled paper")
    safe_language = latex_escape(LANGUAGE_DISPLAY_NAMES.get(language, language_name))
    safe_edition = latex_escape(copy["edition"])
    safe_source_page = latex_escape(copy["source_page"])
    page_sections: list[str] = []
    for index, page_text in enumerate(translated_pages, start=1):
        if index > 1:
            page_sections.append(r"\clearpage")
        page_sections.extend(
            [
                rf"\section*{{{safe_source_page} {index}}}",
                rf"\addcontentsline{{toc}}{{section}}{{{safe_source_page} {index}}}",
                _page_body(page_text, language=language),
            ]
        )
    missing_note = ""
    if pages_without_text:
        numbers = ", ".join(str(page) for page in pages_without_text)
        missing_note = (
            "\\par\\medskip\\noindent\\textbf{"
            f"{latex_escape(copy['text_layer'])}"
            + "} "
            + latex_escape(copy["missing_pages"].format(pages=numbers))
        )
    body = "\n\n".join(page_sections)
    return rf"""\documentclass[10pt]{{article}}
\usepackage{{iftex}}
\ifPDFTeX
  \usepackage[utf8]{{inputenc}}
  \usepackage[T1]{{fontenc}}
  \usepackage{{lmodern}}
\fi
\usepackage[a4paper,margin=24mm,headheight=14pt]{{geometry}}
\usepackage{{microtype}}
\usepackage{{xcolor}}
\usepackage{{graphicx}}
\usepackage{{fancyhdr}}
\usepackage{{parskip}}
\usepackage[hidelinks]{{hyperref}}
\definecolor{{sixpine}}{{HTML}}{{0C1D19}}
\definecolor{{sixmoss}}{{HTML}}{{476F64}}
\definecolor{{sixmuted}}{{HTML}}{{66736F}}
\pagestyle{{fancy}}
\fancyhf{{}}
\fancyhead[L]{{\small\color{{sixmuted}} SixSentences\_ {safe_edition}}}
\fancyhead[R]{{\small\color{{sixmuted}} {safe_language}}}
\fancyfoot[C]{{\small\color{{sixmuted}} \thepage}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0.65em}}
\emergencystretch=2em
\sloppy
\begin{{document}}
\thispagestyle{{empty}}
\vspace*{{0.12\textheight}}
{{\color{{sixmoss}}\small\MakeUppercase{{{safe_edition}}}}}\\[1.2em]
{{\raggedright\color{{sixpine}}\Huge\bfseries {safe_title}\par}}
\vspace{{1.5em}}
{{\Large {safe_language}}}
\vfill
{{\color{{sixmuted}}\small
{latex_escape(copy["notice"])}
{missing_note}
}}
\clearpage
{body}
\end{{document}}
"""


def render_translated_pdf(
    *,
    title: str,
    language: str,
    language_name: str,
    translated_pages: list[str],
    pages_without_text: list[int],
    command: str,
) -> bytes:
    """Compile a complete translated reading edition with the configured engine."""

    source = translated_latex(
        title=title,
        language=language,
        language_name=language_name,
        translated_pages=translated_pages,
        pages_without_text=pages_without_text,
    )
    result = compile_document(source, "", command=command)
    if not result.ok or result.pdf is None:
        raise TranslationRenderError("the translated PDF could not be rendered")
    return result.pdf
