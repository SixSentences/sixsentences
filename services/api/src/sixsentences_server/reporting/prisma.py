"""PRISMA 2020 flow rendering.

The counts come from the pipeline (which derives them from run events); this
module only formats. PRISMA-S search documentation is emitted alongside via
SearchExecution records.
"""

from sixsentences_server.core.models import PrismaCounts, SearchExecution


def render_flow_text(counts: PrismaCounts) -> str:
    lines = [
        "PRISMA 2020 flow",
        "  Identification",
        f"    records identified:        {counts.records_identified}",
        f"    via citation search:       {counts.citation_identified}",
        f"    duplicates removed:        {counts.duplicates_removed}",
        f"    companion reports merged:  {counts.companion_reports_merged}",
        "  Screening",
        f"    records screened:          {counts.records_screened}",
        f"    records excluded:          {counts.records_excluded}",
        f"    records unsure/pending:    {counts.records_unsure}",
        "  Integrity",
        f"    retracted works flagged:   {counts.retracted_flagged}",
        "  Included",
        f"    records included:          {counts.included}",
        f"    records advanced:          {counts.included + counts.records_unsure}",
    ]
    if counts.reports_sought_for_retrieval:
        lines += [
            "  Retrieval of reports",
            f"    reports sought:            {counts.reports_sought_for_retrieval}",
            f"    reports not retrieved:     {counts.reports_not_retrieved}",
        ]
    if counts.reports_assessed_for_eligibility:
        lines += [
            "  Eligibility (full text)",
            f"    reports assessed:          {counts.reports_assessed_for_eligibility}",
            f"    reports excluded:          {counts.reports_excluded_fulltext}",
            f"  Studies included:            {counts.studies_included}",
        ]
    return "\n".join(lines)


# -- SVG flow diagram ---------------------------------------------------------

_PINE = "#0c1d19"
_MOSS = "#33544c"
_MUTED = "#6f7468"
_BORDER = "#d8d4c8"
_SIDE_FILL = "#f5f3ee"
_FINAL_FILL = "#e9efe8"

_SECTION_W = 108
_MAIN_W = 286
_SIDE_W = 252
_COL_GAP = 40
_ROW_H = 58
_ROW_GAP = 20
_PAD = 30
_HEADER_H = 46


def _svg_box(x: int, y: int, w: int, value: int, label: str, tone: str = "main") -> str:
    stroke = _MOSS if tone == "final" else _BORDER
    fill = {"main": "#ffffff", "side": _SIDE_FILL, "final": _FINAL_FILL}[tone]
    dash = ' stroke-dasharray="5 4"' if tone == "side" else ""
    color = _MOSS if tone == "final" else _PINE
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{_ROW_H}" rx="12" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"{dash}/>'
        f'<text x="{x + 16}" y="{y + 26}" font-size="17" font-weight="600" '
        f'fill="{color}" font-family="Helvetica, Arial, sans-serif">{value:,}</text>'
        f'<text x="{x + 16}" y="{y + 44}" font-size="11.5" fill="{_MUTED}" '
        f'font-family="Helvetica, Arial, sans-serif">{label}</text>'
    )


