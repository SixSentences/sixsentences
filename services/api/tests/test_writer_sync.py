from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sixsentences_server.llm.base import LLMCancelledError
from sixsentences_server.writer.service import (
    SynctexUnavailableError,
    _resolve_project_graphic_extensions,
    _with_tectonic_graphics_compatibility,
    _with_unique_nested_asset_aliases,
    compile_document,
    synctex_forward,
    synctex_inverse,
)


def test_compile_assets_add_unique_nested_basename_aliases() -> None:
    files = {
        "figs/clouds.jpg": b"clouds",
        "figs/chart.pdf": b"chart",
        "logo.svg": b"logo",
    }

    aliased = _with_unique_nested_asset_aliases(files)

    assert aliased["figs/clouds.jpg"] == b"clouds"
    assert aliased["clouds.jpg"] == b"clouds"
    assert aliased["chart.pdf"] == b"chart"
    assert aliased["logo.svg"] == b"logo"


def test_compile_assets_do_not_alias_ambiguous_basenames() -> None:
    files = {
        "figs/result.pdf": b"first",
        "appendix/result.pdf": b"second",
    }

    aliased = _with_unique_nested_asset_aliases(files)

    assert "result.pdf" not in aliased
    assert aliased == files


def test_compile_source_resolves_known_extensionless_graphics() -> None:
    files = _with_unique_nested_asset_aliases(
        {
            "figs/clouds.jpg": b"clouds",
            "diamondrule.pdf": b"rule",
        }
    )
    source = (
        "\\includegraphics[width=\\linewidth, alt={Cloud cover}]{clouds}\n"
        "\\includegraphics{diamondrule}\n"
        "\\includegraphics{example-image-a}\n"
    )

    resolved = _resolve_project_graphic_extensions(source, files)

    assert "{clouds.jpg}" in resolved
    assert "{diamondrule.pdf}" in resolved
    assert "{example-image-a}" in resolved


def test_compile_source_preserves_explicit_and_ambiguous_graphics() -> None:
    files = {
        "one/result.pdf": b"first",
        "two/result.png": b"second",
    }
    source = "\\includegraphics{result}\n\\includegraphics{one/result.pdf}\n"

    assert _resolve_project_graphic_extensions(source, files) == source


def test_tectonic_graphics_compatibility_is_applied_after_preamble() -> None:
    source = (
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "Body\n"
        "\\end{document}\n"
    )

    compatible = _with_tectonic_graphics_compatibility(source)

    assert (
        "\\usepackage{graphicx}\n"
        "\\DeclareGraphicsExtensions{.pdf,.png,.jpg,.jpeg}\n"
        "\\begin{document}"
    ) in compatible


def test_tectonic_graphics_compatibility_leaves_text_only_documents_valid() -> None:
    source = "\\documentclass{article}\\begin{document}Body\\end{document}"

    assert _with_tectonic_graphics_compatibility(source) == source


def test_compile_uses_writable_xdg_cache_for_tectonic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tectonic_cache = tmp_path / "tectonic-cache"
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        Path(str(kwargs["cwd"]), "main.pdf").write_bytes(b"%PDF-1.4")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("TECTONIC_CACHE_DIR", str(tectonic_cache))
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = compile_document(
        "\\documentclass{article}\\begin{document}ok\\end{document}",
        "",
        command="tectonic",
        files={"generated-figure.png": b"\x89PNG\r\n\x1a\n"},
    )

    assert result.ok
    assert tectonic_cache.is_dir()
    assert isinstance(seen["env"], dict)
    assert seen["env"]["XDG_CACHE_HOME"] == str(tectonic_cache)  # type: ignore[index]


def test_candidate_compile_honors_agent_cancellation() -> None:
    with pytest.raises(LLMCancelledError):
        compile_document(
            "\\documentclass{article}\\begin{document}ok\\end{document}",
            "",
            command=f'{sys.executable} -c "import time; time.sleep(30)"',
            cancel_check=lambda: True,
            timeout_seconds=5,
        )


@pytest.mark.parametrize(
    ("operation", "kwargs"),
    [
        (
            synctex_forward,
            {"pdf": b"pdf", "synctex": b"sync", "path": "main.tex", "line": 1},
        ),
        (
            synctex_inverse,
            {"pdf": b"pdf", "synctex": b"sync", "page": 1, "x": 1.0, "y": 1.0},
        ),
    ],
)
def test_synctex_missing_binary_is_actionable(
    monkeypatch: pytest.MonkeyPatch, operation: object, kwargs: dict[str, object]
) -> None:
    def missing_binary(*args: object, **run_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("synctex")

    monkeypatch.setattr(subprocess, "run", missing_binary)

    with pytest.raises(SynctexUnavailableError, match="not installed"):
        operation(**kwargs)  # type: ignore[operator]
