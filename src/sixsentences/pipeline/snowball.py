"""Citation snowballing: identification via citation searching (PRISMA 2020).

Caller-selected seed works drive two sweeps. BACKWARD walks their reference
lists, resolved against the local corpus first and then, when a client is
available, against a citation index in id batches. FORWARD asks for works that
cite the seeds, top-cited first and explicitly capped. This module only widens
identification and applies known-record and year filters. The caller must screen
every returned candidate against the frozen protocol before inclusion.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sixsentences.core.models import WorkRecord
from sixsentences.pipeline.dedup import canonicalize_doi

_ID_BATCH = 40  # openalex_id:a|b|… OR-batch size (URL-length safety)
_CITES_BATCH = 20  # cites:a|b|… seeds per forward request


class _CitationClient(Protocol):
    def iter_works(self, oa_filter: str, *, limit: int) -> Any:
        """Yield works matching a provider filter up to the supplied limit."""

        ...


@dataclass
class SnowballHarvest:
    """Identification candidates and transparent collection counters."""

    records: list[WorkRecord] = field(default_factory=list)
    backward_refs: int = 0  # distinct referenced ids across the seeds
    backward_resolved: int = 0  # of which resolved to a record (corpus or live)
    forward_returned: int = 0  # citing works returned by the live index
    skipped_known: int = 0  # already identified by the search
    skipped_filters: int = 0  # outside the protocol's year window


def collect_snowball_candidates(
    seeds: list[WorkRecord],
    *,
    known_ids: set[str],
    known_dois: set[str],
    corpus: Any,  # DuckDBCorpus-like: .by_ids(list[str])
    client: _CitationClient | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    forward_cap: int = 200,
) -> SnowballHarvest:
    """One snowball round over ``seeds``.

    Return only records the run has not seen by exact id or valid canonical DOI,
    inside the year window. Titles never suppress an identification candidate.

    Returned records are identification candidates, not screening decisions.
    """
    harvest = SnowballHarvest()
    picked_ids: set[str] = set()
    picked_dois: set[str] = set()
    known_doi_keys = {
        canonical for value in known_dois if (canonical := canonicalize_doi(value)) is not None
    }

    def accept(record: WorkRecord) -> None:
        """Append one candidate when it is new and within the year window."""

        doi = canonicalize_doi(record.doi)
        if (
            record.id in known_ids
            or record.id in picked_ids
            or (doi is not None and doi in known_doi_keys)
            or (doi is not None and doi in picked_dois)
        ):
            harvest.skipped_known += 1
            return
        if year_from is not None and record.year is not None and record.year < year_from:
            harvest.skipped_filters += 1
            return
        if year_to is not None and record.year is not None and record.year > year_to:
            harvest.skipped_filters += 1
            return
        picked_ids.add(record.id)
        if doi is not None:
            picked_dois.add(doi)
        harvest.records.append(record)

    # backward: the reference lists of the includes
    ref_ids = list(
        dict.fromkeys(
            ref for seed in seeds for ref in seed.referenced_works if ref and ref not in known_ids
        )
    )
    harvest.backward_refs = len(ref_ids)
    if ref_ids:
        resolved = {record.id: record for record in corpus.by_ids(ref_ids)}
        harvest.backward_resolved += len(resolved)
        for record in resolved.values():
            accept(record)
        missing = [ref for ref in ref_ids if ref not in resolved]
        if client is not None and missing:
            for start in range(0, len(missing), _ID_BATCH):
                batch = missing[start : start + _ID_BATCH]
                for record in client.iter_works("openalex_id:" + "|".join(batch), limit=len(batch)):
                    harvest.backward_resolved += 1
                    accept(record)

    # forward: works citing the includes (top-cited first, capped)
    if client is not None and seeds and forward_cap > 0:
        remaining = forward_cap
        for start in range(0, len(seeds), _CITES_BATCH):
            if remaining <= 0:
                break
            seed_batch = seeds[start : start + _CITES_BATCH]
            citing = list(
                client.iter_works(
                    "cites:" + "|".join(seed.id for seed in seed_batch),
                    limit=min(remaining, forward_cap),
                )
            )
            harvest.forward_returned += len(citing)
            remaining -= len(citing)
            for record in citing:
                accept(record)

    return harvest
