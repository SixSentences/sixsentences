"""Deterministic manuscript intelligence shared by the Writer API.

The checks in this module deliberately do not ask an LLM to invent a verdict.
They trace concrete LaTeX anchors, citation keys, cross references and pinned
research-object versions so every finding can jump back to its source line.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any

_CITE = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*|nocite)\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")
_LABEL = re.compile(r"\\label\{([^}]+)\}")
_REFERENCE = re.compile(r"\\(?:ref|pageref|eqref|autoref|[cC]ref)\{([^}]+)\}")
_SECTION = re.compile(
    r"\\(?P<kind>part|chapter|section|subsection|subsubsection)\*?\{(?P<title>[^}]*)\}"
)
_LIVE_MARKER = re.compile(
    r"^% six-live:analysis:(?P<id>[A-Za-z0-9_-]+):dataset-v(?P<version>\d+)\s*$",
    re.MULTILINE,
)
_COMMAND_ONLY = re.compile(r"^\s*(?:\\[A-Za-z@]+|[%{}])")
_CLAIM_SIGNAL = re.compile(
    r"\b(?:show|shows|showed|demonstrate|demonstrates|demonstrated|"
    r"increase|increases|increased|decrease|decreases|decreased|"
    r"improve|improves|improved|outperform|outperforms|outperformed|"
    r"associated|significant|evidence|result|results|found|suggest|suggests)\b|"
    r"\b\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)
_STOPWORDS = {
    "about",
    "after",
    "also",
    "among",
    "because",
    "before",
    "between",
    "from",
    "into",
    "method",
    "paper",
    "results",
    "study",
    "that",
    "their",
    "these",
    "this",
    "using",
    "were",
    "with",
}


def _clean_line(line: str) -> str:
    """Remove unescaped comments while retaining the original line number."""

    return re.split(r"(?<!\\)%", line, maxsplit=1)[0]


def _line_for_offset(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _terms(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{4,}", value.casefold()) if token not in _STOPWORDS
    }


def _finding(
    *,
    category: str,
    severity: str,
    title: str,
    detail: str,
    path: str,
    line: int,
    snippet: str = "",
    suggestion_keys: list[str] | None = None,
) -> dict[str, Any]:
    digest = hashlib.sha1(  # noqa: S324 - stable UI key, not cryptography
        f"{category}:{path}:{line}:{title}:{snippet}".encode()
    ).hexdigest()[:12]
    return {
        "id": digest,
        "category": category,
        "severity": severity,
        "title": title,
        "detail": detail,
        "path": path,
        "line": line,
        "snippet": snippet[:500],
        "suggestion_keys": suggestion_keys or [],
    }


def audit_project(
    project_files: dict[str, str],
    citations: list[dict[str, Any]],
    analyses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit citations, claims, cross-file references and live results."""

    findings: list[dict[str, Any]] = []
    labels: dict[str, list[tuple[str, int]]] = defaultdict(list)
    references: list[tuple[str, str, int]] = []
    used_citations: dict[str, list[tuple[str, int]]] = defaultdict(list)
    section_titles: dict[str, list[tuple[str, int]]] = defaultdict(list)
    known_keys = {str(row.get("key") or "") for row in citations}
    citations_by_key = {str(row.get("key") or ""): row for row in citations if row.get("key")}
    citation_terms = {
        str(row.get("key") or ""): _terms(str(row.get("title") or ""))
        for row in citations
        if row.get("key")
    }

    for path, source in sorted(project_files.items()):
        for match in _LABEL.finditer(source):
            labels[match.group(1)].append((path, _line_for_offset(source, match.start())))
        for match in _REFERENCE.finditer(source):
            references.extend(
                (key.strip(), path, _line_for_offset(source, match.start()))
                for key in match.group(1).split(",")
                if key.strip()
            )
        for match in _CITE.finditer(source):
            for key in match.group(1).split(","):
                key = key.strip()
                if key and key != "*":
                    used_citations[key].append((path, _line_for_offset(source, match.start())))
        for match in _SECTION.finditer(source):
            title = re.sub(r"\\[A-Za-z]+", "", match.group("title")).strip().casefold()
            if title:
                section_titles[title].append((path, _line_for_offset(source, match.start())))

        paragraphs: list[tuple[int, str]] = []
        buffer: list[str] = []
        start_line = 1
        for line_number, raw_line in enumerate(source.splitlines(), start=1):
            cleaned_line = _clean_line(raw_line).strip()
            if not cleaned_line:
                if buffer:
                    paragraphs.append((start_line, " ".join(buffer)))
                    buffer = []
                continue
            if not buffer:
                start_line = line_number
            buffer.append(cleaned_line)
        if buffer:
            paragraphs.append((start_line, " ".join(buffer)))

        for paragraph_line, paragraph in paragraphs:
            plain = re.sub(r"\\[A-Za-z@]+(?:\[[^\]]*\])?", " ", paragraph)
            plain = re.sub(r"[{}$~]", " ", plain)
            word_count = len(re.findall(r"\b[\w'-]+\b", plain))
            if (
                word_count >= 12
                and _CLAIM_SIGNAL.search(plain)
                and not _CITE.search(paragraph)
                and not _COMMAND_ONLY.match(paragraph)
            ):
                paragraph_terms = _terms(plain)
                suggested = [
                    key
                    for key, _score in sorted(
                        (
                            (key, len(paragraph_terms & title_terms))
                            for key, title_terms in citation_terms.items()
                        ),
                        key=lambda item: (-item[1], item[0]),
                    )
                    if _score > 0
                ][:3]
                findings.append(
                    _finding(
                        category="claims",
                        severity="warning",
                        title="Evidence-bearing claim has no citation",
                        detail=(
                            "This paragraph makes a result or effect claim but does not "
                            "carry a citation. Link evidence or mark it as the authors' result."
                        ),
                        path=path,
                        line=paragraph_line,
                        snippet=plain.strip(),
                        suggestion_keys=suggested,
                    )
                )

        for todo in re.finditer(r"\b(?:TODO|FIXME|TBD|CITATION NEEDED)\b", source, re.IGNORECASE):
            findings.append(
                _finding(
                    category="consistency",
                    severity="warning",
                    title="Unresolved manuscript marker",
                    detail="Resolve this drafting marker before submission.",
                    path=path,
                    line=_line_for_offset(source, todo.start()),
                    snippet=source.splitlines()[_line_for_offset(source, todo.start()) - 1],
                )
            )

    for key, occurrences in sorted(used_citations.items()):
        if key not in known_keys:
            path, occurrence_line = occurrences[0]
            findings.append(
                _finding(
                    category="citations",
                    severity="error",
                    title=f"Unknown citation key: {key}",
                    detail="The key is not present in the live bibliography or uploaded sources.",
                    path=path,
                    line=occurrence_line,
                    snippet=key,
                )
            )
        elif citations_by_key[key].get("is_retracted"):
            for path, occurrence_line in occurrences:
                findings.append(
                    _finding(
                        category="citations",
                        severity="error",
                        title=f"Retracted source is cited: {key}",
                        detail=(
                            "This record is flagged as retracted in the linked "
                            "literature search. Replace it or explain its use explicitly."
                        ),
                        path=path,
                        line=occurrence_line,
                        snippet=key,
                    )
                )

    for key, occurrences in sorted(labels.items()):
        if len(occurrences) > 1:
            for path, occurrence_line in occurrences:
                findings.append(
                    _finding(
                        category="consistency",
                        severity="error",
                        title=f"Duplicate label: {key}",
                        detail="Labels must be unique across every project file.",
                        path=path,
                        line=occurrence_line,
                        snippet=key,
                    )
                )
    referenced_keys = {key for key, _, _ in references}
    for key, path, occurrence_line in references:
        if key not in labels:
            findings.append(
                _finding(
                    category="consistency",
                    severity="error",
                    title=f"Missing cross-reference target: {key}",
                    detail="No matching label exists anywhere in the manuscript project.",
                    path=path,
                    line=occurrence_line,
                    snippet=key,
                )
            )
    for key, occurrences in sorted(labels.items()):
        if key not in referenced_keys:
            path, occurrence_line = occurrences[0]
            findings.append(
                _finding(
                    category="consistency",
                    severity="info",
                    title=f"Label is never referenced: {key}",
                    detail=(
                        "This may be intentional, but it often indicates a missing "
                        "in-text reference."
                    ),
                    path=path,
                    line=occurrence_line,
                    snippet=key,
                )
            )
    for title, occurrences in sorted(section_titles.items()):
        if len(occurrences) > 1:
            for path, occurrence_line in occurrences[1:]:
                findings.append(
                    _finding(
                        category="consistency",
                        severity="warning",
                        title="Repeated section heading",
                        detail=f'"{title}" appears more than once across the project.',
                        path=path,
                        line=occurrence_line,
                        snippet=title,
                    )
                )

    analysis_by_id = {str(row.get("public_id")): row for row in analyses or []}
    for path, source in sorted(project_files.items()):
        for marker in _LIVE_MARKER.finditer(source):
            object_id = marker.group("id")
            pinned_version = int(marker.group("version"))
            analysis = analysis_by_id.get(object_id)
            marker_line = _line_for_offset(source, marker.start())
            if analysis is None:
                findings.append(
                    _finding(
                        category="research",
                        severity="error",
                        title="Detached live research object",
                        detail=(
                            "The analysis behind this manuscript block no longer "
                            "exists in the project."
                        ),
                        path=path,
                        line=marker_line,
                        snippet=marker.group(0),
                    )
                )
            elif int(analysis.get("latest_dataset_version") or pinned_version) > pinned_version:
                findings.append(
                    _finding(
                        category="research",
                        severity="warning",
                        title="Live result is out of date",
                        detail=(
                            f"The manuscript uses dataset version {pinned_version}; version "
                            f"{analysis['latest_dataset_version']} is available. "
                            "Review before refreshing."
                        ),
                        path=path,
                        line=marker_line,
                        snippet=marker.group(0),
                    )
                )

    severity_order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda row: (severity_order[row["severity"]], row["path"], row["line"]))
    counts = Counter(row["category"] for row in findings)
    severity_counts = Counter(row["severity"] for row in findings)
    score = max(0, 100 - severity_counts["error"] * 12 - severity_counts["warning"] * 5)
    return {
        "score": score,
        "files_checked": len(project_files),
        "citation_keys": len(known_keys),
        "findings": findings,
        "counts": dict(counts),
        "severity_counts": dict(severity_counts),
        "unused_citation_keys": sorted(known_keys - set(used_citations))[:100],
    }


