"""Writer compile service and document templates.

Compilation shells out to the configured LaTeX engine (SIX_TECTONIC_CMD,
default `tectonic` — the production stack points it at the tectonic
container). The engine runs in a throwaway temp directory containing exactly
main.tex and references.bib; stdout/stderr are parsed into friendly error
entries so the editor can show "line 12: Undefined control sequence" instead
of a TeX log wall. A missing engine degrades to an honest, actionable error.

Every template below is verified to compile cleanly with tectonic against an
empty references.bib, so the first compile of a fresh document always works.
"""

import contextlib
import gzip
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sixsentences_server.llm.base import LLMCancelledError

# generous: tectonic's FIRST run downloads LaTeX packages into its cache
_COMPILE_TIMEOUT = 300
# "! Undefined control sequence." followed (possibly later) by "l.12 ..."
_ERROR_LINE = re.compile(r"^! (.+)$", re.MULTILINE)
_LINE_NO = re.compile(r"^l\.(\d+)", re.MULTILINE)
# tectonic's own reporting: "error: main.tex:3: Undefined control sequence"
_TECTONIC_ERROR = re.compile(
    r"^error: (?:(?P<file>[^:\n]+?):(?P<line>\d+): )?(?P<message>.+)$",
    re.MULTILINE,
)
_INCLUDEGRAPHICS = re.compile(r"(\\includegraphics(?:\s*\[[^\]]*\])?\s*\{)([^{}\n]+)(\})")


@dataclass
class CompileResult:
    ok: bool
    pdf: bytes | None = None
    synctex: bytes | None = None
    errors: list[dict[str, object]] = field(default_factory=list)
    log_tail: str = ""


class SynctexUnavailableError(RuntimeError):
    """Raised when the source-to-PDF mapping utility is not installed."""


def _with_unique_nested_asset_aliases(
    files: dict[str, bytes],
) -> dict[str, bytes]:
    """Expose uniquely named nested assets at the compile root as well.

    Some publisher classes capture ``\\includegraphics`` content before a
    template's later ``\\graphicspath`` declaration is active. Keeping the
    original nested file and adding a root alias makes those templates
    compile without rewriting their source. Ambiguous basenames never receive
    an alias, so separate project directories cannot shadow one another.
    """
    aliases = dict(files)
    normalized: list[tuple[str, Path, bytes]] = []
    basename_counts: dict[str, int] = {}

    for name, blob in files.items():
        relative = Path(name.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            continue
        basename = relative.name
        normalized.append((name, relative, blob))
        basename_counts[basename] = basename_counts.get(basename, 0) + 1

    for _name, relative, blob in normalized:
        basename = relative.name
        if len(relative.parts) > 1 and basename_counts[basename] == 1 and basename not in aliases:
            aliases[basename] = blob
    return aliases


def _resolve_project_graphic_extensions(
    content: str,
    files: dict[str, bytes],
) -> str:
    """Add known extensions to extensionless project graphics at compile time.

    Publisher classes occasionally declare only EPS support when running
    through an XeTeX-based engine even though the project contains PDF, PNG,
    or JPEG assets. Explicit extensions avoid that class-level engine
    misdetection while leaving package-provided images and stored source
    untouched.
    """
    paths: list[Path] = []
    for name in files:
        relative = Path(name.replace("\\", "/"))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or not relative.suffix
        ):
            continue
        paths.append(relative)

    def replace(match: re.Match[str]) -> str:
        target_text = match.group(2).strip()
        target = Path(target_text.replace("\\", "/"))
        if target.suffix or target.is_absolute() or ".." in target.parts:
            return match.group(0)

        if len(target.parts) == 1:
            candidates = [
                path for path in paths if len(path.parts) == 1 and path.stem == target.name
            ]
        else:
            candidates = [path for path in paths if path.with_suffix("") == target]
        if len(candidates) != 1:
            return match.group(0)
        return f"{match.group(1)}{candidates[0].as_posix()}{match.group(3)}"

    return _INCLUDEGRAPHICS.sub(replace, content)


def _with_tectonic_graphics_compatibility(content: str) -> str:
    """Prefer formats supported by Tectonic after the preamble is loaded."""
    marker = "\\begin{document}"
    # ``\DeclareGraphicsExtensions`` is provided by graphicx. Injecting it
    # into a text-only article makes an otherwise valid minimal manuscript
    # fail with ``Undefined control sequence``. An explicit graphicx package
    # or an includegraphics command is enough evidence that the declaration
    # is available (publisher classes may load graphicx themselves).
    uses_graphics = "graphicx" in content or "\\includegraphics" in content
    if marker not in content or not uses_graphics:
        return content
    declaration = "\\DeclareGraphicsExtensions{.pdf,.png,.jpg,.jpeg}"
    return content.replace(marker, f"{declaration}\n{marker}", 1)


