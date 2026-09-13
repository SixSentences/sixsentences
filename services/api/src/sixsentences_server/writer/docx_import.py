"""Word to LaTeX conversion for the Writer.

Deterministic structure mapping via python-docx: headings become sections
(recognized by their stored outline level, so localized style names like
"Überschrift 1" work), bold/italic and hyperlinks keep their meaning, list
paragraphs group into nested itemize/enumerate, tables come out as booktabs
with merged cells deduplicated, and embedded images are extracted as
document assets referenced through \\includegraphics (a following Caption
paragraph becomes the figure's caption). Everything is escaped, so the
imported document compiles on the first click. Layout niceties Word cannot
express in structure (columns, text boxes, equations) are out of scope by
design; the goal is a clean, honest LaTeX starting point."""

from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from sixsentences_server.core.uploads import (
    UnsafeImageError,
    decode_image,
    open_safe_zip,
)
from sixsentences_server.writer.artifacts import tex_escape

_PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage[margin=2.5cm]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage[numbers]{natbib}
"""

_CLOSING = r"""
\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

_SECTIONING = ("section", "subsection", "subsubsection", "paragraph")

# style-name fallback for documents whose headings carry no outline level;
# Word localizes style names, so the German ones are listed alongside
_HEADING_NAMES = {
    "Heading 1": 0,
    "Heading 2": 1,
    "Heading 3": 2,
    "Heading 4": 3,
    "Überschrift 1": 0,
    "Überschrift 2": 1,
    "Überschrift 3": 2,
    "Überschrift 4": 3,
}
_TITLE_NAMES = {"Title", "Titel"}
_CAPTION_PREFIXES = ("Caption", "Beschriftung")
_MAX_DOCX_FILES = 1_000
_MAX_DOCX_UNPACKED = 100_000_000


class DocxImportError(ValueError):
    """The uploaded bytes are not a Word document python-docx can read."""


def _url_escape(url: str) -> str:
    """Just enough escaping for a URL inside \\href's first argument."""
    cleaned = url.replace("\\", "").replace("{", "").replace("}", "")
    return cleaned.replace("%", "\\%").replace("#", "\\#")


def _run_tex(run: Run) -> str:
    # Word's soft line breaks live inside a run. A plain newline is only
    # whitespace to LaTeX, so preserve the author's explicit break.
    text = tex_escape(run.text).replace("\n", "\\\\\n")
    if not text:
        return ""
    if run.font.superscript:
        text = f"\\textsuperscript{{{text}}}"
    elif run.font.subscript:
        text = f"\\textsubscript{{{text}}}"
    if run.underline:
        text = f"\\underline{{{text}}}"
    if run.bold:
        text = f"\\textbf{{{text}}}"
    if run.italic:
        text = f"\\textit{{{text}}}"
    return text


def _runs_to_latex(paragraph: Paragraph) -> str:
    """Inline content including hyperlink runs, which plain .runs skips."""
    parts: list[str] = []
    for item in paragraph.iter_inner_content():
        if isinstance(item, Hyperlink):
            inner = "".join(_run_tex(run) for run in item.runs)
            if not inner:
                continue
            if item.address:
                parts.append(f"\\href{{{_url_escape(item.address)}}}{{{inner}}}")
            else:  # internal bookmark link: keep the text
                parts.append(inner)
        elif isinstance(item, Run):
            parts.append(_run_tex(item))
    return "".join(parts) or tex_escape(paragraph.text)


def _outline_level(paragraph: Paragraph) -> int | None:
    """The stored outline level (0 = Heading 1), from the paragraph itself
    or its style chain. This survives localized style names."""
    value: str | None = None
    pPr = paragraph._p.pPr  # noqa: SLF001 - python-docx exposes no API here
    if pPr is not None:
        node = pPr.find(qn("w:outlineLvl"))
        if node is not None:
            value = node.get(qn("w:val"))
    if value is None:
        style, hops = paragraph.style, 0
        while style is not None and hops < 8:
            pPr = style.element.find(qn("w:pPr"))
            node = pPr.find(qn("w:outlineLvl")) if pPr is not None else None
            if node is not None:
                value = node.get(qn("w:val"))
                break
            style, hops = style.base_style, hops + 1
    try:
        level = int(value) if value is not None else -1
    except ValueError:
        return None
    if 0 <= level <= 8:  # 9 explicitly means "body text"
        return min(level, len(_SECTIONING) - 1)
    return None


def _heading_level_by_name(style_name: str) -> int | None:
    for name, level in _HEADING_NAMES.items():
        if style_name.startswith(name):
            return level
    return None


