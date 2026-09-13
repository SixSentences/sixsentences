"""The interview report: a self-contained, compile-verified LaTeX document.

One deterministic renderer produces the whole report from stored rows: a
metadata page with an honest methods disclosure, the evidence-anchored
analysis, and the full transcript with segment numbers and timestamps so a
paper can cite passages precisely ("P1, seg. 45, 00:14:05"). Compiled with
the same engine as the Writer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sixsentences_server.core.media_provenance import (
    interview_provenance,
    provenance_text,
)
from sixsentences_server.interviews.analysis import contribution_note
from sixsentences_server.interviews.service import format_timestamp

_LABELS: dict[str, dict[str, Any]] = {
    "en": {
        "report": "Interview Report",
        "metadata": "Recording",
        "duration": "Duration",
        "language": "Language",
        "speakers": "Speakers",
        "created": "Uploaded",
        "generated": "Report generated",
        "summary": "Summary",
        "themes": "Themes",
        "findings": "Key findings",
        "tensions": "Tensions and contradictions",
        "hypotheses": "Hypotheses for future testing",
        "followups": "Suggested follow-up questions",
        "transcript": "Full transcript",
        "segment": "Seg.",
        "unverified": "unverified quote",
        "edited_note": "Segments marked with * were manually corrected.",
        "languages": {"de": "German", "en": "English", "auto": "Auto-detected"},
    },
    "de": {
        "report": "Interviewbericht",
        "metadata": "Aufnahme",
        "duration": "Dauer",
        "language": "Sprache",
        "speakers": "Sprecher",
        "created": "Hochgeladen",
        "generated": "Bericht erstellt",
        "summary": "Zusammenfassung",
        "themes": "Themen",
        "findings": "Kernaussagen",
        "tensions": "Spannungen und Widersprüche",
        "hypotheses": "Hypothesen für die weitere Forschung",
        "followups": "Vorgeschlagene Anschlussfragen",
        "transcript": "Vollständiges Transkript",
        "segment": "Seg.",
        "unverified": "nicht verifiziertes Zitat",
        "edited_note": "Mit * markierte Segmente wurden manuell korrigiert.",
        "languages": {"de": "Deutsch", "en": "Englisch", "auto": "Automatisch erkannt"},
    },
}


def _tex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in value)


def _duration_label(duration_ms: int) -> str:
    return format_timestamp(duration_ms)


def _speaker_line(speakers: dict[str, str]) -> str:
    parts = []
    for label in sorted(speakers, key=lambda key: (len(key), key)):
        name = str(speakers[label]).strip()
        parts.append(name if name == label else f"{label}: {name}")
    return ", ".join(parts) or "S1"


def _item_list(items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [r"\begin{itemize}"]
    lines += [rf"  \item {_tex_escape(item)}" for item in items]
    lines.append(r"\end{itemize}")
    return lines


def render_report_tex(
    *,
    title: str,
    language: str,
    duration_ms: int,
    speakers: dict[str, str],
    model: str,
    created_at: datetime,
    generated_at: datetime,
    segments: list[dict[str, Any]],
    analysis: dict[str, Any],
    live: bool = False,
) -> str:
    """The complete report as one compilable LaTeX source."""
    # The concrete inference route is operational metadata. Keep accepting it
    # for backwards-compatible callers, but never disclose it in the report.
    _ = model
    text = _LABELS["de" if language == "de" else "en"]
    heading = _tex_escape(title.strip() or text["report"])
    spoken = text["languages"].get(
        str(analysis.get("language") or language), text["languages"]["auto"]
    )
    provenance = interview_provenance(
        artifact="interview-report",
        live=live,
        analysis=bool(analysis.get("summary") or analysis.get("themes")),
    )
    keywords = (
        "sixsentences-ai-provenance-v1; artifact=interview-report; "
        "scope=not-all-content-is-ai-generated; ai-generated-parts="
        + str(provenance["ai_generated_parts"]).lower()
        + "; contributions="
        + ",".join(provenance["contributions"])
    )
    if provenance.get("digital_source_type"):
        keywords += "; DigitalSourceType=" + provenance["digital_source_type"]
    lines: list[str] = [
        r"\documentclass[11pt]{article}",
        "% " + provenance_text(provenance),
        r"\usepackage[a4paper,margin=2.4cm]{geometry}",
        r"\usepackage{microtype}",
        r"\usepackage{booktabs}",
        r"\usepackage{parskip}",
        r"\usepackage{xcolor}",
        r"\definecolor{pine}{HTML}{0C1D19}",
        r"\definecolor{moss}{HTML}{33544C}",
        r"\usepackage[colorlinks=true,linkcolor=moss,urlcolor=moss]{hyperref}",
        r"\hypersetup{pdfcreator={SixSentences},pdfkeywords={" + keywords + "}}",
        r"\setlength{\emergencystretch}{3em}",
        r"\begin{document}",
        r"\begin{center}",
        rf"{{\LARGE\bfseries {heading}}}\\[0.4em]",
        rf"{{\large {text['report']}}}\\[0.2em]",
        r"{\small SixSentences\_}",
        r"\end{center}",
        r"\vspace{1em}",
        r"\begin{tabular}{@{}ll@{}}",
        r"\toprule",
        rf"{text['duration']} & {_duration_label(duration_ms)} \\",
        rf"{text['language']} & {_tex_escape(spoken)} \\",
        rf"{text['speakers']} & {_tex_escape(_speaker_line(speakers))} \\",
        rf"{text['created']} & {created_at.strftime('%Y-%m-%d')} \\",
        rf"{text['generated']} & {generated_at.strftime('%Y-%m-%d %H:%M')} \\",
        r"\bottomrule",
        r"\end{tabular}",
        "",
        rf"\noindent\emph{{\small {_tex_escape(contribution_note(analysis))}}}",
    ]
    summary = str(analysis.get("summary") or "").strip()
    if summary:
        lines += ["", rf"\section*{{{text['summary']}}}", _tex_escape(summary)]
    themes = analysis.get("themes") or []
    if themes:
        lines += ["", rf"\section*{{{text['themes']}}}"]
        for theme in themes:
            lines.append(rf"\subsection*{{{_tex_escape(str(theme.get('name') or ''))}}}")
            description = str(theme.get("description") or "").strip()
            if description:
                lines.append(_tex_escape(description))
            for quote in theme.get("quotes") or []:
                anchor = f"{text['segment']}\\,{int(quote.get('segment') or 0)}"
                stamp = str(quote.get("timestamp") or "")
                if stamp:
                    anchor += f", {stamp}"
                if not quote.get("verified"):
                    anchor += f", {text['unverified']}"
                lines += [
                    r"\begin{quote}",
                    rf"``{_tex_escape(str(quote.get('text') or ''))}''\\",
                    rf"{{\small\color{{moss}}[{anchor}]}}",
                    r"\end{quote}",
                ]
    lines += _section_list(text["findings"], analysis.get("key_findings") or [])
    lines += _section_list(text["tensions"], analysis.get("tensions") or [])
    lines += _section_list(text["hypotheses"], analysis.get("hypotheses") or [])
    lines += _section_list(text["followups"], analysis.get("followups") or [])
    lines += ["", r"\newpage", rf"\section*{{{text['transcript']}}}"]
    if any(segment.get("edited") for segment in segments):
        lines.append(rf"\noindent\emph{{\small {text['edited_note']}}}")
        lines.append("")
    for segment in segments:
        speaker = str(segment.get("speaker") or "S1")
        name = str(speakers.get(speaker) or speaker)
        stamp = format_timestamp(int(segment.get("start_ms") or 0))
        marker = "*" if segment.get("edited") else ""
        lines.append(
            rf"\noindent\textbf{{[{int(segment['idx'])}{marker}]}} "
            rf"{{\small\color{{moss}}{stamp}}} "
            rf"\textbf{{{_tex_escape(name)}}}: {_tex_escape(str(segment['text']))}"
            r"\par\vspace{0.35em}"
        )
    lines.append(r"\end{document}")
    return "\n".join(lines)


def _section_list(heading: str, items: list[str]) -> list[str]:
    if not items:
        return []
    return ["", rf"\section*{{{heading}}}", *_item_list([str(item) for item in items])]
