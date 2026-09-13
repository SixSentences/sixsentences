"""The Writer's project-aware editing assistant.

A document-bound chat whose replies may carry EDIT PROPOSALS: exact
file-scoped find/replace pairs against the LaTeX project. Nothing is ever applied
silently — the client renders each proposal as a diff the author accepts or
discards. Iterative turns begin with project and evidence catalogs, then inspect
exact source or evidence on demand through bounded read-only tools. A selected
local rewrite keeps a small line-numbered excerpt instead. Highlighted compiled
PDF passages must still be traced back to source before editing.
"""

import json
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sixsentences_server.agent.actions import (
    ground_visual_proposal,
    normalize_workspace_actions_for_request,
    scope_workspace_actions_to_current_resource,
    specialist_resource_actions_system,
    workspace_action_types_requested,
    workspace_actions_requested,
)
from sixsentences_server.agent.events import (
    PUBLIC_PROGRESS_COPY_RULE,
    emit_agent_event,
    emit_change_events,
    safe_agent_text,
    safe_event_value,
)
from sixsentences_server.agent.loop import (
    AgentFinalValidation,
    AgentLimits,
    AgentRunner,
    AgentTool,
    AgentToolResult,
)
from sixsentences_server.agent.runtime import review_action_coverage
from sixsentences_server.core.assistant_preferences import (
    assistant_preference_context,
    assistant_system_instruction,
)
from sixsentences_server.core.conversation import render_model_aware_context
from sixsentences_server.core.grounding import structured_response_failure
from sixsentences_server.core.locale import response_language_instruction
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.core.structured_output import (
    extract_complete_string_field,
    extract_structured_object,
    recover_action_free_answer,
    recover_structured_object,
    request_structured_completion,
)
from sixsentences_server.figures.limits import FIGURE_PROMPT_MAX_CHARACTERS
from sixsentences_server.llm.base import LLMCancelledError
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.writer.artifacts import tex_escape
from sixsentences_server.writer.context_scope import resolve_writer_evidence_scope
from sixsentences_server.writer.dataset_context import (
    exact_dataset_profile_answer,
    requests_dataset_evidence,
)
from sixsentences_server.writer.interview_context import render_interview_evidence
from sixsentences_server.writer.selection import (
    writer_pdf_page_label,
    writer_selection_matching_quote,
)
from sixsentences_server.writer.survey_context import render_survey_evidence

_SYSTEM = (
    "You are the SixSentences_ Writer assistant: a LaTeX writing copilot "
    "with receipts. In iterative turns the initial context is a file manifest, "
    "not the complete contents of the multi-file LaTeX project. Inspect exact "
    "source through the bounded read-only project tools before relying on it; "
    "selected local turns may instead provide a complete bounded, line-numbered "
    "excerpt for the requested rewrite. Linked research starts "
    "from a source catalog and exact passages must be read or searched before "
    "they support prose. Cite linked papers ONLY "
    "with the given BibTeX keys via \\citep{key} or \\citet{key}; never "
    "invent references. Embed uploaded figure files ONLY by "
    "their exact filename inside a figure environment with "
    "\\includegraphics[width=\\linewidth]{filename}). "
    "When the user asks you to write, change, insert or delete something, "
    "the terminal Writer result must have this JSON schema: "
    '{"reply": "<one short paragraph for the author>", '
    '"edits": [{"path": "<exact project path>", "find": "<EXACT, UNIQUE '
    'excerpt copied verbatim from that file>", "replace": "<the new text>"}], '
    '"visual_request": null, "workspace_actions":[]}. '
    "In iterative agent mode this schema describes only the value inside the "
    "controller's final field. Do not return it as a bare one-shot response or "
    "finish merely because it can be formed; inspect and validate every source "
    "needed for the user's requested scope first. A separately appended "
    "direct-response contract is the only exception for an already supplied "
    "bounded local excerpt or safe recovery path. "
    "Rules for edits: copy `find` character-for-character from the source "
    "(never from your imagination) and make it long enough to be unique; "
    "to insert, use a short anchor as `find` and repeat it inside `replace` "
    "with the new text added; to delete, set `replace` to an empty string; "
    "For broad rewrites, prefer bounded logical section or file edits so each "
    "change can be validated independently. Replace a whole file only when the "
    "requested structural transformation genuinely requires it. "
    "Treat recognizable placeholder prose, dummy metrics, sample identities and "
    "empty template sections as scaffolding, not manuscript evidence. When the "
    "author requests a complete document or asks to remove template boilerplate, "
    "inspect the document structure, replace or delete every reported scaffold "
    "block, and keep only the formatting commands genuinely needed by the final "
    "document. Do not append a new report below unchanged sample content. "
    "Every returned edit is only a review proposal. If the author asks to see "
    "or confirm a concrete change before it is applied, still return that exact "
    "edit proposal now and say that it awaits confirmation; never claim it was "
    "already applied. "
    "If your proposed wording is already identical to the source, return that "
    "exact unique path/find/replace pair with find equal to replace as a no-change "
    "receipt. Do not invent an edit just to finish. An empty edits list or a "
    "statement that no change is needed is not such a receipt. The server will "
    "check the exact source and close neutrally without an approval step. "
    "Keep source edits bounded to the requested manuscript scope; propose "
    "nothing when the user only asks a "
    "question (then edits is an empty list). "
    "A wording-only rewrite preserves the source's factual granularity. "
    "Aggregate counts do not identify individual cases: never assign case or "
    "task IDs, names, causes, dates or outcomes that the source did not specify. "
    "Synthetic or fictional data are not permission to invent further details. "
    "Preserve all counts, denominators, uncertainty and source attribution "
    "unless the author explicitly supplies a correction. "
    "If the user asks for a NEW scientific visual that is not already listed "
    "under UPLOADED FIGURES, do not invent a filename and do not propose a "
    "LaTeX edit yet. Instead return visual_request as "
    '{"prompt": "<complete renderer-ready scientific brief>", '
    '"kind": "method|architecture|flow|concept|plot", '
    '"aspect_ratio": "1:1|4:3|3:2|16:9|2:3", '
    '"resolution": "1k|2k|4k", "review_passes": 0|1|2}. '
    "The client must show this request for explicit author confirmation before "
    "Visual Lab is allowed to render it. Never say the render has started. "
    "If a suitable uploaded figure already exists, visual_request is null and "
    "you may propose the normal includegraphics edit using its exact filename. "
    "When a highlighted PDF passage is provided, first locate the LaTeX "
    "project file that produces it (the wording may differ from the raw source "
    "because LaTeX renders it) and target your edits there. "
    "When the latest compile FAILED, you are also the debugger: read the "
    "error lines and the log tail, find the offending LaTeX in the source, "
    "name the root cause in one sentence, and propose the exact edits that "
    "make the document compile again. "
    "User-owned sources appear with explicit BibTeX keys and may be cited. "
    "Unregistered PDF attachments are background only and must not be cited. "
    "Primary research datasets include provenance, exact preview records and "
    "computed statistics. Never invent values, distinguish the author's data "
    "from published literature, and mention limitations or missingness. "
    "Linked interview evidence is first-party qualitative research, not "
    "published literature. Keep every interpretation tied to its named "
    "interview source. Quote only text supplied in a verified analysis or a "
    "retrieved transcript passage, preserve its speaker and timestamp, and "
    "never turn an analysis-only source into a verbatim transcript claim. "
    "When drafting manuscript text from several interviews, distinguish "
    "cross-interview patterns from a single participant's statement. "
    "When the user asks to draft from linked interview evidence and at least "
    "one matching interview is available, draft the requested text now. Do not "
    "refuse, defer or merely offer to write because only one interview is "
    "linked, because the user used a plural colloquially, or because the sample "
    "is small. State the exact evidence count and resulting limitation in the "
    "proposed prose. Only block grounded drafting when no matching linked "
    "interview evidence is available. "
    "Linked survey evidence is first-party primary research. Preserve the "
    "exact question wording, scale, denominator, missingness and descriptive "
    "counts. Do not infer causality or statistical significance from "
    "descriptive results. Individual response rows are de-identified bounded "
    "retrievals, not the full response table; quote open text exactly and tie "
    "it to the supplied survey and response anchor. Keep questionnaire design "
    "facts separate from empirical results. "
    "Apply the same availability rule to every linked evidence family: one "
    "survey, dataset, review, paper or visual can support a bounded requested "
    "draft when it is actually available. Use it and name the limitation; do "
    "not turn an imperfect but valid source count into a generic refusal or a "
    "proposal to create replacement data. "
    "The latest user request is authoritative. Explicit corrections override "
    "older turns and one evidence type must never be silently substituted for "
    "another. If the requested linked source is unavailable, say exactly what "
    "must be linked instead of falling back to unrelated evidence. "
    "The current project files and this turn's linked-source catalog are the "
    "authoritative manuscript and evidence state. Older assistant replies are "
    "conversation history, not proof that a proposed edit was accepted or that "
    "a source remains linked. Never restore old wording or reuse excluded "
    "evidence merely because it appears in that history. "
    "Write manuscript prose in a professional but recognisably human scholarly "
    "voice. Vary sentence length, sentence openings and paragraph rhythm. Avoid "
    "repetitive transitions, formulaic summaries and a uniform AI cadence. Keep "
    "the argument precise and appropriate for the manuscript's discipline. "
    "The reply is plain text: no markdown, no em dashes or en dashes used as "
    "punctuation. Never claim an "
    "edit was applied — the author decides. The short operational reply follows this "
    f"public copy rule: {PUBLIC_PROGRESS_COPY_RULE} "
    + specialist_resource_actions_system("manuscript")
)

_DIRECT_RESPONSE_SYSTEM = (
    " DIRECT RESPONSE MODE: Return the terminal Writer payload itself as one "
    "strict JSON object. Do not return an action, tool or finish envelope. "
    "This bounded exception does not grant access to project files or evidence "
    "that were not supplied in the prompt."
)

_MAX_PROJECT = 90_000
_MAX_FILE = 45_000
MAX_WRITER_EDITS = 24
_LOCAL_EDIT_HISTORY = 2
_LOCAL_EDIT_MAX_TOKENS = 900
_LOCAL_PROJECT_MAX = 18_000
# A selected rewrite only needs enough surrounding source to preserve the
# paragraph structure and produce a unique edit anchor. Sending tens of
# thousands of unrelated characters made simple rewrites wait behind a much
# larger prompt without improving the proposed edit.
_LOCAL_TARGET_BEFORE = 4_000
_LOCAL_TARGET_AFTER = 8_000
_AGENT_READ_MAX_LINES = 240
_AGENT_READ_MAX_CHARS = 18_000
_AGENT_SEARCH_MAX_RESULTS = 20
_LOCAL_EDIT_REQUEST = re.compile(
    r"\b(?:rewrite|rephrase|paraphras\w*|shorten|tighten|polish|clarify|"
    r"proofread|umschreib\w*|umformulier\w*|paraphrasier\w*|kürz\w*|"
    r"kuerz\w*|formulier\w*|überarbeit\w*|ueberarbeit\w*|korrigier\w*|"
    r"verbesser\w*)\b|\bschreib\w*(?:\s+\S+){0,5}\s+um\b",
    re.IGNORECASE,
)
_BROAD_WRITER_REQUEST = re.compile(
    r"\b(?:whole|entire|full|complete|all|every|multiple|several)\b.{0,48}"
    r"\b(?:manuscript|paper|document|project|section|chapter|file)s?\b"
    r"|\b(?:manuscript|paper|document|project)[-\s](?:wide|level)\b"
    r"|\b(?:across|throughout)\b.{0,32}"
    r"\b(?:manuscript|paper|document|project|section|file)s?\b"
    r"|\b(?:ganz\w*|gesamt\w*|vollständig\w*|alle\w*|mehrere\w*)\b.{0,48}"
    r"\b(?:manuskript|paper|dokument|projekt|abschnitt|kapitel|datei)\w*\b"
    r"|\b(?:projektweit\w*|manuskriptweit\w*|dokumentweit\w*|"
    r"überall|ueberall|durchgehend)\b",
    re.IGNORECASE,
)
_TEMPLATE_CLEANUP_REQUEST = re.compile(
    r"\b(?:remove|delete|strip|clear|replace|entfern\w*|lösch\w*|loesch\w*|"
    r"bereinig\w*|ersetz\w*)\b.{0,120}\b(?:boilerplate|bpilerplate|"
    r"template\s+(?:content|boilerplate)|skeleton|vorlageninhalt\w*|"
    r"(?:all|every|remaining|unneeded|unused|alle\w*|sämtlich\w*|saemtlich\w*|"
    r"unnötig\w*|unnoetig\w*)\s+(?:placeholder|platzhalter)\w*)\b"
    r"|\b(?:boilerplate|bpilerplate|template\s+(?:content|boilerplate)|skeleton|"
    r"vorlageninhalt\w*|(?:all|every|remaining|unneeded|unused|alle\w*|"
    r"sämtlich\w*|saemtlich\w*|unnötig\w*|unnoetig\w*)\s+"
    r"(?:placeholder|platzhalter)\w*)\b.{0,120}\b(?:remove|delete|strip|clear|"
    r"replace|entfern\w*|lösch\w*|loesch\w*|bereinig\w*|ersetz\w*)\b",
    re.IGNORECASE,
)
_STRICT_PLACEHOLDER_CLEANUP_REQUEST = re.compile(
    r"\b(?:all|every|remaining|unneeded|unused|alle\w*|sämtlich\w*|saemtlich\w*|"
    r"unnötig\w*|unnoetig\w*)\s+(?:placeholder|platzhalter)\w*\b",
    re.IGNORECASE,
)
_FULL_DOCUMENT_DRAFT_REQUEST = re.compile(
    r"\b(?:write|draft|create|prepare|make|rewrite|schreib\w*|erstell\w*|mach\w*|"
    r"formulier\w*|"
    r"verfass\w*|ersetz\w*)\b.{0,80}\b(?:complete|full|entire|komplett\w*|"
    r"vollständig\w*|ganz\w*)\b.{0,40}\b(?:report|paper|manuscript|document|"
    r"bericht|manuskript|dokument)\w*\b"
    r"|\b(?:complete|full|entire|komplett\w*|vollständig\w*|ganz\w*)\b.{0,40}"
    r"\b(?:report|paper|manuscript|document|bericht|manuskript|dokument)\w*\b"
    r".{0,80}\b(?:write|draft|create|prepare|make|rewrite|schreib\w*|erstell\w*|"
    r"mach\w*|formulier\w*|verfass\w*|ersetz\w*)\b",
    re.IGNORECASE,
)
_BOILERPLATE_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "placeholder",
        re.compile(r"\b(?:placeholder|platzhalter)\w*\b", re.IGNORECASE),
    ),
    (
        "template_title",
        re.compile(
            r"Conference Paper Title|Rewrite This Title with Your Claim|"
            r"Seminar Report:\s*Topic|Thesis Title|Talk Title|Poster Title|"
            r"A Systematic Literature Review of\s*\\ldots",
            re.IGNORECASE,
        ),
    ),
    (
        "template_identity",
        re.compile(
            r"Given (?:Name )?Surname|University Name|Institute, University|"
            r"dept\. name of organization|name of organization|"
            r"email address or ORCID|Matriculation no\.|Course Name|"
            r"Supervisor:\s*Prof|Second examiner:",
            re.IGNORECASE,
        ),
    ),
    (
        "instructional_prose",
        re.compile(
            r"Motivate the problem and preview the contribution|"
            r"This section grows out of your SixSentences|"
            r"The setup and objective, with equations numbered only when referenced|"
            r"Data, baselines, metrics and seeds in one paragraph|"
            r"states the takeaway, not the numbers|"
            r"The claim, the evidence for it, and the one open problem|"
            r"Open with the problem, not the field|"
            r"One honest paragraph on where the method fails|"
            r"Restate the claim, now backed by|"
            r"What the report covers, why the topic matters|"
            r"The concepts a fellow student needs to follow|"
            r"How the surveyed papers were selected|"
            r"What is settled, what is contested|"
            r"The one-paragraph answer to the question|"
            r"Start writing\.",
            re.IGNORECASE,
        ),
    ),
    (
        "dummy_result",
        re.compile(
            r"\bMetric A\b|\bMetric B\b|"
            r"(?:Baseline|Proposed|Ours)\s*&\s*(?:\\textbf\{)?0\.(?:71|79)|"
            r"three public benchmarks|primary metric from 0\.71 to 0\.79",
            re.IGNORECASE,
        ),
    ),
    (
        "template_guidance",
        re.compile(
            r"official IEEE conference skeleton|"
            r"live from your linked SixSentences|"
            r"keys from the Cite menu|"
            r"Paste the exported methods paragraph here|"
            r"Uploaded figures join every compile",
            re.IGNORECASE,
        ),
    ),
)
_STRONG_BOILERPLATE_KINDS = frozenset(
    {
        "template_title",
        "template_identity",
        "instructional_prose",
        "dummy_result",
        "template_guidance",
    }
)
_EXTERNAL_CONTEXT_REQUEST = re.compile(
    r"\b(?:cit(?:e|ation)\w*|reference\w*|source\w*|paper\w*|"
    r"literature\w*|evidence\w*|stud(?:y|ies)|dataset\w*|data|"
    r"interview\w*|survey\w*|figure\w*|visual\w*|graphic\w*|table\w*|"
    r"beleg\w*|zitier\w*|quelle\w*|paper\w*|literatur\w*|evidenz\w*|"
    r"studie\w*|daten\w*|interview\w*|umfrage\w*|grafik\w*|abbildung\w*|"
    r"tabelle\w*)\b",
    re.IGNORECASE,
)
_MANUSCRIPT_MUTATION_REQUEST = re.compile(
    r"\b(?:write|draft|create|prepare|make|insert|revise|rewrite|edit|replace|change|"
    r"update|shorten|"
    r"expand|delete|remove|"
    r"formulat\w*|"
    r"schreib\w*|formulier\w*|erstell\w*|mach\w*|ersetz\w*|"
    r"änder(?:e|n|st|t)\w*|aender(?:e|n|st|t)\w*|"
    r"aktualisier\w*|korrigieren?|füg\w*|fueg\w*|überarbeit\w*|ueberarbeit\w*|"
    r"umschreib\w*|kürz\w*|kuerz\w*|ergänz\w*|ergaenz\w*|lösch\w*|"
    r"loesch\w*|entfern\w*)\b",
    re.IGNORECASE,
)
_NEGATED_MANUSCRIPT_MUTATION = re.compile(
    r"\b(?:do\s+not|don['’]t|without|not\s+yet|nothing|nicht|nichts|noch\s+nicht|ohne)\b"
    r".{0,36}\b(?:write|draft|create|prepare|make|insert|revise|rewrite|edit|replace|"
    r"change|update|"
    r"shorten|expand|"
    r"delete|remove|"
    r"formulat\w*|schreib\w*|formulier\w*|erstell\w*|mach\w*|ersetz\w*|"
    r"änder(?:e|n|st|t)\w*|aender(?:e|n|st|t)\w*|aktualisier\w*|korrigieren?|"
    r"füg\w*|fueg\w*|überarbeit\w*|"
    r"ueberarbeit\w*|umschreib\w*|kürz\w*|kuerz\w*|ergänz\w*|ergaenz\w*|"
    r"lösch\w*|loesch\w*|entfern\w*)\b"
    r"|\b(?:write|draft|create|prepare|make|insert|revise|rewrite|edit|replace|change|"
    r"update|shorten|"
    r"expand|delete|remove|"
    r"formulat\w*|schreib\w*|formulier\w*|erstell\w*|mach\w*|ersetz\w*|"
    r"änder(?:e|n|st|t)\w*|aender(?:e|n|st|t)\w*|aktualisier\w*|korrigieren?|"
    r"füg\w*|fueg\w*|überarbeit\w*|"
    r"ueberarbeit\w*|umschreib\w*|kürz\w*|kuerz\w*|ergänz\w*|ergaenz\w*|"
    r"lösch\w*|loesch\w*|entfern\w*)\b"
    r".{0,24}\b(?:not|nothing|nicht|nichts|noch\s+nicht|noch\s+nichts)\b",
    re.IGNORECASE,
)
_MUTATION_ACTION_STEMS = (
    "write",
    "draft",
    "create",
    "prepare",
    "make",
    "insert",
    "revise",
    "rewrite",
    "edit",
    "replace",
    "change",
    "update",
    "shorten",
    "expand",
    "delete",
    "remove",
    "schreib",
    "formulier",
    "erstell",
    "mach",
    "ersetz",
    "aender",
    "aktualisier",
    "fueg",
    "ueberarbeit",
    "umschreib",
    "kuerz",
    "ergaenz",
    "loesch",
    "entfern",
)
_WRITER_APPLIED_CLAIM = re.compile(
    r"\b(?:already\s+)?(?:applied|saved|published)\b|"
    r"\b(?:i|we)\s+(?:(?:have|has)\s+)?(?:already\s+)?"
    r"(?:rewrote|rewritten|revised|edited|updated|changed|inserted|replaced)\b|"
    r"\b(?:has|have|was|were)\s+(?:already\s+)?"
    r"(?:inserted|replaced|updated|rewritten|revised|edited|changed)\b|"
    r"\b(?:angewendet|gespeichert|veröffentlicht|veroeffentlicht)\b|"
    r"\b(?:ich|wir)\s+(?:habe|haben)\s+(?:(?:\S+\s+){0,6})?(?:bereits\s+)?"
    r"(?:neu\s+geschrieben|umgeschrieben|geändert|geaendert|überarbeitet|"
    r"ueberarbeitet|aktualisiert|ersetzt)\b|"
    r"\b(?:wurde|wurden|habe|haben)\s+(?:bereits\s+)?"
    r"(?:eingefügt|eingefuegt|ersetzt|aktualisiert|überarbeitet|ueberarbeitet|"
    r"übernommen|uebernommen)\b",
    re.IGNORECASE,
)
_WRITER_EVIDENCE_VERIFICATION_CLAIM = re.compile(
    r"\b(?:interview|transcript|transkript|source|evidence|quelle|evidenz)\w*\b"
    r"[^.!?]{0,100}\b(?:read|reread|reviewed|checked|verified|analysed|analyzed|"
    r"les(?:e|en|t)|gelesen|prüf\w*|pruef\w*|verifizier\w*|auswert\w*|analysier\w*)\b|"
    r"\b(?:read|reread|reviewed|checked|verified|analysed|analyzed|les(?:e|en|t)|"
    r"gelesen|prüf\w*|pruef\w*|verifizier\w*|auswert\w*|analysier\w*)\b"
    r"[^.!?]{0,100}\b(?:interview|transcript|transkript|source|evidence|quelle|evidenz)\w*\b|"
    r"\b(?:auf\s+(?:der\s+)?basis\s+(?:des|der|von)|basierend\s+auf|gestützt\s+auf|"
    r"gestuetzt\s+auf|based\s+on|using)\b[^.!?]{0,100}"
    r"\b(?:interview|transcript|transkript|source|evidence|quelle|evidenz)\w*\b",
    re.IGNORECASE,
)
_PROJECT_ONLY_REWRITE_TARGET = re.compile(
    r"\b(?:passage|paragraph|section|sentence|abschnitt|kapitel|satz|text)\w*\b",
    re.IGNORECASE,
)
_EVIDENCE_HANDLE = re.compile(
    r"^(?:paper|source|dataset|interview|survey):[^\s]+$",
    re.IGNORECASE,
)
_WRITER_PROJECT_REPAIR_TOOLS = frozenset(
    {
        "read_file",
        "search_project",
        "inspect_document_structure",
        "compile_candidate",
    }
)
_WRITER_EDIT_REPAIR_TOOLS = frozenset({"read_file", "search_project", "compile_candidate"})
_WRITER_COMPILE_REPAIR_TOOLS = _WRITER_PROJECT_REPAIR_TOOLS
_WRITER_TARGET_REQUEST_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("title", re.compile(r"\b(?:title|titel)\w*\b", re.IGNORECASE)),
    (
        "abstract",
        re.compile(r"\b(?:abstract|summary|zusammenfassung)\w*\b", re.IGNORECASE),
    ),
    (
        "introduction",
        re.compile(r"\b(?:introduction|intro|einleitung)\w*\b", re.IGNORECASE),
    ),
    (
        "background",
        re.compile(r"\b(?:background|related work|hintergrund)\w*\b", re.IGNORECASE),
    ),
    (
        "methods",
        re.compile(r"\b(?:method|methodology|methodik|methode)\w*\b", re.IGNORECASE),
    ),
    (
        "results",
        re.compile(r"\b(?:results|findings|ergebnisse|befunde)\w*\b", re.IGNORECASE),
    ),
    (
        "discussion",
        re.compile(r"\b(?:discussion|diskussion)\w*\b", re.IGNORECASE),
    ),
    (
        "conclusion",
        re.compile(r"\b(?:conclusion|fazit|schlussfolgerung)\w*\b", re.IGNORECASE),
    ),
)
_WRITER_SECTION_HEADING = re.compile(
    r"\\(?P<level>part|chapter|section|subsection|subsubsection)\*?"
    r"\s*(?:\[[^\]]*\])?\{(?P<title>[^{}]*)\}",
    re.IGNORECASE,
)
_WRITER_SECTION_LEVELS = {
    "part": 0,
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
}
_INTERVIEW_QUOTE_REQUEST = re.compile(
    r"\b(?:verbatim|direct\s+quote|exact\s+quote|quote|wörtlich\w*|"
    r"woertlich\w*|direkt\w*\s+zitat|zitat\w*)\b",
    re.IGNORECASE,
)
_INTERVIEW_SPEAKER_REQUEST = re.compile(
    r"\b(?:speaker|sprecher\w*)\b",
    re.IGNORECASE,
)
_INTERVIEW_TIMESTAMP_REQUEST = re.compile(
    r"\b(?:timestamp|time\s*code|zeitangabe\w*|zeitstempel\w*)\b",
    re.IGNORECASE,
)
_CITATION_COMMAND = re.compile(
    r"\\(?:cite|citep|citet|autocite|parencite|textcite)\*?"
    r"(?:\[[^\]]*\]){0,2}\{([^{}]+)\}",
    re.IGNORECASE,
)
_INCLUDE_GRAPHICS = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}",
    re.IGNORECASE,
)

