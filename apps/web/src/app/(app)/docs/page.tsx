"use client";

import { useMemo, useState } from "react";
import type { ComponentType } from "react";
import {
  ArrowRight,
  Braces,
  Check,
  ChevronDown,
  Copy,
  FileText,
  FolderClosed,
  KeyRound,
  Library,
  LockKeyhole,
  Radio,
  Search,
  ShieldCheck,
  SquareTerminal,
  Telescope,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { API_URL } from "@/lib/api";
import { cn } from "@/lib/utils";

type Method = "GET" | "POST" | "PATCH";
type ApiScope = "research:read" | "research:write" | "library:read" | "library:write";

type Endpoint = {
  method: Method;
  path: string;
  scope: ApiScope;
  summary: string;
  detail?: string;
  curl?: string;
  response?: string;
};

type ContractParameter = {
  name: string;
  source: "path" | "query";
  type: string;
  required?: boolean;
  defaultValue?: string;
  description: string;
};

type EndpointContract = {
  parameters?: ContractParameter[];
  request?: string | null;
  requestNote?: string;
  success: string;
  response: string;
  responseNote?: string;
};

type Section = {
  id: string;
  title: string;
  icon: ComponentType<{ className?: string }>;
  blurb: string;
  endpoints: Endpoint[];
};

const SCOPES: Array<{
  id: ApiScope;
  label: string;
  description: string;
  access: string;
}> = [
  {
    id: "research:read",
    label: "Read research",
    description: "Inspect projects, runs, audit events, decisions, reports and exports.",
    access: "No mutations",
  },
  {
    id: "research:write",
    label: "Run research",
    description: "Create and control runs, answer review gates and use grounded run chat.",
    access: "Includes research:read",
  },
  {
    id: "library:read",
    label: "Read papers",
    description: "List acquisition records and download PDFs from the workspace library.",
    access: "No annotations or deletes",
  },
  {
    id: "library:write",
    label: "Add papers",
    description: "Upload, attach and acquire open-access PDFs for a research run.",
    access: "Includes library:read",
  },
];

const METHOD_STYLE: Record<Method, string> = {
  GET: "border-moss/30 bg-accent text-moss",
  POST: "border-pine bg-primary text-primary-foreground",
  PATCH: "border-amber-300/70 bg-amber-100 text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
};

const SECTIONS: Section[] = [
  {
    id: "projects",
    title: "Projects & runs",
    icon: FolderClosed,
    blurb: "Create a research record, start work and follow every stage without exposing the rest of the workspace.",
    endpoints: [
      {
        method: "GET",
        path: "/projects",
        scope: "research:read",
        summary: "List research projects in this workspace.",
      },
      {
        method: "POST",
        path: "/projects",
        scope: "research:write",
        summary: "Create a project.",
        curl: `curl -X POST {BASE}/projects \\
  -H "Authorization: Bearer $SIX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"name":"IaC evidence review"}'`,
        response: `{"id":12,"name":"IaC evidence review","status":"active"}`,
      },
      {
        method: "PATCH",
        path: "/projects/{project_id}",
        scope: "research:write",
        summary: "Update a project's research metadata. Deletion is intentionally unavailable.",
      },
      {
        method: "GET",
        path: "/runs",
        scope: "research:read",
        summary: "List runs, newest first. Filter with ?project_id=.",
      },
      {
        method: "POST",
        path: "/projects/{project_id}/runs",
        scope: "research:write",
        summary: "Start a systematic review inside a project.",
        detail:
          "The response is immediate. Follow progress through the event stream. Deployment-defined rate and resource limits still apply to API requests.",
        curl: `curl -X POST {BASE}/projects/12/runs \\
  -H "Authorization: Bearer $SIX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"question":"How reliable are LLMs for title and abstract screening?","screen":true,"gate_protocol":true}'`,
        response: `{"id":42,"status":"pending","gated":true}`,
      },
      {
        method: "POST",
        path: "/runs",
        scope: "research:write",
        summary: "Start an unfiled run without creating a project first.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}",
        scope: "research:read",
        summary: "Read run status, configuration and PRISMA counts.",
      },
      {
        method: "PATCH",
        path: "/runs/{run_id}",
        scope: "research:write",
        summary: "Update the display title or move the run to another owned project.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/pause",
        scope: "research:write",
        summary: "Pause at the next safe checkpoint.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/resume",
        scope: "research:write",
        summary: "Resume a paused run without repeating completed work.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/cancel",
        scope: "research:write",
        summary: "Cancel active work. The auditable record remains available.",
      },
    ],
  },
  {
    id: "record",
    title: "Live record",
    icon: Radio,
    blurb: "Observe progress, inspect the protocol and resolve the human decisions that keep a review defensible.",
    endpoints: [
      {
        method: "GET",
        path: "/runs/{run_id}/events",
        scope: "research:read",
        summary: "Read the append-only audit event log.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/events/stream",
        scope: "research:read",
        summary: "Stream live progress as server-sent events.",
        detail: "API clients authenticate with the normal bearer header. The stream replays the record and closes with event: done.",
        curl: `curl -N {BASE}/runs/42/events/stream \\
  -H "Authorization: Bearer $SIX_API_KEY"`,
      },
      {
        method: "GET",
        path: "/runs/{run_id}/protocol",
        scope: "research:read",
        summary: "Read the frozen review protocol.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/protocol/regenerate",
        scope: "research:write",
        summary: "Request a new protocol draft while the gate is waiting.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/protocol/approve",
        scope: "research:write",
        summary: "Approve the protocol and continue the run.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/queue",
        scope: "research:read",
        summary: "List records that require a human eligibility decision.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/decisions",
        scope: "research:read",
        summary: "Read recorded human screening decisions.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/decisions",
        scope: "research:write",
        summary: "Record include, exclude or unsure decisions with a rationale.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/calibration",
        scope: "research:write",
        summary: "Calibrate the screener against a reviewed sample.",
      },
    ],
  },
  {
    id: "results",
    title: "Results & grounded chat",
    icon: Telescope,
    blurb: "Use the ranked evidence and generated review record without granting access to manuscripts or unrelated product areas.",
    endpoints: [
      {
        method: "GET",
        path: "/runs/{run_id}/works",
        scope: "research:read",
        summary: "Read ranked works, evidence signals and screening verdicts.",
        curl: `curl "{BASE}/runs/42/works?included_only=true&limit=100" \\
  -H "Authorization: Bearer $SIX_API_KEY"`,
      },
      {
        method: "GET",
        path: "/runs/{run_id}/web-sources",
        scope: "research:read",
        summary: "Read grey-literature sources separately from the scholarly record.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/methods",
        scope: "research:read",
        summary: "Read the generated methods paragraph.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/export",
        scope: "research:read",
        summary: "Export citations as BibTeX, RIS or CSL JSON.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/report",
        scope: "research:read",
        summary: "Read the latest cached synthesis report.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/report",
        scope: "research:write",
        summary: "Synthesize or refresh a grounded review report.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/chat",
        scope: "research:read",
        summary: "Read the run's grounded conversation.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/chat",
        scope: "research:write",
        summary: "Ask a question grounded in this run and its citations.",
        curl: `curl -X POST {BASE}/runs/42/chat \\
  -H "Authorization: Bearer $SIX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"question":"Which findings have the strongest direct support?"}'`,
      },
      {
        method: "POST",
        path: "/runs/{run_id}/living",
        scope: "research:write",
        summary: "Enable or disable monitoring for a completed review.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/recheck",
        scope: "research:write",
        summary: "Run a targeted integrity recheck on the review record.",
      },
    ],
  },
  {
    id: "papers",
    title: "Paper library",
    icon: Library,
    blurb: "Give an integration only the paper access it needs, independently of research-run permissions.",
    endpoints: [
      {
        method: "GET",
        path: "/documents",
        scope: "library:read",
        summary: "List accessible workspace papers and acquisition metadata.",
      },
      {
        method: "GET",
        path: "/documents/{document_id}/file",
        scope: "library:read",
        summary: "Download one owned PDF.",
      },
      {
        method: "GET",
        path: "/runs/{run_id}/documents",
        scope: "library:read",
        summary: "Read a run's full-text acquisition ledger.",
      },
      {
        method: "POST",
        path: "/orgs/current/documents",
        scope: "library:write",
        summary: "Add a PDF from an open URL or a base64 upload.",
        curl: `curl -X POST {BASE}/orgs/current/documents \\
  -H "Authorization: Bearer $SIX_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"url":"https://arxiv.org/pdf/1706.03762"}'`,
      },
      {
        method: "POST",
        path: "/runs/{run_id}/documents",
        scope: "library:write",
        summary: "Attach a PDF directly to an owned run.",
      },
      {
        method: "POST",
        path: "/runs/{run_id}/acquire",
        scope: "library:write",
        summary: "Acquire legal open-access copies for retained works.",
      },
    ],
  },
];

const json = (value: unknown) => JSON.stringify(value, null, 2);
const runId: ContractParameter = {
  name: "run_id",
  source: "path",
  type: "string",
  required: true,
  description: "Numeric or public run ID returned when the run was created.",
};
const projectId: ContractParameter = {
  name: "project_id",
  source: "path",
  type: "integer",
  required: true,
  description: "Workspace-owned project ID.",
};
const documentId: ContractParameter = {
  name: "document_id",
  source: "path",
  type: "integer",
  required: true,
  description: "Workspace-owned document ID returned by a document endpoint.",
};
const runCreated = json({
  id: 42,
  public_id: "r_7sk2m9",
  status: "pending",
  gated: true,
});
const project = json({
  id: 12,
  name: "IaC evidence review",
  description: "A review of infrastructure-as-code evidence.",
  kind: "research",
  phase: "discovery",
  status: "active",
  question: "How reliable are LLMs for title and abstract screening?",
  hypothesis: "Recall-first screening can reduce reviewer workload.",
  metadata: {},
  created_at: "2026-08-01T12:00:00+00:00",
  updated_at: "2026-08-01T12:00:00+00:00",
});
const uploadedDocument = (runId: number | null) => json({
  id: 81,
  run_id: runId,
  work_id: "W2741809807",
  title: "Automated screening for reviews",
  verified: true,
  text_status: "ready",
  byte_size: 842133,
});

const CONTRACTS: Record<string, EndpointContract> = {
  "GET /projects": {
    success: "200 OK · application/json",
    response: `[${project}]`,
  },
  "POST /projects": {
    request: json({
      name: "IaC evidence review",
      description: "A review of infrastructure-as-code evidence.",
      kind: "research",
      question: "How reliable are LLMs for title and abstract screening?",
    }),
    requestNote: "name is required. description, kind and question are optional.",
    success: "200 OK · application/json",
    response: project,
  },
  "PATCH /projects/{project_id}": {
    parameters: [projectId],
    request: json({ name: "Updated review name", phase: "screening", status: "active" }),
    requestNote: "Send only fields that should change. The metadata object accepts integration-owned JSON.",
    success: "200 OK · application/json",
    response: project,
  },
  "GET /runs": {
    parameters: [
      { name: "project_id", source: "query", type: "integer | null", description: "Return runs filed in one project." },
      { name: "limit", source: "query", type: "integer", defaultValue: "200", description: "Number of runs to return; clamped to 1–500." },
    ],
    success: "200 OK · application/json",
    response: json([{ id: 42, public_id: "r_7sk2m9", project_id: 12, question: "How reliable are LLM screeners?", title: null, status: "running", created_at: "2026-08-01T12:00:00+00:00", finished_at: null, prisma: {}, is_demo: false }]),
  },
  "POST /projects/{project_id}/runs": {
    parameters: [projectId],
    request: json({ question: "How reliable are LLMs for title and abstract screening?", mode: "search", screen: true, gate_protocol: true, paper_limit: 50, acquire: true, full_text: true }),
    requestNote: "question is required. The OpenAPI schema lists every review switch, bound and import field.",
    success: "202 Accepted · application/json",
    response: runCreated,
  },
  "POST /runs": {
    request: json({ question: "What evidence supports policy-as-code adoption?", mode: "search", screen: true, paper_limit: 50 }),
    requestNote: "Creates an unfiled run. The request body is otherwise identical to the project run endpoint.",
    success: "202 Accepted · application/json",
    response: runCreated,
  },
  "GET /runs/{run_id}": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json({ id: 42, public_id: "r_7sk2m9", project_id: 12, status: "running", question: "How reliable are LLM screeners?", title: null, config: { screen: true, paper_limit: 50 }, prisma: {}, error: null, is_demo: false, created_at: "2026-08-01T12:00:00+00:00", finished_at: null, last_event: { stage: "screening_title_abstract", event: "screening_progress" } }),
  },
  "PATCH /runs/{run_id}": {
    parameters: [runId],
    request: json({ title: "LLM screening reliability", project_id: 12 }),
    requestNote: "title and project_id are optional. Omit a field to keep its current value.",
    success: "200 OK · application/json",
    response: json({ id: 42, title: "LLM screening reliability", project_id: 12 }),
  },
  "POST /runs/{run_id}/pause": {
    parameters: [runId],
    request: null,
    requestNote: "No request body. The worker pauses at its next durable checkpoint.",
    success: "200 OK · application/json",
    response: json({ run_id: 42, control: "pause" }),
  },
  "POST /runs/{run_id}/resume": {
    parameters: [runId],
    request: null,
    requestNote: "No request body. Completed stages are not repeated.",
    success: "202 Accepted · application/json",
    response: json({ run_id: 42, status: "resuming" }),
  },
  "POST /runs/{run_id}/cancel": {
    parameters: [runId],
    request: null,
    requestNote: "No request body. Cancellation fences run-scoped workers and preserves the audit record.",
    success: "200 OK · application/json",
    response: json({ run_id: 42, control: "cancel", status: "cancelled", cancelled_jobs: 2 }),
  },
  "GET /runs/{run_id}/events": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json([{ id: 311, stage: "retrieval", event: "deduplicated", payload: { identified: 1119, unique: 986 }, created_at: "2026-08-01T12:02:11+00:00" }]),
  },
  "GET /runs/{run_id}/events/stream": {
    parameters: [runId],
    success: "200 OK · text/event-stream",
    response: "event: progress\ndata: {\"stage\":\"screening\",\"percent\":42}\n\nevent: done\ndata: {\"status\":\"completed\"}\n\n",
    responseNote: "Keep the connection open and parse standard SSE frames. API keys authenticate with the Authorization header, not a query ticket.",
  },
  "GET /runs/{run_id}/protocol": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json({ run_status: "awaiting_protocol_approval", protocol: { question: "How reliable are LLM screeners?", query_string: "(LLM OR large language model) AND screening", inclusion_criteria: ["Evaluates screening reliability"], exclusion_criteria: ["No empirical evaluation"] } }),
  },
  "POST /runs/{run_id}/protocol/regenerate": {
    parameters: [runId],
    request: null,
    requestNote: "No request body. The run must currently be waiting at the protocol gate.",
    success: "200 OK · application/json",
    response: json({ run_status: "awaiting_protocol_approval", protocol: { query_string: "(LLM OR large language model) AND screening", inclusion_criteria: ["Evaluates screening reliability"] } }),
  },
  "POST /runs/{run_id}/protocol/approve": {
    parameters: [runId],
    request: json({ inclusion_criteria: ["Reports a screening outcome"], exclusion_criteria: ["Editorial only"], query_string: "(LLM OR large language model) AND screening" }),
    requestNote: "Every field is optional; send {} to approve the draft unchanged.",
    success: "202 Accepted · application/json",
    response: json({ id: 42, status: "running" }),
  },
  "GET /runs/{run_id}/queue": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json([{ work_id: "W2741809807", title: "Automated screening for reviews", stage: "title_abstract", model_reason: "Borderline population match", evidence: "We evaluate automated screening…", reviewer: "screening model" }]),
  },
  "GET /runs/{run_id}/decisions": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json([{ work_id: "W2741809807", verdict: "include", reason: "Meets the protocol", reviewer: "human reviewer", by: "human", stage: "title_abstract" }]),
  },
  "POST /runs/{run_id}/decisions": {
    parameters: [runId],
    request: json([{ work_id: "W2741809807", verdict: "include", reason: "Reports a screening accuracy outcome" }]),
    requestNote: "The body is an array. verdict must be include, exclude or unsure.",
    success: "201 Created · application/json",
    response: json({ recorded: 1 }),
  },
  "POST /runs/{run_id}/calibration": {
    parameters: [runId],
    request: json([{ work_id: "W2741809807", included: true }, { work_id: "W1234567890", included: false }]),
    requestNote: "The body is a human-labelled seed array used to measure the screener against target recall.",
    success: "200 OK · application/json",
    response: json({ seed_size: 2, evaluated: 2, human_includes: 1, recall: 1, agreement: 1, missed: [], verdict: "insufficient_overlap", note: "only 2 of 2 seed labels were screened (need >= 10); label more works or widen the search" }),
  },
  "GET /runs/{run_id}/works": {
    parameters: [runId, { name: "included_only", source: "query", type: "boolean", defaultValue: "false", description: "Return only the final included set." }, { name: "verdict", source: "query", type: "all | include | exclude | unsure", defaultValue: "all", description: "Filter while preserving ranking." }, { name: "limit", source: "query", type: "integer", defaultValue: "100", description: "Page size." }, { name: "offset", source: "query", type: "integer", defaultValue: "0", description: "Zero-based page offset." }],
    success: "200 OK · application/json",
    response: json({
      total: 986,
      identified_total: 1119,
      evidence_counts: { confirmed_include: 12, provisional_include: 38, unsure: 83, exclude: 853, unscreened: 133, retained: 133 },
      result_evidence_counts: { confirmed_include: 12, provisional_include: 38, unsure: 0, exclude: 0, unscreened: 0, retained: 50 },
      paper_limit: 50,
      selected_total: 50,
      works: [{ rank: 1, id: "W2741809807", title: "Automated screening for reviews", authors: ["A. Researcher"], year: 2025, venue: "Journal of Evidence Synthesis", doi: "10.1000/example", abstract: "We evaluate automated screening…", cited_by_count: 18, work_type: "article", oa_status: "gold", oa_url: "https://example.org/article", pdf_url: "https://example.org/paper.pdf", score: 0.93, signals: { relevance: 0.91, impact: 0.88, recency: 0.96 }, retracted: false, explanation: "Directly evaluates screening reliability.", verdict: "include", verdict_by: "model", verdict_reason: "Meets the frozen protocol.", evidence_state: "provisional_include", selected: true }],
      search_string: "(LLM OR large language model) AND screening",
      search_synthesized_by: "AI-assisted query synthesis",
    }),
    responseNote: "works is the requested ranked page. total is the number of records after the verdict filter; selected_total describes the retained result set.",
  },
  "GET /runs/{run_id}/web-sources": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json([{ title: "Reporting guideline", url: "https://example.org/guideline", domain: "example.org", quality: 0.82, category: "organization", snippet: "Current guidance for review reporting…" }]),
  },
  "GET /runs/{run_id}/methods": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json({ methods: "We searched the literature index using a frozen protocol, screened 986 unique records and retained 50 studies…" }),
  },
  "GET /runs/{run_id}/export": {
    parameters: [runId, { name: "format", source: "query", type: "bibtex | ris | csl", defaultValue: "bibtex", description: "Citation export format." }, { name: "included_only", source: "query", type: "boolean", defaultValue: "false", description: "Export only included works." }],
    success: "200 OK · format-specific download",
    response: "@article{example2025,\n  title = {Automated screening for reviews},\n  year = {2025}\n}",
    responseNote: "Content-Type and filename follow the requested BibTeX, RIS or CSL format.",
  },
  "GET /runs/{run_id}/report": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json({ id: 9, created_at: "2026-08-01T13:20:00+00:00", report: { summary: "Evidence indicates…", findings: [], limitations: [] } }),
  },
  "POST /runs/{run_id}/report": {
    parameters: [runId],
    request: json({ force: false }),
    requestNote: "Set force to true to regenerate an existing report. The run must be complete.",
    success: "200 OK · application/json",
    response: json({ id: 9, created_at: "2026-08-01T13:20:00+00:00", report: { summary: "Evidence indicates…", findings: [], limitations: [] } }),
  },
  "GET /runs/{run_id}/chat": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json([{ id: 501, role: "assistant", content: "The strongest directly supported finding is…", citations: [{ id: "W2741809807", title: "Automated screening for reviews" }], payload: {}, created_at: "2026-08-01T13:30:00+00:00" }]),
  },
  "POST /runs/{run_id}/chat": {
    parameters: [runId],
    request: json({ question: "Which findings have the strongest direct support?", context_size: 30, selection: null, model: "auto" }),
    requestNote: "question is required. context_size is adaptive when omitted. selection can ground the answer in a marked PDF passage.",
    success: "200 OK · application/json",
    response: json({ answer: "The strongest directly supported finding is…", citations: [{ id: "W2741809807", title: "Automated screening for reviews" }], sources_considered: 30, tools_used: ["find_papers", "read_paper"], evidence: [{ work_id: "W2741809807", page: 4, quote: "The recall-first configuration…" }], claims_checked: 1, claims_supported: 1, claims_flagged: 0, claim_checks: [{ claim: "The recall-first configuration retained relevant studies.", support: "supported", evidence_ids: ["W2741809807"] }] }),
  },
  "POST /runs/{run_id}/living": {
    parameters: [runId],
    request: json({ enabled: true }),
    success: "200 OK · application/json",
    response: json({ run_id: 42, living: true }),
  },
  "POST /runs/{run_id}/recheck": {
    parameters: [runId],
    request: null,
    requestNote: "No request body. Checks the included set against the current retraction record.",
    success: "200 OK · application/json",
    response: json({ checked: 50, retracted_now: [], newly_retracted: [] }),
  },
  "GET /documents": {
    parameters: [{ name: "q", source: "query", type: "string", defaultValue: "empty", description: "Case-insensitive title or work-ID filter." }, { name: "limit", source: "query", type: "integer", defaultValue: "200", description: "Number of unique PDFs; clamped to 1–500." }],
    success: "200 OK · application/json",
    response: json([{ id: 81, work_id: "W2741809807", title: "Automated screening for reviews", year: 2025, source: "arxiv", legal_basis: "open_access", license: "cc-by", content_type: "application/pdf", byte_size: 842133, text_status: "ready", has_file: true, created_at: "2026-08-01T13:40:00+00:00", project_id: 12, project_name: "IaC evidence review", folder: "Screening evidence", run: { public_id: "r_7sk2m9", label: "LLM screening reliability" } }]),
  },
  "GET /documents/{document_id}/file": {
    parameters: [documentId],
    success: "200 OK · application/pdf",
    response: "<binary PDF bytes>",
    responseNote: "Stream the response to a file. A missing or foreign document ID returns 404 without revealing another workspace.",
  },
  "GET /runs/{run_id}/documents": {
    parameters: [runId],
    success: "200 OK · application/json",
    response: json([{ id: 81, work_id: "W2741809807", title: "Automated screening for reviews", status: "retrieved", source: "arxiv", legal_basis: "open_access", license: "cc-by", version: "accepted", url: "https://arxiv.org/pdf/example", content_type: "application/pdf", checksum: "sha256:…", byte_size: 842133, text_status: "ready", reason: null, has_file: true }]),
  },
  "POST /orgs/current/documents": {
    request: json({ url: "https://arxiv.org/pdf/1706.03762", filename: "attention-is-all-you-need.pdf", project_id: 12 }),
    requestNote: "Provide either url or content_base64. filename and project_id are optional. The maximum request size and storage quota still apply.",
    success: "201 Created · application/json",
    response: uploadedDocument(null),
  },
  "POST /runs/{run_id}/documents": {
    parameters: [runId],
    request: json({ content_base64: "JVBERi0xLjQK…", filename: "paper.pdf" }),
    requestNote: "Provide either url or a base64-encoded PDF. The resulting document is attached to the owned run.",
    success: "201 Created · application/json",
    response: uploadedDocument(42),
  },
  "POST /runs/{run_id}/acquire": {
    parameters: [runId],
    request: null,
    requestNote: "No request body. The completed run's retained works are checked for legal open-access copies.",
    success: "202 Accepted · application/json",
    response: json({ run_id: 42, status: "collecting", note: "open-access sources only" }),
  },
};

