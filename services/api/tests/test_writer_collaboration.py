from sixsentences_server.writer.collaboration import merge_text
from sixsentences_server.writer.retarget import retarget_latex


def test_three_way_merge_keeps_line_disjoint_edits() -> None:
    base = "title\nmethod\nresults\n"
    local = "better title\nmethod\nresults\n"
    remote = "title\nmethod\nstronger results\n"

    assert merge_text(base, local, remote) == "better title\nmethod\nstronger results\n"


def test_three_way_merge_rejects_competing_line_edits() -> None:
    base = "title\nmethod\n"
    local = "local title\nmethod\n"
    remote = "remote title\nmethod\n"

    assert merge_text(base, local, remote) is None


def test_retarget_preserves_research_content_in_destination_shell() -> None:
    source = r"""
\documentclass{article}
\usepackage[capitalise]{cleveref}
\newcommand{\metric}{F1}
\title{Reliable screening}
\author{Ada Author}
\begin{document}
\maketitle
\begin{abstract}
The exact abstract.
\end{abstract}
\section{Method}
We report \metric{} with evidence \citep{ada2026}.
\bibliographystyle{plain}
\bibliography{old}
\end{document}
"""
    target = r"""
\documentclass[conference]{IEEEtran}
\title{Placeholder}
\author{Placeholder}
\begin{document}
\maketitle
\begin{abstract}Placeholder.\end{abstract}
\section{Placeholder}
Remove me.
\bibliographystyle{IEEEtran}
\bibliography{references}
\end{document}
"""

    result = retarget_latex(source, target)

    assert r"\usepackage[capitalise]{cleveref}" in result.content
    assert r"\title{Reliable screening}" in result.content
    assert r"\author{Ada Author}" in result.content
    assert "The exact abstract." in result.content
    assert r"\section{Method}" in result.content
    assert "Remove me." not in result.content
    assert r"\newcommand{\metric}{F1}" in result.content
    assert r"\documentclass[conference]{IEEEtran}" in result.content
    assert r"\bibliographystyle{IEEEtran}" in result.content
    assert r"\bibliography{references}" in result.content
    begin = result.content.index(r"\begin{document}")
    title = result.content.index(r"\title{Reliable screening}")
    author = result.content.index(r"\author{Ada Author}")
    maketitle = result.content.index(r"\maketitle")
    assert begin < title < author < maketitle
    assert result.section_count == 1
    assert result.citation_count == 1


def test_retarget_handles_optional_metadata_arguments_without_duplication() -> None:
    source = r"""
\documentclass{article}
\title[Short title]{Long title}
\author[ada@example.org]{Ada Author}
\begin{document}
\maketitle
\section{Result}
Evidence.
\end{document}
"""
    target = r"""
\documentclass{report}
\begin{document}
\title{Placeholder}
\author{Placeholder}
\maketitle
\chapter{Placeholder}
\end{document}
"""

    result = retarget_latex(source, target)

    assert result.content.count(r"\title{Long title}") == 1
    assert result.content.count(r"\author{Ada Author}") == 1
    assert r"\title[Short title]" not in result.content
    assert r"\author[ada@example.org]" not in result.content
    assert r"\section{Result}" in result.content
