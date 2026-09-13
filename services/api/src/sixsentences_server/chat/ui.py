"""MCP-UI resources for the chat: interactive HTML the frontend renders.

Follows the MCP Apps / mcp-ui pattern: a tool result carries an embedded UI
resource (`ui://` uri, mimeType text/html) that the host renders in a
sandboxed iframe. The page talks back to the host exclusively via
`window.parent.postMessage`:

- {type: "ui-size-change", payload: {height}} — auto-resize
- {type: "prompt", payload: {prompt}} — send a follow-up message as the user
- {type: "link", payload: {url}} — open an external page
- {type: "intent", payload: {intent}} — host-level navigation (e.g. results)

Everything is self-contained (inline CSS/SVG, no external requests) so the
frame works under a strict sandbox, and every dynamic string is escaped.
"""

import hashlib
import html
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from sixsentences_server.core.db import Run
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.reporting.exports import works_for_run
from sixsentences_server.reporting.synthesis import _final_verdicts

PINE = "#0c1d19"
MOSS = "#33544c"
MOSS_SOFT = "#5a897d"
AMBER = "#b45309"
CLAY = "#a13d2d"
MUTED = "#5c6b66"
LINE = "#e3ddd0"

CHART_KINDS = ("works_by_year", "verdicts", "top_venues", "top_cited", "prisma_funnel")


@dataclass
class UiResource:
    """An embedded MCP-UI resource (text/html flavour)."""

    uri: str
    text: str
    mime_type: str = "text/html"

    def payload(self, *, size: str = "wide") -> dict[str, Any]:
        return {
            "kind": "ui",
            "size": size,
            "resource": {
                "uri": self.uri,
                "mimeType": self.mime_type,
                "text": self.text,
            },
        }


_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><style>
* { margin: 0; box-sizing: border-box; }
body {
  font-family: ui-sans-serif, -apple-system, "Segoe UI", Helvetica, sans-serif;
  background: transparent; color: __PINE__; padding: 2px;
  -webkit-font-smoothing: antialiased;
}
.card { background: #ffffff; border: 1px solid __LINE__; border-radius: 16px;
  padding: 18px 20px 16px; }
