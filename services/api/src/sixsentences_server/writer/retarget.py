"""Move a completed LaTeX manuscript into a different submission template.

The transfer is deterministic and intentionally conservative. Research
content remains verbatim; only the destination shell is replaced. The caller
receives a report of mapped and review-required elements before creating a new
document, so the submitted source is never overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BEGIN_DOCUMENT = r"\begin{document}"
_END_DOCUMENT = r"\end{document}"
_ABSTRACT = re.compile(
    r"\\begin\{abstract\}(?P<body>.*?)\\end\{abstract\}",
    re.DOTALL,
)
_BIBLIOGRAPHY_LINE = re.compile(
    r"^[ \t]*\\(?:bibliographystyle|bibliography|printbibliography)\b.*?$",
    re.MULTILINE,
)
_MAKETITLE_LINE = re.compile(r"^[ \t]*\\maketitle[ \t]*$", re.MULTILINE)
_SECTION = re.compile(
    r"\\(?:part|chapter|section|subsection|subsubsection)\*?\{",
)
_CITATION = re.compile(r"\\cite[a-zA-Z*]*\{")
_CUSTOM_DEFINITION = re.compile(
    r"^[ \t]*\\(?:newcommand|renewcommand|providecommand|DeclareMathOperator|"
    r"newenvironment|def)\b.*?(?:\n(?![ \t]).*?)?(?=^[ \t]*\\|\Z)",
    re.MULTILINE | re.DOTALL,
)
_USEPACKAGE = re.compile(r"\\usepackage(?P<options>\[[^\]]*\])?\{(?P<packages>[^}]+)\}")
_PGFPLOTSSET = re.compile(r"^[ \t]*\\pgfplotsset\{.*?\}[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class RetargetResult:
    """Prepared destination source and its human-readable transfer report."""

    content: str
    mapped: tuple[str, ...]
    warnings: tuple[str, ...]
    section_count: int
    citation_count: int


def _command_argument_span(
    source: str,
    command: str,
) -> tuple[int, int, str] | None:
    match = re.search(rf"\\{re.escape(command)}\b", source)
    if match is None:
        return None
    cursor = match.end()
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    while cursor < len(source) and source[cursor] == "[":
        depth = 0
        escaped = False
        for index in range(cursor, len(source)):
            character = source[index]
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    cursor = index + 1
                    break
        else:
            return None
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
    if cursor >= len(source) or source[cursor] != "{":
        return None
    depth = 0
    start = cursor + 1
    escaped = False
    for index in range(cursor, len(source)):
        character = source[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1, source[start:index]
    return None


def _balanced_command(source: str, command: str) -> str | None:
    match = _command_argument_span(source, command)
    return match[2] if match is not None else None


def _remove_balanced_command(source: str, command: str) -> str:
    result = source
    while match := _command_argument_span(result, command):
        start, end, _ = match
        result = result[:start] + result[end:]
    return result


def _document_body(source: str) -> str:
    start = source.find(_BEGIN_DOCUMENT)
    end = source.rfind(_END_DOCUMENT)
    if start < 0 or end < start:
        return source.strip()
    body = source[start + len(_BEGIN_DOCUMENT) : end]
    body = _MAKETITLE_LINE.sub("", body)
    body = _ABSTRACT.sub("", body)
    body = _BIBLIOGRAPHY_LINE.sub("", body)
    for command in ("title", "author", "date"):
        body = _remove_balanced_command(body, command)
    return body.strip()


def _destination_bibliography(template: str) -> str:
    commands = [match.group(0).strip() for match in _BIBLIOGRAPHY_LINE.finditer(template)]
    return "\n".join(dict.fromkeys(commands))


def _missing_package_commands(source_preamble: str, target_preamble: str) -> list[str]:
    target_packages = {
        package.strip()
        for match in _USEPACKAGE.finditer(target_preamble)
        for package in match.group("packages").split(",")
        if package.strip()
    }
    commands: list[str] = []
    copied_packages: set[str] = set()
    for match in _USEPACKAGE.finditer(source_preamble):
        missing = [
            package.strip()
            for package in match.group("packages").split(",")
            if package.strip()
            and package.strip() not in target_packages
            and package.strip() not in copied_packages
        ]
        if not missing:
            continue
        commands.append(f"\\usepackage{match.group('options') or ''}{{{','.join(missing)}}}")
        copied_packages.update(missing)
    if "pgfplots" in copied_packages:
        commands.extend(
            dict.fromkeys(
                match.group(0).strip() for match in _PGFPLOTSSET.finditer(source_preamble)
            )
        )
    return commands


def retarget_latex(source: str, destination_template: str) -> RetargetResult:
    """Place source research content into a destination template shell."""

    if _BEGIN_DOCUMENT not in destination_template or _END_DOCUMENT not in destination_template:
        raise ValueError("the destination template is not a complete LaTeX document")

    mapped: list[str] = []
    warnings: list[str] = []
    destination = destination_template
    metadata: list[str] = []

    for command, label in (("title", "title"), ("author", "authors"), ("date", "date")):
        value = _balanced_command(source, command)
        if value is not None:
            mapped.append(label)
        elif command != "date":
            warnings.append(f"No explicit {label} command was found.")
            value = _balanced_command(destination, command)
        elif value is None:
            value = _balanced_command(destination, command)
        if value is not None:
            metadata.append(f"\\{command}{{{value}}}")

    abstract_match = _ABSTRACT.search(source)
    abstract = abstract_match.group("body").strip() if abstract_match else ""
    if abstract:
        mapped.append("abstract")
    else:
        warnings.append("No abstract environment was found.")

    body = _document_body(source)
    section_count = len(_SECTION.findall(body))
    citation_count = len(_CITATION.findall(body))
    if body:
        mapped.append("manuscript body")
    if section_count:
        mapped.append(f"{section_count} section headings")
    if citation_count:
        mapped.append(f"{citation_count} citation commands")

    source_preamble = source.split(_BEGIN_DOCUMENT, 1)[0]
    target_preamble = destination.split(_BEGIN_DOCUMENT, 1)[0]
    package_commands = _missing_package_commands(source_preamble, target_preamble)
    custom_definitions = [
        definition.strip()
        for definition in _CUSTOM_DEFINITION.findall(source_preamble)
        if definition.strip() and definition.strip() not in target_preamble
    ]
    preserved_preamble = [*package_commands, *custom_definitions]
    if preserved_preamble:
        begin = destination.find(_BEGIN_DOCUMENT)
        destination = (
            destination[:begin].rstrip()
            + "\n\n% Preserved manuscript dependencies\n"
            + "\n".join(preserved_preamble)
            + "\n\n"
            + destination[begin:]
        )
    if package_commands:
        mapped.append(
            f"{len(package_commands)} manuscript "
            f"{'dependency' if len(package_commands) == 1 else 'dependencies'}"
        )
    if custom_definitions:
        mapped.append(f"{len(custom_definitions)} custom definitions")

    begin = destination.find(_BEGIN_DOCUMENT)
    before = destination[: begin + len(_BEGIN_DOCUMENT)].rstrip()
    bibliography = _destination_bibliography(destination)
    parts = [before, *metadata, "\\maketitle"]
    if abstract:
        parts.append(f"\\begin{{abstract}}\n{abstract}\n\\end{{abstract}}")
    if body:
        parts.append(body)
    if bibliography:
        parts.append(bibliography)
        mapped.append("destination bibliography style")
    parts.append(_END_DOCUMENT)

    if "\\documentclass" not in source:
        warnings.append("The source had no document class; inspect the transferred preamble.")
    if re.search(r"\\includeonly\b", source_preamble):
        warnings.append("The source uses \\includeonly; verify included chapter files.")
    if re.search(r"\\bibliography\{(?!references\})", source):
        warnings.append(
            "The source referenced a custom bibliography file. Attached source files are "
            "preserved, but the destination bibliography command should be checked."
        )
    if "IEEEtran" in destination_template and _balanced_command(source, "author"):
        warnings.append(
            "The author text was preserved, but IEEE affiliation blocks should be "
            "reviewed for the destination venue."
        )

    return RetargetResult(
        content="\n\n".join(part for part in parts if part).strip() + "\n",
        mapped=tuple(dict.fromkeys(mapped)),
        warnings=tuple(dict.fromkeys(warnings)),
        section_count=section_count,
        citation_count=citation_count,
    )