_WRITER_RESPONSE_KEYS = frozenset({"reply", "edits", "visual_request", "workspace_actions"})


@dataclass(frozen=True)
class _WriterAgentResponse:
    """Minimal completion returned after adapting a legacy Writer payload."""

    text: str


@dataclass
class _WriterRepairState:
    """Machine-readable next-tool policy for one manuscript repair phase."""

    guidance: str = ""
    allowed_tools: frozenset[str] = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        """Whether the final validator or a staged edit requires correction."""

        return bool(self.guidance)

    def require(self, guidance: str, allowed_tools: Collection[str]) -> None:
        """Set public-safe guidance and an explicit project-tool allowlist."""

        requested = frozenset(str(name) for name in allowed_tools)
        if not requested <= _WRITER_PROJECT_REPAIR_TOOLS:
            raise ValueError("writer repair tools must be manuscript project tools")
        self.guidance = guidance.strip()
        self.allowed_tools = requested

    def clear(self) -> None:
        """Leave repair mode after a validated cumulative candidate."""

        self.guidance = ""
        self.allowed_tools = frozenset()

    def preflight(self, tool_name: str) -> AgentToolResult | None:
        """Silently redirect tools outside the current structured repair policy."""

        if not self.active or tool_name in self.allowed_tools:
            return None
        return AgentToolResult(
            output={
                "status": "manuscript_repair_required",
                "allowed_tools": sorted(self.allowed_tools),
                "next_step": self.guidance,
            },
            summary=self.guidance,
            success=False,
            error_code="manuscript_repair_required",
        )


class _WriterAgentProtocolFallback(RuntimeError):
    """Signal that an unstructured response should use the existing safe recovery."""

    def __init__(self, raw_response: str) -> None:
        super().__init__("writer response did not match the agent decision contract")
        self.raw_response = raw_response


def _writer_json_object(value: str) -> dict[str, Any] | None:
    """Extract one schema-shaped JSON object from a provider response.

    Some reasoning models wrap otherwise valid JSON in prose or a fenced
    block. ``json.loads`` rejects that entire answer. ``raw_decode`` lets us
    recover only a complete object and still rejects truncated or arbitrary
    text. Requiring a Writer schema key avoids accepting an unrelated object
    from model commentary.
    """

    candidate = extract_structured_object(value, required_keys={"reply"})
    if candidate is None or not isinstance(candidate.get("reply"), str):
        return None
    normalized = dict(candidate)
    # Preserve explicitly malformed action fields so deterministic validation
    # can reject them. Coercing them to empty values here would make a partial
    # or missing outcome indistinguishable from an intentional answer-only turn.
    normalized.setdefault("edits", [])
    normalized.setdefault("visual_request", None)
    normalized.setdefault("workspace_actions", [])
    return normalized


class _WriterAgentPool:
    """Adapt the previous direct Writer JSON contract to the iterative loop.

    Existing configured models may need a deployment cycle before they follow
    the Decide -> Act -> Observe envelope consistently. A complete legacy
    Writer payload is therefore treated as an immediate finish decision. New
    models retain the native tool/finish decision unchanged.
    """

    def __init__(
        self,
        pool: Any,
        *,
        legacy_system: str,
        legacy_prompt: str,
        legacy_max_tokens: int,
    ) -> None:
        self._pool = pool
        self._legacy_system = legacy_system
        self._legacy_prompt = legacy_prompt
        self._legacy_max_tokens = legacy_max_tokens
        self._provider_structured_output = callable(getattr(pool, "complete_json", None))
        self.last_text = ""
        self.last_was_legacy = False

    @property
    def cancel_check(self) -> Any:
        """Expose the configured request cancellation callback to AgentRunner."""

        return getattr(self._pool, "cancel_check", None)

    def complete(
        self,
        _task: Any,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> Any:
        """Complete one loop decision through the wrapped provider route."""

        return self._complete(system=system, prompt=prompt, max_tokens=max_tokens)

    def complete_json(
        self,
        _task: Any,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> Any:
        """Prefer the wrapped pool's provider-enforced structured response."""

        return self._complete(system=system, prompt=prompt, max_tokens=max_tokens)

    def _complete(self, *, system: str, prompt: str, max_tokens: int) -> Any:
        response = request_structured_completion(
            self._pool,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
        )
        raw = str(getattr(response, "text", "")).strip()
        self.last_text = raw
        self.last_was_legacy = False
        decision = extract_structured_object(raw, required_keys={"action"})
        action = str((decision or {}).get("action") or "").strip().casefold()
        if action in {"tool", "finish"}:
            return response
        legacy = _writer_json_object(raw)
        if legacy is None:
            raise _WriterAgentProtocolFallback(raw)
        if self._provider_structured_output and "OBSERVATIONS FROM COMPLETED STEPS:\n[]" in prompt:
            grounded_response = request_structured_completion(
                self._pool,
                system=self._legacy_system,
                prompt=self._legacy_prompt,
                max_tokens=self._legacy_max_tokens,
            )
            grounded_raw = str(getattr(grounded_response, "text", "")).strip()
            grounded_legacy = _writer_json_object(grounded_raw)
            if grounded_legacy is None:
                raise _WriterAgentProtocolFallback(grounded_raw)
            raw = grounded_raw
            legacy = grounded_legacy
            self.last_text = raw
        self.last_was_legacy = True
        wrapped = {
            "action": "finish",
            "update": "Prepared the manuscript result for validation.",
            "final": legacy,
        }
        return _WriterAgentResponse(text=json.dumps(wrapped, ensure_ascii=False))


def _writer_agent_tools(
    project_files: dict[str, str],
    active_path: str,
    *,
    structure_receipts: set[str] | None = None,
    repair_state: _WriterRepairState | None = None,
    evidence_tools_available: bool = True,
) -> tuple[AgentTool, ...]:
    """Return bounded read-only tools over the in-memory manuscript project."""

    ordered_paths = [
        active_path,
        *sorted(path for path in project_files if path != active_path),
    ]
    read_coverage: dict[str, list[tuple[int, int]]] = {}

    def resolve_path(raw_path: Any) -> str | None:
        requested = str(raw_path or "").strip()
        if requested in project_files:
            return requested
        if requested.startswith("./") and requested[2:] in project_files:
            return requested[2:]
        return None

    def failure(message: str) -> AgentToolResult:
        return AgentToolResult(
            output={"error": message},
            summary=message,
            success=False,
        )

    def record_read_range(path: str, start: int, end: int) -> None:
        """Remember inclusive manuscript line ranges returned in this turn."""

        if end < start:
            return
        ranges = sorted([*read_coverage.get(path, []), (start, end)])
        merged: list[tuple[int, int]] = []
        for range_start, range_end in ranges:
            if merged and range_start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], range_end))
            else:
                merged.append((range_start, range_end))
        read_coverage[path] = merged

    def first_unread_lines(path: str, start: int, end: int) -> tuple[int, int] | None:
        """Return the first requested line span missing from prior observations."""

        cursor = start
        for covered_start, covered_end in read_coverage.get(path, []):
            if covered_end < cursor:
                continue
            if covered_start > cursor:
                return cursor, min(end, covered_start - 1)
            cursor = max(cursor, covered_end + 1)
            if cursor > end:
                return None
        return (cursor, end) if cursor <= end else None

    def read_file_bounds(
        arguments: dict[str, Any],
    ) -> tuple[str, int, int, int, int] | AgentToolResult:
        """Canonicalize one model-proposed file range against server bounds."""

        path = resolve_path(arguments.get("path"))
        if path is None:
            return failure("The requested path is not part of this manuscript project.")
        start_value = arguments.get("start_line", 1)
        end_value = arguments.get("end_line")
        if isinstance(start_value, bool) or not isinstance(start_value, int):
            return failure("start_line must be a positive integer.")
        if end_value is not None and (
            isinstance(end_value, bool) or not isinstance(end_value, int)
        ):
            return failure("end_line must be an integer when provided.")
        start_line = start_value
        if start_line < 1:
            return failure("start_line must be at least 1.")
        requested_end = end_value if isinstance(end_value, int) else start_line + 119
        if requested_end < start_line:
            return failure("end_line must be greater than or equal to start_line.")
        total_lines = len(project_files[path].splitlines(keepends=True) or [""])
        bounded_end = min(
            total_lines,
            requested_end,
            start_line + _AGENT_READ_MAX_LINES - 1,
        )
        return path, start_line, bounded_end, total_lines, requested_end

    def wrong_tool_result(value: str) -> AgentToolResult:
        """Redirect an evidence handle without exposing a failed tool lifecycle."""

        output: dict[str, Any]
        if evidence_tools_available:
            output = {
                "status": "wrong_tool",
                "provided_kind": "evidence_handle",
                "required_tool": "read_source",
                "handle": value,
            }
            summary = (
                "This value is an evidence handle, not a manuscript path. Use "
                "read_source for that handle or choose a listed project path."
            )
        else:
            output = {
                "status": "project_path_required",
                "provided_kind": "evidence_handle",
                "required_tools": ["search_project", "read_file"],
            }
            summary = (
                "This rewrite is scoped to the manuscript. Locate the passage with "
                "search_project and read it from a listed project path."
            )
        return AgentToolResult(
            output=output,
            summary=summary,
            success=False,
            error_code="wrong_tool",
        )

    def repair_preflight(tool_name: str) -> AgentToolResult | None:
        """Keep a pending correction on the project tools named by the server."""

        return repair_state.preflight(tool_name) if repair_state is not None else None

    def project_tool_preflight(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AgentToolResult | None:
        """Validate tool ownership and semantic read reuse before public events."""

        raw_path = str(arguments.get("path") or "").strip()
        if raw_path and _EVIDENCE_HANDLE.fullmatch(raw_path):
            return wrong_tool_result(raw_path)
        repair = repair_preflight(tool_name)
        if repair is not None:
            return repair
        if tool_name != "read_file":
            return None
        bounds = read_file_bounds(arguments)
        if isinstance(bounds, AgentToolResult):
            return None
        path, start_line, bounded_end, total_lines, _requested_end = bounds
        if start_line > total_lines:
            return AgentToolResult(
                output={
                    "status": "end_of_file",
                    "path": path,
                    "requested_start_line": start_line,
                    "next_line": total_lines + 1,
                    "total_lines": total_lines,
                    "content": "",
                },
                summary=f"Reached the end of {path} at line {total_lines}.",
            )
        if first_unread_lines(path, start_line, bounded_end) is not None:
            return None
        return AgentToolResult(
            output={
                "status": "already_available",
                "path": path,
                "start_line": start_line,
                "end_line": bounded_end,
                "total_lines": total_lines,
                "covered_ranges": [list(item) for item in read_coverage.get(path, [])],
            },
            summary=(
                "That manuscript range is already available. Continue from the existing "
                "observation without reading it again."
            ),
        )

    def list_project_files(_arguments: dict[str, Any]) -> AgentToolResult:
        files = [
            {
                "path": path,
                "active": path == active_path,
                "characters": len(project_files[path]),
                "lines": len(project_files[path].splitlines()) or 1,
            }
            for path in ordered_paths
            if path in project_files
        ]
        return AgentToolResult(
            output={
                "active_path": active_path,
                "total_files": len(files),
                "files": files,
            },
            summary=(
                f"Listed {len(files)} manuscript file"
                f"{'s' if len(files) != 1 else ''}; {active_path} is active."
            ),
        )

    def read_file(arguments: dict[str, Any]) -> AgentToolResult:
        bounds = read_file_bounds(arguments)
        if isinstance(bounds, AgentToolResult):
            return bounds
        path, start_line, bounded_end, total_lines, raw_requested_end = bounds
        source = project_files[path]
        lines = source.splitlines(keepends=True) or [""]
        if start_line > total_lines:
            return AgentToolResult(
                output={
                    "status": "end_of_file",
                    "path": path,
                    "requested_start_line": start_line,
                    "next_line": total_lines + 1,
                    "total_lines": total_lines,
                    "content": "",
                    "truncated": False,
                },
                summary=f"Reached the end of {path} at line {total_lines}.",
            )
        requested_start = start_line
        requested_end = bounded_end
        unread_range = first_unread_lines(path, start_line, bounded_end)
        if unread_range is None:
            return AgentToolResult(
                output={
                    "status": "already_available",
                    "path": path,
                    "start_line": start_line,
                    "end_line": bounded_end,
                    "total_lines": total_lines,
                    "content": "",
                    "truncated": False,
                },
                summary="That manuscript range is already available in this task.",
            )
        start_line, bounded_end = unread_range
        chunks: list[str] = []
        used = 0
        char_truncated = False
        for line in lines[start_line - 1 : bounded_end]:
            remaining = _AGENT_READ_MAX_CHARS - used
            if remaining <= 0:
                char_truncated = True
                break
            if len(line) > remaining:
                chunks.append(line[:remaining])
                used += remaining
                char_truncated = True
                break
            chunks.append(line)
            used += len(line)
        returned_end = start_line + max(0, len(chunks) - 1)
        record_read_range(path, start_line, returned_end)
        truncated = (
            char_truncated
            or start_line > requested_start
            or returned_end < requested_end
            or bounded_end < min(total_lines, raw_requested_end)
        )
        return AgentToolResult(
            output={
                "path": path,
                "requested_start_line": requested_start,
                "requested_end_line": requested_end,
                "start_line": start_line,
                "end_line": returned_end,
                "next_line": min(total_lines + 1, returned_end + 1),
                "total_lines": total_lines,
                "content": "".join(chunks),
                "truncated": truncated,
            },
            summary=(
                f"Read {path} lines {start_line}-{returned_end}"
                + (" within the safe output bound." if truncated else ".")
            ),
        )

    def search_project(arguments: dict[str, Any]) -> AgentToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return failure("query must contain at least one non-whitespace character.")
        if len(query) > 500:
            return failure("query must be 500 characters or fewer.")
        requested_path = arguments.get("path")
        if requested_path not in (None, ""):
            resolved = resolve_path(requested_path)
            if resolved is None:
                return failure("The requested path is not part of this manuscript project.")
            paths = [resolved]
        else:
            paths = ordered_paths
        raw_limit = arguments.get("max_results", 12)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
            return failure("max_results must be an integer.")
        limit = min(_AGENT_SEARCH_MAX_RESULTS, max(1, raw_limit))
        try:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        except re.error:
            return failure("The literal search query could not be compiled safely.")

        matches: list[dict[str, Any]] = []
        for path in paths:
            source = project_files[path]
            for match in pattern.finditer(source):
                snippet_start = max(0, match.start() - 320)
                snippet_end = min(len(source), match.end() + 320)
                snippet = source[snippet_start:snippet_end]
                matches.append(
                    {
                        "path": path,
                        "line": source.count("\n", 0, match.start()) + 1,
                        "start_line": source.count("\n", 0, snippet_start) + 1,
                        "end_line": source.count("\n", 0, snippet_end) + 1,
                        "match": match.group(0),
                        "snippet": snippet,
                    }
                )
                if len(matches) > limit:
                    break
            if len(matches) > limit:
                break
        truncated = len(matches) > limit
        if truncated:
            matches = matches[:limit]
        return AgentToolResult(
            output={
                "query": query,
                "matches": matches,
                "result_count": len(matches),
                "truncated": truncated,
            },
            summary=(
                f"Found {len(matches)} literal project match"
                f"{'es' if len(matches) != 1 else ''} for {query!r}."
            ),
        )

    def inspect_document_structure(_arguments: dict[str, Any]) -> AgentToolResult:
        report = _manuscript_structure_report(project_files)
        if structure_receipts is not None:
            structure_receipts.add("initial_structure")
        marker_count = int(report["marker_count"])
        return AgentToolResult(
            output=report,
            summary=(
                f"Inspected {len(report['sections'])} manuscript sections and found "
                f"{marker_count} template marker"
                f"{'s' if marker_count != 1 else ''}."
            ),
        )

    return (
        AgentTool(
            name="list_project_files",
            label="List manuscript files",
            description=(
                "List the manuscript's exact project paths, active file, line counts and sizes."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_project_files,
            max_calls=1,
            effect="read",
            preflight=lambda arguments: project_tool_preflight("list_project_files", arguments),
        ),
        AgentTool(
            name="read_file",
            label="Read manuscript source",
            description=(
                "Read an exact bounded line range from one listed project file. "
                "Use the returned raw content for exact edit anchors."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 1_000},
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_FILE,
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_FILE,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_file,
            max_calls=32,
            effect="read",
            preflight=lambda arguments: project_tool_preflight("read_file", arguments),
        ),
        AgentTool(
            name="search_project",
            label="Search manuscript source",
            description=(
                "Search exact literal text across the manuscript or one listed path and "
                "return bounded source snippets with line locations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "path": {"type": "string", "minLength": 1, "maxLength": 1_000},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _AGENT_SEARCH_MAX_RESULTS,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_project,
            max_calls=24,
            effect="read",
            preflight=lambda arguments: project_tool_preflight("search_project", arguments),
        ),
        AgentTool(
            name="inspect_document_structure",
            label="Inspect manuscript structure",
            description=(
                "Inventory LaTeX sections, empty sections and recognizable template or "
                "placeholder residue. Use this before a full-document rewrite or any "
                "request to remove boilerplate. Exact unique source anchors are included "
                "for the reported markers."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=inspect_document_structure,
            max_calls=1,
            effect="read",
            preflight=lambda arguments: project_tool_preflight(
                "inspect_document_structure", arguments
            ),
        ),
    )


def _writer_evidence_tools(
    *,
    works: list[WorkRecord],
    citations: list[dict[str, Any]],
    attachments: list[dict[str, Any]] | None,
    datasets: list[str] | None,
    interview_evidence: list[dict[str, Any]] | None,
    survey_evidence: list[dict[str, Any]] | None,
    citation_receipts: set[str],
    source_read_receipts: set[str] | None = None,
    repair_state: _WriterRepairState | None = None,
    project_paths: Collection[str] = (),
) -> tuple[AgentTool, ...]:
    """Build bounded on-demand readers over linked manuscript evidence."""

    records: list[dict[str, Any]] = []
    for index, work in enumerate(works):
        citation_key = (
            str(citations[index].get("key") or "").strip() if index < len(citations) else ""
        )
        metadata = "\n".join(
            part
            for part in (
                f"Title: {work.title}",
                f"Authors: {', '.join(work.authors)}" if work.authors else "",
                f"Year: {work.year}" if work.year else "",
                f"Venue: {work.venue}" if work.venue else "",
                f"DOI: {work.doi}" if work.doi else "",
            )
            if part
        )
        abstract = str(work.abstract or "").strip()
        if abstract:
            abstract_prefix = metadata + ("\n" if metadata else "") + "Abstract: "
            receipt_start: int | None = len(abstract_prefix)
            content = abstract_prefix + abstract
            receipt_end: int | None = len(content)
        else:
            content = metadata + ("\n" if metadata else "") + "Abstract unavailable."
            receipt_start = None
            receipt_end = None
        records.append(
            {
                "handle": f"paper:{index + 1}",
                "kind": "paper",
                "title": work.title,
                "citation_key": citation_key,
                "content": content,
                "receipt_start": receipt_start,
                "receipt_end": receipt_end,
            }
        )
    for index, attachment in enumerate(attachments or []):
        filename = str(attachment.get("filename") or f"Source {index + 1}")
        key_match = re.search(r"\(cite as ([^)]+)\)", filename)
        content = str(attachment.get("text") or "")
        has_passage = bool(content.strip())
        records.append(
            {
                "handle": f"source:{index + 1}",
                "kind": "source",
                "title": filename,
                "citation_key": key_match.group(1).strip() if key_match else "",
                "content": content,
                "receipt_start": 0 if has_passage else None,
                "receipt_end": len(content) if has_passage else None,
            }
        )
    for index, dataset in enumerate(datasets or []):
        records.append(
            {
                "handle": f"dataset:{index + 1}",
                "kind": "dataset",
                "title": f"Linked dataset {index + 1}",
                "citation_key": "",
                "content": str(dataset),
            }
        )
    for index, evidence in enumerate(interview_evidence or []):
        records.append(
            {
                "handle": f"interview:{evidence.get('interview_id') or index + 1}",
                "kind": "interview",
                "title": str(evidence.get("title") or f"Interview {index + 1}"),
                "citation_key": "",
                "content": render_interview_evidence([evidence], source_offset=index),
            }
        )
    for index, evidence in enumerate(survey_evidence or []):
        records.append(
            {
                "handle": f"survey:{evidence.get('survey_id') or index + 1}",
                "kind": "survey",
                "title": str(evidence.get("title") or f"Survey {index + 1}"),
                "citation_key": "",
                "content": render_survey_evidence([evidence], source_offset=index),
            }
        )
    by_handle = {record["handle"]: record for record in records}
    normalized_project_paths = {
        normalized for path in project_paths for normalized in (str(path), f"./{path}")
    }
    read_coverage: dict[str, list[tuple[int, int]]] = {}

    def record_read_range(handle: str, start: int, end: int) -> None:
        """Remember the exact source spans already returned to this agent turn."""

        if end <= start:
            return
        ranges = sorted([*read_coverage.get(handle, []), (start, end)])
        merged: list[tuple[int, int]] = []
        for range_start, range_end in ranges:
            if merged and range_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], range_end))
            else:
                merged.append((range_start, range_end))
        read_coverage[handle] = merged

    def first_unread_range(handle: str, start: int, end: int) -> tuple[int, int] | None:
        """Return only the first contiguous span not already present in observations."""

        cursor = start
        for covered_start, covered_end in read_coverage.get(handle, []):
            if covered_end <= cursor:
                continue
            if covered_start > cursor:
                return cursor, min(end, covered_start)
            cursor = max(cursor, covered_end)
            if cursor >= end:
                return None
        return (cursor, end) if cursor < end else None

    def citation_receipt_for_range(
        record: dict[str, Any],
        start: int,
        end: int,
    ) -> str:
        """Return a citation key only when the range overlaps real source text."""

        citation_key = str(record.get("citation_key") or "")
        receipt_start = record.get("receipt_start")
        receipt_end = record.get("receipt_end")
        if (
            not citation_key
            or not isinstance(receipt_start, int)
            or not isinstance(receipt_end, int)
            or start >= receipt_end
            or end <= receipt_start
        ):
            return ""
        overlap = str(record.get("content") or "")[
            max(start, receipt_start) : min(end, receipt_end)
        ]
        return citation_key if overlap.strip() else ""

    def list_sources(_arguments: dict[str, Any]) -> AgentToolResult:
        catalog = [
            {
                "handle": record["handle"],
                "kind": record["kind"],
                "title": record["title"],
                "citation_key": record["citation_key"] or None,
                "characters": len(record["content"]),
            }
            for record in records
        ]
        return AgentToolResult(
            output={"source_count": len(catalog), "sources": catalog},
            summary=f"Listed {len(catalog)} linked evidence sources.",
        )

    def read_source(arguments: dict[str, Any]) -> AgentToolResult:
        handle = str(arguments.get("handle") or "").strip()
        record = by_handle.get(handle)
        if record is None:
            return AgentToolResult(
                output={"error": "Unknown source handle."},
                summary="The requested source handle is not linked to this manuscript.",
                success=False,
            )
        offset = arguments.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            return AgentToolResult(
                output={"error": "offset must be a non-negative integer."},
                summary="The evidence read offset was invalid.",
                success=False,
            )
        # Chunk size is server-owned. Letting the model choose tiny windows made
        # ordinary interview reads take several avoidable round trips and enabled
        # differently-sized requests to reread content already in observations.
        size = _AGENT_READ_MAX_CHARS
        content = record["content"]
        if offset >= len(content):
            return AgentToolResult(
                output={
                    "status": "end_of_source",
                    "handle": handle,
                    "kind": record["kind"],
                    "title": record["title"],
                    "citation_key": record["citation_key"] or None,
                    "citation_receipt": None,
                    "requested_offset": offset,
                    "offset": len(content),
                    "next_offset": len(content),
                    "total_characters": len(content),
                    "content": "",
                    "truncated": False,
                },
                summary=f"Reached the end of {record['title']}.",
            )
        requested_end = min(len(content), offset + size)
        unread_range = first_unread_range(handle, offset, requested_end)
        if unread_range is None:
            guidance = repair_state.guidance if repair_state is not None else ""
            return AgentToolResult(
                output={
                    "status": "already_available",
                    "handle": handle,
                    "kind": record["kind"],
                    "title": record["title"],
                    "citation_key": record["citation_key"] or None,
                    "citation_receipt": None,
                    "offset": offset,
                    "next_offset": requested_end,
                    "total_characters": len(content),
                    "content": "",
                    "truncated": requested_end < len(content),
                    "covered_ranges": [list(item) for item in read_coverage.get(handle, [])],
                    "next_step": guidance
                    or ("Continue from the evidence already returned; do not reread it."),
                },
                summary=(
                    "That evidence range is already available in this task. "
                    + (
                        guidance
                        if guidance
                        else "Continue from the existing evidence instead of reading it again."
                    )
                ),
            )
        read_start, read_end = unread_range
        excerpt = content[read_start:read_end]
        record_read_range(handle, read_start, read_end)
        if source_read_receipts is not None and first_unread_range(handle, 0, len(content)) is None:
            source_read_receipts.add(handle)
        citation_key = record["citation_key"]
        receipt = citation_receipt_for_range(record, read_start, read_end)
        if receipt:
            citation_receipts.add(receipt)
        return AgentToolResult(
            output={
                "handle": handle,
                "kind": record["kind"],
                "title": record["title"],
                "citation_key": citation_key or None,
                "citation_receipt": receipt or None,
                "requested_offset": offset,
                "offset": read_start,
                "next_offset": read_end,
                "total_characters": len(content),
                "content": excerpt,
                "truncated": read_end < len(content),
            },
            summary=(
                f"Read {len(excerpt)} characters from {record['title']}"
                + (
                    f" with citation receipt {receipt}."
                    if receipt
                    else "; no supporting passage was read for a citation receipt."
                    if citation_key
                    else "."
                )
            ),
        )

    def read_source_preflight(arguments: dict[str, Any]) -> AgentToolResult | None:
        """Reuse covered evidence or redirect a pending manuscript repair."""

        repair = repair_state.preflight("read_source") if repair_state is not None else None
        if repair is not None:
            return repair
        handle = str(arguments.get("handle") or "").strip()
        if handle in normalized_project_paths:
            return AgentToolResult(
                output={
                    "status": "wrong_tool",
                    "provided_kind": "project_path",
                    "required_tool": "read_file",
                    "path": handle[2:] if handle.startswith("./") else handle,
                },
                summary=(
                    "This value is a manuscript path, not an evidence handle. Use "
                    "read_file for that project path."
                ),
                success=False,
                error_code="wrong_tool",
            )
        record = by_handle.get(handle)
        offset = arguments.get("offset", 0)
        if record is None or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            return None
        if offset >= len(record["content"]):
            return AgentToolResult(
                output={
                    "status": "end_of_source",
                    "handle": handle,
                    "requested_offset": offset,
                    "offset": len(record["content"]),
                    "next_offset": len(record["content"]),
                    "total_characters": len(record["content"]),
                    "content": "",
                },
                summary=f"Reached the end of {record['title']}.",
            )
        requested_end = min(len(record["content"]), offset + _AGENT_READ_MAX_CHARS)
        if first_unread_range(handle, offset, requested_end) is not None:
            return None
        return AgentToolResult(
            output={
                "status": "already_available",
                "handle": handle,
                "offset": offset,
                "next_offset": requested_end,
                "total_characters": len(record["content"]),
                "content": "",
            },
            summary=(
                "That evidence range is already available. Continue from the existing "
                "observation without reading it again."
            ),
        )

    def evidence_search_preflight(_arguments: dict[str, Any]) -> AgentToolResult | None:
        """Keep an anchor or compile repair focused on the manuscript itself."""

        return repair_state.preflight("search_evidence") if repair_state is not None else None

    def search_evidence(arguments: dict[str, Any]) -> AgentToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query or len(query) > 500:
            return AgentToolResult(
                output={"error": "query must contain 1 to 500 characters."},
                summary="The evidence search query was invalid.",
                success=False,
            )
        requested_kinds = arguments.get("kinds")
        allowed_kinds = (
            {str(kind).strip().casefold() for kind in requested_kinds if str(kind).strip()}
            if isinstance(requested_kinds, list)
            else set()
        )
        raw_limit = arguments.get("max_results", 12)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
            return AgentToolResult(
                output={"error": "max_results must be an integer."},
                summary="The evidence search result limit was invalid.",
                success=False,
            )
        limit = min(_AGENT_SEARCH_MAX_RESULTS, max(1, raw_limit))
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches: list[dict[str, Any]] = []
        for record in records:
            if allowed_kinds and record["kind"] not in allowed_kinds:
                continue
            for match in pattern.finditer(record["content"]):
                start = max(0, match.start() - 420)
                end = min(len(record["content"]), match.end() + 420)
                receipt = citation_receipt_for_range(record, match.start(), match.end())
                matches.append(
                    {
                        "handle": record["handle"],
                        "kind": record["kind"],
                        "title": record["title"],
                        "citation_key": record["citation_key"] or None,
                        "citation_receipt": receipt or None,
                        "offset": start,
                        "snippet": record["content"][start:end],
                    }
                )
                if len(matches) > limit:
                    break
            if len(matches) > limit:
                break
        truncated = len(matches) > limit
        if truncated:
            matches = matches[:limit]
        citation_receipts.update(
            str(match["citation_receipt"]) for match in matches if match.get("citation_receipt")
        )
        return AgentToolResult(
            output={
                "query": query,
                "matches": matches,
                "result_count": len(matches),
                "truncated": truncated,
            },
            summary=f"Found {len(matches)} linked evidence passages for {query!r}.",
        )

    return (
        AgentTool(
            name="list_sources",
            label="List linked evidence",
            description=(
                "List exact source handles, evidence kinds, titles, citation keys and sizes."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_sources,
            max_calls=1,
            effect="read",
        ),
        AgentTool(
            name="read_source",
            label="Read linked evidence",
            description=(
                "Read a bounded exact excerpt from one linked evidence handle. Use this "
                "before introducing that source's citation key into manuscript prose. "
                "Never reread a range already returned. After edit validation requests "
                "an anchor or compile repair, use read_file, search_project or "
                "compile_candidate instead of returning to the evidence."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "handle": {"type": "string", "minLength": 1, "maxLength": 500},
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": _MAX_PROJECT,
                    },
                },
                "required": ["handle"],
                "additionalProperties": False,
            },
            handler=read_source,
            max_calls=32,
            effect="read",
            preflight=read_source_preflight,
        ),
        AgentTool(
            name="search_evidence",
            label="Search linked evidence",
            description=(
                "Search literal passages across linked papers, sources, datasets, "
                "interviews and surveys. A matching supporting passage can create a "
                "citation receipt; title or metadata matches alone cannot."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "kinds": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "paper",
                                "source",
                                "dataset",
                                "interview",
                                "survey",
                            ],
                        },
                        "maxItems": 5,
                        "uniqueItems": True,
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _AGENT_SEARCH_MAX_RESULTS,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_evidence,
            max_calls=24,
            effect="read",
            preflight=evidence_search_preflight,
        ),
    )


