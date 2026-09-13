"""Safe import of real, multi-file LaTeX projects."""

from __future__ import annotations

from pathlib import PurePosixPath
from zipfile import ZipInfo

from sixsentences_server.core.uploads import (
    UnsafeArchiveError,
    open_safe_zip,
    read_zip_member,
)


class LatexProjectImportError(ValueError):
    pass


_TEXT_SUFFIXES = {
    ".tex",
    ".latex",
    ".bib",
    ".sty",
    ".cls",
    ".bst",
    ".bbx",
    ".cbx",
    ".lbx",
    ".def",
    ".cfg",
    ".clo",
    ".dtx",
    ".ins",
    ".ist",
    ".csv",
    ".txt",
    ".md",
}
_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"}
_TEXT_FILENAMES = {
    "license",
    "license.txt",
    "license.md",
    "copying",
    "readme",
    "readme.txt",
    "readme.md",
}
_MAX_FILES = 200
_MAX_UNPACKED = 25_000_000


def safe_project_path(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or any(part in {"", ".", ".."} for part in path.parts):
        raise LatexProjectImportError("project contains an unsafe file path")
    return str(path)


def import_latex_project(
    blob: bytes, filename: str
) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, bytes]]]:
    """Return title, main source, editable files and binary assets.

    The detected entry point becomes ``main.tex``. If it lived in a folder,
    that folder becomes the project root so its relative ``\\input`` and image
    paths continue to work.
    """
    lower = filename.lower()
    if lower.endswith((".tex", ".latex")):
        try:
            source = blob.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LatexProjectImportError("LaTeX source must be UTF-8") from exc
        title = PurePosixPath(filename).stem.replace("-", " ").replace("_", " ")
        return title or "Imported project", source, [], []
    if not lower.endswith(".zip"):
        raise LatexProjectImportError("upload a .tex file or a .zip project")
    try:
        archive = open_safe_zip(
            blob,
            max_files=_MAX_FILES,
            max_uncompressed_bytes=_MAX_UNPACKED,
            max_member_bytes=_MAX_UNPACKED,
        )
        members: list[ZipInfo] = [item for item in archive.infolist() if not item.is_dir()]
        payloads: dict[str, bytes] = {}
        for item in members:
            path = safe_project_path(item.filename)
            if path.startswith("__MACOSX/") or PurePosixPath(path).name.startswith("."):
                continue
            payloads[path] = read_zip_member(archive, item, max_bytes=_MAX_UNPACKED)
        archive.close()
    except UnsafeArchiveError as exc:
        raise LatexProjectImportError(
            "project is not a safe ZIP (200 files and 25 MB expanded maximum)"
        ) from exc
    tex_paths = [
        path for path in payloads if PurePosixPath(path).suffix.lower() in {".tex", ".latex"}
    ]
    if not tex_paths:
        raise LatexProjectImportError("the ZIP project contains no .tex file")
    decoded: dict[str, str] = {}
    for path in tex_paths:
        try:
            decoded[path] = payloads[path].decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
    entry = next((p for p in tex_paths if PurePosixPath(p).name.lower() == "main.tex"), None)
    entry = entry or next((p for p, text in decoded.items() if "\\documentclass" in text), None)
    if entry is None or entry not in decoded:
        raise LatexProjectImportError("could not find a UTF-8 LaTeX entry file")
    root = str(PurePosixPath(entry).parent)
    prefix = "" if root == "." else root + "/"
    files: list[tuple[str, str]] = []
    assets: list[tuple[str, bytes]] = []
    for original, data in payloads.items():
        if prefix and not original.startswith(prefix):
            continue
        relative = original[len(prefix) :] if prefix else original
        relative = safe_project_path(relative)
        if original == entry:
            continue
        suffix = PurePosixPath(relative).suffix.lower()
        if suffix in _TEXT_SUFFIXES or PurePosixPath(relative).name.lower() in _TEXT_FILENAMES:
            try:
                files.append((relative, data.decode("utf-8-sig")))
            except UnicodeDecodeError:
                continue
        elif suffix in _ASSET_SUFFIXES:
            assets.append((relative, data))
    project_name = PurePosixPath(filename).stem.replace("-", " ").replace("_", " ")
    return project_name or "Imported project", decoded[entry], files, assets
