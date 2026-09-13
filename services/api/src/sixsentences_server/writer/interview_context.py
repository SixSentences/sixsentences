"""Bounded, provenance-preserving interview context for the Writer agent."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "aus",
    "bei",
    "bitte",
    "das",
    "dem",
    "den",
    "der",
    "die",
    "dies",
    "eine",
    "einem",
    "einen",
    "einer",
    "für",
    "from",
    "haben",
    "hat",
    "ich",
    "ist",
    "mit",
    "nach",
    "oder",
    "schreib",
    "schreibe",
    "the",
    "und",
    "von",
    "was",
    "wenn",
    "wie",
    "with",
    "write",
    "zum",
    "zur",
}


def _terms(value: str) -> set[str]:
    return {
        word.casefold() for word in _WORD_RE.findall(value) if word.casefold() not in _STOPWORDS
    }


def _timestamp(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value[:limit] if str(item).strip()]


def _analysis_text(analysis: dict[str, Any], *, max_chars: int = 3000) -> str:
    lines: list[str] = []
    summary = str(analysis.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")
    findings = _string_list(analysis.get("key_findings"), limit=8)
    if findings:
        lines.append("Key findings:\n" + "\n".join(f"- {item}" for item in findings))
    themes = analysis.get("themes")
    if isinstance(themes, list) and themes:
        rendered_themes: list[str] = []
        for theme in themes[:8]:
            if not isinstance(theme, dict):
                continue
            name = str(theme.get("name") or "Theme").strip()
            description = str(theme.get("description") or "").strip()
            rendered = f"- {name}" + (f": {description}" if description else "")
            quotes = theme.get("quotes")
            if isinstance(quotes, list):
                verified = [
                    quote for quote in quotes if isinstance(quote, dict) and quote.get("verified")
                ]
                for quote in verified[:2]:
                    text = str(quote.get("text") or "").strip()
                    if text:
                        speaker = str(quote.get("speaker") or "speaker")
                        stamp = str(quote.get("timestamp") or "")
                        source = speaker + (f", {stamp}" if stamp else "")
                        rendered += f'\n  Verified quote ({source}): "{text}"'
            rendered_themes.append(rendered)
        if rendered_themes:
            lines.append("Themes:\n" + "\n".join(rendered_themes))
    for key, label in (
        ("tensions", "Tensions"),
        ("hypotheses", "Hypotheses"),
        ("followups", "Open follow-ups"),
    ):
        values = _string_list(analysis.get(key), limit=5)
        if values:
            lines.append(f"{label}:\n" + "\n".join(f"- {item}" for item in values))
    rendered = "\n".join(lines).strip() or "No completed analysis is available."
    return rendered[:max_chars]


def _methodology_text(interview: dict[str, Any], *, max_chars: int = 5000) -> str:
    """Describe only recorded design facts, never inferred study claims."""

    kind = str(interview.get("kind") or "upload")
    method = "AI-led synchronous interview" if kind == "live" else "Uploaded interview recording"
    lines = [f"Interview format: {method}"]
    language = str(interview.get("language") or "auto")
    lines.append(
        "Language: automatically detected" if language == "auto" else f"Language: {language}"
    )
    duration_ms = int(interview.get("duration_ms") or 0)
    if duration_ms > 0:
        lines.append(f"Recorded duration: {_timestamp(duration_ms)}")
    segment_count = int(interview.get("segment_count") or 0)
    if segment_count > 0:
        lines.append(f"Transcript turns: {segment_count}")
    speakers = [
        str(label).strip()
        for label in dict(interview.get("speakers") or {}).values()
        if str(label).strip()
    ]
    if speakers:
        lines.append("Speaker roles or labels: " + ", ".join(dict.fromkeys(speakers)))
    guide = str(interview.get("guide") or "").strip()
    if guide:
        lines.append("Interview guide or study context (author supplied):\n" + guide)
    else:
        lines.append("Interview guide or study context: not recorded")
    return "\n".join(lines)[:max_chars]


def _quoted_segment_ids(analysis: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    themes = analysis.get("themes")
    if not isinstance(themes, list):
        return ids
    for theme in themes:
        if not isinstance(theme, dict) or not isinstance(theme.get("quotes"), list):
            continue
        for quote in theme["quotes"]:
            if not isinstance(quote, dict):
                continue
            segment = quote.get("segment")
            try:
                ids.add(int(str(segment)))
            except (TypeError, ValueError):
                continue
    return ids


def _relevant_segment_indices(
    segments: list[dict[str, Any]],
    *,
    query_terms: set[str],
    quoted_ids: set[int],
) -> list[tuple[int, int]]:
    """Return ``(score, position)`` with one surrounding turn for context."""

    ranked: list[tuple[int, int]] = []
    for position, segment in enumerate(segments):
        text_terms = _terms(str(segment.get("text") or ""))
        overlap = len(query_terms & text_terms)
        segment_id = int(segment.get("idx") or position + 1)
        score = overlap * 10 + (4 if segment_id in quoted_ids else 0)
        if score:
            ranked.append((score, position))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    seeds = ranked[:4]
    if not seeds:
        quote_positions = [
            position
            for position, segment in enumerate(segments)
            if int(segment.get("idx") or position + 1) in quoted_ids
        ]
        seeds = [(4, position) for position in quote_positions[:2]]
    if not seeds and segments:
        seeds = [(1, 0)]

    expanded: dict[int, int] = {}
    for score, position in seeds:
        for candidate, penalty in ((position, 0), (position - 1, 2), (position + 1, 2)):
            if 0 <= candidate < len(segments):
                expanded[candidate] = max(expanded.get(candidate, 0), max(1, score - penalty))
    return sorted(((score, position) for position, score in expanded.items()), reverse=True)


def prepare_interview_evidence(
    interviews: Iterable[dict[str, Any]],
    *,
    query: str,
    max_passages: int = 16,
    max_transcript_chars: int = 14_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prepare analysis plus bounded transcript evidence for one Writer turn.

    The complete transcript remains in the interview workspace. This function
    retrieves only query-relevant turns and their immediate conversational
    context, so several long interviews can be linked without flooding the
    model context.
    """

    prepared = [dict(item) for item in interviews]
    query_terms = _terms(query)
    candidates: list[tuple[int, str, int, dict[str, Any]]] = []
    per_interview: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for interview in prepared:
        if interview.get("mode") != "transcript":
            continue
        public_id = str(interview.get("interview_id") or "")
        segments = [
            dict(segment)
            for segment in interview.get("segments") or []
            if isinstance(segment, dict) and str(segment.get("text") or "").strip()
        ]
        selected = _relevant_segment_indices(
            segments,
            query_terms=query_terms,
            quoted_ids=_quoted_segment_ids(dict(interview.get("analysis") or {})),
        )
        rows: list[tuple[int, int, dict[str, Any]]] = []
        for score, position in selected:
            segment = segments[position]
            rows.append((score, position, segment))
            candidates.append((score, public_id, position, segment))
        per_interview[public_id] = rows

    chosen: list[tuple[int, str, int, dict[str, Any]]] = []
    chosen_keys: set[tuple[str, int]] = set()
    # Preserve cross-interview coverage before filling remaining slots by
    # relevance. A synthesis request should never silently omit one linked
    # participant just because another transcript repeats more keywords.
    for public_id, rows in per_interview.items():
        if not rows:
            continue
        score, position, segment = max(rows, key=lambda item: item[0])
        chosen.append((score, public_id, position, segment))
        chosen_keys.add((public_id, position))
    for candidate in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        key = (candidate[1], candidate[2])
        if key in chosen_keys or len(chosen) >= max_passages:
            continue
        chosen.append(candidate)
        chosen_keys.add(key)

    by_id = {str(item.get("interview_id") or ""): item for item in prepared}
    transcript_chars = 0
    excerpts: dict[str, list[dict[str, Any]]] = {}
    for _, public_id, _, segment in sorted(chosen, key=lambda item: (item[1], item[2])):
        text = str(segment.get("text") or "").strip()
        if transcript_chars + len(text) > max_transcript_chars:
            continue
        transcript_chars += len(text)
        interview = by_id[public_id]
        speaker_code = str(segment.get("speaker") or "speaker")
        display_speaker = str(
            dict(interview.get("speakers") or {}).get(speaker_code) or speaker_code
        )
        excerpts.setdefault(public_id, []).append(
            {
                "segment": int(segment.get("idx") or 0),
                "speaker": display_speaker,
                "start_ms": int(segment.get("start_ms") or 0),
                "end_ms": int(segment.get("end_ms") or 0),
                "text": text,
            }
        )

    contexts: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for interview in prepared:
        public_id = str(interview.get("interview_id") or "")
        passages = excerpts.get(public_id, [])
        contexts.append(
            {
                "interview_id": public_id,
                "title": str(interview.get("title") or "Untitled interview"),
                "mode": str(interview.get("mode") or "analysis"),
                "include_methodology": bool(interview.get("include_methodology")),
                "methodology_text": (
                    _methodology_text(interview) if interview.get("include_methodology") else ""
                ),
                "analysis_text": _analysis_text(dict(interview.get("analysis") or {})),
                "passages": passages,
            }
        )
        provenance.append(
            {
                "interview_id": public_id,
                "title": str(interview.get("title") or "Untitled interview"),
                "mode": str(interview.get("mode") or "analysis"),
                "include_methodology": bool(interview.get("include_methodology")),
                "passages": [
                    {
                        "segment": item["segment"],
                        "speaker": item["speaker"],
                        "start_ms": item["start_ms"],
                        "end_ms": item["end_ms"],
                    }
                    for item in passages
                ],
            }
        )
    return contexts, provenance