def snapshot_summary(
    files: dict[str, str], previous: dict[str, str] | None
) -> tuple[str, list[dict[str, Any]]]:
    """Describe a multi-file snapshot relative to its predecessor."""

    previous = previous or {}
    changes: list[dict[str, Any]] = []
    for path in sorted(set(files) | set(previous)):
        before = previous.get(path, "").splitlines()
        after = files.get(path, "").splitlines()
        if before == after:
            continue
        matcher = difflib.SequenceMatcher(a=before, b=after)
        added = removed = 0
        for tag, a1, a2, b1, b2 in matcher.get_opcodes():
            if tag in {"insert", "replace"}:
                added += b2 - b1
            if tag in {"delete", "replace"}:
                removed += a2 - a1
        state = "modified"
        if path not in previous:
            state = "added"
        elif path not in files:
            state = "removed"
        changes.append({"path": path, "state": state, "added": added, "removed": removed})
    if not changes:
        return "No source changes", []
    names = ", ".join(row["path"] for row in changes[:3])
    if len(changes) > 3:
        names += f" and {len(changes) - 3} more"
    added = sum(int(row["added"]) for row in changes)
    removed = sum(int(row["removed"]) for row in changes)
    return f"{len(changes)} file(s): {names} (+{added}/-{removed} lines)", changes