h1 { font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
.sub { font-size: 11.5px; color: __MUTED__; margin: 2px 0 14px; }
.hint { font-size: 10.5px; color: __MUTED__; margin-top: 10px; }
.bar { cursor: pointer; }
.bar rect { transition: filter 0.15s; transform-origin: bottom; transform-box: fill-box;
  animation: grow 0.7s cubic-bezier(0.22, 1, 0.36, 1) backwards; }
.bar:hover rect { filter: brightness(1.25); }
.bar text { opacity: 0; transition: opacity 0.15s; }
.bar:hover text, .bar.hot text { opacity: 1; }
.grid { stroke: #e7e2d5; stroke-width: 1; stroke-dasharray: 3 4; }
.axis { font-size: 10px; fill: __MUTED__; }
@keyframes grow { from { transform: scaleY(0); } }
.funnel-step { cursor: pointer; }
.funnel-step rect { animation: grow 0.7s cubic-bezier(0.22, 1, 0.36, 1) backwards;
  transform-origin: center; transform-box: fill-box; transition: filter 0.15s; }
.funnel-step:hover rect { filter: brightness(1.2); }
.chip { border: 1px solid __LINE__; background: #fff; border-radius: 999px;
  padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: __PINE__;
  font-family: inherit; text-align: left; transition: all 0.15s; }
.chip:hover { border-color: __MOSS__; background: __MOSS__; color: #f4f1ea; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.row { display: flex; align-items: center; gap: 10px; margin: 7px 0; }
.row .label { width: 148px; font-size: 12px; color: __PINE__; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.row .track { flex: 1; height: 14px; background: #f2efe6; border-radius: 999px; overflow: hidden; }
.row .fill { height: 100%; border-radius: 999px; }
.row .value { width: 44px; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums;
  color: __MUTED__; }
.q { margin-bottom: 14px; }
.q p { font-size: 13px; font-weight: 550; margin-bottom: 7px; }
.opts { display: flex; flex-wrap: wrap; gap: 6px; }
.opt { border: 1px solid __LINE__; background: #fff; border-radius: 999px; padding: 6px 13px;
  font-size: 12px; cursor: pointer; color: __PINE__; font-family: inherit; }
.opt:hover { border-color: __MOSS__; }
.opt.on { background: __MOSS__; border-color: __MOSS__; color: #f4f1ea; }
textarea { width: 100%; border: 1px solid __LINE__; border-radius: 12px; padding: 8px 12px;
  font-size: 12px; font-family: inherit; resize: none; margin-top: 2px; }
textarea:focus { outline: none; border-color: __MOSS__; }
.send { margin-top: 12px; background: __PINE__; color: #f4f1ea; border: 0; border-radius: 999px;
  padding: 9px 18px; font-size: 12.5px; font-weight: 550; cursor: pointer; font-family: inherit; }
.send:hover { background: __MOSS__; }
.send:disabled { opacity: 0.5; cursor: default; }
svg text { font-family: inherit; }
</style></head><body>
__BODY__
<script>
const post = (msg) => window.parent.postMessage(msg, "*");
const sendSize = () => post({ type: "ui-size-change",
  payload: { height: document.documentElement.scrollHeight } });
new ResizeObserver(sendSize).observe(document.body);
window.addEventListener("load", sendSize);
sendSize();
document.querySelectorAll("[data-prompt]").forEach((el) =>
  el.addEventListener("click", () =>
    post({ type: "prompt", payload: { prompt: el.dataset.prompt } })));
__SCRIPT__
</script></body></html>"""


def _shell(body: str, script: str = "") -> str:
    return (
        _SHELL.replace("__PINE__", PINE)
        .replace("__MOSS__", MOSS)
        .replace("__MUTED__", MUTED)
        .replace("__LINE__", LINE)
        .replace("__BODY__", body)
        .replace("__SCRIPT__", script)
    )


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


# -- charts -------------------------------------------------------------------


_BAR_GRADIENT = (
    '<defs><linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">'
    f'<stop offset="0%" stop-color="{MOSS_SOFT}"/>'
    f'<stop offset="100%" stop-color="{MOSS}"/>'
    "</linearGradient></defs>"
)


def works_by_year_chart(
    works: list[WorkRecord],
    run_id: int,
    *,
    scope_label: str = "retrieved",
    scope_key: str = "all",
) -> UiResource | None:
    years = sorted(Counter(w.year for w in works if w.year).items())
    if len(years) < 2:
        return None
    dated_count = sum(years_count for _, years_count in years)
    missing_years = max(0, len(works) - dated_count)
    max_count = max(count for _, count in years)
    width, height, pad = 720, 250, 34
    plot_h = height - 54
    bar_gap = 6
    bar_w = max(10, (width - 2 * pad) // len(years) - bar_gap)
    # Dashed gridlines with distinct integer labels ground the bars. Rounding
    # four fractions independently produced duplicate ticks for small counts
    # (for example 0, 1, 2, 2 when the maximum was 2).
    grid: list[str] = []
    tick_count = min(4, max_count)
    tick_values = sorted(
        {max(1, round(max_count * index / tick_count)) for index in range(1, tick_count + 1)}
    )
    for value in tick_values:
        fraction = value / max_count
        gy = 16 + plot_h * (1 - fraction)
        grid.append(f'<line class="grid" x1="{pad}" y1="{gy}" x2="{width - 8}" y2="{gy}"/>')
        grid.append(
            f'<text class="axis" x="{pad - 6}" y="{gy + 3}" text-anchor="end">{value}</text>'
        )
    bars: list[str] = []
    label_every = 1 + len(years) // 16
    peak = max(range(len(years)), key=lambda i: years[i][1])
    for index, (year, count) in enumerate(years):
        bar_h = max(3, round(plot_h * count / max_count))
        x = pad + index * (bar_w + bar_gap)
        y = 16 + (plot_h - bar_h)
        prompt = _esc(f"Tell me about the {scope_label} works from {year} in this run.")
        delay = f' style="animation-delay:{index * 45}ms"'
        bars.append(
            f'<g class="bar{" hot" if index == peak else ""}" data-prompt="{prompt}">'
            f"<title>{year}: {count} works</title>"
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bar_h}" rx="6" '
            f'fill="url(#bar)"{delay}/>'
            f'<text x="{x + bar_w / 2}" y="{y - 5}" text-anchor="middle" font-size="10.5" '
            f'font-weight="600" fill="{PINE}">{count}</text>'
            + (
                f'<text class="axis" x="{x + bar_w / 2}" y="{height - 10}" '
                f'text-anchor="middle">{year}</text>'
                if index % label_every == 0
                else ""
            )
            + "</g>"
        )
    body = (
        f'<div class="card"><h1>{scope_label.capitalize()} works by publication year</h1>'
        f'<p class="sub">{dated_count} of {len(works)} {scope_label} works have a year, '
        f"{years[0][0]} to {years[-1][0]}"
        + (f"; {missing_years} without year" if missing_years else "")
        + "</p>"
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">'
        f"{_BAR_GRADIENT}{''.join(grid)}{''.join(bars)}</svg>"
        '<p class="hint">Click a bar to ask about that year.</p></div>'
    )
    suffix = "" if scope_key == "all" else f"/{scope_key}"
    return UiResource(
        uri=f"ui://sixsentences/run/{run_id}/works-by-year{suffix}",
        text=_shell(body),
    )


def top_cited_chart(
    works: list[WorkRecord],
    run_id: int,
    *,
    scope_label: str = "retrieved",
    scope_key: str = "all",
) -> UiResource | None:
    ranked = sorted((w for w in works if w.cited_by_count), key=lambda w: -w.cited_by_count)[:8]
    if not ranked:
        return None
    rows: list[tuple[str, int, str, str | None]] = [
        (
            f"{work.title[:58]}{'…' if len(work.title) > 58 else ''}"
            + (f" ({work.year})" if work.year else ""),
            work.cited_by_count,
            MOSS,
            f'Tell me about "{work.title[:80]}" and why it matters here.',
        )
        for work in ranked
    ]
    scope_heading = "" if scope_key == "all" else f"{scope_label} "
    body = (
        f'<div class="card"><h1>Most cited {scope_heading}works</h1>'
        f'<p class="sub">Top {len(ranked)} of {len(works)} {scope_label} works with '
        "available citation metadata; counts are not quality scores</p>"
        + _bar_rows(rows)
        + '<p class="hint">Click a work to dig into it.</p></div>'
    )
    suffix = "" if scope_key == "all" else f"/{scope_key}"
    return UiResource(
        uri=f"ui://sixsentences/run/{run_id}/top-cited{suffix}",
        text=_shell(body),
    )


def prisma_funnel_chart(prisma: dict[str, Any], run_id: int) -> UiResource | None:
    identified = int(prisma.get("records_identified", 0) or 0)
    if identified <= 0:
        return None
    duplicates = int(prisma.get("duplicates_removed", 0) or 0)
    unique = identified - duplicates
    screened = int(prisma.get("records_screened", 0) or 0)
    included = int(prisma.get("included", 0) or 0)
    studies = int(prisma.get("studies_included", 0) or 0)
    final_included = studies or included
    # A funnel implies a non-increasing sequence. Suppress it when persisted
    # counts violate that contract instead of drawing a chronologically
    # impossible but visually convincing review flow.
    if (
        duplicates < 0
        or unique < 0
        or screened < 0
        or final_included < 0
        or screened > unique
        or final_included > screened
    ):
        return None
    steps = [
        ("Records identified", identified, "What did the search strategy look like?"),
        ("After deduplication", unique, "How many duplicates were removed and how?"),
        ("Screened", screened, "How did the screening ensemble decide?"),
        ("Included", final_included, "Which works were included, and why?"),
    ]
    width, row_h, gap, height = 720, 44, 12, 4 * 56 + 16
    top = max(value for _, value, _ in steps) or 1
    rendered: list[str] = []
    for index, (label, value, prompt) in enumerate(steps):
        bar_w = max(72, round((width - 220) * value / top))
        x = (width - bar_w) / 2 + 70
        y = 8 + index * (row_h + gap)
        rendered.append(
            f'<g class="funnel-step" data-prompt="{_esc(prompt)}">'
            f"<title>{label}: {value}</title>"
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{row_h}" rx="10" '
            f'fill="url(#bar)" style="animation-delay:{index * 90}ms"/>'
            f'<text x="{x + bar_w / 2}" y="{y + row_h / 2 + 4}" text-anchor="middle" '
            f'font-size="13" font-weight="600" fill="#f4f1ea">{value}</text>'
            f'<text class="axis" x="{x - 10}" y="{y + row_h / 2 + 3}" '
            f'text-anchor="end">{label}</text>'
            "</g>"
        )
        if index < len(steps) - 1:
            rendered.append(
                f'<path d="M {width / 2 + 70} {y + row_h + 2} l -4 {gap - 5} l 8 0 z" '
                f'fill="{MOSS_SOFT}" opacity="0.6"/>'
            )
    body = (
        '<div class="card"><h1>PRISMA flow</h1>'
        '<p class="sub">From identification to inclusion, every number audited</p>'
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">'
        f"{_BAR_GRADIENT}{''.join(rendered)}</svg>"
        '<p class="hint">Click a stage to ask what happened there.</p></div>'
    )
    return UiResource(uri=f"ui://sixsentences/run/{run_id}/prisma-funnel", text=_shell(body))


def _bar_rows(rows: list[tuple[str, int, str, str | None]]) -> str:
    """rows: (label, value, color, click-prompt|None) -> horizontal bars."""
    top = max((value for _, value, _, _ in rows), default=0) or 1
    rendered = []
    for label, value, color, prompt in rows:
        width_pct = round(100 * value / top, 1)
        click = f' data-prompt="{_esc(prompt)}" style="cursor:pointer"' if prompt else ""
        rendered.append(
            f'<div class="row"{click}><span class="label" title="{_esc(label)}">{_esc(label)}'
            f'</span><span class="track"><span class="fill" '
            f'style="width:{width_pct}%;background:{color}"></span></span>'
            f'<span class="value">{value}</span></div>'
        )
    return "".join(rendered)


def verdict_chart(counts: dict[str, int], run_id: int) -> UiResource:
    rows = [
        (
            "Included",
            counts.get("include", 0),
            MOSS,
            "Which works were included, and why?",
        ),
        (
            "Unsure (needs you)",
            counts.get("unsure", 0),
            AMBER,
            "What is the ensemble unsure about?",
        ),
        (
            "Excluded",
            counts.get("exclude", 0),
            CLAY,
            "What were the main exclusion reasons?",
        ),
        ("Not screened", counts.get("unscreened", 0), "#9aa6a0", None),
    ]
    total = sum(value for _, value, _, _ in rows)
    body = (
        '<div class="card"><h1>Screening verdicts</h1>'
        f'<p class="sub">{total} unique works, effective decisions (human overrides model)</p>'
        + _bar_rows(rows)
        + '<p class="hint">Click a row to dig into it.</p></div>'
    )
    return UiResource(uri=f"ui://sixsentences/run/{run_id}/verdicts", text=_shell(body))


def venue_chart(
    works: list[WorkRecord],
    run_id: int,
    *,
    scope_label: str = "retrieved",
    scope_key: str = "all",
) -> UiResource | None:
    venues = Counter(w.venue for w in works if w.venue)
    if not venues:
        return None
    with_venue = sum(venues.values())
    rows: list[tuple[str, int, str, str | None]] = [
        (venue, count, MOSS, f'What did this run retrieve from "{venue}"?')
        for venue, count in venues.most_common(8)
    ]
    body = (
        f'<div class="card"><h1>Top venues for {scope_label} works</h1>'
        f'<p class="sub">{with_venue} of {len(works)} works have venue metadata '
        f"({len(venues)} distinct venues)</p>"
        + _bar_rows(rows)
        + '<p class="hint">Click a venue to ask about its works.</p></div>'
    )
    suffix = "" if scope_key == "all" else f"/{scope_key}"
    return UiResource(
        uri=f"ui://sixsentences/run/{run_id}/top-venues{suffix}",
        text=_shell(body),
    )


def build_chart(
    kind: str,
    session: Session,
    run: Run,
    *,
    scope: str = "all",
) -> tuple[UiResource | None, dict[str, Any]]:
    """Render one chart over the run's data; returns (resource, summary facts)."""
    works = works_for_run(session, run.id)
    scope_aliases = {
        "all": ("all", "retrieved"),
        "included": ("include", "included"),
        "include": ("include", "included"),
        "excluded": ("exclude", "excluded"),
        "exclude": ("exclude", "excluded"),
        "unsure": ("unsure", "unsure"),
    }
    verdict_scope, scope_label = scope_aliases.get(scope, scope_aliases["all"])
    scope_key = "all" if verdict_scope == "all" else verdict_scope
    if verdict_scope != "all" and kind not in ("verdicts", "prisma_funnel"):
        verdicts = _final_verdicts(session, run)
        works = [
            work
            for work in works
            if (decision := verdicts.get(work.id)) is not None and decision.verdict == verdict_scope
        ]
    if kind == "works_by_year":
        resource = works_by_year_chart(
            works,
            run.id,
            scope_label=scope_label,
            scope_key=scope_key,
        )
        by_year = dict(sorted(Counter(w.year for w in works if w.year).items()))
        summary = {
            "chart": kind,
            "scope": scope_label,
            "works": len(works),
            "by_year": {str(year): count for year, count in by_year.items()},
            "rendered": resource is not None,
        }
        if resource is None:
            summary["reason"] = (
                "At least two distinct publication years are needed for a useful chart."
            )
        return resource, summary
    if kind == "verdicts":
        verdicts = _final_verdicts(session, run)
        counts = Counter(d.verdict for d in verdicts.values())
        data = {
            "include": counts.get("include", 0),
            "unsure": counts.get("unsure", 0),
            "exclude": counts.get("exclude", 0),
            "unscreened": max(0, len(works) - len(verdicts)),
        }
        total = sum(data.values())
        resource = verdict_chart(data, run.id) if total else None
        return resource, {
            "chart": kind,
            **data,
            "rendered": resource is not None,
            **(
                {}
                if resource is not None
                else {"reason": "No screening decisions are available to chart yet."}
            ),
        }
    if kind == "top_venues":
        resource = venue_chart(
            works,
            run.id,
            scope_label=scope_label,
            scope_key=scope_key,
        )
        return resource, {
            "chart": kind,
            "scope": scope_label,
            "works": len(works),
            "rendered": resource is not None,
            **(
                {}
                if resource is not None
                else {"reason": "No publication venues are available to compare."}
            ),
        }
    if kind == "top_cited":
        resource = top_cited_chart(
            works,
            run.id,
            scope_label=scope_label,
            scope_key=scope_key,
        )
        leaders = sorted((w for w in works if w.cited_by_count), key=lambda w: -w.cited_by_count)
        summary = {
            "chart": kind,
            "scope": scope_label,
            "top": [
                {"id": w.id, "title": w.title, "cited_by_count": w.cited_by_count}
                for w in leaders[:5]
            ],
            "rendered": resource is not None,
        }
        if resource is None:
            summary["reason"] = "No citation counts are available to compare."
        return resource, summary
    if kind == "prisma_funnel":
        prisma = dict(run.prisma or {})
        resource = prisma_funnel_chart(prisma, run.id)
        has_counts = int(prisma.get("records_identified", 0) or 0) > 0
        return resource, {
            "chart": kind,
            **prisma,
            "rendered": resource is not None,
            **(
                {}
                if resource is not None
                else {
                    "reason": (
                        "The review-flow counts are incomplete or internally "
                        "inconsistent, so a chronological funnel would mislead."
                        if has_counts
                        else "No review-flow counts are available yet."
                    )
                }
            ),
        }
    return None, {"chart": kind, "error": "unknown chart kind"}


# -- citations ------------------------------------------------------------------


def _apa(work: WorkRecord) -> str:
    """A plain APA-style line (good enough to paste, honest about gaps)."""
    if not work.authors:
        authors = ""
    elif len(work.authors) == 1:
        authors = work.authors[0]
    elif len(work.authors) <= 4:
        authors = ", ".join(work.authors[:-1]) + " & " + work.authors[-1]
    else:
        authors = work.authors[0] + " et al."
    parts = [p for p in (authors, f"({work.year})" if work.year else "(n.d.)") if p]
    line = " ".join(parts) + f". {work.title.rstrip('.')}."
    if work.venue:
        line += f" {work.venue}."
    if work.doi:
        line += f" https://doi.org/{work.doi}"
    return line


def citation_card(
    work: WorkRecord, *, bibtex: str, ris: str, run_id: int, verified: bool
) -> UiResource:
    """Ready-to-paste citation formats with one-click copy (host does the copy
    via the `intent` action, so the clipboard works inside the sandbox)."""
    badge = (
        f'<span class="badge ok">Verified metadata{" · DOI" if work.doi else ""}</span>'
        if verified
        else '<span class="badge warn">Metadata read from the PDF, not verified</span>'
    )
    apa = _apa(work)
    tabs = (
        '<div class="tabs">'
        '<button type="button" class="tab on" data-pane="bibtex">BibTeX</button>'
        '<button type="button" class="tab" data-pane="ris">RIS (Zotero)</button>'
        '<button type="button" class="tab" data-pane="apa">APA</button>'
        '<button type="button" class="copy" id="copy">Copy</button>'
        "</div>"
    )
    panes = (
        f'<pre class="pane on" data-pane="bibtex">{_esc(bibtex.strip())}</pre>'
        f'<pre class="pane" data-pane="ris">{_esc(ris.strip())}</pre>'
        f'<pre class="pane" data-pane="apa">{_esc(apa)}</pre>'
    )
    body = (
        "<style>"
        f".badge {{ display:inline-block; border-radius:999px; padding:3px 10px; font-size:10.5px;"
        f" font-weight:600; margin-bottom:12px; }}"
        f".badge.ok {{ background:{MOSS}1a; color:{MOSS}; border:1px solid {MOSS}55; }}"
        f".badge.warn {{ background:{AMBER}14; color:{AMBER}; border:1px solid {AMBER}55; }}"
        f".tabs {{ display:flex; gap:6px; align-items:center; margin-bottom:8px; }}"
        f".tab {{ border:1px solid {LINE}; background:#fff; border-radius:999px; padding:5px 12px;"
        f" font-size:11.5px; cursor:pointer; color:{PINE}; font-family:inherit; }}"
        f".tab.on {{ background:{PINE}; border-color:{PINE}; color:#f4f1ea; }}"
        f".copy {{ margin-left:auto; border:1px solid {LINE}; background:#fff; border-radius:999px;"
        f" padding:5px 14px; font-size:11.5px; font-weight:600; cursor:pointer; color:{MOSS};"
        f" font-family:inherit; }}"
        f".copy:hover {{ border-color:{MOSS}; }}"
        f".pane {{ display:none; background:#f7f5ee; border:1px solid {LINE}; border-radius:12px;"
        f" padding:12px 14px; font-size:11px; line-height:1.55; overflow-x:auto;"
        f" font-family:ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap; }}"
        f".pane.on {{ display:block; }}"
        "</style>"
        f'<div class="card"><h1>Ready to cite</h1>'
        f'<p class="sub">{_esc(work.title[:110])}</p>{badge}{tabs}{panes}'
        '<p class="hint">RIS imports straight into Zotero (File &gt; Import). '
        "Copy puts the active tab on your clipboard.</p></div>"
    )
    script = """
const panes = document.querySelectorAll(".pane");
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("on"));
  tab.classList.add("on");
  panes.forEach((p) => p.classList.toggle("on", p.dataset.pane === tab.dataset.pane));
  sendSize();
}));
const copyBtn = document.getElementById("copy");
copyBtn.addEventListener("click", () => {
  const active = document.querySelector(".pane.on");
  post({ type: "intent", payload: { intent: "copy", text: active.textContent } });
  copyBtn.textContent = "Copied";
  setTimeout(() => { copyBtn.textContent = "Copy"; }, 1600);
});
"""
    return UiResource(
        uri=f"ui://sixsentences/run/{run_id}/cite/{work.id}", text=_shell(body, script)
    )


# -- follow-up suggestions ------------------------------------------------------


def followups_form(questions: list[str], run_id: int) -> UiResource:
    """Clickable next-step questions; a click submits the prompt as the user."""
    chips = "".join(
        f'<button type="button" class="chip" data-prompt="{_esc(question)}">'
        f"{_esc(question)}</button>"
        for question in questions
    )
    body = (
        '<div class="card"><h1>Where to next?</h1>'
        '<p class="sub">Pick a thread to pull on; each one asks for you.</p>'
        f'<div class="chips">{chips}</div></div>'
    )
    return UiResource(uri=f"ui://sixsentences/run/{run_id}/followups", text=_shell(body))


# -- clarifying questions ------------------------------------------------------


def clarify_form(questions: list[dict[str, Any]], run_id: int) -> UiResource:
    """An interactive form for the assistant's clarifying questions.

    Selecting options and sending posts a `prompt` action back to the host,
    which submits the answers as the user's next chat message.
    """
    blocks: list[str] = []
    for index, item in enumerate(questions):
        options = "".join(
            f'<button type="button" class="opt" data-q="{index}">{_esc(str(option))}</button>'
            for option in item.get("options", [])
        )
        blocks.append(
            f'<div class="q" data-question="{_esc(str(item.get("question", "")))}">'
            f"<p>{_esc(str(item.get('question', '')))}</p>"
            f'<div class="opts">{options}</div></div>'
        )
    body = (
        '<div class="card"><h1>Quick check before I answer</h1>'
        '<p class="sub">Pick what fits; add detail if you like.</p>'
        + "".join(blocks)
        + '<textarea id="extra" rows="2" placeholder="Anything else? (optional)"></textarea>'
        '<button type="button" class="send" id="send" disabled>Send answers</button></div>'
    )
    script = """
const picked = new Map();
const sendBtn = document.getElementById("send");
document.querySelectorAll(".opt").forEach((btn) => btn.addEventListener("click", () => {
  const q = btn.dataset.q;
  const prev = picked.get(q);
  if (prev) prev.classList.remove("on");
  if (prev === btn) { picked.delete(q); } else { btn.classList.add("on"); picked.set(q, btn); }
  sendBtn.disabled = picked.size === 0 && !document.getElementById("extra").value.trim();
}));
document.getElementById("extra").addEventListener("input", (e) => {
  sendBtn.disabled = picked.size === 0 && !e.target.value.trim();
});
sendBtn.addEventListener("click", () => {
  const parts = [];
  document.querySelectorAll(".q").forEach((q) => {
    const chosen = q.querySelector(".opt.on");
    if (chosen) parts.push(q.dataset.question + " " + chosen.textContent.trim());
  });
  const extra = document.getElementById("extra").value.trim();
  if (extra) parts.push("Also: " + extra);
  if (!parts.length) return;
  sendBtn.disabled = true;
  post({ type: "prompt", payload: { prompt: "To clarify: " + parts.join(" | ") } });
});
"""
    return UiResource(uri=f"ui://sixsentences/run/{run_id}/clarify", text=_shell(body, script))


# -- data table ---------------------------------------------------------------

_CELL_WORK = re.compile(r"\[(W\d+)\]|\b(W\d{4,})\b")


def data_table(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    run_id: int,
    *,
    numbering: dict[str, int] | None = None,
) -> UiResource:
    """A comparison/overview table as a polished card. Citation ids inside
    cells become the same numbered chips the prose uses (clicking one asks
    a follow-up about that source)."""
    numbers = numbering or {}

    def cell_html(text: str) -> str:
        def chip(match: re.Match[str]) -> str:
            work_id = match.group(1) or match.group(2)
            label = numbers.get(work_id)
            prompt = _esc(f"Tell me more about {work_id} in this context.")
            return (
                f'<button class="wchip" data-prompt="{prompt}" '
                f'title="{_esc(work_id)}">{label if label else "•"}</button>'
            )

        return _CELL_WORK.sub(chip, _esc(text)).replace("\n", "<br>")

    head = "".join(f"<th>{cell_html(c)}</th>" for c in columns)
    body_rows = []
    width = len(columns)
    for row in rows:
        padded = (row + [""] * width)[:width]
        body_rows.append("<tr>" + "".join(f"<td>{cell_html(c)}</td>" for c in padded) + "</tr>")
    caption = f"<h1>{_esc(title)}</h1>" if title else ""
    body = (
        '<div class="card table-card">'
        + caption
        + '<div class="scroll"><table>'
        + f"<thead><tr>{head}</tr></thead>"
        + f"<tbody>{''.join(body_rows)}</tbody>"
        + "</table></div>"
        + '<p class="hint">Click a source number to ask about that paper.</p>'
        + "</div>"
        + """<style>
.table-card .scroll { overflow-x: auto; margin-top: 10px; }
.table-card table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.table-card th { text-align: left; font-weight: 600; font-size: 11.5px;
  text-transform: uppercase; letter-spacing: 0.06em; color: #5c6b66;
  padding: 8px 12px 8px 0; border-bottom: 1.5px solid #e3ddd0; white-space: nowrap; }
.table-card td { vertical-align: top; padding: 9px 12px 9px 0;
  border-bottom: 1px solid #eee9dd; line-height: 1.5; min-width: 120px; }
.table-card tbody tr:last-child td { border-bottom: 0; }
.table-card tbody tr:hover td { background: #faf8f2; }
.table-card td:first-child, .table-card th:first-child { font-weight: 550;
  color: #0c1d19; }
.wchip { display: inline-flex; align-items: center; justify-content: center;
  min-width: 17px; height: 17px; padding: 0 4px; margin: 0 2px;
  border-radius: 999px; border: 1px solid rgba(51, 84, 76, 0.25);
  background: #eef2ec; color: #33544c; font-size: 10px; font-weight: 600;
  font-family: ui-monospace, monospace; cursor: pointer; vertical-align: middle; }
.wchip:hover { background: #33544c; color: #f4f1ea; }
</style>"""
    )
    identity = hashlib.sha256(repr((title, columns, rows)).encode("utf-8")).hexdigest()[:12]
    return UiResource(
        uri=f"ui://sixsentences/run/{run_id}/table/{identity}",
        text=_shell(body),
    )
