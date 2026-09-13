"""Golden-set eval harness (retrieval-recall regression).

Uses the SYNERGY dataset (CC0; 26 published systematic reviews with labeled
included works) as ground truth. The skeleton measures the *retrieval* stage:
of a review's included works, how many exist in our corpus and how many does a
given query retrieve. Recall — not accuracy — is the headline metric.

Requires the optional eval dependency group: `uv sync --group eval`.
"""

from dataclasses import dataclass

from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.querylang.parser import parse_query


@dataclass
class RecallReport:
    dataset: str
    included_total: int
    in_corpus: int
    retrieved: int

    @property
    def corpus_coverage(self) -> float:
        return self.in_corpus / self.included_total if self.included_total else 0.0

    @property
    def retrieval_recall(self) -> float:
        """Recall against the included works that the corpus could contain."""
        return self.retrieved / self.in_corpus if self.in_corpus else 0.0

    def render(self) -> str:
        return (
            f"golden set: {self.dataset}\n"
            f"  included works (ground truth): {self.included_total}\n"
            f"  present in corpus:             {self.in_corpus} "
            f"({self.corpus_coverage:.1%} coverage)\n"
            f"  retrieved by query:            {self.retrieved} "
            f"({self.retrieval_recall:.1%} recall@retrieval)"
        )


def _load_synergy_included_ids(dataset_name: str) -> set[str]:
    try:
        # Import from the submodule (not the package root): synergy-dataset ships
        # no py.typed, so the root re-export trips mypy strict when the optional
        # eval extra is installed; the submodule path resolves cleanly either way.
        from synergy_dataset.base import Dataset
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("synergy-dataset not installed; run `uv sync --group eval`") from exc
    dataset = Dataset(dataset_name)
    included: set[str] = set()
    for openalex_id, record in dataset.to_dict().items():
        if record.get("label_included") == 1:
            included.add(openalex_id.removeprefix("https://openalex.org/"))
    return included


def evaluate_retrieval_recall(
    corpus: DuckDBCorpus, dataset_name: str, query_string: str
) -> RecallReport:
    included = _load_synergy_included_ids(dataset_name)
    hits = corpus.search(parse_query(query_string), limit=100_000)
    hit_ids = {work.id for work in hits}

    # corpus coverage: which included works exist in the corpus at all
    all_ids: set[str] = set()
    import duckdb

    with duckdb.connect() as con:
        rows = con.execute("SELECT id FROM read_parquet(?)", [str(corpus.works_path)]).fetchall()
    all_ids = {row[0] for row in rows}

    in_corpus = included & all_ids
    retrieved = included & hit_ids
    return RecallReport(
        dataset=dataset_name,
        included_total=len(included),
        in_corpus=len(in_corpus),
        retrieved=len(retrieved),
    )