def _is_adjacent_transposition(value: str, target: str) -> bool:
    """Return whether ``value`` swaps exactly one adjacent pair in ``target``."""

    if len(value) != len(target) or value == target:
        return False
    differences = [
        index for index, pair in enumerate(zip(value, target, strict=True)) if pair[0] != pair[1]
    ]
    return bool(
        len(differences) == 2
        and differences[1] == differences[0] + 1
        and value[differences[0]] == target[differences[1]]
        and value[differences[1]] == target[differences[0]]
    )


def _normalize_mutation_action_typos(message: str) -> str:
    """Correct only unambiguous adjacent swaps in explicit mutation verb stems."""

    command_match = re.match(
        r"\s{0,40}(?:(?:please|pls|bitte)\b[\s,:]{0,40})?"
        r"(?:(?:can\s+you|could\s+you|kannst\s+du|könntest\s+du|"
        r"koenntest\s+du)\b[\s,:]{0,40})?"
        r"(?:(?:please|pls|bitte)\b[\s,:]{0,40})?"
        r"(?P<action>[^\W\d_]+)",
        message,
        re.IGNORECASE | re.UNICODE,
    )
    command_action_span = command_match.span("action") if command_match is not None else None
    typo_command_context = not message.rstrip().endswith("?") and bool(
        re.search(r"\b(?:please|pls|bitte|mal|mla|neu|um)\b", message, re.IGNORECASE)
    )

    def normalize_token(match: re.Match[str]) -> str:
        token = match.group(0)
        folded = token.casefold()
        if not typo_command_context or match.span() != command_action_span:
            return folded
        for stem in _MUTATION_ACTION_STEMS:
            if len(folded) < len(stem):
                continue
            prefix = folded[: len(stem)]
            if _is_adjacent_transposition(prefix, stem):
                return stem + folded[len(stem) :]
        return folded

    return re.sub(r"[^\W\d_]+", normalize_token, message, flags=re.UNICODE)


def _requests_manuscript_mutation(message: str) -> bool:
    """Return whether the latest request clearly asks to modify this manuscript."""

    normalized = _normalize_mutation_action_typos(message)
    # A restriction on a different action is not a veto of the requested edit:
    # "rewrite this paragraph; do not publish or create sources" asks for a
    # proposal while explicitly limiting its effects. Keep negation local to
    # its clause; this classifier never authorizes applying the proposal.
    clauses = re.split(
        r"(?<=[.!?;])\s+|\n+|,\s*(?=(?:but|aber|do\s+not|don't|nothing|nicht|nichts)\b)",
        normalized,
    )
    for clause in clauses:
        if re.match(
            r"\s{0,40}(?:(?:please|pls|bitte|just|nur)\s{1,40}){0,8}"
            r"(?:explain|describe|tell\s+me|erkl(?:ä|ae)r\w*|beschreib\w*)"
            r"\s+(?:how|whether|why|wie|ob|warum)\b",
            clause,
        ):
            continue
        if _MANUSCRIPT_MUTATION_REQUEST.search(clause) and not (
            _NEGATED_MANUSCRIPT_MUTATION.search(clause)
        ):
            return True
    return False


def _clarify_pending_writer_review(
    reply: str,
    *,
    response_language: str,
    has_edits: bool,
) -> str:
    """Make the approval boundary explicit in every reply carrying source edits."""

    if not has_edits:
        claims_applied = bool(
            _WRITER_APPLIED_CLAIM.search(reply)
            and re.search(
                r"\b(?:I|we|ich|wir)\s+(?:(?:have|has|habe|haben)\s+)?"
                r"(?:(?:already|bereits)\s+)?"
                r"(?:applied|saved|published|rewrote|rewritten|revised|edited|updated|"
                r"changed|inserted|replaced|geändert|geaendert|überarbeitet|ueberarbeitet|"
                r"ersetzt|gespeichert|angewendet)\b",
                reply,
                re.IGNORECASE,
            )
        )
        if claims_applied or re.search(
            r"\b(?:ready\s+for\s+review|awaits?\s+(?:your\s+)?confirmation|"
            r"waiting\s+for\s+(?:your\s+)?approval|compiled\s+successfully|"
            r"wartet\s+auf\s+(?:deine|Ihre)\s+Bestätigung|erfolgreich\s+kompiliert)\b",
            reply,
            re.IGNORECASE,
        ):
            return (
                "Es liegt kein geprüfter Änderungsvorschlag zur Bestätigung vor. "
                "Das Manuskript wurde nicht verändert."
                if response_language.lower().startswith("de")
                else "No validated edit proposal is available for approval. "
                "The manuscript was not changed."
            )
        return reply
    german = response_language.lower().startswith("de")
    clarification = (
        "Der Änderungsvorschlag wurde noch nicht angewendet und wartet auf deine Bestätigung."
        if german
        else "The proposed change has not been applied and is waiting for your approval."
    )
    if _WRITER_APPLIED_CLAIM.search(reply):
        reply = (
            "Die angeforderte Neufassung ist als Änderungsvorschlag vorbereitet."
            if german
            else "The requested revision is prepared as a proposal."
        )
    if clarification.casefold() in reply.casefold():
        return reply
    return f"{reply.rstrip()} {clarification}".strip()


def _project_only_rewrite_request(message: str) -> bool:
    """Identify bounded passage rewrites that need manuscript source, not research."""

    normalized = _normalize_mutation_action_typos(message)
    return bool(
        _requests_manuscript_mutation(message)
        and _PROJECT_ONLY_REWRITE_TARGET.search(normalized)
        and not _EXTERNAL_CONTEXT_REQUEST.search(normalized)
    )


def _wording_only_rewrite_request(message: str) -> bool:
    """Recognize explicitly fact-preserving rewrites without changing tool scope.

    Mentioning existing synthetic data or prohibiting new sources is not a
    request for outside evidence. Positive evidence requests remain outside
    this narrow guard because their new values need source-level validation.
    """

    normalized = _normalize_mutation_action_typos(message).casefold()
    if not (
        _requests_manuscript_mutation(message)
        and _LOCAL_EDIT_REQUEST.search(normalized)
        and (
            _PROJECT_ONLY_REWRITE_TARGET.search(normalized)
            or re.search(r"\b(?:absatz|absätz\w*|absaetz\w*)\b", normalized)
        )
        and re.search(
            r"\b(?:keep|preserv(?:e|ing)|retain|maintain)\b[^.!?;]{0,60}"
            r"\b(?:meaning|facts?|counts?|numbers?|values?|details?)\b"
            r"|\b(?:keep|preserv(?:e|ing)|retain|maintain)\s+exactly\s+"
            r"(?:\d+(?:[.,]\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|"
            r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
            r"nineteen|twenty)\b"
            r"|\b(?:without\s+(?:changing|adding|inventing)|do\s+not\s+(?:change|add|invent)|"
            r"don't\s+(?:change|add|invent)|no\s+new)\b[^.!?;]{0,40}"
            r"\b(?:facts?|counts?|numbers?|values?|details?|meaning)\b"
            r"|\b(?:behalt\w*|beibehalt\w*|erhalt\w*|bewahr\w*)\b[^.!?;]{0,60}"
            r"\b(?:bedeutung|aussage\w*|fakt\w*|zahl\w*|zähl\w*|zaehl\w*|wert\w*)\b"
            r"|\b(?:keine\s+neuen|ohne\s+neue)\s+(?:fakt\w*|zahl\w*|detail\w*)\b"
            r"|\b(?:fakt\w*|zahl\w*|wert\w*|bedeutung)\b[^.!?;]{0,40}"
            r"\b(?:unverändert|unveraendert|beibehalten)\b",
            normalized,
        )
        and not re.search(
            r"\b(?:do\s+not|don't|never|without)\s+(?:keep\w*|preserv\w*|retain\w*)\b",
            normalized,
        )
    ):
        return False
    external_request = re.compile(
        r"\b(?:search|browse|look\s+up|research(?=\s+(?:the|a|an|new|latest|current)\b)|"
        r"recherchier\w*|such\w*|"
        r"use|using|consult|cite|citing|read|add|include|incorporat\w*|compare|"
        r"check|verify|fetch|retrieve|based\s+on|nutz\w*|verwend\w*|lies|"
        r"les\w*|zitier\w*|ergänz\w*|ergaenz\w*|übernimm|uebernimm|"
        r"basierend\s+auf)\b[^.!?;]{0,64}?"
        r"\b(?:web|internet|online|sources?|papers?|literature|study|studies|interviews?|"
        r"transcripts?|surveys?|datasets?|evidence|references?|quell\w*|literatur|"
        r"studie\w*|transkript\w*|umfrage\w*|datensatz\w*|datensätz\w*|beleg\w*|"
        r"(?:external|linked|uploaded|new|published|latest)\s+(?:data|results?|statistics)|"
        r"(?:externe\w*|verknüpfte\w*|neue\w*)\s+(?:daten|ergebnis\w*))\b"
    )
    clauses = re.split(r"[.!?;\n]|\b(?:but|aber)\b", normalized)
    for clause in clauses:
        for match in external_request.finditer(clause):
            negated_prefix = re.search(
                r"\b(?:do\s+not|don't|never|without|nicht|keinesfalls)\s+"
                r"(?:(?:also|ever|again|auch|bitte)\s+){0,2}$",
                clause[: match.start()],
            )
            # Keep a coordinated prohibition local: "do not change other
            # sections or add sources" does not positively request sources.
            negated_coordination = re.search(
                r"\b(?:do\s+not|don't|never)\s+"
                r"(?:change|edit|modify|publish|create|remove|delete|add)\b"
                r"[^.!?;]{0,72}\b(?:or|nor)\s+$",
                clause[: match.start()],
            )
            negated_action = re.search(r"\b(?:keine\w*|nicht(?!\s+nur\b))\b", match.group())
            if not negated_prefix and not negated_coordination and not negated_action:
                return False
    return True