const ERROR_ROWS = [
  ["401", "invalid or expired token", "Replace or rotate the key."],
  ["403", "insufficient_scope", "Create a key with the required scope."],
  ["403", "api_key_endpoint_forbidden", "Use an interactive session; this route is not public API."],
  ["404", "resource not found", "IDs outside the key's workspace are deliberately hidden."],
  ["429", "api_key_rate_limited", "Honor Retry-After before retrying."],
] as const;

function MethodBadge({ method }: { method: Method }) {
  return (
    <span
      className={cn(
        "inline-flex w-14 shrink-0 justify-center rounded-full border px-2 py-1 font-mono text-[0.625rem] font-semibold tracking-[0.12em]",
        METHOD_STYLE[method],
      )}
    >
      {method}
    </span>
  );
}

function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1.5 text-[0.6875rem] font-medium transition-colors hover:bg-muted"
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
      {copied ? "Copied" : label}
    </button>
  );
}

function endpointContract(endpoint: Endpoint): EndpointContract {
  const contract = CONTRACTS[`${endpoint.method} ${endpoint.path}`];
  if (!contract) {
    throw new Error(`Missing API documentation contract for ${endpoint.method} ${endpoint.path}`);
  }
  return contract;
}

function defaultCurl(endpoint: Endpoint, contract: EndpointContract): string {
  const path = endpoint.path
    .replace("{project_id}", "12")
    .replace("{run_id}", "r_7sk2m9")
    .replace("{document_id}", "81");
  const firstLine = `curl${path.endsWith("/events/stream") ? " -N" : ""}${endpoint.method === "GET" ? "" : ` -X ${endpoint.method}`} ${API_URL}${path}`;
  const command = [firstLine, '-H "Authorization: Bearer $SIX_API_KEY"'];
  if (contract.request) {
    command.push('-H "Content-Type: application/json"');
    command.push(`-d '${contract.request.replaceAll("\n", "")}'`);
  }
  if (path.endsWith("/file")) command.push("-o paper.pdf");
  if (path.endsWith("/export")) command.push("-o references.bib");
  return command.join(` ${String.fromCharCode(92)}\n  `);
}

