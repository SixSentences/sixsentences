"""Research-integrity signals: retractions, zombie citations, tortured phrases."""

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.integrity.report import Severity, assess_corpus
from sixsentences_server.integrity.tortured import TorturedScreen


def _work(wid: str, title: str = "t", **kw: object) -> WorkRecord:
    return WorkRecord(id=wid, title=title, **kw)  # type: ignore[arg-type]


def test_tortured_screen_flags_known_manglings() -> None:
    screen = TorturedScreen()
    hits = screen.scan("A novel irregular backwoods for colossal information analysis")
    joined = " ".join(hits)
    assert "random forest" in joined
    assert "big data" in joined


def test_tortured_screen_ignores_legitimate_text() -> None:
    screen = TorturedScreen()
    assert screen.scan("We train a random forest on a big data set") == []


def test_retracted_work_is_critical() -> None:
    works = [_work("W1", is_retracted=True)]
    report = assess_corpus(works)["W1"]
    assert report.severity is Severity.CRITICAL
    assert report.is_retracted is True


def test_zombie_citation_flags_only_in_set_retractions() -> None:
    works = [
        _work("W1", referenced_works=["W2", "W_absent"]),  # cites retracted W2 (in set)
        _work("W2", is_retracted=True),
    ]
    reports = assess_corpus(works)
    assert reports["W1"].severity is Severity.WARNING
    assert reports["W1"].cites_retracted == ["W2"]
    # a citation to a retracted work NOT in the set is not fabricated as a finding
    assert "W_absent" not in reports["W1"].cites_retracted


def test_tortured_phrase_is_warning_not_exclusion() -> None:
    works = [_work("W1", title="Deep profound learning survey")]
    report = assess_corpus(works)["W1"]
    assert report.severity is Severity.WARNING  # signal, never auto-reject
    assert report.tortured_phrases
    assert report.flagged is True


def test_predatory_venue_is_a_warning_via_list() -> None:
    from sixsentences_server.integrity.venue import normalize_venue

    works = [_work("W1", title="A study of neural methods", venue="Predatory Press")]
    reports = assess_corpus(works, predatory_venues=frozenset({normalize_venue("Predatory Press")}))
    assert reports["W1"].severity is Severity.WARNING
    assert reports["W1"].venue_status == "predatory"


def test_clean_work_has_no_flags() -> None:
    report = assess_corpus([_work("W1", title="A clean study of neural networks")])["W1"]
    assert report.severity is Severity.CLEAN
    assert report.flagged is False


def test_extended_phrase_list_loads_from_data_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "tortured_phrases.tsv").write_text("made up phrase\treal term\n", encoding="utf-8")
    from sixsentences_server.integrity.tortured import load_phrases

    phrases = load_phrases(tmp_path)
    assert phrases["made up phrase"] == "real term"
    assert TorturedScreen(phrases).scan("this is a made up phrase here")