def _project_only_reply(value: str, *, response_language: str) -> str:
    """Remove claims of fresh evidence work from a manuscript-only rewrite."""

    if not _WRITER_EVIDENCE_VERIFICATION_CLAIM.search(value):
        return value
    if response_language.lower().startswith("de"):
        return "Die Neufassung der vorhandenen Manuskriptpassage ist vorbereitet."
    return "The revision of the existing manuscript passage is prepared."


def _writer_public_update(
    value: str,
    *,
    response_language: str,
    project_only: bool = False,
) -> str:
    """Prevent progress copy from presenting an unapproved proposal as applied."""

    if project_only and _WRITER_EVIDENCE_VERIFICATION_CLAIM.search(value):
        if response_language.lower().startswith("de"):
            return "Die Neufassung der Manuskriptpassage wird zur Prüfung vorbereitet."
        return "The manuscript passage revision is being prepared for review."
    if not _WRITER_APPLIED_CLAIM.search(value):
        return value
    if response_language.lower().startswith("de"):
        return "Der Änderungsvorschlag wird für deine Prüfung vorbereitet."
    return "The proposed change is being prepared for your review."


def _requests_template_cleanup(message: str) -> bool:
    """Return whether a full draft must replace initial template content."""

    if not _requests_manuscript_mutation(message):
        return False
    return bool(
        _TEMPLATE_CLEANUP_REQUEST.search(message) or _FULL_DOCUMENT_DRAFT_REQUEST.search(message)
    )


def _source_anchor_around(source: str, start: int, end: int) -> str:
    """Return one exact bounded paragraph anchor around a structure marker."""

    block_start = source.rfind("\n\n", 0, start)
    block_start = 0 if block_start < 0 else block_start + 2
    block_end = source.find("\n\n", end)
    block_end = len(source) if block_end < 0 else block_end
    block = source[block_start:block_end]
    if 0 < len(block) <= 4_000 and source.count(block) == 1:
        return block
    line_start = source.rfind("\n", 0, start)
    line_start = 0 if line_start < 0 else line_start + 1
    line_end = source.find("\n", end)
    line_end = len(source) if line_end < 0 else line_end
    line = source[line_start:line_end]
    return line if 0 < len(line) <= 1_600 and source.count(line) == 1 else ""


def _without_latex_comments(value: str) -> str:
    """Remove LaTeX comments from a bounded section body for emptiness checks."""

    return "\n".join(line.split("%", 1)[0] for line in value.splitlines())


def _manuscript_structure_report(project_files: dict[str, str]) -> dict[str, Any]:
    """Inventory sections and recognizable template residue in exact source."""

    markers: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    marker_keys: set[tuple[str, str, int]] = set()
    for path in sorted(project_files):
        suffix = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
        if suffix not in {"tex", "latex", "ltx"}:
            continue
        source = project_files[path]
        headings = list(_WRITER_SECTION_HEADING.finditer(source))
        for index, heading in enumerate(headings):
            level = _WRITER_SECTION_LEVELS.get(heading.group("level").casefold(), 4)
            section_end = len(source)
            for following in headings[index + 1 :]:
                following_level = _WRITER_SECTION_LEVELS.get(
                    following.group("level").casefold(),
                    4,
                )
                if following_level <= level:
                    section_end = following.start()
                    break
            sections.append(
                {
                    "path": path,
                    "level": heading.group("level").casefold(),
                    "title": heading.group("title").strip(),
                    "line": source.count("\n", 0, heading.start()) + 1,
                }
            )
            body = source[heading.end() : section_end]
            content_boundary = re.search(
                r"\\(?:bibliographystyle|bibliography|appendix)\b|"
                r"\\end\{document\}",
                body,
                re.IGNORECASE,
            )
            if content_boundary is not None:
                body = body[: content_boundary.start()]
            if not _without_latex_comments(body).strip():
                key = (path, "empty_section", heading.start())
                marker_keys.add(key)
                markers.append(
                    {
                        "path": path,
                        "kind": "empty_section",
                        "line": source.count("\n", 0, heading.start()) + 1,
                        "match": heading.group(0),
                        "anchor": heading.group(0),
                    }
                )
        for kind, pattern in _BOILERPLATE_SIGNALS:
            for match in pattern.finditer(source):
                key = (path, kind, match.start())
                if key in marker_keys:
                    continue
                marker_keys.add(key)
                markers.append(
                    {
                        "path": path,
                        "kind": kind,
                        "line": source.count("\n", 0, match.start()) + 1,
                        "match": match.group(0)[:300],
                        "anchor": _source_anchor_around(
                            source,
                            match.start(),
                            match.end(),
                        ),
                    }
                )
    markers.sort(key=lambda item: (str(item["path"]), int(item["line"]), str(item["kind"])))
    strong_marker_count = sum(
        1 for marker in markers if marker["kind"] in _STRONG_BOILERPLATE_KINDS
    )
    return {
        # Generic words such as "placeholder" can be the legitimate subject of
        # a paper. Infer a template only from a known scaffold signal; explicit
        # cleanup requests still receive the complete marker inventory below.
        "template_like": strong_marker_count > 0,
        "strong_marker_count": strong_marker_count,
        "marker_count": len(markers),
        "markers": markers[:80],
        "sections": sections[:120],
    }


def _remaining_cleanup_markers(
    initial_report: dict[str, Any],
    current_report: dict[str, Any],
    *,
    strict_weak_cleanup: bool,
) -> list[dict[str, Any]]:
    """Return scaffold residue without banning newly authored domain terms."""

    current = [marker for marker in current_report.get("markers") or [] if isinstance(marker, dict)]
    if strict_weak_cleanup:
        return current
    initial_signatures = {
        (
            str(marker.get("path") or ""),
            str(marker.get("kind") or ""),
            str(marker.get("anchor") or marker.get("match") or ""),
        )
        for marker in initial_report.get("markers") or []
        if isinstance(marker, dict)
    }
    return [
        marker
        for marker in current
        if str(marker.get("kind") or "") in _STRONG_BOILERPLATE_KINDS
        or (
            str(marker.get("path") or ""),
            str(marker.get("kind") or ""),
            str(marker.get("anchor") or marker.get("match") or ""),
        )
        in initial_signatures
    ]


def _project_after_writer_edits(
    project_files: dict[str, str],
    edits: list[dict[str, Any]],
) -> dict[str, str]:
    """Project an already validated ordered edit set onto an in-memory project."""

    projected = dict(project_files)
    for edit in edits:
        path = str(edit.get("path") or "")
        find = str(edit.get("find") or "")
        replace = str(edit.get("replace") or "")
        source = projected.get(path)
        if source is None or not find or source.count(find) != 1:
            continue
        projected[path] = source.replace(find, replace, 1)
    return projected


def _writer_target_names(value: str) -> set[str]:
    """Return manuscript targets explicitly named by text or a project path."""

    return {name for name, pattern in _WRITER_TARGET_REQUEST_PATTERNS if pattern.search(value)}


def _writer_target_ranges(source: str, path: str) -> dict[str, list[tuple[int, int]]]:
    """Locate bounded title, abstract and section blocks in one LaTeX file."""

    ranges: dict[str, list[tuple[int, int]]] = {}
    for target in _writer_target_names(path.replace("/", " ").replace("_", " ")):
        ranges.setdefault(target, []).append((0, len(source)))
    for match in re.finditer(r"\\title(?:\[[^\]]*\])?\{", source, re.IGNORECASE):
        line_end = source.find("\n", match.start())
        ranges.setdefault("title", []).append(
            (match.start(), len(source) if line_end < 0 else line_end)
        )
    for match in re.finditer(
        r"\\begin\{abstract\}(?P<body>.*?)\\end\{abstract\}",
        source,
        re.IGNORECASE | re.DOTALL,
    ):
        ranges.setdefault("abstract", []).append((match.start(), match.end()))
    headings = list(_WRITER_SECTION_HEADING.finditer(source))
    for index, heading in enumerate(headings):
        level = _WRITER_SECTION_LEVELS.get(heading.group("level").casefold(), 4)
        end = len(source)
        for following in headings[index + 1 :]:
            following_level = _WRITER_SECTION_LEVELS.get(
                following.group("level").casefold(),
                4,
            )
            if following_level <= level:
                end = following.start()
                break
        for target in _writer_target_names(heading.group("title")):
            ranges.setdefault(target, []).append((heading.start(), end))
    return ranges


def _writer_edit_target_coverage(
    edits: list[dict[str, Any]],
    project_files: dict[str, str],
) -> set[str]:
    """Project applicable edits and return the manuscript targets they touch."""

    covered: set[str] = set()
    projected = dict(project_files)
    for edit in edits:
        if edit.get("applicable") is not True:
            continue
        path = str(edit.get("path") or "")
        find = str(edit.get("find") or "")
        replacement = str(edit.get("replace") or "")
        source = projected.get(path)
        if source is None or not find or source.count(find) != 1:
            continue
        start = source.find(find)
        end = start + len(find)
        for target, target_ranges in _writer_target_ranges(source, path).items():
            if any(
                start < target_end and end > target_start
                for target_start, target_end in target_ranges
            ):
                covered.add(target)
        if re.search(r"\\title(?:\[[^\]]*\])?\{", replacement, re.IGNORECASE):
            covered.add("title")
        if re.search(r"\\begin\{abstract\}", replacement, re.IGNORECASE):
            covered.add("abstract")
        for heading in _WRITER_SECTION_HEADING.finditer(replacement):
            covered.update(_writer_target_names(heading.group("title")))
        projected[path] = source.replace(find, replacement, 1)
    return covered


@dataclass
class AssistantTurn:
    reply: str
    edits: list[dict[str, Any]] = field(default_factory=list)
    visual_request: dict[str, Any] | None = None
    workspace_actions: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] | None = None
    tools_used: tuple[str, ...] = field(default_factory=tuple)


def _transcript_timestamp(milliseconds: int) -> str:
    """Render a transcript offset in the same compact form shown to the model."""

    seconds = max(0, milliseconds // 1_000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _contains_source_text(haystack: str, source_text: str) -> bool:
    """Match exact evidence in either plain text or its safe LaTeX form."""

    folded = haystack.casefold()
    raw = source_text.strip()
    if not raw:
        return False
    return raw.casefold() in folded or tex_escape(raw).casefold() in folded


def _has_requested_interview_attribution(
    message: str,
    edits: list[dict[str, Any]],
    interview_evidence: list[dict[str, Any]],
    project_files: dict[str, str] | None = None,
) -> bool:
    """Verify requested attribution in the source produced by the edit sequence.

    Models occasionally return two individually applicable edits for the same
    anchor. Looking at replacement strings in isolation can then approve a
    quote that disappears when the edits are applied in order. Projecting the
    sequence first checks what the author would actually receive.
    """

    require_quote = bool(_INTERVIEW_QUOTE_REQUEST.search(message))
    require_speaker = bool(_INTERVIEW_SPEAKER_REQUEST.search(message))
    require_timestamp = bool(_INTERVIEW_TIMESTAMP_REQUEST.search(message))
    if not (require_quote or require_speaker or require_timestamp):
        return True
    applied_edits: list[tuple[str, str]] = []
    if project_files is None:
        proposed = "\n".join(
            str(edit.get("replace") or "") for edit in edits if edit.get("applicable")
        ).casefold()
    else:
        projected = dict(project_files)
        for edit in edits:
            if not edit.get("applicable"):
                continue
            path = str(edit.get("path") or "")
            find = str(edit.get("find") or "")
            if not path or not find or path not in projected:
                continue
            if projected[path].count(find) != 1:
                continue
            replacement = str(edit.get("replace") or "")
            applied_edits.append((find, replacement))
            projected[path] = projected[path].replace(find, replacement, 1)
        proposed = "\n".join(projected.values()).casefold()
    if not proposed:
        return False
    for context in interview_evidence:
        for passage in context.get("passages") or []:
            if not isinstance(passage, dict):
                continue
            quote = str(passage.get("text") or "").strip()
            speaker = str(passage.get("speaker") or "").strip().casefold()
            timestamp = _transcript_timestamp(int(passage.get("start_ms") or 0))
            quote_ok = not require_quote or _contains_source_text(proposed, quote)
            if quote_ok and require_quote and project_files is not None:
                # An earlier copy elsewhere in the manuscript must not mask an
                # edit that corrupts the exact source quote it replaces. Every
                # applicable edit that touches this quote has to preserve it in
                # its own replacement.
                touching_replacements = [
                    replacement
                    for find, replacement in applied_edits
                    if _contains_source_text(find, quote)
                ]
                if touching_replacements:
                    quote_ok = all(
                        _contains_source_text(replacement, quote)
                        for replacement in touching_replacements
                    )
            speaker_ok = not require_speaker or bool(speaker and speaker in proposed)
            timestamp_ok = not require_timestamp or timestamp in proposed
            if quote_ok and speaker_ok and timestamp_ok:
                return True
    return False


def _repair_requested_interview_attribution(
    message: str,
    edits: list[dict[str, Any]],
    interview_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add requested source labels from one exact linked transcript passage.

    This fallback is intentionally narrow. It only augments an already
    applicable edit whose proposed prose overlaps the linked interview
    evidence. It never invents a quote, speaker or timestamp and never creates
    a replacement research artifact.
    """

    require_quote = bool(_INTERVIEW_QUOTE_REQUEST.search(message))
    require_speaker = bool(_INTERVIEW_SPEAKER_REQUEST.search(message))
    require_timestamp = bool(_INTERVIEW_TIMESTAMP_REQUEST.search(message))
    if not (require_quote or require_speaker or require_timestamp):
        return edits
    for context in interview_evidence:
        passages = context.get("passages") or []
        analysis_text = str(context.get("analysis_text") or "")
        for passage in passages:
            if not isinstance(passage, dict):
                continue
            quote = str(passage.get("text") or "").strip()
            speaker = str(passage.get("speaker") or "").strip()
            timestamp = _transcript_timestamp(int(passage.get("start_ms") or 0))
            evidence_terms = {
                token.casefold()
                for token in re.findall(r"[^\W\d_]{5,}", f"{analysis_text} {quote}")
            }
            for index, edit in enumerate(edits):
                if not edit.get("applicable"):
                    continue
                replacement = str(edit.get("replace") or "")
                replacement_terms = {
                    token.casefold() for token in re.findall(r"[^\W\d_]{5,}", replacement)
                }
                if not evidence_terms.intersection(replacement_terms):
                    continue
                additions: list[str] = []
                folded = replacement.casefold()
                if require_quote and quote and not _contains_source_text(folded, quote):
                    additions.append(f"``{tex_escape(quote)}''")
                labels: list[str] = []
                if require_speaker and speaker and speaker.casefold() not in folded:
                    labels.append(tex_escape(speaker))
                if require_timestamp and timestamp not in replacement:
                    labels.append(timestamp)
                if labels:
                    additions.append(f"({', '.join(labels)})")
                if not additions:
                    return edits
                repaired = [dict(item) for item in edits]
                repaired[index]["replace"] = replacement.rstrip() + " " + " ".join(additions)
                return repaired
    return edits


def _citation_keys(source: str) -> set[str]:
    return {
        key.strip()
        for match in _CITATION_COMMAND.finditer(source)
        for key in match.group(1).split(",")
        if key.strip()
    }


def _asset_references(source: str) -> set[str]:
    return {
        match.group(1).strip().removeprefix("./")
        for match in _INCLUDE_GRAPHICS.finditer(source)
        if match.group(1).strip()
    }


def _known_asset_references(assets: list[str]) -> set[str]:
    known: set[str] = set()
    for raw in assets:
        asset = raw.strip().removeprefix("./")
        if not asset:
            continue
        known.add(asset)
        if "." in asset.rsplit("/", 1)[-1]:
            known.add(asset.rsplit(".", 1)[0])
    return known


def _edit_integrity_errors(
    *,
    find: str,
    replace: str,
    known_citations: set[str],
    known_assets: set[str],
    allow_new_references: bool,
) -> list[str]:
    """Reject only references newly introduced by this exact replacement."""

    introduced_citations = _citation_keys(replace) - _citation_keys(find)
    introduced_assets = _asset_references(replace) - _asset_references(find)
    errors: list[str] = []
    if not allow_new_references and introduced_citations:
        errors.append("New citations were not requested for this local edit")
    else:
        unknown_citations = sorted(introduced_citations - known_citations)
        if unknown_citations:
            errors.append("Unknown citation keys: " + ", ".join(unknown_citations))
    if not allow_new_references and introduced_assets:
        errors.append("New figures were not requested for this local edit")
    else:
        unknown_assets = sorted(introduced_assets - known_assets)
        if unknown_assets:
            errors.append("Unknown figure files: " + ", ".join(unknown_assets))
    return errors


def _rewrite_numeric_facts(text: str, *, include_words: bool = True) -> set[str]:
    """Normalize numeric literals and common EN/DE number words for a rewrite guard.

    This is a narrow fabrication check, not semantic entailment: it rejects new
    numbers but cannot prove that existing numbers keep their original meaning.
    """

    # Citation/asset identifiers are not prose evidence.
    prose = re.sub(
        r"\\(?:cite\w{0,20}|ref|eqref|label|includegraphics|url|href)"
        r"\*?(?:\[[^\]\n]{0,400}\])?\{[^}\n]{0,2000}\}",
        " ",
        text,
    )
    facts = {
        format(Decimal(match.replace(",", ".")).normalize(), "f")
        for match in re.findall(r"(?<![\w])\d{1,15}(?:[.,]\d{1,15})?(?![\w])", prose)
    }
    if not include_words:
        return facts
    words = (
        "zero null",
        "one eins first",
        "two zwei second",
        "three drei third",
        "four vier fourth",
        "five fünf fuenf fifth",
        "six sechs sixth",
        "seven sieben seventh",
        "eight acht eighth",
        "nine neun ninth",
        "ten zehn tenth",
        "eleven elf eleventh",
        "twelve zwölf zwoelf twelfth",
        "thirteen dreizehn",
        "fourteen vierzehn",
        "fifteen fünfzehn fuenfzehn",
        "sixteen sechzehn",
        "seventeen siebzehn",
        "eighteen achtzehn",
        "nineteen neunzehn",
        "twenty zwanzig",
    )
    tokens = set(re.findall(r"\b\w+\b", prose.casefold()))
    for value, variants in enumerate(words):
        if tokens.intersection(variants.split()):
            facts.add(str(value))
    return facts


def _validated_writer_edits(
    data: dict[str, Any],
    *,
    project_files: dict[str, str],
    active_path: str,
    known_citations: set[str],
    known_assets: set[str],
    allow_new_references: bool,
    rewrite_request: str | None = None,
) -> list[dict[str, Any]]:
    """Validate edits in the same order in which the client can apply them."""

    edits: list[dict[str, Any]] = []
    projected_files = dict(project_files)
    raw_edits = data.get("edits")
    edit_items = raw_edits if isinstance(raw_edits, list) else []
    for item in edit_items[:MAX_WRITER_EDITS]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or active_path).strip()
        find = str(item.get("find", ""))
        replace = str(item.get("replace", ""))
        source = projected_files.get(path)
        if not find or source is None or find == replace:
            continue
        occurrences = source.count(find)
        integrity_errors = _edit_integrity_errors(
            find=find,
            replace=replace,
            known_citations=known_citations,
            known_assets=known_assets,
            allow_new_references=allow_new_references,
        )
        if rewrite_request is not None:
            unsupported_numbers = _rewrite_numeric_facts(
                replace, include_words=False
            ) - _rewrite_numeric_facts(find + "\n" + rewrite_request)
            if unsupported_numbers:
                integrity_errors.append(
                    "The wording-only rewrite introduced unsupported numeric details: "
                    + ", ".join(sorted(unsupported_numbers))
                    + ". Preserve aggregate counts without inventing individual case IDs "
                    "or outcomes."
                )
        applicable = occurrences == 1 and not integrity_errors
        edits.append(
            {
                "path": path,
                "find": find,
                "replace": replace,
                "applicable": applicable,
                "occurrences": occurrences,
                "integrity_errors": integrity_errors,
            }
        )
        if applicable:
            projected_files[path] = source.replace(find, replace, 1)
    return edits


def _writer_identity_receipts(
    raw_edits: Any,
    *,
    project_files: dict[str, str],
    active_path: str,
    message: str,
    selection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Verify exact no-change anchors within a source-backed requested scope.

    Identity proves only that the proposal equals existing text, not that every
    semantic requirement was satisfied. Unknown targets remain unverified.
    """

    if not isinstance(raw_edits, list) or not 1 <= len(raw_edits) <= MAX_WRITER_EDITS:
        return []
    named_paths = set(re.findall(r"(?<![\w/])[\w./-]{1,200}\.(?:tex|latex|ltx)\b", message))
    if named_paths - project_files.keys():
        return []
    targets: list[tuple[str, int, int]] = []
    if selection and selection.get("kind") == "source":
        path = str(selection.get("path") or active_path)
        quote = selection.get("quote")
        source = project_files.get(path)
        if isinstance(quote, str) and quote and source is not None and source.count(quote) == 1:
            start = source.index(quote)
            targets.append((path, start, start + len(quote)))
    # Match actual headings from the owned project rather than expanding the
    # general mutation/intent parser. Merely naming a nonexistent section is
    # not sufficient to prove a no-op target.
    for path, source in project_files.items():
        if named_paths and path not in named_paths:
            continue
        headings = list(_WRITER_SECTION_HEADING.finditer(source))
        for index, heading in enumerate(headings):
            title = heading.group("title").strip()
            if not title or not re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", message, re.I):
                continue
            end = len(source)
            level = _WRITER_SECTION_LEVELS.get(heading.group("level").casefold(), 4)
            for following in headings[index + 1 :]:
                if _WRITER_SECTION_LEVELS.get(following.group("level").casefold(), 4) <= level:
                    end = following.start()
                    break
            targets.append((path, heading.start(), end))
    if not targets:
        return []
    receipts: list[dict[str, Any]] = []
    covered: set[int] = set()
    for item in raw_edits:
        if not isinstance(item, dict):
            return []
        raw_path, find, replacement = (
            item.get("path"),
            item.get("find"),
            item.get("replace"),
        )
        if not isinstance(raw_path, str) or not isinstance(find, str) or not find:
            return []
        path = raw_path
        source = project_files.get(path)
        if (
            source is None
            or replacement != find
            or source.count(find) != 1
            or (named_paths and path not in named_paths)
        ):
            return []
        start, end = source.index(find), source.index(find) + len(find)
        matching = {
            index
            for index, (target_path, target_start, target_end) in enumerate(targets)
            if path == target_path and start >= target_start and end <= target_end
        }
        if not matching:
            return []
        covered.update(matching)
        receipts.append({"path": path, "find": find, "replace": find})
    if len(covered) != len(targets):
        return []
    known_targets = _writer_target_names(message)
    if known_targets and not known_targets.issubset(
        _writer_edit_target_coverage(
            [{**receipt, "applicable": True} for receipt in receipts], project_files
        )
    ):
        return []
    return receipts


def _edit_anchor_catalog(
    project_files: dict[str, str],
    active_path: str,
    *,
    limit: int = 20,
) -> str:
    """Return bounded verbatim source blocks a repair pass may safely target."""

    ordered_paths = [
        active_path,
        *sorted(path for path in project_files if path != active_path),
    ]
    catalog: list[dict[str, str]] = []
    for path in ordered_paths:
        source = project_files.get(path, "")
        blocks = source.split("\n\n")
        for block in reversed(blocks):
            if not block or len(block) > 1_600 or source.count(block) != 1:
                continue
            catalog.append({"path": path, "find": block})
            if len(catalog) >= limit:
                return json.dumps(catalog, ensure_ascii=False)
    return json.dumps(catalog, ensure_ascii=False)


_SECTION_DELETE_REQUEST = re.compile(
    r"\b(?:delete|remove|lösch\w*|loesch\w*|entfern\w*)\b",
    re.IGNORECASE,
)
_SECTION_TARGETS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"\b(?:results?|findings?|ergebnis\w*|befund\w*)\b", re.I),
        ("result", "finding", "ergebnis", "befund"),
    ),
    (
        re.compile(r"\b(?:discussion|diskussion)\w*\b", re.I),
        ("discussion", "diskussion"),
    ),
    (
        re.compile(r"\b(?:methods?|methodology|methodik|methode\w*)\b", re.I),
        ("method", "methodology", "methodik", "methode"),
    ),
    (
        re.compile(r"\b(?:conclusion|conclusions|fazit|schlussfolgerung\w*)\b", re.I),
        ("conclusion", "fazit", "schlussfolgerung"),
    ),
    (
        re.compile(r"\b(?:introduction|einleitung)\w*\b", re.I),
        ("introduction", "einleitung"),
    ),
)
_LATEX_SECTION = re.compile(
    r"(?m)^\\(?P<command>section|subsection|subsubsection)\*?\{(?P<title>[^{}]+)\}"
)