def parse_errors(log: str) -> list[dict[str, object]]:
    """Friendly error entries from either error dialect: classic TeX
    `! message` (+ nearest following `l.<n>`), and tectonic's own
    `error: file:line: message` reporting."""
    errors: list[dict[str, object]] = []
    for match in _ERROR_LINE.finditer(log):
        message = match.group(1).strip()
        line_match = _LINE_NO.search(log, match.end())
        errors.append(
            {
                "line": int(line_match.group(1)) if line_match else None,
                "message": message,
            }
        )
    for match in _TECTONIC_ERROR.finditer(log):
        errors.append(
            {
                "path": match.group("file") or None,
                "line": int(match.group("line")) if match.group("line") else None,
                "message": match.group("message").strip(),
            }
        )
    return errors[:20]


def compile_document(
    content: str,
    bib: str,
    *,
    command: str,
    files: dict[str, bytes] | None = None,
    text_files: dict[str, str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    timeout_seconds: int = _COMPILE_TIMEOUT,
) -> CompileResult:
    """Compile a complete project rooted at main.tex with the engine."""
    with tempfile.TemporaryDirectory(prefix="sixwriter-") as workdir:
        # macOS exposes /var through /private/var; the engine records the
        # canonical path in SyncTeX, so normalize the working root first.
        root = Path(workdir).resolve()
        argv = shlex.split(command)
        is_tectonic = bool(argv and "tectonic" in Path(argv[0]).name)
        compile_files = _with_unique_nested_asset_aliases(files or {})
        compile_content = _resolve_project_graphic_extensions(content, compile_files)
        if is_tectonic:
            compile_content = _with_tectonic_graphics_compatibility(compile_content)
        (root / "main.tex").write_text(compile_content, encoding="utf-8")
        (root / "references.bib").write_text(bib, encoding="utf-8")
        for name, source in (text_files or {}).items():
            relative = Path(name.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                continue
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            compile_source = _resolve_project_graphic_extensions(source, compile_files)
            target.write_text(compile_source, encoding="utf-8")
        for name, blob in compile_files.items():
            relative = Path(name.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                continue
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        try:
            process_env: dict[str, str] | None = None
            if is_tectonic:
                argv.extend(["--synctex", "--keep-intermediates"])
                # Tectonic resolves its writable bundle cache through the
                # platform XDG directory. ``TECTONIC_CACHE_DIR`` alone is not
                # sufficient for every code path, which made production
                # compiles fail with EROFS inside the intentionally read-only
                # worker container. Keep the persistent operator cache when it
                # is configured and otherwise isolate it with this compile.
                process_env = os.environ.copy()
                cache_home = process_env.get("XDG_CACHE_HOME") or process_env.get(
                    "TECTONIC_CACHE_DIR"
                )
                if not cache_home:
                    cache_home = str(root / ".cache")
                Path(cache_home).mkdir(parents=True, exist_ok=True)
                process_env["XDG_CACHE_HOME"] = cache_home
            compile_argv = [*argv, "main.tex"]
            if cancel_check is None:
                completed = subprocess.run(  # noqa: S603 - operator-configured command
                    compile_argv,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    env=process_env,
                )
            else:
                completed = _run_cancellable_compile(
                    compile_argv,
                    cwd=root,
                    env=process_env,
                    cancel_check=cancel_check,
                    timeout_seconds=timeout_seconds,
                )
        except FileNotFoundError:
            return CompileResult(
                ok=False,
                errors=[
                    {
                        "line": None,
                        "message": "No LaTeX engine on this host. Install "
                        "tectonic (or set SIX_TECTONIC_CMD) and compile again.",
                    }
                ],
                log_tail="engine not found",
            )
        except subprocess.TimeoutExpired:
            return CompileResult(
                ok=False,
                errors=[
                    {
                        "line": None,
                        "message": f"Compilation timed out after {timeout_seconds}s.",
                    }
                ],
                log_tail="timeout",
            )
        log = (completed.stdout or "") + "\n" + (completed.stderr or "")
        pdf_path = root / "main.pdf"
        if completed.returncode == 0 and pdf_path.exists():
            synctex_path = root / "main.synctex.gz"
            normalized_synctex: bytes | None = None
            if synctex_path.exists():
                try:
                    synctex_text = gzip.decompress(synctex_path.read_bytes()).decode(
                        "utf-8", errors="replace"
                    )
                    # The compile directory is disposable. Relative Input records let
                    # later forward/inverse lookups rehydrate the project anywhere.
                    synctex_text = synctex_text.replace(f"{root}/", "")
                    normalized_synctex = gzip.compress(synctex_text.encode("utf-8"))
                except OSError:
                    normalized_synctex = None
            return CompileResult(
                ok=True,
                pdf=pdf_path.read_bytes(),
                synctex=normalized_synctex,
                log_tail=log[-2000:],
            )
        errors = parse_errors(log) or [
            {"line": None, "message": "Compilation failed; see the log."}
        ]
        return CompileResult(ok=False, errors=errors, log_tail=log[-2000:])


def _run_cancellable_compile(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    cancel_check: Callable[[], bool],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run the LaTeX process while honoring the agent turn's stop signal."""

    process = subprocess.Popen(  # noqa: S603 - operator-configured command
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=os.name != "nt",
    )
    started_at = time.monotonic()
    while True:
        if cancel_check():
            _stop_compile_process(process)
            raise LLMCancelledError("manuscript candidate compilation cancelled")
        elapsed = time.monotonic() - started_at
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            _stop_compile_process(process)
            raise subprocess.TimeoutExpired(argv, timeout_seconds)
        try:
            stdout, stderr = process.communicate(timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            continue
        return subprocess.CompletedProcess(
            argv,
            process.returncode,
            stdout,
            stderr,
        )


def _stop_compile_process(process: subprocess.Popen[str]) -> None:
    """Stop only the exact compile process tree and reap it promptly."""

    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name != "nt":
                with contextlib.suppress(OSError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=2)


def _synctex_fields(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    allowed = {
        "Output",
        "Page",
        "Input",
        "Line",
        "Column",
        "x",
        "y",
        "h",
        "v",
        "W",
        "H",
    }
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in allowed:
            fields.setdefault(key, value.strip())
    return fields


def synctex_forward(
    *, pdf: bytes, synctex: bytes, path: str, line: int, column: int = 1
) -> dict[str, Any] | None:
    """Map a source location to the first matching PDF rectangle."""

    with tempfile.TemporaryDirectory(prefix="sixwriter-sync-") as workdir:
        root = Path(workdir)
        (root / "main.pdf").write_bytes(pdf)
        (root / "main.synctex.gz").write_bytes(synctex)
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        try:
            completed = subprocess.run(  # noqa: S603 - fixed local utility
                [
                    "synctex",
                    "view",
                    "-i",
                    f"{max(line, 1)}:{max(column, 1)}:{path}",
                    "-o",
                    str(root / "main.pdf"),
                    "-d",
                    str(root),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError as exc:
            raise SynctexUnavailableError("the SyncTeX utility is not installed") from exc
        if completed.returncode != 0:
            return None
        fields = _synctex_fields(completed.stdout)
        if "Page" not in fields:
            return None
        return {
            "page": int(fields["Page"]),
            "x": float(fields.get("x", "0")),
            "y": float(fields.get("y", "0")),
            "width": float(fields.get("W", "0")),
            "height": float(fields.get("H", "0")),
        }


def synctex_inverse(
    *, pdf: bytes, synctex: bytes, page: int, x: float, y: float
) -> dict[str, Any] | None:
    """Map a PDF coordinate in big points back to a project source line."""

    with tempfile.TemporaryDirectory(prefix="sixwriter-sync-") as workdir:
        root = Path(workdir)
        (root / "main.pdf").write_bytes(pdf)
        (root / "main.synctex.gz").write_bytes(synctex)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed local utility
                [
                    "synctex",
                    "edit",
                    "-o",
                    f"{max(page, 1)}:{x}:{y}:{root / 'main.pdf'}",
                    "-d",
                    str(root),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError as exc:
            raise SynctexUnavailableError("the SyncTeX utility is not installed") from exc
        if completed.returncode != 0:
            return None
        fields = _synctex_fields(completed.stdout)
        if "Input" not in fields or "Line" not in fields:
            return None
        input_path = fields["Input"].replace("\\", "/")
        return {
            "path": input_path.rsplit("/", 1)[-1]
            if input_path.endswith("main.tex")
            else input_path,
            "line": int(fields["Line"]),
            "column": int(fields.get("Column", "0")),
        }


# -- templates ---------------------------------------------------------------
#
# Design rules: every template compiles on the first click (no empty \citep{}
# that renders as "?"), carries the structure reviewers of that genre expect,
# and shows where SixSentences_ exports (methods paragraph, evidence table,
# PRISMA figure, references.bib) plug in.

ARTICLE_TEMPLATE = r"""\documentclass[twocolumn]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage[hidelinks]{hyperref}
\usepackage[capitalise]{cleveref}
\usepackage[numbers]{natbib}

\title{\textbf{A Simple Method that Holds Up:\\ Rewrite This Title with Your Claim}}
\author{Given Surname\\
  \small Institute, University\\
  \small \texttt{given.surname@university.edu}}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
\noindent Placeholder prose with the right shape. Practitioners face a
concrete problem, and the standard remedy is expensive. We propose a
lightweight alternative that needs no extra supervision. Across three
benchmarks it improves the primary metric from 0.71 to 0.79 while halving
compute. The gain persists under distribution shift, which suggests the
mechanism, not tuning, does the work.
\end{abstract}

\section{Introduction}\label{sec:intro}
Open with the problem, not the field. A reader should finish this
paragraph knowing who is blocked by the problem and what breaks without a
solution. The second paragraph states the gap: prior remedies either
demand labeled data or degrade sharply outside the training domain.

Our contributions:
\begin{itemize}
  \item A method that removes the expensive step, stated here as a claim.
  \item Evidence across three benchmarks and two shift conditions
    (\cref{tab:results}, \cref{fig:ablation}).
  \item An ablation isolating which component carries the gain.
\end{itemize}
Cite with \verb|\citep{key}| or \verb|\citet{key}| using the keys from the
Cite menu; they resolve against the live references.bib of your linked
searches.

\section{Related Work}
This section grows out of your SixSentences\_ include set. Organize by
theme, not by paper: one paragraph per line of work, closed by the open
question your method answers. Every claim about a prior result carries a
citation from the include set, so the survey stays defensible.

\section{Method}\label{sec:method}
State the setup in two sentences, then the objective. Equations get
numbers only when referenced:
\begin{equation}
  \mathcal{L} = \mathcal{L}_{\mathrm{task}} + \lambda\,\mathcal{L}_{\mathrm{reg}}.
  \label{eq:loss}
\end{equation}
\Cref{eq:loss} balances the task objective against regularization; the
ablation in \cref{fig:ablation} sweeps $\lambda$ and shows a broad
plateau, so the method is not a tuning artifact.

\section{Experiments}\label{sec:experiments}
Describe data, baselines, metrics and seeds in one paragraph, then let
\cref{tab:results} carry the numbers.

\begin{table}[t]
  \centering
  \caption{Main results, placeholder numbers. Best in bold; mean over
    three seeds.}
  \label{tab:results}
  \begin{tabular}{lcc}
    \toprule
    Method & Metric A & Metric B \\
    \midrule
    Baseline & 0.71 & 0.63 \\
    Strong variant & 0.74 & 0.66 \\
    Ours & \textbf{0.79} & \textbf{0.70} \\
    \bottomrule
  \end{tabular}
\end{table}

\begin{figure}[t]
  \centering
  \begin{tikzpicture}
    \begin{axis}[
      width=\linewidth, height=4.4cm,
      xlabel={$\lambda$}, ylabel={Metric A},
      ymin=0.6, ymax=0.85,
      axis lines*=left, tick style={draw=none},
      grid=major, grid style={black!8},
    ]
      \addplot[thick, black, mark=*, mark size=1.6pt]
        coordinates {(0,0.71) (0.1,0.76) (0.2,0.79) (0.3,0.78) (0.4,0.74)};
    \end{axis}
  \end{tikzpicture}
  \caption{Placeholder ablation: the gain is stable across a broad range
    of $\lambda$, so no fine tuning is required.}
  \label{fig:ablation}
\end{figure}

% Uploaded figures join every compile; embed them with
% \includegraphics[width=\linewidth]{figure.png} inside a figure block.

\section{Discussion}
One honest paragraph on where the method fails, one on what the result
means for practice. Reviewers trust papers that name their limits.

\section{Conclusion}
Restate the claim, now backed by \cref{sec:experiments}, and the one
follow-up question this result makes urgent.

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

REVIEW_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage[hidelinks]{hyperref}
\usepackage[capitalise]{cleveref}
\usepackage[numbers]{natbib}

\title{A Systematic Literature Review of \ldots}
\author{Given Surname\\ \small Institute, University}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
\noindent Structured, PRISMA style, filled with placeholder prose.
\textbf{Background:} the intervention is widely adopted while its evidence
base remains scattered. \textbf{Objective:} synthesize what controlled
studies report about its effectiveness. \textbf{Methods:} four databases
searched from 2015 to today under a protocol frozen before the first
query; two-stage screening with logged reasons. \textbf{Results:} of
1{,}284 records identified, 1{,}102 were screened and 43 included; effects
are consistently positive but small, and reporting quality varies widely.
\textbf{Conclusions:} the evidence supports cautious adoption and clearly
maps where new primary studies are needed.
\end{abstract}

\section{Introduction}
Two paragraphs of placeholder argument: why this synthesis is needed now,
and what existing reviews miss (outdated search window, narrower scope, no
protocol). Close with the review questions.

\subsection{Research questions}
\begin{itemize}
  \item[RQ1] What effects does the intervention show in controlled studies?
  \item[RQ2] Which study characteristics moderate those effects?
\end{itemize}

\section{Method}
Report the search to PRISMA 2020. SixSentences\_ generates the pieces:
Export gives the methods paragraph (databases, query, dates, dedup rule),
the PRISMA flow diagram (\cref{tab:flow}), and the PRISMA-S search
appendix for the supplement.

\subsection{Eligibility criteria}
Placeholder criteria, as applied during screening: peer-reviewed,
empirical, English or German, published 2015 or later; excluded are
position papers and studies without a comparison condition.

\subsection{Search strategy}
% Paste the exported methods paragraph here; it documents databases,
% query strings, run dates and the deduplication rule.

\subsection{Study selection}
\begin{table}[t]
  \centering
  \caption{Flow of records through the review (placeholder numbers; the
    exported PRISMA figure replaces or accompanies this table).}
  \label{tab:flow}
  \begin{tabular}{lr}
    \toprule
    Stage & Records \\
    \midrule
    Identified & 1{,}284 \\
    After deduplication & 1{,}102 \\
    Screened at title and abstract & 1{,}102 \\
    Excluded with logged reason & 1{,}041 \\
    Assessed at full text & 61 \\
    \textbf{Included} & \textbf{43} \\
    \bottomrule
  \end{tabular}
\end{table}

\subsection{Data extraction}
Which fields were extracted from each included study, and by whom. The
evidence table export drops a ready booktabs table of these fields.

\section{Results}
\Cref{fig:years} summarizes the shape of the evidence base; the sections
below synthesize by research question, citing included studies with
\verb|\citep{key}|.

\begin{figure}[t]
  \centering
  \begin{tikzpicture}
    \begin{axis}[
      width=.9\linewidth, height=4.2cm,
      ybar, bar width=11pt,
      ymin=0,
      symbolic x coords={2019, 2020, 2021, 2022, 2023, 2024},
      xtick=data,
      ylabel={Included studies},
      nodes near coords,
      every node near coord/.append style={font=\footnotesize},
      axis lines*=left, tick style={draw=none},
    ]
      \addplot[ybar, fill=black!70, draw=none]
        coordinates {(2019,3) (2020,5) (2021,7) (2022,9) (2023,11) (2024,8)};
    \end{axis}
  \end{tikzpicture}
  \caption{Included studies by publication year (placeholder counts).}
  \label{fig:years}
\end{figure}

\section{Discussion}
What the synthesis settles, what stays contested, and how the findings
relate to the reviews this one supersedes.

\section{Threats to validity}
Search coverage, screening reliability, publication bias; note that the
frozen protocol and the logged per-record reasons bound the first two.

\section{Conclusion}

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

THESIS_TEMPLATE = r"""\documentclass[11pt,a4paper,oneside]{report}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage[margin=2.5cm]{geometry}
\usepackage{setspace}
\usepackage[hidelinks]{hyperref}
\usepackage[capitalise]{cleveref}
\usepackage[numbers]{natbib}

\begin{document}

\begin{titlepage}
  \centering
  {\scshape\large University Name\\ Faculty of \ldots\par}
  \vspace{3cm}
  {\huge\bfseries Thesis Title\par}
  \vspace{1cm}
  {\large A thesis submitted for the degree of\\ Master of Science\par}
  \vspace{2cm}
  {\Large Given Surname\par}
  \vfill
  Supervisor: Prof.\ Dr.\ \ldots\\
  Second examiner: \ldots
  \vspace{1cm}
  {\large \today\par}
\end{titlepage}

\begin{abstract}
\noindent One page at most, placeholder shape: the context in two
sentences, the gap one prior approach leaves open, the approach this
thesis takes, the headline result with a number, and what follows from it
for the field.
\end{abstract}

\tableofcontents

\chapter{Introduction}
\section{Motivation}
Placeholder prose: name the concrete situation in which the problem
appears and who is blocked by it. One paragraph, no survey.

\section{Problem statement}
The precise technical problem, stated so that \cref{ch:evaluation} can
verifiably answer whether it was solved.

\section{Research questions}
\begin{itemize}
  \item[RQ1] Does the proposed approach improve the primary metric over
    the strongest baseline?
  \item[RQ2] Which of its components carries the improvement?
\end{itemize}

\section{Outline}
One sentence per chapter, written last.

\chapter{Background}
Concepts and notation the reader needs; no survey yet. Definitions get
numbered equations only when later chapters reference them:
\begin{equation}
  f_\theta : \mathcal{X} \to \mathcal{Y}, \qquad
  \hat{y} = f_\theta(x).
  \label{eq:model}
\end{equation}

\chapter{Related Work}
Grounded in your SixSentences\_ search: the methods paragraph documents
how the literature was collected, and every claim cites the include set
with \verb|\citep{key}|. Organize by theme; close each section with the
open question that motivates this thesis.

\chapter{Method}
The approach, presented so a fellow student could reimplement it from
this chapter alone.

\chapter{Evaluation}\label{ch:evaluation}
\section{Setup}
Data, baselines, metrics, seeds and hardware in one honest page.

\section{Results}
\Cref{tab:thesis-results} carries the comparison, \cref{fig:thesis-abl}
the ablation; the text states what they mean, not what they contain.

\begin{table}[t]
  \centering
  \caption{Main comparison (placeholder numbers, mean over three seeds).}
  \label{tab:thesis-results}
  \begin{tabular}{lccc}
    \toprule
    Method & Metric A & Metric B & Runtime (min) \\
    \midrule
    Baseline & 0.71 & 0.63 & 118 \\
    Proposed & \textbf{0.79} & \textbf{0.70} & \textbf{54} \\
    \bottomrule
  \end{tabular}
\end{table}

\begin{figure}[t]
  \centering
  \begin{tikzpicture}
    \begin{axis}[
      width=.7\linewidth, height=5cm,
      ybar, bar width=14pt,
      ymin=0, ymax=1,
      symbolic x coords={Full, Without A, Without B},
      xtick=data,
      ylabel={Metric A},
      nodes near coords,
      every node near coord/.append style={font=\footnotesize},
      axis lines*=left, tick style={draw=none},
    ]
      \addplot[ybar, fill=black!70, draw=none]
        coordinates {(Full,0.79) (Without A,0.72) (Without B,0.75)};
    \end{axis}
  \end{tikzpicture}
  \caption{Ablation (placeholder): removing component A costs the most,
    so it carries the improvement claimed in RQ2.}
  \label{fig:thesis-abl}
\end{figure}

\section{Discussion}

\chapter{Conclusion}
\section{Summary}
\section{Limitations}
\section{Future work}

\appendix
\chapter{Supplementary Material}
% PRISMA-S search appendix, extra tables, proofs.

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

BLANK_TEMPLATE = r"""\documentclass{article}
\usepackage[numbers]{natbib}

\begin{document}

Start writing.

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

IEEE_TEMPLATE = r"""\documentclass[conference]{IEEEtran}
\IEEEoverridecommandlockouts
% The official IEEE conference skeleton (bare_conf). references.bib is fed
% live from your linked SixSentences_ searches. natbib + IEEEtranN renders
% identical [1]-style citations while keeping \citep from the Cite menu
% working (and it survives an empty bibliography, unlike the cite package).
\usepackage[numbers]{natbib}
\usepackage{amsmath,amssymb,amsfonts}
\usepackage{algorithmic}
\usepackage{graphicx}
\usepackage{textcomp}
\usepackage{xcolor}
\usepackage{booktabs}
\def\BibTeX{{\rm B\kern-.05em{\sc i\kern-.025em b}\kern-.08em
    T\kern-.1667em\lower.7ex\hbox{E}\kern-.125emX}}

\begin{document}

\title{Conference Paper Title*\\
{\footnotesize \textsuperscript{*}Note: Sub-titles are not captured in
Xplore and should not be used}}

\author{\IEEEauthorblockN{1\textsuperscript{st} Given Name Surname}
\IEEEauthorblockA{\textit{dept. name of organization} \\
\textit{name of organization}\\
City, Country \\
email address or ORCID}
\and
\IEEEauthorblockN{2\textsuperscript{nd} Given Name Surname}
\IEEEauthorblockA{\textit{dept. name of organization} \\
\textit{name of organization}\\
City, Country \\
email address or ORCID}
}

\maketitle

\begin{abstract}
Placeholder abstract with the expected shape: deployed systems face a
concrete problem; existing remedies trade accuracy for cost. We propose a
method that avoids the trade, evaluate it on three public benchmarks, and
improve the primary metric from 0.71 to 0.79 at half the runtime. Results
are stable across seeds and under moderate distribution shift.
\end{abstract}

\begin{IEEEkeywords}
placeholder, keywords, five, comma, separated
\end{IEEEkeywords}

\section{Introduction}
Motivate the problem and preview the contribution in three paragraphs:
the problem and its cost, the gap prior work leaves, and what this paper
adds. Close with a one-sentence roadmap.

\section{Related Work}
This section grows out of your SixSentences\_ search; cite the include
set with \verb|\citep{key}|. Group prior work by approach and end each
group with the limitation your method addresses.

\section{Method}
The setup and objective, with equations numbered only when referenced:
\begin{equation}
  \hat{y} = \arg\max_{y} \; p_\theta(y \mid x).
  \label{eq:decision}
\end{equation}

\section{Evaluation}
\subsection{Setup}
Data, baselines, metrics and seeds in one paragraph each.

\subsection{Results}
Table~\ref{tab:ieee-results} reports the main comparison; the prose
states the takeaway, not the numbers.

\begin{table}[t]
  \centering
  \caption{Main results (placeholder numbers, mean over three seeds).}
  \label{tab:ieee-results}
  \begin{tabular}{lcc}
    \toprule
    Method & Metric A & Metric B \\
    \midrule
    Baseline & 0.71 & 0.63 \\
    Proposed & \textbf{0.79} & \textbf{0.70} \\
    \bottomrule
  \end{tabular}
\end{table}

\section{Conclusion}
The claim, the evidence for it, and the one open problem this result
exposes.

\section*{Acknowledgment}

\bibliographystyle{IEEEtranN}
\bibliography{references}

\end{document}
"""

REPORT_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath}
\usepackage[margin=2.5cm]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage[capitalise]{cleveref}
\usepackage[numbers]{natbib}

\title{Seminar Report: Topic}
\author{Given Surname\\
  \small Course Name, Winter/Summer Term 20XX\\
  \small Matriculation no.\ 000000 \quad Advisor: \ldots}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
\noindent Placeholder, three sentences: the report surveys a family of
approaches to a course-relevant problem, compares them along three
criteria drawn from the literature, and finds that the simplest approach
remains competitive except under scale.
\end{abstract}

\section{Introduction}
What the report covers, why the topic matters for the course, and the
question the comparison answers. Keep it to half a page.

\section{Background}
The concepts a fellow student needs to follow the rest, each introduced
in two or three sentences. Cite with \verb|\citep{key}| from your linked
search.

\section{Main Part}
\subsection{Approach}
How the surveyed papers were selected and along which criteria they are
compared; the linked search documents the selection.

\subsection{Findings}
\Cref{tab:survey} condenses the comparison; the text walks through the
rows and names the pattern.

\begin{table}[t]
  \centering
  \caption{Comparison of the surveyed approaches (placeholder rows).}
  \label{tab:survey}
  \begin{tabular}{llll}
    \toprule
    Approach & Core idea & Strength & Weakness \\
    \midrule
    Family A & Filter first & Simple, fast & Misses rare cases \\
    Family B & Learn end to end & Best accuracy & Data hungry \\
    Family C & Hybrid pipeline & Robust & Hard to tune \\
    \bottomrule
  \end{tabular}
\end{table}

\section{Discussion}
What is settled, what is contested, what surprised you while reading.

\section{Conclusion}
The one-paragraph answer to the question from the introduction.

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

BEAMER_TEMPLATE = r"""\documentclass[aspectratio=169]{beamer}
\usetheme{metropolis}
\usepackage{booktabs}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage[numbers]{natbib}

\title{Talk Title}
\subtitle{One line that frames the story}
\author{Given Surname}
\institute{Institute, University}
\date{\today}

\begin{document}

\maketitle

\begin{frame}{Agenda}
  \tableofcontents
\end{frame}

\section{Motivation}

\begin{frame}{The problem in one slide}
  \begin{itemize}
    \item Placeholder bullets that already argue: the problem costs real
      time or money today
    \item The standard remedy trades accuracy for cost
    \item This talk shows a way to keep both
  \end{itemize}
\end{frame}

\section{Background}

\begin{frame}{What the literature says}
  % cite with \citep{key} using keys from the Cite menu
  Grounded in your SixSentences\_ search: three lines of prior work, one
  open question.
  \begin{block}{Key insight from prior work}
    The expensive step exists only to compensate for a missing signal.
  \end{block}
\end{frame}

\section{Method}

\begin{frame}{Approach}
  The idea in one sentence, then the pipeline in one picture.
  % One diagram beats three bullet lists: upload a figure and
  % \includegraphics[width=.8\linewidth]{figure.png}
\end{frame}

\section{Results}

\begin{frame}{Main result}
  \begin{columns}
    \begin{column}{0.48\textwidth}
      \begin{table}
        \centering
        \begin{tabular}{lcc}
          \toprule
          Method & Metric A & Metric B \\
          \midrule
          Baseline & 0.71 & 0.63 \\
          Ours & \textbf{0.79} & \textbf{0.70} \\
          \bottomrule
        \end{tabular}
      \end{table}
    \end{column}
    \begin{column}{0.48\textwidth}
      \begin{tikzpicture}
        \begin{axis}[
          width=\textwidth, height=4.6cm,
          ybar, bar width=12pt,
          ymin=0, ymax=1,
          symbolic x coords={Baseline, Ours},
          xtick=data,
          ylabel={Metric A},
          nodes near coords,
          axis lines*=left, tick style={draw=none},
        ]
          \addplot[ybar, fill=black!70, draw=none]
            coordinates {(Baseline,0.71) (Ours,0.79)};
        \end{axis}
      \end{tikzpicture}
    \end{column}
  \end{columns}
\end{frame}

\begin{frame}[standout]
  Takeaway in one sentence.
\end{frame}

\begin{frame}[allowframebreaks]{References}
  \bibliographystyle{plainnat}
  \bibliography{references}
\end{frame}

\end{document}
"""

PROPOSAL_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{booktabs}
\usepackage[margin=2.5cm]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage[capitalise]{cleveref}
\usepackage[numbers]{natbib}

\title{Research Proposal: \ldots}
\author{Given Surname\\ \small Supervisor: Prof.\ Dr.\ \ldots}
\date{\today}

\begin{document}
\maketitle

\section{Problem Statement}
Placeholder prose: the concrete problem, who has it, and why current
solutions fall short. Two paragraphs, no history lesson.

\section{State of the Art}
What the literature already answers and the precise gap. Cite from your
linked search with \verb|\citep{key}|; the exported methods paragraph
documents that the gap claim rests on a systematic search, not vibes.

\section{Research Questions and Hypotheses}
\begin{itemize}
  \item[RQ1] Does the proposed approach outperform the strongest
    published baseline on the primary metric?
  \item[RQ2] Which component of the approach carries the effect?
\end{itemize}
\noindent Hypothesis H1: the approach improves the primary metric by at
least five points; H2: removing its core component erases the gain.

\section{Method and Work Plan}
Design, data and analysis in one paragraph each; \cref{tab:workplan}
maps each research question to a work package and a deliverable.

\begin{table}[h]
  \centering
  \caption{Work packages (placeholder).}
  \label{tab:workplan}
  \begin{tabular}{llll}
    \toprule
    WP & Answers & Months & Deliverable \\
    \midrule
    Literature and protocol & RQ1, RQ2 & 1--2 & Review protocol, related work \\
    Implementation & RQ1 & 3--5 & Prototype \\
    Evaluation & RQ1, RQ2 & 6--7 & Experiment results \\
    Writing & all & 8--9 & Thesis draft \\
    \bottomrule
  \end{tabular}
\end{table}

\section{Expected Contribution}
One paragraph naming the artifact, the evidence and the audience.

\section{Risks and Mitigations}
The two most likely failure modes and the concrete fallback for each.

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

ABSTRACT_TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage[margin=2.5cm]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage[numbers]{natbib}

\title{Extended Abstract: \ldots}
\author{Given Surname \and Second Author}
\date{}

\begin{document}
\maketitle

\noindent\textbf{Keywords:} three, comma-separated, terms

\medskip
\noindent\textbf{Motivation.} Placeholder: practitioners repeat an
expensive manual step because no tool is trusted to take it over, and the
cost compounds with every project.

\medskip
\noindent\textbf{Related work.} Prior systems automate the step but were
never evaluated against the manual gold standard; that gap is documented
by the linked systematic search (cite with \verb|\citep{key}|).

\medskip
\noindent\textbf{Method.} We evaluate the proposed system against expert
annotations on three public datasets, reporting agreement and cost per
processed item over three runs.

\medskip
\noindent\textbf{Results.} The system reaches 0.92 agreement with experts
at one tenth of the cost; disagreements concentrate in one identifiable
category, which suggests a targeted human-in-the-loop fix.

\medskip
\noindent\textbf{Significance.} If the pattern holds beyond these
datasets, the manual step becomes a review task rather than a bottleneck;
the next study tests exactly that.

\bibliographystyle{plainnat}
\bibliography{references}

\end{document}
"""

POSTER_TEMPLATE = r"""\documentclass[final]{beamer}
\usepackage[orientation=portrait,size=a0,scale=1.4]{beamerposter}
\usetheme{metropolis}
\usepackage{booktabs}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage[numbers]{natbib}

\title{Poster Title: The Finding in One Line}
\author{Given Surname, Co Author}
\institute{Institute, University}

\begin{document}
\begin{frame}[fragile]
  \begin{columns}[t]
    \begin{column}{0.32\paperwidth}
      \begin{block}{Motivation}
        The problem in three sentences: who suffers, what it costs today,
        and why the standard remedy trades accuracy for cost.
      \end{block}
      \begin{block}{Prior work}
        Two lines of landscape, one open question \citep{key}.
      \end{block}
      \begin{block}{Method}
        The pipeline in one picture and one sentence.
        % \includegraphics[width=.9\linewidth]{pipeline.png}
      \end{block}
    \end{column}
    \begin{column}{0.32\paperwidth}
      \begin{block}{Main result}
        \begin{table}
          \centering
          \begin{tabular}{lcc}
            \toprule
            Method & Metric A & Metric B \\
            \midrule
            Baseline & 0.71 & 0.63 \\
            Ours & \textbf{0.79} & \textbf{0.70} \\
            \bottomrule
          \end{tabular}
        \end{table}
        \vspace{1ex}
        \begin{tikzpicture}
          \begin{axis}[
            width=.8\linewidth, height=14cm,
            ybar, bar width=18pt,
            ymin=0, ymax=1,
            symbolic x coords={Baseline, Ours},
            xtick=data,
            ylabel={Metric A},
            nodes near coords,
            axis lines*=left, tick style={draw=none},
          ]
            \addplot[ybar, fill=black!70, draw=none]
              coordinates {(Baseline,0.71) (Ours,0.79)};
          \end{axis}
        \end{tikzpicture}
      \end{block}
    \end{column}
    \begin{column}{0.32\paperwidth}
      \begin{block}{What it means}
        The takeaway in one sentence, large enough to read from two meters.
      \end{block}
      \begin{block}{Limitations and next steps}
        Honest boundaries of the claim, and the one experiment that would
        change your mind.
      \end{block}
      \begin{block}{References}
        \small
        \bibliographystyle{plainnat}
        \bibliography{references}
      \end{block}
    \end{column}
  \end{columns}
\end{frame}
\end{document}
"""

TEMPLATES: dict[str, str] = {
    "article": ARTICLE_TEMPLATE,
    "review": REVIEW_TEMPLATE,
    "thesis": THESIS_TEMPLATE,
    "ieee": IEEE_TEMPLATE,
    "report": REPORT_TEMPLATE,
    "proposal": PROPOSAL_TEMPLATE,
    "beamer": BEAMER_TEMPLATE,
    "poster": POSTER_TEMPLATE,
    "abstract": ABSTRACT_TEMPLATE,
    "blank": BLANK_TEMPLATE,
}
