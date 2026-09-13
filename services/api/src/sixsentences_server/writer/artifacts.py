"""One-click Writer artifacts: paper-ready LaTeX built from a linked run's
audited record. The evidence table reuses the reporting renderer; this
module adds the LaTeX-escaped methods paragraph and the PRISMA 2020 flow
as plain TikZ (absolute coordinates, no tikz libraries, so it compiles in
any document that loads tikz or pgfplots)."""

from typing import Any

from sixsentences_server.core.models import PrismaCounts
from sixsentences_server.reporting.extraction import _tex_escape


def tex_escape(value: str) -> str:
    """Escape user-controlled text for inclusion in LaTeX."""
    return _tex_escape(value)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def build_disclosure(summary: dict[str, Any]) -> str:
    """An honest AI assistance statement built ONLY from recorded events.
    It never claims review or authorship beyond what the ledger shows."""
    parts = [
        "This manuscript was written in the SixSentences_ Writer with a documented AI assistant."
    ]
    turns = int(summary.get("assistant_turns") or 0)
    proposed = int(summary.get("edits_proposed") or 0)
    applied = int(summary.get("edits_applied") or 0)
    applied_auto = int(summary.get("edits_applied_auto") or 0)
    if turns == 0:
        parts.append("No assistant turns were recorded for this document.")
    else:
        sentence = (
            f"The assistant answered {_n(turns, 'request')} and proposed {_n(proposed, 'edit')}"
        )
        if applied:
            sentence += f"; {applied} of these were applied to the text"
            if applied_auto:
                sentence += (
                    f" ({applied_auto} automatically under the author's "
                    "standing auto apply setting)"
                )
            sentence += (
                f", adding {summary.get('ai_chars_added', 0)} and removing "
                f"{summary.get('ai_chars_removed', 0)} characters"
            )
        elif proposed:
            sentence += "; none of these were applied to the text"
        parts.append(sentence + ".")
    linked = list(summary.get("linked_searches") or [])
    if linked:
        parts.append(
            "Citations are drawn from the live bibliography of the audited "
            f"literature search{'' if len(linked) == 1 else 'es'} "
            f"{', '.join(linked)}."
        )
    parts.append("The full, append-only event log is stored with the document.")
    return " ".join(parts)


def disclosure_tex(summary: dict[str, Any]) -> str:
    return f"\\section*{{AI assistance disclosure}}\n{tex_escape(build_disclosure(summary))}\n"


def methods_paragraph_tex(text: str, run_public_id: str) -> str:
    """The exported methods paragraph as safe LaTeX (search strings love
    characters like & _ % that would otherwise break the compile)."""
    return (
        f"% Methods paragraph generated from search {run_public_id} by "
        "SixSentences_\n"
        f"{tex_escape(text.strip())}\n"
    )


def _box(name: str, x: float, y: float, lines: list[str], *, wide: bool = False) -> str:
    body = r"\\ ".join(lines)
    width = "5.2cm" if wide else "4.6cm"
    return (
        f"    \\node[draw, rounded corners=1pt, align=center, inner sep=5pt, "
        f"font=\\small, text width={width}] ({name}) at ({x},{y:.1f}) "
        f"{{{body}}};"
    )


def prisma_flow_tikz(counts: PrismaCounts, run_public_id: str) -> str:
    """The PRISMA 2020 flow as a TikZ figure whose numbers are the run's
    audited counts. Main stream on the left, removals on the right."""
    main_x, side_x, step = 0.0, 6.4, -2.1

    identified = [f"Records identified (n = {counts.records_identified})"]
    if counts.citation_identified:
        identified.append(f"via citation search: n = {counts.citation_identified}")
    removed = [f"Duplicates removed (n = {counts.duplicates_removed})"]
    if counts.companion_reports_merged:
        removed.append(f"companion reports merged: n = {counts.companion_reports_merged}")
    excluded = [f"Excluded with reason (n = {counts.records_excluded})"]
    if counts.records_unsure:
        excluded.append(f"flagged unsure: n = {counts.records_unsure}")

    # (main lines, side lines or None) per row of the flow
    rows: list[tuple[list[str], list[str] | None]] = [
        (identified, removed),
        ([f"Records screened (n = {counts.records_screened})"], excluded),
    ]
    if counts.reports_sought_for_retrieval:
        rows.append(
            (
                [f"Reports sought for retrieval (n = {counts.reports_sought_for_retrieval})"],
                [f"Not retrieved (n = {counts.reports_not_retrieved})"],
            )
        )
    included = [f"\\textbf{{Included}} (n = {counts.included})"]
    if counts.studies_included:
        included.append(f"studies after merging: n = {counts.studies_included}")
    rows.append((included, None))

    nodes: list[str] = []
    arrows: list[str] = []
    for index, (main_lines, side_lines) in enumerate(rows):
        y = index * step
        nodes.append(_box(f"main{index}", main_x, y, main_lines))
        if index > 0:
            arrows.append(f"    \\draw[-stealth, thick] (main{index - 1}) -- (main{index});")
        if side_lines is not None:
            nodes.append(_box(f"side{index}", side_x, y, side_lines, wide=True))
            arrows.append(f"    \\draw[-stealth, thick] (main{index}) -- (side{index});")

    return "\n".join(
        [
            f"% PRISMA 2020 flow generated from search {run_public_id} by "
            "SixSentences_; the counts are the run's audited record.",
            "% Needs tikz in the preamble (the built-in templates load pgfplots, which brings it).",
            "\\begin{figure*}[t]",
            "  \\centering",
            "  \\begin{tikzpicture}",
            *nodes,
            *arrows,
            "  \\end{tikzpicture}",
            "  \\caption{PRISMA 2020 flow of records through the search.}",
            "  \\label{fig:prisma}",
            "\\end{figure*}",
            "",
        ]
    )