def _deterministic_section_deletion(
    message: str,
    project_files: dict[str, str],
    active_path: str,
) -> dict[str, str] | None:
    """Locate one explicitly named LaTeX section for a review-only deletion.

    This is a narrow fallback for a provider that acknowledges a destructive
    request but omits the concrete edit. It never applies the deletion. The
    client receives the exact source block as a proposal and retains the
    normal explicit author-confirmation step.
    """

    if not _SECTION_DELETE_REQUEST.search(message):
        return None
    aliases: tuple[str, ...] | None = None
    for request_pattern, candidate_aliases in _SECTION_TARGETS:
        if request_pattern.search(message):
            aliases = candidate_aliases
            break
    if aliases is None:
        return None
    paths = [
        active_path,
        *sorted(path for path in project_files if path != active_path),
    ]
    for path in paths:
        source = project_files.get(path, "")
        matches = list(_LATEX_SECTION.finditer(source))
        for index, match in enumerate(matches):
            title = match.group("title").casefold()
            if not any(alias in title for alias in aliases):
                continue
            command = match.group("command")
            level = {"section": 1, "subsection": 2, "subsubsection": 3}[command]
            end = len(source)
            for following in matches[index + 1 :]:
                following_level = {
                    "section": 1,
                    "subsection": 2,
                    "subsubsection": 3,
                }[following.group("command")]
                if following_level <= level:
                    end = following.start()
                    break
            block = source[match.start() : end]
            if block and source.count(block) == 1:
                return {"path": path, "find": block, "replace": ""}
    return None


def _visual_evidence_context(
    project_files: dict[str, str],
    works: list[WorkRecord],
    datasets: list[str] | None,
    interview_evidence: list[dict[str, Any]] | None,
    survey_evidence: list[dict[str, Any]] | None,
) -> str:
    """Provide exact evidence without artificial source line numbers."""

    parts = ["\n".join(project_files.values())[:20_000]]
    if datasets:
        parts.append("\n".join(datasets)[:20_000])
    if interview_evidence:
        parts.append(render_interview_evidence(interview_evidence)[:12_000])
    if survey_evidence:
        parts.append(render_survey_evidence(survey_evidence)[:12_000])
    if works:
        parts.append(
            "\n".join(f"{work.title}: {(work.abstract or '')[:600]}" for work in works[:20])
        )
    return "\n\n".join(part for part in parts if part.strip())


def _numbered(source: str) -> str:
    lines = source.splitlines()
    return "\n".join(f"{i:4d}| {line}" for i, line in enumerate(lines, start=1))


def _numbered_excerpt(source: str, start: int, end: int) -> str:
    """Render a bounded source excerpt while preserving original line numbers."""

    line_start = source.rfind("\n", 0, max(0, start)) + 1
    next_break = source.find("\n", min(len(source), end))
    line_end = len(source) if next_break < 0 else next_break
    first_line = source.count("\n", 0, line_start) + 1
    return "\n".join(
        f"{line_number:4d}| {line}"
        for line_number, line in enumerate(
            source[line_start:line_end].splitlines(),
            start=first_line,
        )
    )


def _approximate_quote_offset(source: str, quote: str) -> int | None:
    """Locate rendered PDF text in its LaTeX source without exact equality.

    PDF selections lose commands, braces and non-breaking spaces. Score small
    windows around distinctive selected words and accept only strong overlap,
    so a local rewrite can stay bounded without guessing at an unrelated
    passage.
    """

    words = [word.casefold() for word in re.findall(r"[^\W_]{3,}", quote, re.UNICODE)]
    if len(words) < 3:
        return None
    folded = source.casefold()
    best: tuple[float, int] | None = None
    for anchor in dict.fromkeys(words[:16]):
        for match in re.finditer(re.escape(anchor), folded):
            start = max(0, match.start() - 1_200)
            end = min(len(source), match.start() + 3_600)
            window_words = set(re.findall(r"[^\W_]{3,}", folded[start:end], re.UNICODE))
            overlap = sum(word in window_words for word in words[:24]) / min(len(words), 24)
            if best is None or overlap > best[0]:
                best = (overlap, match.start())
    return best[1] if best is not None and best[0] >= 0.6 else None


def _local_project_context(
    project_files: dict[str, str],
    active_path: str,
    selection: dict[str, Any] | None,
) -> list[str]:
    """Prefer the exact selected source region over an entire large project."""

    quote = writer_selection_matching_quote(selection)
    explicit_path = str((selection or {}).get("source_path") or (selection or {}).get("path") or "")
    candidates = [
        explicit_path,
        active_path,
        *sorted(project_files),
    ]
    ordered_paths = list(dict.fromkeys(path for path in candidates if path in project_files))
    target_path = ""
    target_offset: int | None = None
    if quote:
        for path in ordered_paths:
            source = project_files[path]
            offset = source.find(quote)
            if offset < 0:
                approximate = _approximate_quote_offset(source, quote)
                offset = approximate if approximate is not None else -1
            if offset >= 0:
                target_path = path
                target_offset = offset
                break
    if target_offset is None and explicit_path in project_files:
        try:
            selected_line = max(
                1,
                int((selection or {}).get("source_line") or (selection or {}).get("line") or 1),
            )
        except (TypeError, ValueError):
            selected_line = 1
        source = project_files[explicit_path]
        target_path = explicit_path
        target_offset = sum(len(line) + 1 for line in source.splitlines()[: selected_line - 1])

    if target_offset is not None:
        source = project_files[target_path]
        start = max(0, target_offset - _LOCAL_TARGET_BEFORE)
        end = min(len(source), target_offset + max(len(quote), 1) + _LOCAL_TARGET_AFTER)
        excerpts = [
            f"FILE: {target_path}"
            f"{' (ACTIVE)' if target_path == active_path else ''}"
            " (SELECTION REGION)\n"
            f"{_numbered_excerpt(source, start, end)}"
        ]
        if active_path != target_path and active_path in project_files:
            active = project_files[active_path][:_LOCAL_TARGET_BEFORE]
            excerpts.append(f"FILE: {active_path} (ACTIVE)\n{_numbered(active)}")
        return excerpts

    remaining = _LOCAL_PROJECT_MAX
    excerpts = []
    for path in ordered_paths:
        if remaining <= 0:
            break
        limit = min(14_000 if path == active_path else 4_000, remaining)
        source = project_files[path][:limit]
        remaining -= len(source)
        excerpts.append(
            f"FILE: {path}{' (ACTIVE)' if path == active_path else ''}\n{_numbered(source)}"
        )
    return excerpts


def local_writer_project_context_chars(
    project_files: dict[str, str],
    active_path: str,
    selection: dict[str, Any] | None,
) -> int:
    """Measure the exact bounded file excerpts rendered for usage accounting."""

    return len("\n\n".join(_local_project_context(project_files, active_path, selection)))


def writer_selection_prompt(
    selection: dict[str, Any] | None,
    active_path: str,
) -> str:
    """Render the exact bounded selection block sent to the Writer model."""

    if not selection:
        return ""
    quote = writer_selection_matching_quote(selection)
    if selection.get("kind") == "source":
        line = selection.get("line")
        line_end = selection.get("line_end")
        selection_path = str(selection.get("path") or active_path)
        return (
            f"HIGHLIGHTED SOURCE SELECTION IN {selection_path}"
            + (
                f" (lines {line}\u2013{line_end})"
                if line and line_end and line_end != line
                else (f" (near line {line})" if line else "")
            )
            + f":\n<selection>\n{quote}\n</selection>\n"
            "The user selected this text directly in the LaTeX source; "
            "target your edits at exactly this selection unless they ask "
            "for something broader."
        )
    source_hint = ""
    if selection.get("source_path") and selection.get("source_line"):
        source_hint = (
            " Server-verified SyncTeX source anchor: "
            f"{selection['source_path']}, near line {selection['source_line']}."
        )
    return (
        "HIGHLIGHTED PDF PASSAGE ("
        f"{writer_pdf_page_label(selection)}):\n"
        f"<selection>\n{quote}\n</selection>\n"
        + source_hint
        + "\nLocate the LaTeX source producing this passage before editing."
    )


def is_local_writer_edit_request(
    message: str,
    selection: dict[str, Any] | None,
    *,
    has_compile_errors: bool = False,
) -> bool:
    """Return whether a selected-text edit can skip unrelated research context.

    This is intentionally narrow. Ambiguous writing requests, citation work,
    data-backed drafting, compile repair and cross-feature actions retain the
    iterative project/evidence catalogs and their on-demand tools.
    """

    quote = writer_selection_matching_quote(selection)
    if not quote or has_compile_errors:
        return False
    if _BROAD_WRITER_REQUEST.search(message):
        return False
    if not _LOCAL_EDIT_REQUEST.search(message):
        return False
    if _EXTERNAL_CONTEXT_REQUEST.search(message):
        return False
    return not workspace_actions_requested(message)


def build_context(
    project_files: dict[str, str],
    active_path: str,
    citations: list[dict[str, Any]],
    assets: list[str],
    works: list[WorkRecord],
    compile_errors: list[dict[str, Any]] | None = None,
    compile_log: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    datasets: list[str] | None = None,
    interview_evidence: list[dict[str, Any]] | None = None,
    survey_evidence: list[dict[str, Any]] | None = None,
    include_research_context: bool = True,
    selection: dict[str, Any] | None = None,
    project_manifest_only: bool = False,
) -> str:
    rendered_files: list[str]
    if project_manifest_only:
        ordered_paths = [
            active_path,
            *sorted(path for path in project_files if path != active_path),
        ]
        manifest = [
            {
                "path": path,
                "active": path == active_path,
                "characters": len(project_files[path]),
                "lines": len(project_files[path].splitlines()) or 1,
            }
            for path in ordered_paths
            if path in project_files
        ]
        rendered_files = [json.dumps(manifest, ensure_ascii=False)]
    elif include_research_context:
        remaining = _MAX_PROJECT
        rendered_files = []
        ordered_paths = [
            active_path,
            *sorted(path for path in project_files if path != active_path),
        ]
        for path in ordered_paths:
            source = project_files.get(path)
            if source is None or remaining <= 0:
                continue
            excerpt = source[: min(_MAX_FILE, remaining)]
            remaining -= len(excerpt)
            rendered_files.append(
                f"FILE: {path}{' (ACTIVE)' if path == active_path else ''}\n{_numbered(excerpt)}"
            )
    else:
        rendered_files = _local_project_context(project_files, active_path, selection)
    project_heading = (
        "LATEX PROJECT MANIFEST (use the read-only project tools to inspect exact "
        "source before proposing edits):"
        if project_manifest_only
        else (
            "LATEX PROJECT (line-numbered by file; `find` strings must match the "
            "raw text WITHOUT the number prefix and every edit must name its FILE path):"
        )
    )
    parts = [project_heading, "\n\n".join(rendered_files)]
    if compile_errors:
        listed = "\n".join(
            f"- line {e.get('line') or '?'}: {e.get('message', '')}" for e in compile_errors[:10]
        )
        parts.append(
            "LATEST COMPILE FAILED. Errors:\n"
            f"{listed}\n" + (f"Log tail:\n{compile_log[-1200:]}" if compile_log else "")
        )
    if not include_research_context:
        parts.append(
            "LOCAL EDIT MODE: only the manuscript and selected passage are "
            "relevant to this turn. Do not add citations, evidence, data or "
            "assets that the user did not request."
        )
    elif citations:
        listed = "\n".join(
            f"- {c['key']}: {c['title']} ({c.get('year') or 'n.d.'})" for c in citations[:40]
        )
        parts.append(f"CITABLE INCLUDES (BibTeX keys):\n{listed}")
    else:
        parts.append("CITABLE INCLUDES: none linked yet — do not cite anything.")
    if include_research_context and assets:
        parts.append("UPLOADED FIGURES: " + ", ".join(assets[:20]))
    if include_research_context and attachments:
        if project_manifest_only:
            catalog = "\n".join(
                f"- source:{index}: {item.get('filename', '?')} "
                f"({len(str(item.get('text') or ''))} characters)"
                for index, item in enumerate(attachments, start=1)
            )
            parts.append(
                "LINKED USER-OWNED SOURCE CATALOG (use read_source or "
                f"search_evidence for passages):\n{catalog}"
            )
        else:
            excerpts = "\n\n".join(
                f"[{a.get('filename', '?')}]\n{str(a.get('text', ''))[:3000]}"
                for a in attachments[:3]
            )
            parts.append(
                "ATTACHED PDF DOCUMENTS / USER-OWNED SOURCE TEXT (each filename "
                "is paired with its citable BibTeX key in CITABLE INCLUDES):\n" + excerpts
            )
    if include_research_context and datasets:
        if project_manifest_only:
            parts.append(
                "LINKED DATASET CATALOG (use read_source or search_evidence for "
                "exact profiled values):\n"
                + "\n".join(
                    f"- dataset:{index}: {len(str(dataset))} characters"
                    for index, dataset in enumerate(datasets, start=1)
                )
            )
        else:
            parts.append("PRIMARY RESEARCH DATA (exact profiled values):\n" + "\n\n".join(datasets))
    if include_research_context and interview_evidence:
        if project_manifest_only:
            parts.append(
                "LINKED INTERVIEW CATALOG (use read_source or search_evidence for "
                "analysis and retrieved transcript passages):\n"
                + "\n".join(
                    f"- interview:{item.get('interview_id') or index}: "
                    f"{item.get('title') or 'Untitled interview'}; mode={item.get('mode')}"
                    for index, item in enumerate(interview_evidence, start=1)
                )
            )
        else:
            parts.append(
                "LINKED INTERVIEW EVIDENCE (source-separated qualitative evidence; "
                "transcript passages are bounded retrievals, not the full recording):\n"
                + render_interview_evidence(interview_evidence)
            )
    if include_research_context and survey_evidence:
        if project_manifest_only:
            parts.append(
                "LINKED SURVEY CATALOG (use read_source or search_evidence for "
                "instrument wording and descriptive results):\n"
                + "\n".join(
                    f"- survey:{item.get('survey_id') or index}: "
                    f"{item.get('title') or 'Untitled survey'}; mode={item.get('mode')}"
                    for index, item in enumerate(survey_evidence, start=1)
                )
            )
        else:
            parts.append(
                "LINKED SURVEY EVIDENCE (instrument structure plus exact descriptive "
                "results; response rows are bounded, de-identified retrievals):\n"
                + render_survey_evidence(survey_evidence)
            )
    if include_research_context and works:
        if project_manifest_only:
            paper_catalog_rows: list[str] = []
            for index, work in enumerate(works, start=1):
                citation_key = citations[index - 1].get("key") if index <= len(citations) else ""
                paper_catalog_rows.append(
                    f"- paper:{index}: {work.title}; citation_key={citation_key}; "
                    f"abstract_characters={len(work.abstract or '')}"
                )
            parts.append(
                "LINKED PAPER CATALOG (read or search the evidence before citing):\n"
                + "\n".join(paper_catalog_rows)
            )
        else:
            abstracts = "\n".join(
                f"[{w.id}] {w.title}: {(w.abstract or '')[:350]}" for w in works[:20]
            )
            parts.append(f"PAPER ABSTRACTS (background knowledge):\n{abstracts}")
    return "\n\n".join(parts)