function EndpointCard({ endpoint }: { endpoint: Endpoint }) {
  const [open, setOpen] = useState(false);
  const contract = endpointContract(endpoint);
  const curl = endpoint.curl?.replaceAll("{BASE}", API_URL) ?? defaultCurl(endpoint, contract);
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card transition-colors hover:border-moss/35">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start gap-3 px-4 py-4 text-left sm:items-center"
        aria-expanded={open}
      >
        <MethodBadge method={endpoint.method} />
        <div className="min-w-0 flex-1">
          <code className="block break-all font-mono text-[0.75rem] font-medium sm:text-[0.8125rem]">
            {endpoint.path}
          </code>
          <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
            {endpoint.summary}
          </p>
        </div>
        <div className="hidden shrink-0 items-center gap-3 sm:flex">
          <Badge variant="secondary" className="rounded-full font-mono text-[0.5625rem] font-normal">
            {endpoint.scope}
          </Badge>
          <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
        </div>
      </button>
      {open && (
        <div className="border-t border-border px-4 py-4 sm:pl-[5.25rem]">
          <Badge variant="secondary" className="mb-3 rounded-full font-mono text-[0.5625rem] font-normal sm:hidden">
            {endpoint.scope}
          </Badge>
          {endpoint.detail && (
            <p className="mb-4 max-w-3xl text-[0.75rem] leading-6 text-muted-foreground">
              {endpoint.detail}
            </p>
          )}
          {contract.parameters && contract.parameters.length > 0 && (
            <div className="mb-3 overflow-hidden rounded-xl border border-border">
              <div className="border-b border-border bg-muted/35 px-4 py-2 font-mono text-[0.5625rem] uppercase tracking-[0.2em] text-muted-foreground">
                Parameters
              </div>
              <div className="divide-y divide-border">
                {contract.parameters.map((parameter) => (
                  <div key={`${parameter.source}:${parameter.name}`} className="grid gap-1 px-4 py-3 text-[0.6875rem] sm:grid-cols-[9rem_7rem_minmax(0,1fr)] sm:items-start">
                    <div>
                      <code className="font-mono font-medium">{parameter.name}</code>
                      <span className="ml-2 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">{parameter.source}</span>
                    </div>
                    <div className="font-mono text-muted-foreground">
                      {parameter.type}
                      {parameter.required ? " · required" : parameter.defaultValue ? ` · default ${parameter.defaultValue}` : " · optional"}
                    </div>
                    <p className="leading-5 text-muted-foreground">{parameter.description}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
          {curl && (
            <div className="overflow-hidden rounded-xl border border-border bg-[#0e1513] text-[#e8edea]">
              <div className="flex items-center justify-between border-b border-white/10 px-4 py-2">
                <span className="font-mono text-[0.5625rem] uppercase tracking-[0.2em] text-white/55">Example curl</span>
                <CopyButton value={curl} label="Copy curl" />
              </div>
              <pre className="max-h-[28rem] overflow-auto overscroll-contain p-4 font-mono text-[0.6875rem] leading-5"><code>{curl}</code></pre>
            </div>
          )}
          {endpoint.method !== "GET" && (
            <div className="mt-3 overflow-hidden rounded-xl border border-border bg-muted/20">
              <div className="flex items-center justify-between border-b border-border px-4 py-2">
                <span className="font-mono text-[0.5625rem] uppercase tracking-[0.2em] text-muted-foreground">JSON request body</span>
                {contract.request && <CopyButton value={contract.request} label="Copy JSON" />}
              </div>
              {contract.request ? (
                <pre className="max-h-[28rem] overflow-auto overscroll-contain p-4 font-mono text-[0.6875rem] leading-5"><code>{contract.request}</code></pre>
              ) : (
                <p className="px-4 py-3 text-[0.75rem] text-muted-foreground">No request body.</p>
              )}
              {contract.requestNote && <p className="border-t border-border px-4 py-3 text-[0.6875rem] leading-5 text-muted-foreground">{contract.requestNote}</p>}
            </div>
          )}
          <div className="mt-3 overflow-hidden rounded-xl border border-border bg-muted/35">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-2">
              <span className="font-mono text-[0.5625rem] uppercase tracking-[0.2em] text-muted-foreground">Example response</span>
              <div className="flex items-center gap-2">
                <code className="text-right font-mono text-[0.625rem] text-moss">{contract.success}</code>
                <CopyButton value={contract.response} label="Copy response" />
              </div>
            </div>
            <pre className="max-h-[28rem] overflow-auto overscroll-contain p-4 font-mono text-[0.6875rem] leading-5"><code>{contract.response}</code></pre>
            {contract.responseNote && <p className="border-t border-border px-4 py-3 text-[0.6875rem] leading-5 text-muted-foreground">{contract.responseNote}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ApiDocsPage() {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<ApiScope | "all">("all");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return SECTIONS.map((section) => ({
      ...section,
      endpoints: section.endpoints.filter((endpoint) => {
        const matchesScope = scope === "all" || endpoint.scope === scope;
        const matchesQuery =
          !needle ||
          endpoint.path.toLowerCase().includes(needle) ||
          endpoint.summary.toLowerCase().includes(needle) ||
          endpoint.method.toLowerCase().includes(needle);
        return matchesScope && matchesQuery;
      }),
    })).filter((section) => section.endpoints.length > 0);
  }, [query, scope]);

  const endpointCount = filtered.reduce((total, section) => total + section.endpoints.length, 0);

  return (
    <main className="h-full min-h-0 overflow-y-auto overscroll-contain bg-background text-foreground">
      <section className="border-b border-border bg-[radial-gradient(circle_at_85%_10%,hsl(var(--accent))_0,transparent_32%)]">
        <div className="mx-auto max-w-[92rem] px-5 py-12 sm:px-8 sm:py-16 lg:px-12">
          <div className="grid gap-10 2xl:grid-cols-[minmax(0,1.15fr)_minmax(23rem,.85fr)] 2xl:items-end">
            <div className="max-w-4xl">
              <div className="mb-5 flex flex-wrap items-center gap-2">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.26em] text-moss">Developer API</span>
                <Badge variant="outline" className="rounded-full font-mono text-[0.5625rem] font-normal">v1 · restricted surface</Badge>
              </div>
              <h1 className="max-w-5xl font-display text-5xl leading-[0.95] tracking-[-0.035em] sm:text-6xl xl:text-7xl">
                Automate the research record.
              </h1>
              <p className="mt-6 max-w-2xl text-base leading-7 text-muted-foreground sm:text-lg">
                Build defensible review workflows without handing a machine credential the keys to your entire workspace.
                Every route below is explicitly scoped, tenant-bound and non-destructive.
              </p>
            </div>
            <div className="rounded-3xl border border-border bg-card/90 p-5 shadow-sm backdrop-blur sm:p-6">
              <div className="flex items-center gap-3">
                <span className="flex size-10 items-center justify-center rounded-full bg-primary text-primary-foreground"><KeyRound className="size-4" /></span>
                <div>
                  <p className="text-sm font-medium">Bearer authentication</p>
                  <p className="text-xs text-muted-foreground">Raw keys are shown once and stored hashed.</p>
                </div>
              </div>
              <div className="mt-5 rounded-xl border border-border bg-muted/45 px-4 py-3 font-mono text-[0.6875rem] text-muted-foreground">
                Authorization: Bearer six_sk_…
              </div>
              <div className="mt-4 grid grid-cols-3 divide-x divide-border text-center">
                <div><p className="font-display text-2xl">4</p><p className="text-[0.625rem] text-muted-foreground">scopes</p></div>
                <div><p className="font-display text-2xl">0</p><p className="text-[0.625rem] text-muted-foreground">delete routes</p></div>
                <div><p className="font-display text-2xl">90d</p><p className="text-[0.625rem] text-muted-foreground">default expiry</p></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-[92rem] px-5 py-10 sm:px-8 lg:px-12">
        <section className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(20rem,.7fr)]">
          <div className="rounded-3xl border border-border bg-card p-5 sm:p-7">
            <div className="flex items-center gap-3"><ShieldCheck className="size-5 text-moss" /><h2 className="font-display text-2xl">Narrow by design</h2></div>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">
              API keys are denied everywhere except the routes in this reference. Writer and manuscripts, Visual Lab,
              Surveys, Interviews, Data Hub, connectors, webhooks, account, team, deployment configuration, shares,
              annotations and every destructive delete remain interactive-session only.
            </p>
            <div className="mt-5 grid gap-2 sm:grid-cols-3">
              {[
                [LockKeyhole, "Function guard", "Unknown routes fail closed."],
                [FolderClosed, "Object guard", "Every ID is checked against the key's workspace."],
                [Radio, "Resource guard", "Configured rate and resource limits still apply."],
              ].map(([Icon, title, body]) => {
                const ItemIcon = Icon as ComponentType<{ className?: string }>;
                return (
                  <div key={String(title)} className="rounded-2xl border border-border bg-muted/25 p-4">
                    <ItemIcon className="size-4 text-moss" />
                    <p className="mt-3 text-xs font-medium">{String(title)}</p>
                    <p className="mt-1 text-[0.6875rem] leading-5 text-muted-foreground">{String(body)}</p>
                  </div>
                );
              })}
            </div>
          </div>
          <div id="quickstart" className="rounded-3xl border border-border bg-[#0e1513] p-5 text-[#e8edea] sm:p-7">
            <div className="flex items-center gap-3"><SquareTerminal className="size-5 text-[#9fc2b7]" /><h2 className="font-display text-2xl">First request</h2></div>
            <ol className="mt-4 space-y-3 text-xs leading-5 text-white/65">
              <li><span className="mr-2 font-mono text-[#9fc2b7]">01</span>Create a key under Settings → API keys.</li>
              <li><span className="mr-2 font-mono text-[#9fc2b7]">02</span>Select only the scopes the integration needs.</li>
              <li><span className="mr-2 font-mono text-[#9fc2b7]">03</span>Store the raw value in a secret manager.</li>
            </ol>
            <div className="mt-5 rounded-xl border border-white/10 bg-black/20 p-4 font-mono text-[0.6875rem] leading-5">
              <span className="text-white/40">$ </span>export SIX_API_KEY=&quot;six_sk_…&quot;<br />
              <span className="text-white/40">$ </span>curl {API_URL}/projects \<br />
              <span className="pl-4 text-white/65">-H &quot;Authorization: Bearer $SIX_API_KEY&quot;</span>
            </div>
            <a
              href={`${API_URL}/public-api/openapi.json`}
              target="_blank"
              rel="noreferrer"
              className="mt-4 flex items-center justify-between rounded-xl border border-white/10 px-4 py-3 text-xs transition-colors hover:bg-white/5"
            >
              <span><strong className="font-medium text-white">OpenAPI 3.1</strong><span className="ml-2 text-white/50">Postman · SDKs · code generation</span></span>
              <ArrowRight className="size-3.5 text-[#9fc2b7]" />
            </a>
          </div>
        </section>

        <section className="mt-10">
          <div className="mb-5 flex items-end justify-between gap-4">
            <div><span className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">Permission model</span><h2 className="mt-2 font-display text-3xl">One purpose per key.</h2></div>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {SCOPES.map((item) => (
              <article key={item.id} className="flex min-h-48 flex-col rounded-3xl border border-border bg-card p-5">
                <code className="font-mono text-[0.6875rem] text-moss">{item.id}</code>
                <h3 className="mt-5 text-sm font-semibold">{item.label}</h3>
                <p className="mt-2 flex-1 text-xs leading-5 text-muted-foreground">{item.description}</p>
                <p className="mt-5 border-t border-border pt-3 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground">{item.access}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="mt-12 grid gap-8 2xl:grid-cols-[17rem_minmax(0,1fr)]">
          <aside className="space-y-5 2xl:sticky 2xl:top-6 2xl:self-start">
            <div>
              <label htmlFor="endpoint-search" className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-muted-foreground">Find an endpoint</label>
              <div className="relative mt-2"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="endpoint-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Path or action" className="h-10 rounded-xl pl-9" /></div>
            </div>
            <div>
              <p className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-muted-foreground">Scope</p>
              <div className="mt-2 flex flex-wrap gap-1.5 2xl:flex-col">
                {(["all", ...SCOPES.map((item) => item.id)] as const).map((item) => (
                  <button key={item} type="button" onClick={() => setScope(item)} className={cn("rounded-full border px-3 py-2 text-left font-mono text-[0.625rem] transition-colors 2xl:w-full", scope === item ? "border-moss bg-accent text-moss" : "border-border bg-card text-muted-foreground hover:bg-muted")}>
                    {item === "all" ? "All public routes" : item}
                  </button>
                ))}
              </div>
            </div>
            <div className="rounded-2xl border border-border bg-muted/25 p-4">
              <p className="text-xs font-medium">{endpointCount} routes shown</p>
              <p className="mt-1 text-[0.6875rem] leading-5 text-muted-foreground">If a route is not listed here, an API key receives 403 even when its user can access it in the app.</p>
            </div>
          </aside>

          <div className="space-y-10">
            {filtered.map((section) => {
              const Icon = section.icon;
              return (
                <section key={section.id} id={section.id} className="scroll-mt-6">
                  <div className="mb-4 flex items-start gap-3">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-border bg-card"><Icon className="size-4 text-moss" /></span>
                    <div><h2 className="font-display text-2xl">{section.title}</h2><p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">{section.blurb}</p></div>
                  </div>
                  <div className="space-y-2">{section.endpoints.map((endpoint) => <EndpointCard key={`${endpoint.method}:${endpoint.path}`} endpoint={endpoint} />)}</div>
                </section>
              );
            })}
            {filtered.length === 0 && (
              <div className="rounded-3xl border border-dashed border-border px-6 py-14 text-center"><Braces className="mx-auto size-5 text-muted-foreground" /><p className="mt-3 text-sm font-medium">No public route matches.</p><p className="mt-1 text-xs text-muted-foreground">Try another search or scope.</p></div>
            )}
          </div>
        </section>

        <section className="mt-14 grid gap-4 2xl:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="overflow-hidden rounded-3xl border border-border bg-card">
            <div className="border-b border-border px-5 py-4 sm:px-6">
              <p className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-muted-foreground">Error contract</p>
              <h2 className="mt-2 font-display text-2xl">Failures you can handle.</h2>
              <p className="mt-2 max-w-3xl text-[0.6875rem] leading-5 text-muted-foreground">
                JSON errors use <code className="font-mono">{`{"detail":"message"}`}</code> or a structured <code className="font-mono">detail</code> object with a stable <code className="font-mono">code</code>. Validation failures use FastAPI&apos;s standard 422 detail array.
              </p>
            </div>
            <div className="divide-y divide-border">
              {ERROR_ROWS.map(([status, code, action]) => (
                <div key={`${status}:${code}`} className="grid gap-1 px-5 py-3 text-xs sm:grid-cols-[3rem_minmax(9rem,11rem)_minmax(10rem,1fr)] sm:items-center sm:px-6">
                  <code className="font-mono font-semibold text-moss">{status}</code><code className="font-mono text-[0.6875rem]">{code}</code><span className="text-muted-foreground">{action}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-3xl border border-border bg-primary p-6 text-primary-foreground">
            <FileText className="size-5" /><h2 className="mt-5 font-display text-2xl">Safe defaults</h2>
            <ul className="mt-4 space-y-3 text-xs leading-5 text-primary-foreground/70">
              <li className="flex gap-2"><Check className="mt-0.5 size-3 shrink-0" />30, 90, 180 or 365-day expiry</li>
              <li className="flex gap-2"><Check className="mt-0.5 size-3 shrink-0" />120 requests per key per minute</li>
              <li className="flex gap-2"><Check className="mt-0.5 size-3 shrink-0" />Read prerequisites for write scopes</li>
              <li className="flex gap-2"><Check className="mt-0.5 size-3 shrink-0" />Immediate revocation in Settings</li>
            </ul>
            <a href="#quickstart" className="mt-6 inline-flex items-center gap-2 text-xs font-medium">Create the right key <ArrowRight className="size-3" /></a>
          </div>
        </section>
      </div>
    </main>
  );
}
