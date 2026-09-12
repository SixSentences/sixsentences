"""Tortured-phrase detection — a paper-mill / plagiarism signal.

"Tortured phrases" are established technical terms run through a synonymizer to
evade plagiarism checks ("random forest" -> "irregular backwoods"). Their
presence is a strong red flag for machine-generated / paper-mill content
(Cabanac, Labbe & Magazinov 2021; the Problematic Paper Screener tracks
>7,500). We ship a curated high-confidence seed and load an extended newline-
separated `tortured_phrase<TAB>canonical` list from the data dir when present.

Per QUALITY.md layer 4 this is a SIGNAL, never an auto-reject: mills adapt, and
a legitimate paper can quote a tortured phrase to critique it. It flags for
review and feeds the composite integrity score; it never excludes on its own.
"""

import re
from pathlib import Path

# Curated high-confidence CS/ML tortured phrases -> the term they mangle.
SEED_PHRASES: dict[str, str] = {
    "colossal information": "big data",
    "enormous information": "big data",
    "counterfeit consciousness": "artificial intelligence",
    "counterfeit neural": "artificial neural",
    "profound learning": "deep learning",
    "profound neural organization": "deep neural network",
    "convolutional neural organization": "convolutional neural network",
    "recurrent neural organization": "recurrent neural network",
    "neural organization": "neural network",
    "flag to commotion": "signal to noise",
    "irregular backwoods": "random forest",
    "arbitrary timberland": "random forest",
    "bolster vector machine": "support vector machine",
    "bolster vector": "support vector",
    "gullible bayes": "naive bayes",
    "credulous bayes": "naive bayes",
    "mean square blunder": "mean squared error",
    "leftover organization": "residual network",
    "slope plunge": "gradient descent",
    "angle plunge": "gradient descent",
    "learning calculation": "learning algorithm",
    "highlight extraction": "feature extraction",
    "choice tree": "decision tree",
    "web of things": "internet of things",
    "cloud figuring": "cloud computing",
    "arbitrary get to memory": "random access memory",
    "kth closest neighbor": "k nearest neighbor",
    "information mining": "data mining",
    "regular language handling": "natural language processing",
    "enormous information mining": "big data mining",
}


def _compile(phrases: dict[str, str]) -> list[tuple[re.Pattern[str], str, str]]:
    compiled: list[tuple[re.Pattern[str], str, str]] = []
    for phrase, canonical in phrases.items():
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        compiled.append((pattern, phrase, canonical))
    return compiled


def load_phrases(data_dir: Path | None = None) -> dict[str, str]:
    """Seed phrases, optionally extended by `<data_dir>/tortured_phrases.tsv`."""
    phrases = dict(SEED_PHRASES)
    if data_dir is not None:
        extra = data_dir / "tortured_phrases.tsv"
        if extra.exists():
            for line in extra.read_text(encoding="utf-8").splitlines():
                if "\t" in line:
                    tortured, canonical = line.split("\t", 1)
                    phrases[tortured.strip().lower()] = canonical.strip()
    return phrases


class TorturedScreen:
    """Reusable matcher (compile once, scan many works)."""

    def __init__(self, phrases: dict[str, str] | None = None) -> None:
        self._compiled = _compile(phrases or SEED_PHRASES)

    def scan(self, *texts: str | None) -> list[str]:
        """Return the canonical terms whose tortured variants appear in texts."""
        haystack = " ".join(t for t in texts if t)
        found: list[str] = []
        for pattern, phrase, canonical in self._compiled:
            if pattern.search(haystack):
                found.append(f"{phrase} (~{canonical})")
        return found