def run_assistant_turn(
    pool: LLMPool,
    *,
    project_files: dict[str, str],
    active_path: str,
    message: str,
    citations: list[dict[str, Any]],
    assets: list[str],
    works: list[WorkRecord],
    history: list[dict[str, str]],
    selection: dict[str, Any] | None = None,
    compile_errors: list[dict[str, Any]] | None = None,
    compile_log: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    datasets: list[str] | None = None,
    dataset_profiles: list[dict[str, Any]] | None = None,
    interview_evidence: list[dict[str, Any]] | None = None,
    survey_evidence: list[dict[str, Any]] | None = None,
    evidence_scope_note: str = "",
    response_language: str = "en",
    assistant_preferences: dict[str, Any] | None = None,
    candidate_compiler: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
) -> AssistantTurn:
    local_edit = is_local_writer_edit_request(
        message,
        selection,
        has_compile_errors=bool(compile_errors),
    )
    project_only_rewrite = _project_only_rewrite_request(message)
    rewrite_request = message if _wording_only_rewrite_request(message) else None
    include_research_context = not (local_edit or project_only_rewrite)
    dataset_scope_allowed = resolve_writer_evidence_scope(message, history).include_datasets
    if not include_research_context or not dataset_scope_allowed:
        datasets, dataset_profiles = None, None
    emit_agent_event(
        "context.loaded",
        tool="manuscript.inspect",
        label="Read the manuscript workspace",
        detail=(
            f"{len(project_files)} project files, {len(citations)} citations, "
            f"{len(assets)} figures and workspace context"
        ),
    )
    # Straightforward profile questions use server-owned statistics. A model
    # must not invent arithmetic (or row identities) for values we already hold.
    exact_profile_reply = (
        exact_dataset_profile_answer(
            message,
            dataset_profiles,
            language=response_language,
            history=history,
        )
        if include_research_context
        and dataset_scope_allowed
        and not _requests_manuscript_mutation(message)
        else None
    )
    if exact_profile_reply is not None:
        emit_agent_event(
            "tool.started",
            tool="manuscript.read_dataset_profile",
            label="Read linked dataset statistics",
            detail="Reading the current linked dataset's saved full-table profile.",
        )
        emit_agent_event(
            "tool.completed",
            tool="manuscript.read_dataset_profile",
            label="Linked dataset statistics checked",
            detail="The requested statistics came directly from the current dataset profile.",
        )
        return AssistantTurn(reply=exact_profile_reply, tools_used=("read_dataset_profile",))
    source_read_receipts: set[str] = set()
    required_dataset_handles = (
        {f"dataset:{index}" for index, _ in enumerate(datasets or [], start=1)}
        if include_research_context
        and dataset_scope_allowed
        and requests_dataset_evidence(message, dataset_profiles, history)
        else set()
    )
    initial_structure_report = _manuscript_structure_report(project_files)
    explicit_template_cleanup = bool(_TEMPLATE_CLEANUP_REQUEST.search(message))
    strict_placeholder_cleanup = bool(_STRICT_PLACEHOLDER_CLEANUP_REQUEST.search(message))
    template_cleanup_contract = (
        not local_edit
        and _requests_template_cleanup(message)
        and (explicit_template_cleanup or bool(initial_structure_report.get("template_like")))
    )
    structure_receipts: set[str] = set()
    context = build_context(
        project_files,
        active_path,
        citations,
        assets,
        works,
        compile_errors,
        compile_log,
        attachments,
        datasets,
        interview_evidence,
        survey_evidence,
        include_research_context=include_research_context,
        selection=selection,
    )
    iterative_workspace_context = context
    if not local_edit:
        iterative_workspace_context = build_context(
            project_files,
            active_path,
            citations,
            assets,
            works,
            compile_errors,
            compile_log,
            attachments,
            datasets,
            interview_evidence,
            survey_evidence,
            include_research_context=include_research_context,
            selection=selection,
            project_manifest_only=True,
        )
    convo = render_model_aware_context(
        history,
        pool=pool,
        local_edit=local_edit,
        current_request=message,
    )
    parts = [context]
    if convo:
        parts.append(convo)
    if evidence_scope_note and include_research_context:
        parts.append(evidence_scope_note)
    if not dataset_scope_allowed:
        parts.append(
            "DATASET EVIDENCE IS EXCLUDED FOR THIS TURN. Do not read linked datasets "
            "or reuse their values from older conversation turns. Answer only from "
            "the currently permitted source context."
        )
    selection_prompt = writer_selection_prompt(selection, active_path)
    if selection_prompt:
        parts.append(selection_prompt)
    preference_context = assistant_preference_context(assistant_preferences)
    if preference_context:
        parts.append(preference_context)
    if not local_edit and _requests_manuscript_mutation(message):
        # Give the first model call a few exact raw-source anchors alongside
        # the manifest. These bounded anchors preserve exact-match safety for
        # simple changes while read_file/search_project remain available for
        # broader or cross-file work.
        parts.append(
            "SAFE EDIT ANCHORS (raw source without line numbers; copy one "
            "complete path/find pair exactly when proposing an edit):\n"
            + _edit_anchor_catalog(project_files, active_path, limit=6)
        )
    if template_cleanup_contract:
        parts.append(
            "TEMPLATE CLEANUP CONTRACT: The current project contains "
            f"{initial_structure_report['marker_count']} recognizable boilerplate "
            "markers. Call inspect_document_structure before drafting edits. The final "
            "cumulative candidate must contain none of the reported placeholder, dummy, "
            "instructional or empty-section residue. Preserve required LaTeX formatting, "
            "but do not append the requested report alongside unchanged sample content."
        )
    iterative_context = "\n\n".join([iterative_workspace_context, *parts[1:]])
    parts.append(f"USER: {message}")
    agent_prompt = "\n\n".join(parts)
    system_prompt = (
        _SYSTEM
        + response_language_instruction(response_language)
        + assistant_system_instruction(assistant_preferences)
    )
    direct_system_prompt = system_prompt + _DIRECT_RESPONSE_SYSTEM

    def try_completion(
        target_pool: LLMPool,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> Any | None:
        """Return a completion or let the caller retain the last safe result."""

        try:
            return request_structured_completion(
                target_pool,
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
            )
        except ProviderError:
            return None

    max_tokens = (
        _LOCAL_EDIT_MAX_TOKENS
        if local_edit
        else 4_800
        if _requests_manuscript_mutation(message)
        else 3_200
    )
    known_citations = {
        str(citation.get("key") or "").strip()
        for citation in citations
        if str(citation.get("key") or "").strip()
    }
    known_assets = _known_asset_references(assets)
    compiled_edits: dict[str, dict[str, Any]] = {}
    iterative_tools_used: list[str] = []
    staged_edits: list[dict[str, str]] = []
    staged_step_ids: list[str] = []
    pending_step_id = ""
    repair_state = _WriterRepairState()
    sequential_final_accepted = False
    no_change_receipts: list[dict[str, Any]] = []
    no_change_terminal: bool | None = None
    empty_finish_attempts = 0
    sequential_edit_contract = (
        not local_edit and candidate_compiler is not None and _requests_manuscript_mutation(message)
    )
    visual_evidence_context = _visual_evidence_context(
        project_files,
        works,
        datasets,
        interview_evidence,
        survey_evidence,
    )
    message_folded = message.casefold()
    references_known_asset = any(
        reference.casefold() in message_folded for reference in known_assets
    )
    references_existing_visual = bool(
        assets
        and re.search(
            r"\b(?:existing|uploaded|attached|vorhanden\w*|hochgeladen\w*|"
            r"angeh(?:ä|ae)ngt\w*)\b.{0,60}\b(?:figure|image|graphic|visual|"
            r"abbildung|bild|grafik)\w*\b",
            message,
            re.IGNORECASE,
        )
    )
    explicitly_requests_new_visual = bool(
        re.search(
            r"\b(?:create|generate|render|draw|design|erstell\w*|generier\w*|"
            r"zeichne\w*|rendere\w*)\b.{0,80}\b(?:(?:new|additional|separate|"
            r"neu\w*|zus(?:ä|ae)tzlich\w*)\s+)?(?:visual|figure|image|graphic|"
            r"diagram|plot|visualisierung|abbildung|bild|grafik|diagramm)\w*\b",
            message,
            re.IGNORECASE,
        )
    )
    existing_visual_reuse_requested = (
        references_known_asset or references_existing_visual
    ) and not explicitly_requests_new_visual
    visual_outcome_requested = (
        "create_visual" in workspace_action_types_requested(message)
        and not existing_visual_reuse_requested
    )
    requested_edit_targets = _writer_target_names(message)
    explicit_edit_target_count = len(requested_edit_targets)
    explicit_multi_target_marker = bool(re.search(r"\b(?:both|beide\w*)\b", message, re.IGNORECASE))
    explicit_multi_target_contract = (
        explicit_multi_target_marker and explicit_edit_target_count >= 2
    )
    require_named_target_coverage = (
        explicit_multi_target_contract and _requests_manuscript_mutation(message)
    )
    minimum_repair_edit_count = 0

    def edit_signature(edits: list[dict[str, Any]]) -> str:
        return json.dumps(
            [
                {
                    "path": str(edit.get("path") or "main.tex"),
                    "find": str(edit.get("find") or ""),
                    "replace": str(edit.get("replace") or ""),
                }
                for edit in edits
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def compiled_candidate_passed(edits: list[dict[str, Any]]) -> bool:
        """Return whether this exact ordered candidate has a successful receipt."""

        verification = compiled_edits.get(edit_signature(edits))
        return bool(verification and str(verification.get("status") or "").casefold() == "passed")

    def compile_exact_candidate(
        edits: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Compile one exact edit set once and expose a bounded public event."""

        if candidate_compiler is None:
            return None
        signature = edit_signature(edits)
        cached = compiled_edits.get(signature)
        if cached is not None:
            return cached
        emit_agent_event(
            "tool.started",
            tool="manuscript.compile_candidate",
            label="Compile the proposed manuscript changes",
            detail="Checking the exact proposed changes in a compile-ready manuscript.",
            input=safe_event_value(
                {
                    "files": sorted({str(edit.get("path") or active_path) for edit in edits}),
                    "edit_count": len(edits),
                }
            ),
        )
        try:
            verification = dict(candidate_compiler(edits))
        except LLMCancelledError:
            raise
        except Exception:  # noqa: BLE001 - deterministic compile failures fail closed
            verification = {
                "status": "failed",
                "errors": ["Candidate compilation was unavailable."],
                "log_tail": "",
            }
        compiled_edits[signature] = verification
        passed = str(verification.get("status") or "").casefold() == "passed"
        errors = verification.get("errors")
        emit_agent_event(
            "tool.completed" if passed else "tool.failed",
            tool="manuscript.compile_candidate",
            label=(
                "Candidate manuscript compiled"
                if passed
                else "Candidate manuscript needs a LaTeX correction"
            ),
            detail=(
                "The proposed edits compile successfully."
                if passed
                else "The candidate needs a LaTeX correction before review."
            ),
            output=safe_event_value(verification),
            result_count=len(errors) if isinstance(errors, list) else 0,
        )
        return verification

    def complete_applicable_edit_set(
        payload: dict[str, Any],
        validated: list[dict[str, Any]],
    ) -> bool:
        """Return whether every proposed edit survived deterministic validation."""

        raw_edits = payload.get("edits")
        if not isinstance(raw_edits, list) or not raw_edits or len(raw_edits) > MAX_WRITER_EDITS:
            return False
        return (
            all(isinstance(item, dict) for item in raw_edits)
            and len(validated) == len(raw_edits)
            and all(edit.get("applicable") is True for edit in validated)
        )

    def complete_edit_set_contract(
        payload: dict[str, Any],
        validated: list[dict[str, Any]],
    ) -> bool:
        """Return whether raw and validated edits describe the same complete set."""

        raw_edits = payload.get("edits")
        if (
            not isinstance(raw_edits, list)
            or len(raw_edits) > MAX_WRITER_EDITS
            or len(raw_edits) < minimum_repair_edit_count
        ):
            return False
        if not raw_edits:
            return not validated
        return complete_applicable_edit_set(payload, validated)

    def payload_with_validated_edits(
        payload: dict[str, Any],
        validated: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Bind server-generated repairs back to the raw whole-set contract."""

        rebound = dict(payload)
        rebound["edits"] = [
            {
                "path": str(edit.get("path") or active_path),
                "find": str(edit.get("find") or ""),
                "replace": str(edit.get("replace") or ""),
            }
            for edit in validated
        ]
        return rebound

    def canonical_edits(edits: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Return the exact public edit fields used by the atomic review payload."""

        return [
            {
                "path": str(edit.get("path") or active_path),
                "find": str(edit.get("find") or ""),
                "replace": str(edit.get("replace") or ""),
            }
            for edit in edits
        ]

    def identity_outcome(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Recognize only a pure no-op, never discard staged or failed changes."""

        if (
            staged_edits
            or pending_step_id
            or repair_state.active
            or template_cleanup_contract
            or visual_outcome_requested
            or workspace_action_types_requested(message)
            or payload.get("visual_request") is not None
            or payload.get("workspace_actions") != []
        ):
            return []
        return _writer_identity_receipts(
            payload.get("edits"),
            project_files=project_files,
            active_path=active_path,
            message=message,
            selection=selection,
        )

    def unchanged_payload(verified: bool) -> dict[str, Any]:
        """Use server-authored copy without a mutation or approval claim."""

        german = response_language.lower().startswith("de")
        if verified:
            reply = (
                "Die vorgeschlagene Fassung entspricht bereits der vorhandenen Passage. "
                "Es wurde nichts geändert; eine Bestätigung ist nicht erforderlich."
                if german
                else "The proposed wording is identical to the existing passage. "
                "Nothing was changed; no approval is needed."
            )
        else:
            reply = (
                "Es liegt kein geprüfter Änderungsvorschlag vor. Dein Manuskript ist "
                "unverändert. Markiere die gewünschte Passage, wenn du sie anders "
                "formulieren möchtest."
                if german
                else "No verified edit proposal is available. Your manuscript is unchanged. "
                "Select the intended passage if you want different wording."
            )
        return {
            "reply": reply,
            "edits": [],
            "visual_request": None,
            "workspace_actions": [],
        }

    def finish_without_changes(verified: bool) -> AssistantTurn:
        """Exit before edit repair/coverage can manufacture a needless change."""

        payload = unchanged_payload(verified)
        emit_agent_event(
            "tool.completed" if verified else "tool.failed",
            tool="manuscript.propose_edits",
            label="Manuscript unchanged" if verified else "No manuscript change prepared",
            detail=payload["reply"],
            output={
                "status": "unchanged" if verified else "unverified",
                "matched_source_anchors": len(no_change_receipts) if verified else 0,
                "edit_count": 0,
                "compile_status": "not_requested",
            },
            result_count=0,
        )
        return AssistantTurn(reply=payload["reply"], tools_used=tuple(iterative_tools_used))

    def rollback_staged_interview_attribution() -> None:
        """Roll back from the earliest staged edit that corrupted attribution."""

        nonlocal pending_step_id, staged_edits
        if not staged_edits or not staged_step_ids:
            return
        require_quote = bool(_INTERVIEW_QUOTE_REQUEST.search(message))
        require_speaker = bool(_INTERVIEW_SPEAKER_REQUEST.search(message))
        require_timestamp = bool(_INTERVIEW_TIMESTAMP_REQUEST.search(message))
        culprit_index: int | None = None
        for index, edit in enumerate(staged_edits):
            find = str(edit.get("find") or "")
            replacement = str(edit.get("replace") or "")
            replacement_folded = replacement.casefold()
            for context in interview_evidence or []:
                for passage in context.get("passages") or []:
                    if not isinstance(passage, dict):
                        continue
                    quote = str(passage.get("text") or "").strip()
                    if not quote or not _contains_source_text(find, quote):
                        continue
                    speaker = str(passage.get("speaker") or "").strip().casefold()
                    timestamp = _transcript_timestamp(int(passage.get("start_ms") or 0))
                    quote_ok = not require_quote or _contains_source_text(
                        replacement,
                        quote,
                    )
                    speaker_ok = not require_speaker or bool(
                        speaker and speaker in replacement_folded
                    )
                    timestamp_ok = not require_timestamp or timestamp in replacement
                    if not (quote_ok and speaker_ok and timestamp_ok):
                        culprit_index = index
                        break
                if culprit_index is not None:
                    break
            if culprit_index is not None:
                break
        rollback_index = culprit_index if culprit_index is not None else 0
        pending_step_id = staged_step_ids[rollback_index]
        staged_edits = staged_edits[:rollback_index]
        del staged_step_ids[rollback_index:]

    def normalize_visual_candidate(
        candidate: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        """Validate and ground a renderer request before it can complete the turn."""

        if candidate is None:
            return None, ""
        if not isinstance(candidate, dict):
            return None, "The visual request must be one complete structured object."
        if existing_visual_reuse_requested:
            return None, (
                "The request references an existing uploaded visual. Insert that asset "
                "with an exact manuscript edit rather than creating another visual."
            )
        visual_prompt = str(candidate.get("prompt") or "").strip()
        kind = str(candidate.get("kind") or "method")
        aspect_ratio = str(candidate.get("aspect_ratio") or "4:3")
        resolution = str(candidate.get("resolution") or "2k")
        try:
            review_passes = int(candidate.get("review_passes", 1))
        except (TypeError, ValueError):
            review_passes = 1
        if (
            len(visual_prompt) < 12
            or kind not in {"method", "architecture", "flow", "concept", "plot"}
            or aspect_ratio not in {"1:1", "4:3", "3:2", "16:9", "2:3"}
            or resolution not in {"1k", "2k", "4k"}
        ):
            return None, "The visual request did not satisfy the renderer contract."
        grounded = ground_visual_proposal(
            {
                "type": "create_visual",
                "prompt": visual_prompt[:FIGURE_PROMPT_MAX_CHARACTERS],
                "kind": kind,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "review_passes": min(2, max(0, review_passes)),
            },
            request=message,
            context=visual_evidence_context,
        )
        if grounded.get("grounding_mode") == "missing_quantitative_data":
            note = str(grounded.get("grounding_note") or "").strip()
            return None, note or (
                "A quantitative plot needs exact labelled values from the source material."
            )
        return (
            {
                "prompt": str(grounded.get("prompt") or "")[:FIGURE_PROMPT_MAX_CHARACTERS],
                "kind": str(grounded.get("kind") or kind),
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "review_passes": min(2, max(0, review_passes)),
            },
            "",
        )

    def normalize_writer_workspace_candidates(
        raw_actions: Any,
    ) -> tuple[list[dict[str, Any]], str]:
        """Normalize Writer actions without silently discarding raw proposals."""

        if raw_actions is None:
            raw_actions = []
        if not isinstance(raw_actions, list) or any(
            not isinstance(action, dict) for action in raw_actions
        ):
            return [], "workspace_actions must be one complete list of action objects."
        if any(action.get("type") == "create_visual" for action in raw_actions):
            return [], (
                "In Writer, put the requested figure in visual_request and remove "
                "create_visual from workspace_actions before finishing."
            )
        normalized = normalize_workspace_actions_for_request(raw_actions, message)
        scoped = scope_workspace_actions_to_current_resource(
            normalized,
            resource_type="manuscript",
        )
        if len(normalized) != len(raw_actions) or len(scoped) != len(raw_actions):
            return [], (
                "Every workspace action must be authorized by this request and target "
                "the open manuscript. Remove or repair every discarded action before "
                "finishing."
            )
        return scoped, ""

    if local_edit:
        emit_agent_event(
            "plan.created",
            label="Plan the manuscript turn",
            detail=(
                "Locate the selected source anchor, draft the bounded rewrite, "
                "then validate it before presenting it."
            ),
            steps=[
                "Inspect the selected source",
                "Draft the exact file edit",
                "Verify the edit anchor",
            ],
        )
        emit_agent_event(
            "tool.started",
            tool="manuscript.propose_edits",
            label="Draft the manuscript response",
            detail=f"Targeting {active_path} with an exact, reversible source change.",
        )
    response = None
    raw_response = ""
    data: dict[str, Any] | None = None
    if local_edit:
        try:
            response = request_structured_completion(
                pool,
                system=direct_system_prompt,
                prompt=agent_prompt,
                max_tokens=max_tokens,
            )
            raw_response = response.text.strip()
            data = _writer_json_object(raw_response)
        except ProviderError:
            # A temporary failure of the selected model must not abort a grounded
            # Writer turn. Continue through the bounded formatter below. Explicit
            # cancellation is a separate exception and remains terminal.
            pass
    else:
        agent_pool = _WriterAgentPool(
            pool,
            legacy_system=direct_system_prompt,
            legacy_prompt=agent_prompt,
            legacy_max_tokens=max_tokens,
        )
        citation_receipts: set[str] = set()

        def compile_candidate(arguments: dict[str, Any]) -> AgentToolResult:
            """Validate and compile exactly one edit onto the staged candidate."""

            nonlocal pending_step_id, staged_edits, sequential_edit_contract, no_change_receipts
            # Once the agent starts staging an exact candidate, the final
            # payload must account for it even if the wording classifier did
            # not recognize the original edit request. This never applies it.
            if candidate_compiler is not None:
                sequential_edit_contract = True
            step_id = str(arguments.get("step_id") or "").strip()[:80]
            raw_edit = arguments.get("edit")
            if not step_id or not isinstance(raw_edit, dict):
                repair_state.require(
                    "Provide one exact manuscript edit, then validate it with compile_candidate.",
                    _WRITER_EDIT_REPAIR_TOOLS,
                )
                return AgentToolResult(
                    output={
                        "status": "rejected",
                        "staged_count": len(staged_edits),
                    },
                    summary="This candidate step requires one named exact source edit.",
                    success=False,
                )
            if pending_step_id and step_id != pending_step_id:
                repair_state.require(
                    "Correct the current manuscript edit with read_file or search_project, "
                    "then retry its stable step_id with compile_candidate.",
                    _WRITER_EDIT_REPAIR_TOOLS,
                )
                return AgentToolResult(
                    output={
                        "status": "retry_required",
                        "retry_step_id": pending_step_id,
                        "staged_count": len(staged_edits),
                    },
                    summary=("Correct the current manuscript change before starting another one."),
                    success=False,
                )
            if step_id in staged_step_ids:
                return AgentToolResult(
                    output={
                        "status": "already_staged",
                        "staged_count": len(staged_edits),
                        "step_id": step_id,
                    },
                    summary=(
                        "That manuscript change is already validated in the staged candidate."
                    ),
                    success=False,
                )

            receipts = identity_outcome(
                {
                    "edits": [raw_edit],
                    "visual_request": None,
                    "workspace_actions": [],
                }
            )
            if receipts:
                no_change_receipts = receipts
                return AgentToolResult(
                    output={
                        "status": "unchanged",
                        "edit_count": 0,
                        "path": raw_edit["path"],
                    },
                    summary=(
                        "The proposed wording is identical to the existing passage. "
                        "No edit was staged."
                    ),
                    success=True,
                )
            # A later real or invalid edit invalidates the earlier identity
            # receipt; it cannot excuse an outstanding correction.
            no_change_receipts = []
            raw_candidate = [*staged_edits, dict(raw_edit)]
            candidate_data = {
                "reply": "Candidate compile",
                "edits": raw_candidate,
                "visual_request": None,
                "workspace_actions": [],
            }
            validated = _validated_writer_edits(
                candidate_data,
                project_files=project_files,
                active_path=active_path,
                known_citations=known_citations,
                known_assets=known_assets,
                allow_new_references=True,
                rewrite_request=rewrite_request,
            )
            applicable = [edit for edit in validated if edit.get("applicable")]
            if len(validated) != len(raw_candidate) or len(applicable) != len(raw_candidate):
                pending_step_id = step_id
                repair_state.require(
                    "The evidence is already available. Read the exact manuscript target "
                    "with read_file or search_project, then retry this same change with "
                    "compile_candidate.",
                    _WRITER_EDIT_REPAIR_TOOLS,
                )
                failed_edit = validated[-1] if len(validated) > len(staged_edits) else {}
                return AgentToolResult(
                    output={
                        "status": "rejected",
                        "edit": failed_edit,
                        "retry_step_id": step_id,
                        "staged_count": len(staged_edits),
                    },
                    summary=(
                        "The current source anchor is missing, ambiguous or unsafe. "
                        "Correct this same manuscript change and retry it."
                    ),
                    success=False,
                )
            if candidate_compiler is None:
                pending_step_id = step_id
                repair_state.require(
                    "Keep the exact manuscript edit and retry compile_candidate when "
                    "candidate compilation is available.",
                    {"compile_candidate"},
                )
                return AgentToolResult(
                    output={
                        "status": "unavailable",
                        "retry_step_id": step_id,
                        "staged_count": len(staged_edits),
                    },
                    summary="Candidate compilation is not available in this execution path.",
                    success=False,
                )
            canonical_candidate = canonical_edits(applicable)
            signature = edit_signature(canonical_candidate)
            verification = compiled_edits.get(signature)
            transient_compile_failure = False
            if verification is None:
                try:
                    verification = dict(candidate_compiler(applicable))
                except LLMCancelledError:
                    raise
                except Exception:  # noqa: BLE001 - staged compilation fails closed
                    transient_compile_failure = True
                    verification = {
                        "status": "failed",
                        "errors": ["Candidate compilation was unavailable."],
                        "log_tail": "",
                    }
                if not transient_compile_failure:
                    compiled_edits[signature] = verification
            passed = str(verification.get("status") or "").casefold() == "passed"
            if not passed:
                pending_step_id = step_id
                repair_state.require(
                    "Inspect the manuscript source and compile diagnostics, correct this "
                    "same edit, then retry it with compile_candidate.",
                    _WRITER_COMPILE_REPAIR_TOOLS,
                )
                raw_errors = verification.get("errors")
                diagnostics = (
                    "; ".join(
                        str(error.get("message") or error)
                        if isinstance(error, dict)
                        else str(error)
                        for error in raw_errors[:3]
                    )
                    if isinstance(raw_errors, list)
                    else ""
                )
                diagnostic_suffix = f" Diagnostics: {diagnostics}." if diagnostics else ""
                return AgentToolResult(
                    output={
                        **verification,
                        "retry_step_id": step_id,
                        "staged_count": len(staged_edits),
                    },
                    summary=(
                        "The current manuscript change did not compile. Correct this "
                        "same change and retry the cumulative candidate." + diagnostic_suffix
                    ),
                    success=False,
                    retryable=transient_compile_failure,
                )

            staged_edits = canonical_candidate
            staged_step_ids.append(step_id)
            pending_step_id = ""
            repair_state.clear()
            remaining_markers = 0
            if template_cleanup_contract:
                current_structure = _manuscript_structure_report(
                    _project_after_writer_edits(project_files, staged_edits)
                )
                remaining_markers = len(
                    _remaining_cleanup_markers(
                        initial_structure_report,
                        current_structure,
                        strict_weak_cleanup=strict_placeholder_cleanup,
                    )
                )
            return AgentToolResult(
                output={
                    **verification,
                    "edit_count": len(staged_edits),
                    "step_id": step_id,
                    "path": staged_edits[-1]["path"],
                    "compile_status": "passed",
                    "remaining_boilerplate_markers": remaining_markers,
                },
                summary=(
                    f"Validated manuscript change {len(staged_edits)} and compiled the "
                    "cumulative candidate."
                    + (
                        f" {remaining_markers} template marker"
                        f"{'s' if remaining_markers != 1 else ''} remain."
                        if template_cleanup_contract
                        else ""
                    )
                ),
                success=True,
            )

        def stage_final_edit_batch(raw_edits: list[Any]) -> AgentToolResult:
            """Sequentially stage a terminal batch from legacy or recovery output."""

            if len(raw_edits) > MAX_WRITER_EDITS:
                return AgentToolResult(
                    output={"status": "rejected", "staged_count": len(staged_edits)},
                    summary=(
                        "The manuscript proposal is too broad for one atomic review set. "
                        "Split it into bounded section-level changes."
                    ),
                    success=False,
                )
            if len(raw_edits) < len(staged_edits):
                return AgentToolResult(
                    output={"status": "mismatch", "staged_count": len(staged_edits)},
                    summary=(
                        "The final proposal omitted a manuscript change that was already validated."
                    ),
                    success=False,
                )
            raw_prefix = canonical_edits(
                [item for item in raw_edits[: len(staged_edits)] if isinstance(item, dict)]
            )
            if edit_signature(raw_prefix) != edit_signature(staged_edits):
                return AgentToolResult(
                    output={"status": "mismatch", "staged_count": len(staged_edits)},
                    summary=(
                        "The final proposal changed a manuscript edit that was already validated."
                    ),
                    success=False,
                )

            for index in range(len(staged_edits), len(raw_edits)):
                raw_edit = raw_edits[index]
                step_id = pending_step_id or f"final-edit-{index + 1}"
                path = (
                    str(raw_edit.get("path") or active_path)
                    if isinstance(raw_edit, dict)
                    else active_path
                )
                event_input = {
                    "change": index + 1,
                    "path": path,
                }
                emit_agent_event(
                    "tool.started",
                    tool="manuscript.compile_candidate",
                    label=f"Validate manuscript change {index + 1}",
                    detail=(
                        "Checking this exact source edit together with the manuscript "
                        "changes already validated."
                    ),
                    input=event_input,
                )
                result = compile_candidate(
                    {
                        "step_id": step_id,
                        "edit": raw_edit,
                    }
                )
                emit_agent_event(
                    "tool.completed" if result.success else "tool.failed",
                    tool="manuscript.compile_candidate",
                    label=result.summary,
                    detail=result.summary,
                    input=event_input,
                    output=safe_event_value(result.output),
                    result_count=1 if result.success else 0,
                )
                if not result.success:
                    return result
            return AgentToolResult(
                output={
                    "status": "passed",
                    "staged_count": len(staged_edits),
                },
                summary="The cumulative manuscript candidate is validated.",
            )

        def compile_candidate_preflight(
            arguments: dict[str, Any],
        ) -> AgentToolResult | None:
            """Keep staged edits on manuscript paths and the active repair step."""

            raw_edit = arguments.get("edit")
            raw_path = str(raw_edit.get("path") or "").strip() if isinstance(raw_edit, dict) else ""
            if raw_path and _EVIDENCE_HANDLE.fullmatch(raw_path):
                return AgentToolResult(
                    output={
                        "status": "project_path_required",
                        "provided_kind": "evidence_handle",
                        "required_tools": ["search_project", "read_file"],
                    },
                    summary=(
                        "An edit path must name a manuscript project file. Locate the "
                        "target with search_project or read_file, then compile that edit."
                    ),
                    success=False,
                    error_code="wrong_tool",
                )
            return repair_state.preflight("compile_candidate")

        agent_tools = list(
            _writer_agent_tools(
                project_files,
                active_path,
                structure_receipts=structure_receipts,
                repair_state=repair_state,
                evidence_tools_available=not project_only_rewrite,
            )
        )
        if not project_only_rewrite:
            agent_tools.extend(
                _writer_evidence_tools(
                    works=works,
                    citations=citations,
                    attachments=attachments,
                    datasets=datasets,
                    interview_evidence=interview_evidence,
                    survey_evidence=survey_evidence,
                    citation_receipts=citation_receipts,
                    source_read_receipts=source_read_receipts,
                    repair_state=repair_state,
                    project_paths=project_files,
                )
            )
        if candidate_compiler is not None:
            agent_tools.append(
                AgentTool(
                    name="compile_candidate",
                    label="Compile candidate manuscript",
                    description=(
                        "Validate the next exact logical source edit and compile it "
                        "together with the manuscript changes already validated."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "step_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 200,
                            },
                            "edit": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 1_000,
                                    },
                                    "find": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": _MAX_FILE,
                                    },
                                    "replace": {
                                        "type": "string",
                                        "maxLength": _MAX_PROJECT,
                                    },
                                },
                                "required": ["path", "find", "replace"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["step_id", "edit"],
                        "additionalProperties": False,
                    },
                    handler=compile_candidate,
                    max_calls=32,
                    allow_repeated=True,
                    effect="staged",
                    preflight=compile_candidate_preflight,
                )
            )

        def validate_agent_final(payload: dict[str, Any]) -> AgentFinalValidation:
            nonlocal minimum_repair_edit_count
            nonlocal sequential_final_accepted, staged_edits
            nonlocal no_change_receipts, no_change_terminal, empty_finish_attempts
            try:
                normalized = _writer_json_object(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError):
                normalized = None
            if normalized is None:
                if (
                    sequential_edit_contract
                    and staged_edits
                    and not pending_step_id
                    and compiled_candidate_passed(staged_edits)
                ):
                    # The exact staged patch is already server-validated and compiled.
                    # A provider sometimes emits a useful public update but leaves the
                    # terminal `final` object empty. Bind only the trusted staged actions
                    # and close with deterministic review copy instead of sending the
                    # model through repeated read/compile cycles.
                    normalized = {
                        "reply": (
                            "Der exakte Manuskriptvorschlag wurde erfolgreich kompiliert "
                            "und ist zur Prüfung bereit."
                            if response_language.lower().startswith("de")
                            else "The exact manuscript proposal compiled successfully "
                            "and is ready for review."
                        ),
                        "edits": [],
                        "visual_request": None,
                        "workspace_actions": [],
                    }
                else:
                    repair_state.require(
                        "Return the complete Writer proposal, then validate its exact "
                        "manuscript edit with compile_candidate.",
                        _WRITER_EDIT_REPAIR_TOOLS,
                    )
                    return AgentFinalValidation(
                        False,
                        {},
                        "The final payload must contain a string reply and the Writer "
                        "fields edits, visual_request and workspace_actions.",
                    )
            # Direct legacy Writer payloads still pass through the same exact
            # anchor and cumulative compile sequence before completion.
            unread_datasets = sorted(required_dataset_handles - source_read_receipts)
            if unread_datasets:
                return AgentFinalValidation(
                    False,
                    normalized,
                    "Read the exact linked dataset profile with read_source before "
                    "claiming values, missingness, row identities or conclusions. "
                    "The dataset catalog and previous replies contain no usable values. "
                    "Unread handles: " + ", ".join(unread_datasets),
                )
            legacy_final = agent_pool.last_was_legacy
            raw_edits = normalized.get("edits")
            receipt_payload = normalized
            if raw_edits == [] and no_change_receipts:
                receipt_payload = {**normalized, "edits": no_change_receipts}
            receipts = identity_outcome(receipt_payload)
            if receipts:
                no_change_receipts = receipts
                no_change_terminal = True
                return AgentFinalValidation(
                    True,
                    unchanged_payload(True),
                    completion_label="Manuscript unchanged",
                    completion_detail=(
                        "The proposed wording matches existing source. "
                        "No edit or approval is needed."
                    ),
                )
            identity_only = (
                bool(raw_edits)
                and isinstance(raw_edits, list)
                and all(
                    isinstance(edit, dict)
                    and isinstance(edit.get("find"), str)
                    and edit.get("find") == edit.get("replace")
                    for edit in raw_edits
                )
            )
            bounded_rewrite = rewrite_request is not None or project_only_rewrite
            if (
                not staged_edits
                and not pending_step_id
                and not repair_state.active
                and normalized.get("visual_request") is None
                and normalized.get("workspace_actions") == []
                and (identity_only or (bounded_rewrite and raw_edits == []))
            ):
                empty_finish_attempts += 1
                if identity_only or empty_finish_attempts >= 2:
                    # This is a neutral failed outcome, not verification of an
                    # empty model claim. Do not spend the full loop repeatedly
                    # demanding a change which the model refuses to propose.
                    no_change_terminal = False
                    return AgentFinalValidation(
                        True,
                        unchanged_payload(False),
                        completion_label="No manuscript change prepared",
                        completion_detail=(
                            "No verified edit proposal is available. "
                            "The manuscript remains unchanged."
                        ),
                    )
                return AgentFinalValidation(
                    False,
                    normalized,
                    "An empty edits list is not a verified outcome. Inspect the exact "
                    "target. If your proposed wording is identical, return the unique "
                    "path/find/replace pair with find equal to replace as a no-change "
                    "receipt; otherwise validate the exact real edit. Do not invent a "
                    "change merely to finish.",
                )
            if sequential_edit_contract:
                if isinstance(raw_edits, list) and raw_edits:
                    staged_result = stage_final_edit_batch(raw_edits)
                    if not staged_result.success:
                        return AgentFinalValidation(
                            False,
                            normalized,
                            staged_result.summary,
                        )
                    normalized["edits"] = list(staged_edits)
                    raw_edits = normalized["edits"]
                elif isinstance(raw_edits, list) and staged_edits and not pending_step_id:
                    # A compact terminal payload does not need to repeat potentially
                    # long replacements. The server binds it to the exact staged set.
                    normalized["edits"] = list(staged_edits)
                    raw_edits = normalized["edits"]
                if pending_step_id:
                    if not repair_state.active:
                        repair_state.require(
                            "Correct the current manuscript edit and retry it with "
                            "compile_candidate.",
                            _WRITER_EDIT_REPAIR_TOOLS,
                        )
                    return AgentFinalValidation(
                        False,
                        normalized,
                        "The current manuscript change still needs correction. Retry that "
                        "same staged edit and compile it successfully before finishing.",
                    )
            validated = _validated_writer_edits(
                normalized,
                project_files=project_files,
                active_path=active_path,
                known_citations=known_citations,
                known_assets=known_assets,
                allow_new_references=True,
                rewrite_request=rewrite_request,
            )
            applicable = [edit for edit in validated if edit.get("applicable")]
            if sequential_edit_contract and edit_signature(
                canonical_edits(applicable)
            ) != edit_signature(staged_edits):
                repair_state.require(
                    "Keep the already compiled manuscript changes unchanged and finish "
                    "with the exact staged edit set.",
                    (),
                )
                return AgentFinalValidation(
                    False,
                    normalized,
                    "The final edit list must exactly match the cumulatively validated "
                    "and compiled staged edits. Do not add, remove or rewrite edits in "
                    "the final payload.",
                )
            if not complete_edit_set_contract(
                normalized,
                validated,
            ):
                repair_state.require(
                    "The evidence is already available. Inspect the exact manuscript "
                    "target with read_file or search_project, repair the source anchor, "
                    "then validate the edit with compile_candidate.",
                    _WRITER_EDIT_REPAIR_TOOLS,
                )
                staged_target_count = len(raw_edits) if isinstance(raw_edits, list) else 0
                valid_staged_prefix = (
                    isinstance(raw_edits, list)
                    and len(validated) == len(raw_edits)
                    and all(edit.get("applicable") is True for edit in validated)
                )
                if (
                    minimum_repair_edit_count >= 2
                    and staged_target_count < minimum_repair_edit_count
                    and valid_staged_prefix
                ):
                    missing_target_count = minimum_repair_edit_count - staged_target_count
                    return AgentFinalValidation(
                        False,
                        normalized,
                        f"{missing_target_count} explicitly requested manuscript target"
                        f"{'s still need' if missing_target_count != 1 else ' still needs'} "
                        "a validated source edit. Keep the already staged changes and "
                        "validate the missing target before finishing.",
                    )
                if isinstance(raw_edits, list) and explicit_edit_target_count >= 2:
                    minimum_repair_edit_count = max(
                        minimum_repair_edit_count,
                        min(
                            MAX_WRITER_EDITS,
                            len(raw_edits),
                            explicit_edit_target_count,
                        ),
                    )
                details = [
                    (
                        f"{edit.get('path')}: occurrences={edit.get('occurrences')}; "
                        + "; ".join(str(error) for error in edit.get("integrity_errors") or [])
                    ).rstrip("; ")
                    for edit in validated[:3]
                ]
                suffix = f" Current validation: {' | '.join(details)}." if details else ""
                return AgentFinalValidation(
                    False,
                    normalized,
                    "Every proposed source edit must have a unique existing source anchor "
                    "and pass the integrity checks. Inspect the targets with read_file or "
                    "search_project, then repair the complete edit set before finishing."
                    f"{suffix}",
                )

            if require_named_target_coverage:
                covered_targets = _writer_edit_target_coverage(
                    applicable,
                    project_files,
                )
                missing_targets = sorted(requested_edit_targets - covered_targets)
                if missing_targets:
                    return AgentFinalValidation(
                        False,
                        normalized,
                        "Missing explicitly requested manuscript targets: "
                        + ", ".join(missing_targets)
                        + ". Keep the already staged changes and validate an edit for "
                        "each missing target before finishing.",
                    )

            if template_cleanup_contract:
                projected_structure = _manuscript_structure_report(
                    _project_after_writer_edits(project_files, applicable)
                )
                remaining_markers = _remaining_cleanup_markers(
                    initial_structure_report,
                    projected_structure,
                    strict_weak_cleanup=strict_placeholder_cleanup,
                )
                if remaining_markers:
                    locations = ", ".join(
                        f"{marker.get('path')}:{marker.get('line')} ({marker.get('kind')})"
                        for marker in remaining_markers[:6]
                        if isinstance(marker, dict)
                    )
                    return AgentFinalValidation(
                        False,
                        normalized,
                        f"{len(remaining_markers)} template or placeholder marker"
                        f"{'s remain' if len(remaining_markers) != 1 else ' remains'} "
                        "in the cumulative candidate"
                        + (f": {locations}." if locations else ".")
                        + " Keep the compiled staged changes, replace or delete the "
                        "remaining scaffold blocks with exact source edits, and compile "
                        "each next logical change before finishing.",
                    )

            # The accepted terminal payload contains only server-normalized actions.
            # Invalid raw edit, visual or workspace dictionaries must not survive as a
            # side channel around deterministic anchor and authorization checks.
            normalized["edits"] = [
                {
                    "path": str(edit.get("path") or active_path),
                    "find": str(edit.get("find") or ""),
                    "replace": str(edit.get("replace") or ""),
                }
                for edit in applicable
            ]
            canonical_visual, visual_validation_error = normalize_visual_candidate(
                normalized.get("visual_request")
            )
            if visual_validation_error:
                return AgentFinalValidation(
                    False,
                    normalized,
                    visual_validation_error,
                )
            normalized["visual_request"] = canonical_visual
            canonical_workspace_actions, workspace_validation_error = (
                normalize_writer_workspace_candidates(normalized.get("workspace_actions"))
            )
            if workspace_validation_error:
                return AgentFinalValidation(
                    False,
                    normalized,
                    workspace_validation_error,
                )
            normalized["workspace_actions"] = canonical_workspace_actions
            has_visual = canonical_visual is not None
            has_workspace_action = bool(canonical_workspace_actions)
            if visual_outcome_requested and not has_visual:
                return AgentFinalValidation(
                    False,
                    normalized,
                    "This Writer request requires a grounded visual_request. Inspect the "
                    "available evidence, then provide that renderer request before "
                    "finishing.",
                )
            if (
                _requests_manuscript_mutation(message)
                and not has_visual
                and not has_workspace_action
                and not applicable
            ):
                repair_state.require(
                    "Read the exact manuscript target with read_file or search_project, "
                    "then validate the corrected edit with compile_candidate.",
                    _WRITER_EDIT_REPAIR_TOOLS,
                )
                details = [
                    (
                        f"{edit.get('path')}: occurrences={edit.get('occurrences')}; "
                        + "; ".join(str(error) for error in edit.get("integrity_errors") or [])
                    ).rstrip("; ")
                    for edit in validated[:3]
                ]
                suffix = f" Current validation: {' | '.join(details)}." if details else ""
                return AgentFinalValidation(
                    False,
                    normalized,
                    "The request requires at least one exact edit proposal with a unique "
                    "existing source anchor. Inspect the target with read_file or "
                    f"search_project, then correct the final payload.{suffix}",
                )
            introduced_citations = {
                key
                for edit in applicable
                for key in (
                    _citation_keys(str(edit.get("replace") or ""))
                    - _citation_keys(str(edit.get("find") or ""))
                )
            }
            unread_citations = sorted(introduced_citations - citation_receipts)
            if unread_citations and not legacy_final:
                return AgentFinalValidation(
                    False,
                    normalized,
                    "Read or search a supporting passage before introducing these citation "
                    "keys: "
                    + ", ".join(unread_citations)
                    + ". A catalog entry alone is not evidence.",
                )
            if candidate_compiler is not None and applicable:
                signature = edit_signature(applicable)
                verification = compiled_edits.get(signature)
                if verification is None and legacy_final:
                    verification = compile_exact_candidate(applicable)
                if verification is None:
                    repair_state.require(
                        "The evidence is already available. Compile the exact manuscript "
                        "edit with compile_candidate before finishing.",
                        {"compile_candidate"},
                    )
                    return AgentFinalValidation(
                        False,
                        normalized,
                        "Compile this exact edit set with compile_candidate, observe the "
                        "result, and only then finish with the same verified edits.",
                    )
                if str(verification.get("status") or "").casefold() != "passed":
                    repair_state.require(
                        "Inspect the compile diagnostics and manuscript source, correct "
                        "the edit, then retry compile_candidate.",
                        _WRITER_COMPILE_REPAIR_TOOLS,
                    )
                    raw_errors = verification.get("errors")
                    error_details = []
                    if isinstance(raw_errors, list):
                        for error in raw_errors[:3]:
                            if isinstance(error, dict):
                                line = error.get("line")
                                message_text = str(error.get("message") or error).strip()
                                error_details.append(
                                    f"line {line}: {message_text}"
                                    if line not in (None, "")
                                    else message_text
                                )
                            else:
                                error_details.append(str(error).strip())
                    log_tail = str(verification.get("log_tail") or "").strip()[-800:]
                    diagnostics = "; ".join(item for item in error_details if item)
                    if log_tail:
                        diagnostics = f"{diagnostics}; log: {log_tail}".strip("; ")
                    suffix = f" Diagnostics: {diagnostics}" if diagnostics else ""
                    return AgentFinalValidation(
                        False,
                        normalized,
                        "The exact proposed edit set did not compile. Inspect the compile "
                        "errors, repair the source proposal, and compile the corrected "
                        f"set before finishing.{suffix}",
                    )
            if (
                interview_evidence
                and _requests_manuscript_mutation(message)
                and not _has_requested_interview_attribution(
                    message,
                    applicable,
                    interview_evidence,
                    project_files,
                )
            ):
                if sequential_edit_contract:
                    rollback_staged_interview_attribution()
                return AgentFinalValidation(
                    False,
                    normalized,
                    "The proposed edit does not preserve the explicitly requested "
                    "interview quote, speaker or timestamp from the linked evidence.",
                )
            sequential_final_accepted = True
            repair_state.clear()
            return AgentFinalValidation(True, normalized)

        iterative_scope_instruction = (
            "The initial context contains only the manuscript project manifest and safe "
            "source anchors. This bounded rewrite does not expose linked evidence or "
            "evidence handles. Use only read_file or search_project to locate the prose, "
            "then validate the proposal with compile_candidate. "
            if project_only_rewrite
            else (
                "The initial manuscript source context is a file and evidence catalog. "
                "Choose the read-only project and evidence tools whenever exact source, "
                "cross-file structure, facts or citations are needed. A citation may be "
                "introduced only after its passage was read or returned by evidence "
                "search. For broad or cross-file requests, inspect every relevant file "
                "or evidence source needed to cover the requested scope. "
            )
        )
        runner = AgentRunner(
            agent_pool,
            workspace="manuscript",
            instructions=(
                system_prompt
                + " ITERATIVE AGENT MODE: "
                + iterative_scope_instruction
                + "Observe each result before deciding the next step. One valid edit is "
                "not proof that a multi-part request is complete. "
                "For a full draft based on an initial template, or whenever the author "
                "mentions boilerplate or placeholders, call inspect_document_structure "
                "first and use its remaining-marker count as a completion condition. "
                "Do not claim template cleanup while a reported marker remains. When "
                "compile_candidate "
                "is available, validate exactly one logical edit per call. Keep one stable "
                "step_id while correcting that edit, wait for its successful cumulative "
                "compile, and only then start the next edit. After staging, finish with "
                "an empty edits list so the server can bind the exact staged candidate; "
                "if edits are included, they must match the staged set exactly. Never "
                "append an unvalidated batch in the final payload. During this loop use the "
                "controller's action envelope; put the complete Writer JSON object inside "
                "`final` only "
                "after the requested scope, grounding and validation checks are satisfied."
            ),
            tools=agent_tools,
            limits=AgentLimits(
                max_iterations=64,
                max_tool_calls=48,
                max_consecutive_failures=8,
                observation_chars=40_000,
                decision_tokens=max_tokens,
                max_seconds=1800,
            ),
            final_validator=validate_agent_final,
            public_update_filter=lambda value: (
                "Ich prüfe die aktuell verknüpften Daten."
                if required_dataset_handles and response_language.lower().startswith("de")
                else "I am checking the currently linked data."
                if required_dataset_handles
                else _writer_public_update(
                    value,
                    response_language=response_language,
                    project_only=project_only_rewrite,
                )
            ),
            plan_steps=(
                "Inspect the relevant manuscript files",
                "Draft grounded review proposals",
                "Validate exact anchors and attribution",
            ),
        )
        try:
            result = runner.run(request=message, context=iterative_context)
            raw_response = agent_pool.last_text
            iterative_tools_used = [
                observation.name
                for observation in result.observations
                if observation.kind == "tool" and observation.executed
            ]
            iterative_tools_used.extend(
                ["manuscript.agent_tool"] * max(0, result.tool_calls - len(iterative_tools_used))
            )
            if result.completed:
                data = result.final
        except _WriterAgentProtocolFallback as exc:
            raw_response = exc.raw_response
        except ProviderError:
            # Preserve the existing answer-only safety nets when the selected
            # model route is temporarily unavailable.
            pass
    if data is None:
        emit_agent_event(
            "tool.progress",
            tool="manuscript.propose_edits",
            label="Validate the structured manuscript proposal",
            detail="Preparing the manuscript proposal in the required structured format.",
        )
        # A valid answer is often present but wrapped in reasoning prose.
        # Retry the original grounded request once with an explicit
        # format-recovery instruction. We do not pass the unverified raw
        # output back to the model and never salvage proposed edits from
        # arbitrary text.
        data = recover_structured_object(
            pool,
            system=direct_system_prompt,
            prompt=agent_prompt,
            max_tokens=max_tokens,
            required_keys=_WRITER_RESPONSE_KEYS,
        )
        if data is None:
            partial_reply = extract_complete_string_field(
                raw_response,
                field_names=("reply",),
            )
            if partial_reply:
                emit_agent_event(
                    "tool.progress",
                    tool="manuscript.propose_edits",
                    label="Keep the completed explanation",
                    detail=(
                        "The completed explanation remains available while the source "
                        "changes await a fully validated proposal."
                    ),
                )
                data = {
                    "reply": partial_reply,
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                }
        if data is None:
            emit_agent_event(
                "tool.progress",
                tool="manuscript.propose_edits",
                label="Prepare the manuscript answer",
                detail="Preparing a grounded answer from the available manuscript context.",
            )
            data = recover_action_free_answer(
                pool,
                system=direct_system_prompt,
                prompt=agent_prompt,
                max_tokens=max_tokens,
                answer_key="reply",
                empty_fields={
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            )
        if data is None:
            emit_agent_event(
                "tool.failed",
                tool="manuscript.propose_edits",
                label="Manuscript proposal needs attention",
                detail="A verified manuscript proposal is not available for this request.",
            )
            return AssistantTurn(
                reply=structured_response_failure(response_language),
                workspace_actions=[],
            )
    # A formatting retry or legacy response must not bypass the source-read
    # requirement or preserve ungrounded prose from an aborted agent attempt.
    if required_dataset_handles - source_read_receipts:
        return AssistantTurn(
            reply=(
                "Ich konnte die aktuell verknüpften Daten für diese Antwort nicht "
                "vollständig prüfen. Bitte versuche die Datenfrage erneut; es wurden "
                "keine Werte übernommen und das Manuskript ist unverändert."
                if response_language.lower().startswith("de")
                else "I could not fully inspect the currently linked data for this answer. "
                "Please retry the data question; no values were adopted and the manuscript "
                "is unchanged."
            ),
            tools_used=tuple(iterative_tools_used),
        )
    if no_change_terminal is not None:
        return finish_without_changes(no_change_terminal)
    # The bounded local and structured recovery paths use the same source
    # identity rule, without entering any forced-edit repair/coverage passes.
    direct_identity = identity_outcome(data)
    if direct_identity:
        no_change_receipts = direct_identity
        return finish_without_changes(True)
    if (
        not local_edit
        and _requests_manuscript_mutation(message)
        and not isinstance(data.get("visual_request"), dict)
        and not (
            isinstance(data.get("edits"), list)
            and any(isinstance(item, dict) for item in data["edits"])
        )
    ):
        # A common novice flow is "use the interview stuff I linked and write
        # the findings here". Some providers acknowledge that request in prose
        # but omit the edit entirely, which leaves the author with no next
        # action. Retry once against the exact same grounded context and require
        # an applicable manuscript proposal. The original answer remains the
        # fallback if the provider still cannot satisfy the contract.
        edit_completion = try_completion(
            pool,
            system=(
                direct_system_prompt
                + " EDIT COMPLETION: The user clearly asked to modify the current "
                "manuscript. Your previous response deferred or omitted the required "
                "edit. Answer again now with at least one exact, file-scoped edit "
                "grounded in the supplied source. If linked evidence is available, "
                "use it and state bounded limitations. Do not create replacement "
                "surveys, interviews, datasets or manuscripts. An edit in this "
                "response is only a proposal. If the author asked to confirm before "
                "application, return the concrete proposal now because the client "
                "will hold it for explicit confirmation."
            ),
            prompt=agent_prompt,
            max_tokens=max_tokens,
        )
        recovered_edit = (
            _writer_json_object(edit_completion.text.strip())
            if edit_completion is not None
            else None
        )
        if (
            recovered_edit is not None
            and isinstance(recovered_edit.get("edits"), list)
            and any(isinstance(item, dict) for item in recovered_edit["edits"])
        ):
            data = recovered_edit
    reply = str(data.get("reply", "")).strip() or "Done."
    edits = _validated_writer_edits(
        data,
        project_files=project_files,
        active_path=active_path,
        known_citations=known_citations,
        known_assets=known_assets,
        allow_new_references=not local_edit,
        rewrite_request=rewrite_request,
    )
    edit_set_validation_error = not complete_edit_set_contract(data, edits)
    if not local_edit and _requests_manuscript_mutation(message) and edit_set_validation_error:
        anchor_catalog = _edit_anchor_catalog(project_files, active_path)
        anchor_repair = try_completion(
            pool,
            system=(
                direct_system_prompt
                + " EDIT REPAIR: At least one proposed edit did not pass validation. "
                "Return the complete Writer JSON again. Copy every find value "
                "character-for-character from one of the ALLOWED EDIT ANCHORS in the "
                "user prompt, including exact LaTeX commands and punctuation. Never "
                "paraphrase or invent a find value. To insert new content, repeat the "
                "chosen anchor unchanged inside replace and add the content before or "
                "after it. Address the EDIT VALIDATION FINDINGS as well: remove any "
                "unsupported numbers or invented individual case outcomes. Preserve "
                "only information supported by the source and the author's request. "
                "Do not create replacement artifacts."
            ),
            prompt=(
                agent_prompt
                + "\n\nEDIT VALIDATION FINDINGS:\n"
                + "\n".join(
                    str(error) for edit in edits for error in edit.get("integrity_errors", [])
                )
                + "\n\nALLOWED EDIT ANCHORS (copy one complete path/find pair "
                "exactly; these are raw source without line numbers):\n" + anchor_catalog
            ),
            max_tokens=max_tokens,
        )
        repaired_data = (
            _writer_json_object(anchor_repair.text.strip()) if anchor_repair is not None else None
        )
        if repaired_data is not None:
            repaired_edits = _validated_writer_edits(
                repaired_data,
                project_files=project_files,
                active_path=active_path,
                known_citations=known_citations,
                known_assets=known_assets,
                allow_new_references=not local_edit,
                rewrite_request=rewrite_request,
            )
            if complete_applicable_edit_set(repaired_data, repaired_edits):
                data = repaired_data
                reply = str(data.get("reply", "")).strip() or reply
                edits = repaired_edits
                edit_set_validation_error = False
    if (
        not local_edit
        and _requests_manuscript_mutation(message)
        and not any(edit.get("applicable") for edit in edits)
    ):
        # A provider can acknowledge a destructive request yet incorrectly
        # defer the *proposal* until after confirmation. The UI already holds
        # every proposal for explicit author review, so return one exact,
        # bounded section deletion when the target is unambiguous. This never
        # applies the change and never guesses an unnamed section.
        deletion = _deterministic_section_deletion(
            message,
            project_files,
            active_path,
        )
        if deletion is not None:
            deletion_edits = _validated_writer_edits(
                {"edits": [deletion]},
                project_files=project_files,
                active_path=active_path,
                known_citations=known_citations,
                known_assets=known_assets,
                allow_new_references=True,
                rewrite_request=rewrite_request,
            )
            if any(edit.get("applicable") for edit in deletion_edits):
                edits = deletion_edits
                data = payload_with_validated_edits(data, edits)
                edit_set_validation_error = False
                if response_language.lower().startswith("de"):
                    reply = (
                        "Ich habe den exakten Löschvorschlag vorbereitet. Noch wurde "
                        "nichts verändert. Prüfe und bestätige die Änderung im Review."
                    )
                else:
                    reply = (
                        "I prepared the exact deletion proposal. Nothing has been "
                        "changed yet. Review and confirm the change before it is applied."
                    )
    if (
        not local_edit
        and interview_evidence
        and _requests_manuscript_mutation(message)
        and not _has_requested_interview_attribution(
            message,
            edits,
            interview_evidence,
            project_files,
        )
    ):
        requested_attribution = [
            label
            for requested, label in (
                (bool(_INTERVIEW_QUOTE_REQUEST.search(message)), "verbatim quote"),
                (bool(_INTERVIEW_SPEAKER_REQUEST.search(message)), "speaker"),
                (bool(_INTERVIEW_TIMESTAMP_REQUEST.search(message)), "timestamp"),
            )
            if requested
        ]
        attribution_label = ", ".join(requested_attribution)
        attribution_repair = try_completion(
            pool,
            system=(
                direct_system_prompt + " INTERVIEW ATTRIBUTION REPAIR: The author explicitly asked "
                f"to retain these source attributes: {attribution_label}. "
                "Return the complete Writer JSON again with an applicable edit. "
                "Copy every requested attribute exactly from LINKED INTERVIEW EVIDENCE. "
                "When a verbatim quote was requested, keep its wording in the original "
                "language even if the surrounding prose is rewritten. Do not create a "
                "new interview or substitute survey evidence."
            ),
            prompt=agent_prompt,
            max_tokens=max_tokens,
        )
        repaired_data = (
            _writer_json_object(attribution_repair.text.strip())
            if attribution_repair is not None
            else None
        )
        repair_candidates = edits if complete_applicable_edit_set(data, edits) else []
        if repaired_data is not None:
            repaired_edits = _validated_writer_edits(
                repaired_data,
                project_files=project_files,
                active_path=active_path,
                known_citations=known_citations,
                known_assets=known_assets,
                allow_new_references=True,
                rewrite_request=rewrite_request,
            )
            repaired_complete = complete_applicable_edit_set(
                repaired_data,
                repaired_edits,
            )
            if repaired_complete:
                repair_candidates = repaired_edits
            if repaired_complete and _has_requested_interview_attribution(
                message,
                repaired_edits,
                interview_evidence,
                project_files,
            ):
                data = repaired_data
                reply = str(data.get("reply", "")).strip() or reply
                edits = repaired_edits
                edit_set_validation_error = False
        if not _has_requested_interview_attribution(
            message,
            edits,
            interview_evidence,
            project_files,
        ):
            repaired_edits = _repair_requested_interview_attribution(
                message,
                repair_candidates,
                interview_evidence,
            )
            if _has_requested_interview_attribution(
                message,
                repaired_edits,
                interview_evidence,
                project_files,
            ):
                repaired_payload = payload_with_validated_edits(
                    data,
                    repaired_edits,
                )
                validated_repair = _validated_writer_edits(
                    repaired_payload,
                    project_files=project_files,
                    active_path=active_path,
                    known_citations=known_citations,
                    known_assets=known_assets,
                    allow_new_references=True,
                    rewrite_request=rewrite_request,
                )
                if complete_applicable_edit_set(
                    repaired_payload,
                    validated_repair,
                ):
                    data = repaired_payload
                    edits = validated_repair
                    edit_set_validation_error = False
        if not _has_requested_interview_attribution(
            message,
            edits,
            interview_evidence,
            project_files,
        ):
            # Never offer a fluent but de-grounded rewrite after the user has
            # explicitly requested source-level attribution. The author keeps
            # the current manuscript and can retry without losing evidence.
            edits = []
            if response_language.lower().startswith("de"):
                reply = (
                    "Ich konnte keine Änderung erstellen, die die angeforderte "
                    "Interviewzuordnung zuverlässig erhält. Das Manuskript wurde nicht "
                    "verändert."
                )
            else:
                reply = (
                    "I could not prepare an edit that safely preserves the requested "
                    "interview attribution. The manuscript was not changed."
                )
    visual_request, visual_validation_error = normalize_visual_candidate(data.get("visual_request"))
    workspace_actions, workspace_validation_error = normalize_writer_workspace_candidates(
        data.get("workspace_actions")
    )
    workspace_actions = [
        ground_visual_proposal(
            action,
            request=message,
            context=visual_evidence_context,
        )
        for action in workspace_actions
    ]
    outcome_validation_error = visual_validation_error or workspace_validation_error
    if visual_outcome_requested and visual_request is None and not outcome_validation_error:
        outcome_validation_error = (
            "This Writer request requires a grounded visual_request. Inspect the "
            "available evidence and provide that renderer request before finishing."
        )
    if (
        not local_edit
        and _requests_manuscript_mutation(message)
        and not edits
        and visual_request is None
        and not workspace_actions
    ):
        validated_actions = [
            {
                "operation": "edit_source",
                "path": edit.get("path"),
                "find": edit.get("find"),
                "replace": edit.get("replace"),
                "applicable": edit.get("applicable") is True,
            }
            for edit in edits
        ]
        if visual_request is not None:
            validated_actions.append(
                {
                    "operation": "prepare_visual_request",
                    "kind": visual_request.get("kind"),
                    "prompt": visual_request.get("prompt"),
                }
            )
        validated_actions.extend(workspace_actions)
        coverage = review_action_coverage(
            pool,
            workspace="manuscript",
            request=message,
            state_summary=(
                f"Active file: {active_path}. Project files: {sorted(project_files)}. "
                "Exact source anchors available for any missing edit: "
                f"{_edit_anchor_catalog(project_files, active_path, limit=8)}"
            ),
            validated_actions=validated_actions,
            action_contract=(
                "Only a missing manuscript source edit is allowed here: "
                '{"operation":"edit_source","path":"existing/file.tex",'
                '"find":"one exact unique source anchor","replace":"replacement that '
                'contains the requested change"}. Do not create surveys, interviews, '
                "datasets, manuscripts or unrequested visuals."
            ),
            language=response_language,
            max_missing_actions=3,
        )
        if not coverage.complete:
            missing_edits = [
                action
                for action in coverage.missing_actions
                if str(action.get("operation") or "edit_source") == "edit_source"
            ]
            if missing_edits:
                combined = [
                    {
                        "path": edit.get("path"),
                        "find": edit.get("find"),
                        "replace": edit.get("replace"),
                    }
                    for edit in edits
                ] + missing_edits
                completed_edits = _validated_writer_edits(
                    {"edits": combined},
                    project_files=project_files,
                    active_path=active_path,
                    known_citations=known_citations,
                    known_assets=known_assets,
                    allow_new_references=not local_edit,
                    rewrite_request=rewrite_request,
                )
                if len(completed_edits) > len(edits):
                    edits = completed_edits
                    data = payload_with_validated_edits(data, edits)
                    edit_set_validation_error = not complete_edit_set_contract(
                        data,
                        edits,
                    )
    sequential_edit_mismatch = sequential_edit_contract and (
        not sequential_final_accepted
        or bool(pending_step_id)
        or edit_signature(canonical_edits(edits)) != edit_signature(staged_edits)
    )
    edit_set_validation_error = (
        edit_set_validation_error
        or sequential_edit_mismatch
        or not complete_edit_set_contract(data, edits)
    )
    raw_edits = data.get("edits")
    edit_outcome_failed = edit_set_validation_error and (
        _requests_manuscript_mutation(message)
        or bool(edits)
        or (isinstance(raw_edits, list) and bool(raw_edits))
    )
    rejected_edit_count = sum(edit.get("applicable") is not True for edit in edits)
    unsupported_rewrite_details = any(
        "unsupported numeric details" in str(error)
        for edit in edits
        for error in edit.get("integrity_errors", [])
    )
    if edit_outcome_failed:
        raw_edit_count = len(raw_edits) if isinstance(raw_edits, list) else 1
        rejected_edit_count = max(rejected_edit_count, max(1, raw_edit_count))
    if edit_set_validation_error or outcome_validation_error:
        # A response is one proposed outcome. Never silently turn an invalid
        # edit, visual or workspace-action set into a smaller task while
        # retaining the model's completion claim.
        edits = []
        visual_request = None
        workspace_actions = []
    else:
        edits = [edit for edit in edits if edit.get("applicable") is True]
    final_verification: dict[str, Any] | None = (
        compiled_edits[next(reversed(compiled_edits))]
        if sequential_edit_contract and compiled_edits
        else None
    )
    if sequential_edit_mismatch and (
        final_verification is None
        or str(final_verification.get("status") or "").casefold() == "passed"
    ):
        final_verification = {
            "status": "rejected",
            "errors": ["The terminal edit set did not match the validated staged candidate."],
            "log_tail": "",
        }
    compile_validation_error = ""
    if (
        sequential_edit_mismatch
        and final_verification is not None
        and str(final_verification.get("status") or "").casefold() not in {"passed", "rejected"}
    ):
        compile_validation_error = (
            "The exact final manuscript patch did not compile. No edit was returned or applied."
        )
    if edits and candidate_compiler is not None:
        verification = compile_exact_candidate(edits)
        final_verification = verification
        if verification is None or str(verification.get("status") or "").casefold() != "passed":
            compile_validation_error = (
                "The exact final manuscript patch did not compile. No edit was returned or applied."
            )
            rejected_edit_count += len(edits)
            edits = []
            visual_request = None
            workspace_actions = []
    missing_requested_outcome = (
        (
            _requests_manuscript_mutation(message)
            or sequential_edit_contract
            or rejected_edit_count > 0
            or bool(outcome_validation_error)
            or bool(compile_validation_error)
            or visual_outcome_requested
        )
        and not edits
        and visual_request is None
        and not workspace_actions
    )
    if missing_requested_outcome:
        if unsupported_rewrite_details:
            reply = (
                "Der Vorschlag enthielt zusätzliche Zahlen oder Fallzuordnungen, die "
                "nicht aus der ursprünglichen Passage hervorgehen. Dein Manuskript "
                "ist unverändert. Bitte versuche die reine Umformulierung erneut "
                "oder ergänze die fehlenden Angaben."
                if response_language.lower().startswith("de")
                else "The proposal added numbers or case details not supported by the "
                "original passage. Your manuscript is unchanged. Please retry the "
                "wording-only rewrite or provide the missing details."
            )
        elif compile_validation_error and response_language.lower().startswith("de"):
            reply = (
                "Der exakte finale Manuskriptvorschlag ließ sich nicht kompilieren. "
                "Es wurde keine Änderung zurückgegeben oder angewendet."
            )
        elif compile_validation_error:
            reply = compile_validation_error
        elif outcome_validation_error:
            reply = outcome_validation_error
        elif response_language.lower().startswith("de"):
            reply = (
                "Ich konnte die gewünschte Änderung nicht sicher der richtigen Passage "
                "zuordnen. Dein Manuskript ist unverändert. Markiere die Passage oder "
                "nenne den Abschnitt und versuche es erneut."
            )
        else:
            reply = (
                "I could not safely match the change to the right passage. Your "
                "manuscript is unchanged. Select the passage or name the section "
                "and try again."
            )
    if project_only_rewrite:
        reply = _project_only_reply(
            reply,
            response_language=response_language,
        )
    if edits or (visual_request is None and not workspace_actions):
        reply = _clarify_pending_writer_review(
            reply,
            response_language=response_language,
            has_edits=bool(edits),
        )
    reply = safe_agent_text(
        reply,
        fallback=(
            "Der geprüfte Manuskriptvorschlag steht zur Durchsicht bereit."
            if response_language.lower().startswith("de")
            and (edits or visual_request is not None or workspace_actions)
            else "Ich konnte keine sichere Writer-Antwort übernehmen."
            if response_language.lower().startswith("de")
            else "The verified manuscript proposal is ready for review."
            if edits or visual_request is not None or workspace_actions
            else "I could not preserve a safe Writer response."
        ),
    )
    if missing_requested_outcome:
        emit_agent_event(
            "tool.failed",
            tool="manuscript.propose_edits",
            label="Manuscript change needs attention",
            detail=(
                "The proposed rewrite added unsupported details and was not applied."
                if unsupported_rewrite_details
                else f"{rejected_edit_count} proposed change"
                f"{'s' if rejected_edit_count != 1 else ''} could not be matched safely "
                "to the manuscript."
                if rejected_edit_count
                else "The requested manuscript outcome still needs validation."
            ),
            output={
                "status": "rejected",
                "rejected_edits": rejected_edit_count,
                "compile_status": ("failed" if compile_validation_error else "not_requested"),
            },
        )
    else:
        emit_agent_event(
            "tool.completed",
            tool="manuscript.propose_edits",
            label=(
                "Manuscript proposal validated"
                if edits or visual_request is not None or workspace_actions
                else "Manuscript answer prepared"
            ),
            detail=(
                f"{len(edits)} exact source edit{'s' if len(edits) != 1 else ''}, "
                f"{1 if visual_request is not None else 0} visual request and "
                f"{len(workspace_actions)} workspace action"
                f"{'s' if len(workspace_actions) != 1 else ''}."
            ),
            result_count=(
                len(edits) + (1 if visual_request is not None else 0) + len(workspace_actions)
            ),
        )
    emit_change_events(workspace="manuscript", changes=edits, applied=False)
    return AssistantTurn(
        reply=reply,
        edits=edits,
        visual_request=visual_request,
        workspace_actions=workspace_actions,
        verification=final_verification,
        tools_used=tuple(iterative_tools_used),
    )
