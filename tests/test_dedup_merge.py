"""Same-study companion-report merging (preprint + published article)."""

from sixsentences.core.models import WorkRecord
from sixsentences.pipeline.dedup import link_same_study_reports


def _work(
    work_id: str,
    title: str,
    *,
    venue: str | None = None,
    year: int | None = None,
    authors: list[str] | None = None,
    doi: str | None = None,
    cited: int = 0,
    work_type: str | None = None,
) -> WorkRecord:
    return WorkRecord(
        id=work_id,
        title=title,
        venue=venue,
        year=year,
        authors=authors or [],
        doi=doi,
        cited_by_count=cited,
        work_type=work_type,
    )


def test_preprint_and_published_report_fold_into_the_journal_version() -> None:
    preprint = _work(
        "W1",
        "Robust ensemble screening for systematic reviews",
        venue="arXiv (Cornell University)",
        year=2022,
        authors=["A. Smith", "B. Jones"],
        work_type="preprint",
    )
    journal = _work(
        "W2",
        "Robust ensemble screening for systematic reviews: a randomized evaluation",
        venue="Journal of Machine Learning Research",
        year=2023,
        authors=["Alice Smith", "Bella Jones"],
        doi="10.1/jmlr.robust",
        cited=41,
    )
    unrelated = _work(
        "W3",
        "A completely different matter of quantum chemistry",
        venue="Nature",
        year=2023,
        authors=["C. Miller"],
        doi="10.1/nature.q",
    )

    linked = link_same_study_reports([preprint, journal, unrelated])

    assert len(linked) == 1
    earlier, later = linked[0]
    assert earlier.id == "W1" and later.id == "W2"


def test_distinct_studies_never_merge() -> None:
    part_one = _work(
        "W1",
        "Longitudinal screening outcomes part 1 methodology",
        venue="arXiv",
        year=2022,
        authors=["D. Chen"],
        work_type="preprint",
    )
    part_two = _work(
        "W2",
        "Longitudinal screening outcomes part 2 methodology",
        venue="arXiv",
        year=2022,
        authors=["D. Chen"],
        work_type="preprint",
    )
    other_author = _work(
        "W3",
        "Robust ensemble screening for systematic reviews",
        venue="arXiv",
        year=2022,
        authors=["E. Weber"],
        work_type="preprint",
    )
    same_title_no_preprint = _work(
        "W4",
        "Robust ensemble screening for systematic reviews extended",
        venue="TMLR",
        year=2023,
        authors=["E. Weber"],
        doi="10.1/tmlr.x",
    )

    linked = link_same_study_reports([part_one, part_two, other_author, same_title_no_preprint])

    # digits differ -> parts stay separate; W3 is a preprint by Weber and W4
    # a journal paper by Weber with subtitle drift -> THAT pair may merge
    assert all(a.id not in {"W1", "W2"} and b.id not in {"W1", "W2"} for a, b in linked)


def test_two_published_articles_do_not_merge_without_a_preprint_side() -> None:
    a = _work(
        "W1",
        "Deep screening agreement across reviewer ensembles",
        venue="JAMIA",
        year=2022,
        authors=["F. Braun"],
        doi="10.1/a",
    )
    b = _work(
        "W2",
        "Deep screening agreement across reviewer ensembles revisited",
        venue="BMJ",
        year=2023,
        authors=["F. Braun"],
        doi="10.1/b",
    )
    linked = link_same_study_reports([a, b])
    assert not linked