def _numbering_formats(document: Any) -> dict[tuple[int, int], str]:
    """(numId, ilvl) -> numFmt ("bullet", "decimal", ...) from numbering.xml,
    so bullets and numbered lists come out as the right environment."""
    try:
        root = document.part.numbering_part.element
    except Exception:  # no numbering part in this package
        return {}
    abstract: dict[int, dict[int, str]] = {}
    for node in root.findall(qn("w:abstractNum")):
        levels: dict[int, str] = {}
        for lvl in node.findall(qn("w:lvl")):
            fmt = lvl.find(qn("w:numFmt"))
            if fmt is not None:
                levels[int(lvl.get(qn("w:ilvl")))] = fmt.get(qn("w:val"))
        abstract[int(node.get(qn("w:abstractNumId")))] = levels
    formats: dict[tuple[int, int], str] = {}
    for node in root.findall(qn("w:num")):
        ref = node.find(qn("w:abstractNumId"))
        if ref is None:
            continue
        for ilvl, fmt in abstract.get(int(ref.get(qn("w:val"))), {}).items():
            formats[(int(node.get(qn("w:numId"))), ilvl)] = fmt
    return formats


def _paragraph_numbering(paragraph: Paragraph) -> tuple[int, int] | None:
    """(numId, ilvl) when the paragraph itself carries list numbering."""
    pPr = paragraph._p.pPr  # noqa: SLF001
    numPr: Any = pPr.numPr if pPr is not None else None
    if numPr is None or numPr.numId is None or not numPr.numId.val:
        return None
    ilvl = numPr.ilvl.val if numPr.ilvl is not None else 0
    return (int(numPr.numId.val), int(ilvl or 0))


def _is_list_style(style_name: str) -> bool:
    return style_name.startswith(("List", "Listen", "Aufzählung"))


def _is_numbered_style(style_name: str) -> bool:
    return "Number" in style_name or "nummer" in style_name.lower()


def _image_asset(part: Any) -> tuple[str, bytes] | None:
    """(extension, bytes) for formats LaTeX can include. Bitmap formats
    Pillow understands are converted to PNG; Word vector formats (EMF/WMF)
    have no LaTeX-usable representation and yield None."""
    suffix = PurePosixPath(str(part.partname)).suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg"):
        return (".png" if suffix == ".png" else ".jpg", part.blob)
    try:
        image, _ = decode_image(part.blob)
        out = BytesIO()
        image.convert("RGB").save(out, format="PNG")
        return (".png", out.getvalue())
    except UnsafeImageError:
        return None


class _ImageCollector:
    """Embedded pictures, extracted once per relationship id and named
    word-image-N so they can ship as Writer assets."""

    def __init__(self, document: Any) -> None:
        self._document = document
        self._by_rid: dict[str, str | None] = {}
        self.images: list[tuple[str, bytes]] = []

    def filename_for(self, rid: str) -> str | None:
        if rid in self._by_rid:
            return self._by_rid[rid]
        filename: str | None = None
        part = self._document.part.related_parts.get(rid)
        asset = _image_asset(part) if part is not None else None
        if asset is not None:
            extension, blob = asset
            filename = f"word-image-{len(self.images) + 1}{extension}"
            self.images.append((filename, blob))
        self._by_rid[rid] = filename
        return filename


def _paragraph_image_rids(paragraph: Paragraph) -> list[str]:
    """Relationship ids of every picture anchored in this paragraph."""
    rids: list[str] = []
    for blip in paragraph._p.findall(".//" + qn("a:blip")):  # noqa: SLF001
        rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
        if rid:
            rids.append(rid)
    return rids


def _table_to_latex(table: Table) -> str:
    # merged cells repeat the same underlying tc element across the grid;
    # keep the text once and blank the repeats so nothing shows up twice
    grid: list[list[str]] = []
    previous_tcs: list[object] = []
    for row in table.rows:
        texts: list[str] = []
        row_tcs: list[object] = []
        last_tc: object = None
        for index, cell in enumerate(row.cells):
            tc = cell._tc  # noqa: SLF001
            duplicate = tc is last_tc or (index < len(previous_tcs) and tc is previous_tcs[index])
            texts.append("" if duplicate else " ".join(cell.text.split()))
            row_tcs.append(tc)
            last_tc = tc
        grid.append(texts)
        previous_tcs = row_tcs
    if not grid or not grid[0]:
        return ""
    columns = len(grid[0])
    lines = [
        "\\begin{table}[t]",
        "  \\centering",
        "  \\begin{tabular}{" + "l" * columns + "}",
        "    \\toprule",
        "    " + " & ".join(tex_escape(c) for c in grid[0]) + " \\\\",
        "    \\midrule",
    ]
    for row_texts in grid[1:]:
        padded = (row_texts + [""] * columns)[:columns]
        lines.append("    " + " & ".join(tex_escape(c) for c in padded) + " \\\\")
    lines += ["    \\bottomrule", "  \\end{tabular}", "\\end{table}"]
    return "\n".join(lines)


