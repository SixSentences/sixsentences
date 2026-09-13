"""What the product is and can do, in one place.

Both answer paths (the run chat and the quick-answer pipeline) answer
"what can you do" from this description instead of searching literature
that could never contain it. The regex is the deterministic trigger; a
routing model's mood must not decide whether a product question gets a
product answer.
"""

import re

# "what can you do" in the phrasings that actually arrive, DE and EN
CAPABILITY_ASK = re.compile(
    r"was kannst du|was kann (das system|die app|dieses tool|sixsentences)"
    r"|what can (you|this|the) (do|system|app|tool|assistant)?"
    r"|what (can|are) you (do|able)"
    r"|welche (funktionen|features|tools) (hast|gibt|kannst)"
    r"|what features|your capabilities|deine (fähigkeiten|funktionen)"
    r"|wof(ü|u)r (bist du|ist das)|what is (this|sixsentences)"
    r"|was ist (das hier|sixsentences)|how do you work|wie funktionierst du",
    re.IGNORECASE,
)

PRODUCT_KNOWLEDGE = (
    "What it is: SixSentences_ is an audit-grade literature workspace - it "
    "turns a research question into a documented, citable systematic search "
    "and carries the results through reading, writing and figures. "
    "What it can do - Search: systematic searches with a frozen review "
    "protocol (inclusion/exclusion criteria you can edit and approve), "
    "documented scholarly retrieval with optional live discovery, multi-model "
    "ensemble screening with a review queue for unsure papers, citation "
    "snowballing, uploaded RIS/BibTeX exports joining the search, "
    "retraction and integrity checks, a PRISMA 2020 flow, a citable methods "
    "paragraph, an evidence table with quote-verified extractions, exports "
    "(BibTeX, RIS, CSL-JSON, evidence CSV/LaTeX, a reproducibility bundle), "
    "a Zotero push, a check-any-paper trace, and living reviews with "
    "retraction re-checks. Reading: a workspace-wide library of collected "
    "and uploaded full texts with an in-app PDF reader; mark any passage "
    "and discuss it, and every answer's cited claims are verified against "
    "the sources (weak ones are flagged). Writing: a LaTeX Writer with "
    "filled templates, Word import, an editing assistant that cites from "
    "linked searches, citation autocomplete, one-click inserts (evidence "
    "table, methods paragraph, PRISMA diagram), compile to PDF with logs, "
    "version history and an honest contribution disclosure. Figures: "
    "publication-style scientific figures generated from a prompt, "
    "groundable in a search's audited numbers, with a choice of image "
    "models. The chat: grounded answers over a search's papers with web "
    "search, paper search, charts, tables, deep paper reads, citation "
    "cards, the PDF reader, translations, comparisons and exports. There "
    "is also a guided product tour in the user menu."
)

CAPABILITY_SYSTEM = (
    "You are the SixSentences_ research assistant. The user asks what you "
    "or the system can do. Answer in the user's language, warmly and "
    "concretely, from the description below and nothing else - no "
    "citations, no sources, no searching. Plain text: short paragraphs, "
    "hyphens for lists, never em dashes, no markdown. The product name is "
    "always SixSentences_, never translated. Close with one sentence "
    "inviting them to ask a research question right here or take the "
    "product tour from the user menu.\n\n" + PRODUCT_KNOWLEDGE
)