def snapshot_diff(files: dict[str, str], previous: dict[str, str]) -> list[dict[str, Any]]:
    """Return bounded unified diffs for a semantic version comparison."""

    result: list[dict[str, Any]] = []
    for path in sorted(set(files) | set(previous)):
        before = previous.get(path, "")
        after = files.get(path, "")
        if before == after:
            continue
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"before/{path}",
                tofile=f"after/{path}",
                n=3,
            )
        )
        result.append({"path": path, "diff": diff[:40_000]})
    return result


def render_analysis_latex(analysis: dict[str, Any]) -> str:
    """Render a version-pinned analysis result as refreshable LaTeX."""

    public_id = str(analysis["public_id"])
    version = int(analysis["dataset_version"])
    result = dict(analysis.get("result") or {})
    safe_name = re.sub(r"[^A-Za-z]", "", str(analysis.get("name") or public_id))
    safe_name = safe_name[:28] or "Result"
    lines = [
        f"% six-live:analysis:{public_id}:dataset-v{version}",
        f"% Generated from {analysis.get('name') or 'analysis'}; review before submission.",
    ]
    scalar_values = [
        (key, value)
        for key, value in result.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    for key, value in scalar_values:
        macro_key = re.sub(r"[^A-Za-z]", "", key.title())
        formatted = f"{value:.4g}" if isinstance(value, float) else str(value)
        lines.append(f"\\providecommand{{\\Six{safe_name}{macro_key}}}{{{formatted}}}")
    # two-number ranges (e.g. ci_95) become a Low/High macro pair
    for key, value in result.items():
        if (
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
        ):
            macro_key = re.sub(r"[^A-Za-z]", "", key.title())
            low = f"{value[0]:.4g}" if isinstance(value[0], float) else str(value[0])
            high = f"{value[1]:.4g}" if isinstance(value[1], float) else str(value[1])
            lines.append(f"\\providecommand{{\\Six{safe_name}{macro_key}Low}}{{{low}}}")
            lines.append(f"\\providecommand{{\\Six{safe_name}{macro_key}High}}{{{high}}}")
    groups = result.get("groups")
    if isinstance(groups, list) and groups:
        lines.extend(
            [
                "\\begin{table}[t]",
                "  \\centering",
                f"  \\caption{{{analysis.get('name') or 'Analysis result'} (dataset v{version}).}}",
                f"  \\label{{tab:six-{public_id.lower()}}}",
                "  \\begin{tabular}{lrr}",
                "    \\toprule",
                "    Group & $n$ & Value \\\\",
                "    \\midrule",
            ]
        )
        for group in groups[:40]:
            label = str(group.get("group") or "").replace("&", r"\&")
            value = group.get("value")
            rendered = f"{value:.4g}" if isinstance(value, float) else str(value)
            lines.append(f"    {label} & {group.get('n', '')} & {rendered} \\\\")
        lines.extend(["    \\bottomrule", "  \\end{tabular}", "\\end{table}"])
    if not scalar_values and not groups:
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        lines.append(f"% Exact result: {payload[:3000]}")
    lines.append(f"% six-live-end:{public_id}")
    return "\n".join(lines) + "\n"