def docx_to_latex(blob: bytes, fallback_title: str) -> tuple[str, list[tuple[str, bytes]]]:
    """A complete, compilable LaTeX document from a .docx upload, plus the
    embedded images as (filename, bytes) ready to become Writer assets."""
    try:
        archive = open_safe_zip(
            blob,
            max_files=_MAX_DOCX_FILES,
            max_uncompressed_bytes=_MAX_DOCX_UNPACKED,
            max_member_bytes=_MAX_DOCX_UNPACKED,
        )
        archive.close()
        document = Document(BytesIO(blob))
    except Exception as exc:  # zip/package/schema errors from arbitrary bytes
        raise DocxImportError("not a readable Word (.docx) file") from exc

    numbering_formats = _numbering_formats(document)
    collector = _ImageCollector(document)
    title = tex_escape(fallback_title)
    body: list[str] = []
    list_stack: list[str] = []  # open list environments, index = depth
    pending_figure: list[str] | None = None  # awaiting a Caption paragraph

    def close_lists(depth: int = 0) -> None:
        while len(list_stack) > depth:
            body.append("  " * (len(list_stack) - 1) + f"\\end{{{list_stack.pop()}}}")

    def flush_figure(caption: str | None) -> None:
        nonlocal pending_figure
        if pending_figure is None:
            return
        lines = pending_figure
        pending_figure = None
        if caption:
            lines.insert(-1, f"  \\caption{{{caption}}}")
        body.append("\n".join(lines))

    def figure_lines(filename: str) -> list[str]:
        return [
            "\\begin{figure}[t]",
            "  \\centering",
            f"  \\includegraphics[width=0.85\\linewidth]{{{filename}}}",
            "\\end{figure}",
        ]

    def list_item(block: Paragraph, level: int, kind: str) -> None:
        while len(list_stack) > level + 1 or (
            list_stack and len(list_stack) == level + 1 and list_stack[-1] != kind
        ):
            close_lists(len(list_stack) - 1)
        while len(list_stack) < level + 1:
            body.append("  " * len(list_stack) + f"\\begin{{{kind}}}")
            list_stack.append(kind)
        body.append("  " * len(list_stack) + f"\\item {_runs_to_latex(block)}")

    for block in document.iter_inner_content():
        if isinstance(block, Table):
            close_lists()
            flush_figure(None)
            table_tex = _table_to_latex(block)
            if table_tex:
                body.append(table_tex)
            continue
        if not isinstance(block, Paragraph):
            continue
        style_name = (block.style.name or "") if block.style else ""
        text = block.text.strip()

        # pictures anchored here become figures; a Caption paragraph that
        # follows directly provides the \caption
        rids = _paragraph_image_rids(block)
        if rids:
            close_lists()
            flush_figure(None)
            filenames = [collector.filename_for(rid) for rid in rids]
            usable = [name for name in filenames if name]
            if not usable and filenames:
                body.append(
                    "% An embedded image was skipped (format LaTeX cannot include, e.g. EMF/WMF)."
                )
            for name in usable[:-1]:
                body.append("\n".join(figure_lines(name)))
            if usable:
                pending_figure = figure_lines(usable[-1])
            if text:
                flush_figure(None)
                body.append(_runs_to_latex(block))
            continue

        if not text:
            close_lists()
            continue
        if style_name in _TITLE_NAMES:
            flush_figure(None)
            title = tex_escape(text)
            continue
        if style_name.startswith(_CAPTION_PREFIXES) and pending_figure:
            flush_figure(tex_escape(text))
            continue

        heading = _outline_level(block)
        if heading is None:
            heading = _heading_level_by_name(style_name)
        if heading is not None:
            close_lists()
            flush_figure(None)
            command = _SECTIONING[heading]
            body.append(f"\\{command}{{{tex_escape(text)}}}")
            continue

        numbering = _paragraph_numbering(block)
        if numbering is not None:
            flush_figure(None)
            num_id, ilvl = numbering
            fmt = numbering_formats.get((num_id, ilvl), "")
            if fmt == "bullet":
                kind = "itemize"
            elif fmt:
                kind = "enumerate"
            else:
                kind = "enumerate" if _is_numbered_style(style_name) else "itemize"
            list_item(block, ilvl, kind)
            continue
        if _is_list_style(style_name):
            flush_figure(None)
            kind = "enumerate" if _is_numbered_style(style_name) else "itemize"
            list_item(block, 0, kind)
            continue

        close_lists()
        flush_figure(None)
        body.append(_runs_to_latex(block))
    close_lists()
    flush_figure(None)

    latex = (
        _PREAMBLE
        + f"\n\\title{{{title}}}\n\\author{{}}\n\\date{{\\today}}\n\n"
        + "\\begin{document}\n\\maketitle\n\n"
        + "\n\n".join(body)
        + "\n"
        + _CLOSING
    )
    return latex, collector.images