def render_interview_evidence(
    contexts: list[dict[str, Any]],
    *,
    source_offset: int = 0,
) -> str:
    """Render evidence, preserving catalog ordinals when reading one source."""

    sections: list[str] = []
    for index, context in enumerate(contexts, start=source_offset + 1):
        source_id = f"I{index}"
        access = (
            "analysis plus retrieved transcript passages"
            if context.get("mode") == "transcript"
            else "analysis only; raw transcript is not available in this turn"
        )
        section = (
            f"SOURCE {source_id}: {context.get('title')} "
            f"(interview_id={context.get('interview_id')})\n"
            f"Access: {access}\n"
        )
        if context.get("include_methodology"):
            section += f"METHODOLOGY:\n{context.get('methodology_text')}\n"
        section += f"ANALYSIS:\n{context.get('analysis_text')}"
        passages = context.get("passages")
        if isinstance(passages, list) and passages:
            rendered = []
            for passage in passages:
                rendered.append(
                    f"[{source_id}:S{passage.get('segment')} | "
                    f"{passage.get('speaker')} | "
                    f"{_timestamp(int(passage.get('start_ms') or 0))}-"
                    f"{_timestamp(int(passage.get('end_ms') or 0))}]\n"
                    f"{passage.get('text')}"
                )
            section += "\nRETRIEVED TRANSCRIPT PASSAGES:\n" + "\n\n".join(rendered)
        sections.append(section)
    return "\n\n".join(sections)