def render_flow_svg(counts: PrismaCounts) -> str:
    """The PRISMA 2020 flow as a paper-ready SVG: identification down to the
    studies included, exclusions branching right. Mirrors the in-app diagram:
    stages that did not run are absent, never zero-padded."""
    has_screening = counts.records_screened > 0
    has_retrieval = counts.reports_sought_for_retrieval > 0
    has_eligibility = counts.reports_assessed_for_eligibility > 0

    identified_label = "records identified"
    if counts.citation_identified:
        identified_label += f" ({counts.citation_identified:,} via citation search)"
    duplicates_label = "duplicates removed"
    if counts.companion_reports_merged:
        duplicates_label += f" ({counts.companion_reports_merged:,} companion reports)"

    Row = tuple[str, tuple[int, str, str], tuple[int, str] | None]
    rows: list[Row] = [
        (
            "Identification",
            (counts.records_identified, identified_label, "main"),
            (counts.duplicates_removed, duplicates_label),
        )
    ]
    if has_screening:
        rows.append(
            (
                "Screening",
                (counts.records_screened, "records screened", "main"),
                (counts.records_excluded, "records excluded"),
            )
        )
    advanced = counts.included + counts.records_unsure
    advanced_label = "advanced after title/abstract"
    if counts.records_unsure:
        advanced_label += f" ({counts.records_unsure:,} unsure)"
    rows.append(
        (
            "Advanced",
            (advanced, advanced_label, "main" if has_retrieval else "final"),
            (counts.retracted_flagged, "retracted, flagged") if counts.retracted_flagged else None,
        )
    )
    if has_retrieval:
        rows.append(
            (
                "Retrieval",
                (
                    counts.reports_sought_for_retrieval,
                    "reports sought (open access)",
                    "main",
                ),
                (counts.reports_not_retrieved, "not retrieved (no OA copy)"),
            )
        )
    if has_eligibility:
        rows.append(
            (
                "Eligibility",
                (
                    counts.reports_assessed_for_eligibility,
                    "assessed on full text",
                    "main",
                ),
                (counts.reports_excluded_fulltext, "excluded on full text"),
            )
        )
        rows.append(("Studies", (counts.studies_included, "studies included", "final"), None))

    width = _PAD * 2 + _SECTION_W + _MAIN_W + _COL_GAP + _SIDE_W
    height = _PAD * 2 + _HEADER_H + len(rows) * _ROW_H + (len(rows) - 1) * _ROW_GAP
    main_x = _PAD + _SECTION_W
    side_x = main_x + _MAIN_W + _COL_GAP
    mid_x = main_x + _MAIN_W // 2

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 8 4 L 0 8 z" fill="{_MUTED}"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{_PAD}" y="{_PAD + 14}" font-size="12" letter-spacing="2.5" '
        f'fill="{_PINE}" font-family="Courier New, monospace">PRISMA 2020 FLOW</text>',
        f'<text x="{width - _PAD}" y="{_PAD + 14}" font-size="11" text-anchor="end" '
        f'fill="{_MUTED}" font-family="Courier New, monospace">SIXSENTENCES_</text>',
    ]
    for i, (section, main, side) in enumerate(rows):
        y = _PAD + _HEADER_H + i * (_ROW_H + _ROW_GAP)
        value, label, tone = main
        parts.append(
            f'<text x="{_PAD}" y="{y + _ROW_H // 2 + 4}" font-size="9.5" '
            f'letter-spacing="1.8" fill="{_MUTED}" '
            f'font-family="Courier New, monospace">{section.upper()}</text>'
        )
        parts.append(_svg_box(main_x, y, _MAIN_W, value, label, tone))
        if side is not None:
            side_value, side_label = side
            parts.append(_svg_box(side_x, y, _SIDE_W, side_value, side_label, "side"))
            parts.append(
                f'<line x1="{main_x + _MAIN_W}" y1="{y + _ROW_H // 2}" '
                f'x2="{side_x - 8}" y2="{y + _ROW_H // 2}" stroke="{_MUTED}" '
                'stroke-width="1.2" marker-end="url(#arrow)"/>'
            )
        if i < len(rows) - 1:
            parts.append(
                f'<line x1="{mid_x}" y1="{y + _ROW_H}" x2="{mid_x}" '
                f'y2="{y + _ROW_H + _ROW_GAP - 7}" stroke="{_MUTED}" '
                'stroke-width="1.2" marker-end="url(#arrow)"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def render_search_appendix(executions: list[SearchExecution]) -> str:
    """PRISMA-S style appendix: one block per executed search, verbatim query."""
    blocks = []
    for i, ex in enumerate(executions, start=1):
        blocks.append(
            "\n".join(
                [
                    f"Search {i}",
                    f"  source:            {ex.source}",
                    f"  platform:          {ex.platform}",
                    f"  date run:          {ex.date_run.isoformat()}",
                    f"  query (verbatim):  {ex.query_verbatim}",
                    f"  limits:            {'; '.join(ex.limits) or 'none'}",
                    f"  records returned:  {ex.records_returned}",
                    f"  deduplication:     {ex.deduplication_method}",
                ]
            )
        )
    return "\n\n".join(blocks)
