/** Wire types for the bundled self-hosted SixSentences workspace API. */

export type Role = "owner" | "member";

export interface AssistantPreferences {
  detail: "concise" | "balanced" | "thorough";
  tone: "direct" | "academic" | "explanatory" | "critical";
  format: "adaptive" | "prose" | "structured";
  custom_instructions: string;
}

export type RunStatus =
  | "pending"
  | "running"
  | "awaiting_protocol_approval"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type Verdict = "include" | "exclude" | "unsure";

export interface Me {
  user_id: number;
  email: string;
  org_id: number;
  role: Role;
  first_name: string;
  language: "en" | "de";
  assistant_preferences: AssistantPreferences;
  onboarded: boolean;
  two_factor_enabled: boolean;
  terms_version: string | null;
  privacy_version: string | null;
  dpa_version: string | null;
  current_terms_version: string;
  current_privacy_version: string;
  current_dpa_version: string;
  legal_reaccept_required: boolean;
  /** Last controller identity explicitly confirmed for this workspace, never inferred. */
  legal_controller_name?: string | null;
  privacy_notice_update_available?: boolean;
  legal_requirements?: {
    age: boolean;
    terms: boolean;
    dpa: boolean;
    dpa_can_accept: boolean;
    privacy: boolean;
  };
}

export type LoginResponse =
  | { token: string; mfa_required?: false }
  | {
      mfa_required: true;
      challenge: string;
      expires_in_seconds: number;
    };

export interface TwoFactorStatus {
  enabled: boolean;
  recovery_codes_remaining: number;
}

export interface TwoFactorSetup {
  secret: string;
  otpauth_uri: string;
  issuer: string;
}

export type RegisterResponse =
  | {
      verification_required: true;
      email_sent: boolean;
      email: string;
    }
  | {
      verification_required: false;
      token: string;
      user: { id: number; email: string; role: Role };
      org_id: number;
    };

export type GoogleAuthResponse = LoginResponse | RegisterResponse;

export interface ChatModelOption {
  id: string;
  provider?: string;
  label: string;
  tagline: string;
  tagline_de: string;
  impact: "low" | "medium" | "high" | "very_high";
  reasoning: boolean;
  default: boolean;
  locked: boolean;
}

export interface ChatModelCatalog {
  default_id?: string;
  /** Opaque deployment route declared by the connected API. */
  routing_mode?: string;
  content_scope?: "private";
  /** Provider features that are enabled on the connected API deployment. */
  runtime_capabilities?: {
    web_search: boolean;
    pubmed: boolean;
  };
  models: ChatModelOption[];
}

export interface ScreeningMethod {
  id: "prisma" | "cochrane" | "jbi" | "campbell" | "kitchenham";
  label: string;
  short: string;
  best_for: string;
  reporting_standard: string;
  screening: string;
  appraisal: string;
  dual_review: string;
  guidance_url: string;
}

export type ApiKeyScope =
  | "research:read"
  | "research:write"
  | "library:read"
  | "library:write";
export type IssuedApiKeyScope = ApiKeyScope | "companion:read" | "companion:write" | "capture:read" | "capture:write";

export interface ApiKeyScopeDefinition {
  id: ApiKeyScope;
  label: string;
  description: string;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  scopes: IssuedApiKeyScope[];
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

export interface Member {
  id: number;
  email: string;
  role: string;
  is_active: boolean;
}

export interface Webhook {
  id: number;
  url: string;
  events: string[];
  active: boolean;
}

export interface WebhookCreated {
  id: number;
  url: string;
  events: string[];
  secret: string;
  note: string;
}

export interface Project {
  id: number;
  name: string;
  description: string;
  kind: string;
  phase: string;
  status: "active" | "paused" | "complete" | "archived";
  question: string;
  hypothesis: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export type KnowledgePageState =
  | "inbox"
  | "developing"
  | "evergreen"
  | "archived";

export type KnowledgePageStateFilter = KnowledgePageState | "active";

/** One creator-private thought page in the Knowledge workspace. */
export interface KnowledgePageRecord {
  public_id: string;
  title: string;
  body_markdown: string;
  state: KnowledgePageState;
  pinned: boolean;
  tags: string[];
  parent_id: string | null;
  parent_title: string | null;
  project_id: number | null;
  project_name: string | null;
  position: number;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgePageList {
  items: KnowledgePageRecord[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface KnowledgePageFilters {
  q?: string;
  search_scope?: "all" | "title";
  state?: KnowledgePageStateFilter;
  tag?: string;
  pinned?: boolean;
  project_id?: number;
  parent_id?: string;
  offset?: number;
  limit?: number;
}

export interface KnowledgePageCreate {
  client_request_id: string;
  title?: string;
  body_markdown?: string;
  state?: KnowledgePageState;
  pinned?: boolean;
  tags?: string[];
  parent_id?: string | null;
  project_id?: number | null;
  position?: number;
}

export interface KnowledgePageUpdate {
  expected_revision: number;
  title?: string;
  body_markdown?: string;
  state?: KnowledgePageState;
  pinned?: boolean;
  tags?: string[];
  parent_id?: string | null;
  project_id?: number | null;
  position?: number;
}

export type LiveSessionStatus = "recording" | "completed" | "cancelled" | "failed";
export type LiveSessionPurpose = "conversation" | "brainstorm";
export type LiveAudioChannel = "microphone" | "system" | "typed";

export interface LiveSessionConfig {
  enabled: boolean;
  max_session_minutes: number;
  max_batch_segments: number;
  max_segment_chars: number;
  session_purposes: LiveSessionPurpose[];
  brainstorm: {
    schema_version: 1;
    output_languages: Array<"de" | "en">;
    microphone_only: true;
    web_input_channels: Array<"typed" | "microphone">;
    raw_audio_upload: false;
    max_transcript_chars: number;
    max_segments: number;
    max_chunks: number;
  };
  poll_interval_ms: number;
  projects: Array<Pick<Project, "id" | "name">>;
  desktop: {
    status: "available" | "unavailable";
    platforms: Array<"macos">;
    download_url: string | null;
    minimum_version: string | null;
    permissions: Array<"microphone" | "system_audio" | "speech_recognition">;
  };
}

export interface LiveSessionConsent {
  participants_notified: true;
  notice_text: string;
}

export interface LiveSession {
  id: string;
  title: string;
  purpose: LiveSessionPurpose;
  project_id: number | null;
  project: Pick<Project, "id" | "name"> | null;
  status: LiveSessionStatus;
  language: "auto" | "de" | "en";
  started_at: string;
  ended_at: string | null;
  duration_ms: number;
  max_duration_ms: number;
  consent: LiveSessionConsent | null;
  segment_count: number;
  transcript_char_count: number;
  ask_count: number;
  last_segment_sequence: number;
  completed_through_sequence: number | null;
  interview_id: string | null;
  web_url: string;
  revision: number;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * One-time optimistic concurrency challenge returned by Core before a
 * destructive brainstorm project move. The UI must echo these values exactly;
 * substituting a freshly fetched revision could invalidate newer evidence.
 */
export interface BrainstormFilingChallenge {
  session_revision: number;
  source_project_id: number;
  source_document_id: string;
  source_document_revision: number;
}

/** One-time impact snapshot required before deleting project-grounded data. */
export interface BrainstormDeleteChallenge {
  session_revision: number;
  impact_sha256: string;
  affected_document_count: number;
  affected_synthesis_count: number;
  pending_synthesis_count: number;
}

export interface LiveSessionPage {
  sessions: LiveSession[];
  total: number;
}

export interface LiveTranscriptSegment {
  id?: string;
  client_event_id?: string;
  channel: LiveAudioChannel;
  speaker: string;
  start_ms: number;
  end_ms: number;
  text: string;
  is_final: true;
}

export interface LiveTranscriptSegmentCreate extends LiveTranscriptSegment {
  client_event_id: string;
}

/** Exact creator-scoped transcript row used by evidence deep links. */
export interface LiveSessionSegment {
  id: string;
  sequence: number;
  channel: LiveAudioChannel;
  speaker: string;
  start_ms: number;
  end_ms: number;
  text: string;
  is_final: true;
  created_at: string;
}

export interface LiveSegmentAppendResult {
  accepted: number;
  duplicates: number;
  revision: number;
  transcript_char_count: number;
  last_event_sequence: number;
}

export interface LiveBrainstormCreate {
  client_request_id: string;
  output_language: "de" | "en";
  context_through_sequence: number;
  schema_version: 1;
}

export interface LiveBrainstormCompleteResult {
  session: LiveSession;
  brainstorm: LiveBrainstormReceipt;
}

export interface LiveTranscriptSource {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  speaker: string;
  quote: string;
}

export interface LiveProjectSource {
  type: string;
  id: string;
  title: string;
  locator: string;
  quote: string;
}

export interface LiveAskReceipt {
  id: string;
  client_request_id: string;
  question: string;
  status: "pending" | "completed" | "failed";
  answer: string;
  error: string | null;
  transcript_sources: LiveTranscriptSource[];
  project_sources: LiveProjectSource[];
  created_at: string;
}

export type LiveBrainstormStatus = "pending" | "completed" | "failed";

export interface LiveBrainstormTheme {
  title: string;
  description: string;
}

export interface LiveBrainstormIdea {
  title: string;
  description: string;
}

export interface LiveBrainstormNextStep {
  action: string;
  owner: string | null;
}

export type LiveBrainstormEvidenceKind =
  | "summary"
  | "themes"
  | "ideas"
  | "open_questions"
  | "decisions"
  | "next_steps";

export interface LiveBrainstormEvidence {
  kind: LiveBrainstormEvidenceKind;
  index: number;
  segment_id: string;
  quote: string;
}

export interface LiveBrainstormResult {
  summary: string;
  themes: LiveBrainstormTheme[];
  ideas: LiveBrainstormIdea[];
  open_questions: string[];
  decisions: string[];
  next_steps: LiveBrainstormNextStep[];
  evidence: LiveBrainstormEvidence[];
}

export interface LiveBrainstormReceipt {
  id: string;
  client_request_id: string;
  status: LiveBrainstormStatus;
  output_language: "de" | "en";
  schema_version: 1;
  context_through_sequence: number;
  result: LiveBrainstormResult | null;
  error: string | null;
  error_code: string | null;
  created_at: string;
}

export interface LiveBrainstormPage {
  brainstorms: LiveBrainstormReceipt[];
}

export interface ProjectBrainstormEvidence {
  session_id: string;
  segment_id: string;
  quote: string;
}

export interface ProjectBrainstormCluster {
  id: string;
  title: string;
  summary: string;
  session_ids: string[];
  evidence: ProjectBrainstormEvidence[];
}

export interface ProjectBrainstormConnection {
  title: string;
  description: string;
  cluster_ids: string[];
  evidence: ProjectBrainstormEvidence[];
}

/** A grounded cross-session result. Unrelated clusters are deliberately retained. */
export interface ProjectBrainstormResult {
  title: string;
  summary: string;
  summary_evidence: ProjectBrainstormEvidence[];
  clusters: ProjectBrainstormCluster[];
  connections: ProjectBrainstormConnection[];
  unconnected_cluster_ids: string[];
  source_session_ids: string[];
}

export interface ProjectBrainstormSourceSession {
  session_id: string;
  title?: string;
  brainstorm_id?: string;
  context_through_sequence?: number;
}

export interface ProjectBrainstormDocument {
  id: string;
  project_id: number;
  revision: number;
  manual_markdown: string;
  manual_revision: number;
  update_available: boolean;
  result: ProjectBrainstormResult | null;
  source_sessions: ProjectBrainstormSourceSession[];
  created_at: string;
  updated_at: string;
}

export type ProjectBrainstormSynthesisStatus = "pending" | "completed" | "failed";

export interface ProjectBrainstormSynthesisReceipt {
  id: string;
  project_id: number;
  document_id: string;
  client_request_id: string;
  retry_of_id: string | null;
  mode: "selected" | "all_completed";
  output_language: "de" | "en";
  schema_version: 1;
  base_revision: number;
  status: ProjectBrainstormSynthesisStatus;
  result: ProjectBrainstormResult | null;
  source_sessions: ProjectBrainstormSourceSession[];
  error: string | null;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ProjectBrainstormSynthesisPage {
  syntheses: ProjectBrainstormSynthesisReceipt[];
  total: number;
}

export interface ProjectBrainstormSynthesisCreate {
  client_request_id: string;
  output_language: "de" | "en";
  session_ids: string[];
  include_all_completed: boolean;
  expected_document_revision: number;
  schema_version: 1;
}

export interface LiveSessionEvent {
  sequence: number;
  type: "segment" | "ask" | "brainstorm" | "session";
  created_at: string;
  payload: Record<string, unknown>;
}

export interface LiveSessionEventPage {
  events: LiveSessionEvent[];
  cursor: number;
  has_more: boolean;
  session: LiveSession;
}

export interface LiveCompanionPairRequest {
  code_challenge: string;
  state: string;
  device_name?: string;
}

export interface LiveCompanionPairResponse {
  code: string;
  state: string;
  expires_in_seconds: 300;
  deep_link: string;
}

/** Browser-session-only metadata for a connected desktop companion. */
export interface LiveCompanionDevice {
  id: number;
  name: string;
  /** Public key identifier only. The API never returns the credential itself. */
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface LiveCompanionDevicePage {
  devices: LiveCompanionDevice[];
}

export interface ProjectStudy {
  id: number;
  public_id: string;
  title: string;
  design: string;
  status: string;
  registry_id: string;
  population: string;
  intervention: string;
  comparator: string;
  outcomes: string[];
  report_work_ids: string[];
  identifiers: Record<string, unknown>;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectTask {
  id: number;
  title: string;
  description: string;
  status: "open" | "doing" | "blocked" | "done";
  priority: "low" | "normal" | "high" | "critical";
  assignee_user_id: number | null;
  due_at: string | null;
  linked_type: string;
  linked_id: string;
  created_at: string;
  updated_at: string;
}

export interface ClaimEvidence {
  id: number;
  target_type: "study" | "work" | "quote" | "dataset" | "analysis";
  target_id: string;
  relationship: EvidenceRelationship;
  locator: string;
  quote: string;
  note: string;
  source_version: string;
  verified: boolean;
}

export type EvidenceRelationship =
  | "supports"
  | "contradicts"
  | "qualifies"
  | "mixed"
  | "indirect"
  | "outdated"
  | "retracted"
  | "corrected";

export interface EvidenceClaim {
  id: number;
  public_id: string;
  text: string;
  section: string;
  status: string;
  confidence: string;
  writer_document_id: number | null;
  support_count: number;
  contradiction_count: number;
  unverified_count: number;
  evidence: ClaimEvidence[];
  created_at: string;
  updated_at: string;
}

export interface ProjectSubmission {
  id: number;
  public_id: string;
  journal: string;
  article_type: string;
  status: string;
  writer_document_id: number | null;
  target_date: string | null;
  checklist: { id: string; label: string; complete: boolean }[];
  metadata: Record<string, unknown>;
  completed: number;
  total: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectWorkspace {
  project: Project;
  readiness: {
    score: number;
    ready: number;
    total: number;
    checks: { label: string; ready: boolean }[];
    next_actions: { label: string; reason: string }[];
  };
  counts: {
    runs: number;
    studies: number;
    datasets: number;
    interviews: number;
    analyses: number;
    claims: number;
    unsupported_claims: number;
    writers: number;
    figures: number;
    documents: number;
    surveys: number;
    open_tasks: number;
  };
  interviews: {
    id: string;
    title: string;
    kind: "upload" | "live";
    status: string;
    duration_ms: number;
    created_at: string;
  }[];
  studies: ProjectStudy[];
  tasks: ProjectTask[];
  claims: EvidenceClaim[];
  risk_of_bias: {
    id: number;
    study_id: number | null;
    run_id: number | null;
    work_id: string;
    tool: string;
    overall: string;
    domains: Record<string, unknown>[];
    rationale: string;
    status: string;
    reviewer_user_id: number;
  }[];
  analyses: {
    id: number;
    public_id: string;
    dataset_id: number;
    dataset_version: number;
    name: string;
    kind: string;
    definition: Record<string, unknown>;
    result: Record<string, unknown>;
    status: string;
  }[];
  submissions: ProjectSubmission[];
  runs: { id: number; public_id: string; title: string; status: RunStatus }[];
  datasets: ResearchDataset[];
  writers: {
    id: number;
    public_id: string;
    title: string;
    compile_status: string;
    updated_at: string;
  }[];
  figures: Figure[];
  documents: {
    id: number;
    work_id: string;
    title: string;
    year: number | null;
    source: string | null;
  }[];
  surveys: {
    id: string;
    title: string;
    status: "draft" | "live" | "closed";
    question_count: number;
    response_count: number;
    updated_at: string;
  }[];
  activity: {
    id: number;
    event: string;
    payload: Record<string, unknown>;
    user_id: number | null;
    created_at: string;
  }[];
}

export interface PrismaCounts {
  records_identified: number;
  /** Papers recovered from web results (identification via other methods). */
  other_identified?: number;
  /** Records found via citation snowballing (part of records_identified). */
  citation_identified?: number;
  duplicates_removed: number;
  /** Companion reports of the same study folded into one record. */
  companion_reports_merged?: number;
  records_screened: number;
  records_excluded: number;
  records_unsure: number;
  retracted_flagged: number;
  included: number;
  reports_sought_for_retrieval: number;
  reports_not_retrieved: number;
  reports_assessed_for_eligibility: number;
  reports_excluded_fulltext: number;
  studies_included: number;
}

export type RunMode = "search" | "ask";

export interface RunConfig {
  mode?: RunMode;
  language?: "de" | "en";
  /** Model selected when the run was created. */
  model?: string;
  /** Last model selected inside this conversation. */
  chat_model?: string;
  query: string | null;
  live: boolean;
  screen: boolean;
  review_method?: ScreeningMethod["id"];
  /** Maximum final relevance-ranked eligible output; 0 keeps every eligible paper. */
  paper_limit?: number;
  paper_limit_semantics?: "final_eligible_output";
  /** Deprecated alias retained for older runs. */
  screen_limit: number;
  acquire: boolean;
  full_text: boolean;
  year_from: number | null;
  year_to: number | null;
  peer_reviewed_only: boolean;
  web_search: boolean;
  /** Explicit, run-bound confirmation that web-search input is public data only. */
  web_search_public_data_confirmed?: boolean;
  /** Search PubMed as an additional public biomedical literature source. */
  pubmed?: boolean;
  retrieval_limit: number;
  exhaustive: boolean;
  canary_ids: string[];
  gate_protocol: boolean;
  living?: boolean;
  snowball?: boolean;
  snowball_rounds?: number;
  semantic?: boolean;
  import_batch_ids?: number[];
  /** Public id of the run this one refines (documented iteration). */
  parent_run?: string | null;
  quality_warnings?: Array<{
    code: string;
    severity: "info" | "warning";
    title: string;
    detail: string;
  }>;
}

export interface RunCreateRequest {
  question: string;
  mode?: RunMode;
  /** Model menu id (GET /models); omitted = auto. */
  model?: string;
  query?: string | null;
  live?: boolean;
  screen?: boolean;
  review_method?: ScreeningMethod["id"];
  paper_limit?: number;
  screen_limit?: number;
  acquire?: boolean;
  full_text?: boolean;
  year_from?: number | null;
  year_to?: number | null;
  peer_reviewed_only?: boolean;
  web_search?: boolean;
  /** Required and true when web_search is enabled; omitted otherwise. */
  web_search_public_data_confirmed?: boolean;
  /** Search PubMed as an additional public biomedical literature source. */
  pubmed?: boolean;
  retrieval_limit?: number;
  exhaustive?: boolean;
  canary_ids?: string[];
  gate_protocol?: boolean;
  /** Previously uploaded documents to attach to the new run. */
  document_ids?: number[];
  /** Citation snowballing: references of includes + citing works. */
  snowball?: boolean;
  /** Semantic sweep: paraphrased relevance searches of the question. */
  semantic?: boolean;
  /** Uploaded RIS/BibTeX batches joining identification. */
  import_batch_ids?: number[];
  /** The run this one refines; recorded for version diffs. */
  parent_run_id?: string;
}

/** GET /runs/{id}/diff?against= — what a refined search changed. */
export interface RunDiffWork {
  work_id: string;
  title: string;
  year: number | null;
}

export interface RunDiff {
  run: string;
  against: string;
  identified: { run: number; against: number; added: number; removed: number };
  added_includes: RunDiffWork[];
  removed_includes: RunDiffWork[];
  changed_verdicts: (RunDiffWork & { from: string; to: string })[];
  query: { run: string | null; against: string | null };
}

export interface ProbeStep {
  stage: string;
  detail: string;
  outcome: string;
}

export interface ProbeResult {
  status: string;
  resolved: { work_id: string; title: string; doi: string | null; year: number | null } | null;
  steps: ProbeStep[];
  suggestion: string;
}

export interface ImportBatch {
  id: number;
  label: string;
  filename: string;
  count: number;
  created_at?: string;
}

export interface ZoteroCanaries {
  canary_ids: string[];
  resolved: number;
  total_items: number;
  unresolved: { title: string; doi: string }[];
  note: string;
}

export interface QueryTranslations {
  internal: string;
  targets: { pubmed: string; scopus: string; wos: string; ieee: string };
  note: string;
}

export interface EvidenceCell {
  value: string;
  quote: string;
  page: number | null;
  verified: boolean;
  source: "fulltext" | "abstract" | "human";
  confidence?: "high" | "medium" | "low";
  review_status?: "confirmed" | "corrected" | "needs_attention" | "conflict";
  reviews?: Array<{
    round: 1 | 2;
    verdict: "confirmed" | "corrected" | "needs_attention" | "conflict";
    value: string;
    note: string;
    reviewer_user_id: number;
    reviewed_at: string;
  }>;
}

export interface EvidenceRow {
  work_id: string;
  title: string;
  year: number | null;
  document_id: number | null;
  status: "pending" | "done" | "failed";
  payload: Record<string, EvidenceCell>;
}

export interface EvidenceTable {
  status: "none" | "pending" | "done" | "failed";
  is_running: boolean;
  unfinished_count: number;
  failed_count: number;
  fields: string[];
  schema: {
    name: string;
    fields: string[];
    reviewer_mode: "single" | "double" | "blinded";
    instructions: string;
  };
  summary: {
    works: number;
    completed: number;
    reviewed_cells: number;
    total_cells: number;
    conflicts: number;
  };
  rows: EvidenceRow[];
}

export interface ControlRoomStage {
  id: string;
  label: string;
  description: string;
  status: "waiting" | "active" | "paused" | "failed" | "completed";
  progress: number;
  completed_units: number;
  total_units: number;
  duration_seconds: number | null;
  event_count: number;
}

export interface ResearchControlRoom {
  run_id: number;
  status: RunStatus;
  current_stage: string | null;
  overall_progress: number;
  elapsed_seconds: number;
  eta_seconds: number | null;
  eta_scope: "complete" | "stage" | "unknown";
  eta_basis: string;
  eta_open_ended: boolean;
  throughput_per_minute: number;
  stages: ControlRoomStage[];
  decisions: {
    include: number;
    unsure: number;
    exclude: number;
    total: number;
    pending: number;
  };
  records: {
    identified: number;
    snowball_candidates: number;
    full_texts_retrieved: number;
    extractions_completed: number;
  };
  worker: {
    status: string;
    attempt: number;
    max_attempts: number;
    last_error: string;
  };
  activity: RunEvent[];
  active_event: {
    stage: string;
    event: string;
    payload: Record<string, unknown>;
  } | null;
}

export interface EvidenceGraphNode {
  id: string;
  target_type: ClaimEvidence["target_type"];
  target_id: string;
  title: string;
  year: number | null;
  retracted: boolean;
  in_run: boolean;
}

export interface EvidenceGraphEdge extends ClaimEvidence {
  claim_id: number;
  evidence_id: string;
}

export interface EvidenceGraph {
  project_id: number | null;
  project_name?: string;
  claims: Array<
    EvidenceClaim & {
      writer_title: string | null;
      impact: "critical" | "attention" | "stable" | "unsupported";
    }
  >;
  evidence: EvidenceGraphNode[];
  edges: EvidenceGraphEdge[];
  available_evidence: Array<{
    target_type: "work";
    target_id: string;
    title: string;
    year: number | null;
  }>;
  writers: Array<{ id: number; title: string }>;
  summary: {
    claims: number;
    evidence: number;
    unsupported: number;
    unverified: number;
    contradictions: number;
  };
  note: string;
}

export interface LivingResearchWorkspace {
  run_id: number;
  enabled: boolean;
  cadence: "weekly" | "monthly" | "quarterly" | "manual";
  auto_screen: boolean;
  notify: boolean;
  watch_sources: string[];
  last_checked_at: string | null;
  next_check_at: string | null;
  baseline: {
    identified: number;
    included: number;
    claims: number;
    manuscripts: number;
  };
  checks: Array<{
    id: string;
    status: RunStatus;
    created_at: string;
    finished_at: string | null;
    identified: number;
    new_records: number;
    added_includes: Array<{ work_id: string; title: string; year: number | null }>;
    removed_includes: Array<{ work_id: string; title: string; year: number | null }>;
  }>;
  impacts: Array<{
    kind: "claim" | "manuscript";
    id: number;
    title: string;
    reason: string;
    severity: "critical" | "review";
  }>;
  retractions: RetractionDelta;
}

export interface RunCreated {
  id: number;
  public_id: string;
  status: RunStatus;
  gated: boolean;
}

export interface RunSummary {
  id: number;
  public_id: string;
  project_id: number | null;
  question: string;
  title: string | null;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  prisma: PrismaCounts | null;
  /** The seeded example search every workspace starts with. */
  is_demo?: boolean;
}

export interface RunDetail {
  id: number;
  public_id: string;
  project_id: number | null;
  status: RunStatus;
  question: string;
  title: string | null;
  config: Partial<RunConfig>;
  prisma: PrismaCounts | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  last_event: { stage: string; event: string } | null;
}

export interface RunEvent {
  id: number;
  stage: string;
  event: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RankingSignals {
  relevance: number;
  impact: number;
  recency: number;
}

export interface RankedWork {
  rank: number;
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  abstract: string | null;
  cited_by_count: number;
  work_type: string | null;
  oa_status: string | null;
  oa_url: string | null;
  pdf_url: string | null;
  score: number;
  signals: RankingSignals;
  retracted: boolean;
  explanation: string;
  verdict: Verdict | null;
  verdict_by: "human" | "model" | null;
  verdict_reason: string | null;
  evidence_state:
    | "confirmed_include"
    | "provisional_include"
    | "unsure"
    | "exclude"
    | "unscreened";
  selected: boolean;
}

export interface EvidenceCounts {
  confirmed_include: number;
  provisional_include: number;
  unsure: number;
  exclude: number;
  unscreened: number;
  retained: number;
}

export type WorkVerdictFilter = "all" | "include" | "exclude" | "unsure";

export interface WorksPage {
  total: number;
  /** Complete deduplicated identification ledger before the working-set limit. */
  identified_total?: number;
  paper_limit?: number | null;
  selected_total?: number;
  evidence_counts?: EvidenceCounts;
  result_evidence_counts?: EvidenceCounts;
  works: RankedWork[];
  /** The executed boolean search string — reusable in Scopus / WoS / IEEE. */
  search_string?: string | null;
  search_synthesized_by?: string | null;
}

export interface Protocol {
  question: string;
  inclusion_criteria: string[];
  exclusion_criteria: string[];
  year_from: number | null;
  year_to: number | null;
  languages: string[];
  peer_reviewed_only: boolean;
  query_string: string;
  synthesized_by: string;
  version: number;
}

export interface ProtocolResponse {
  run_status: RunStatus;
  protocol: Protocol;
}

export interface QueueItem {
  work_id: string;
  title: string | null;
  stage: "full_text" | "title_abstract";
  model_reason: string;
  evidence: string | null;
  reviewer: string;
}

export interface QueuePage {
  items: QueueItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface HumanDecision {
  work_id: string;
  verdict: Verdict;
  reason?: string;
}

export interface Decision {
  work_id: string;
  verdict: Verdict;
  reason: string;
  reviewer: string;
  by: "human" | "model";
  stage: string;
}

export interface DocumentEntry {
  id: number;
  work_id: string;
  title: string | null;
  status: "retrieved" | "not_retrieved";
  source: string | null;
  legal_basis: string | null;
  license: string | null;
  version: string | null;
  url: string | null;
  content_type: string | null;
  checksum: string | null;
  byte_size: number;
  text_status: string;
  reason: string | null;
  has_file: boolean;
  doi: string | null;
  authors: string[];
  browser_capture: Record<string, unknown>;
}

export interface LibraryMetadataProvenance {
  source: "user" | "user_fill" | "browser_capture" | "work" | "document" | "paper_enrichment";
  updated_at: string | null;
  provider?: string;
  url?: string;
  confidence?: number;
}

export type PaperEnrichmentStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "applied";

export interface PaperEnrichmentSource {
  provider: "openalex" | "crossref";
  url: string;
  matched_identifier: { kind: string; value: string };
}

export interface PaperEnrichmentSuggestion {
  field: keyof LibraryPaperMetadata;
  current: null;
  value: string | string[];
  confidence: number;
  source: PaperEnrichmentSource;
}

export interface PaperEnrichmentJob {
  id: string;
  document_id: number;
  status: PaperEnrichmentStatus;
  identity: { kind: string; value: string };
  source_revision: string;
  attempt_count: number;
  retry_count: number;
  suggestions: PaperEnrichmentSuggestion[];
  sources: PaperEnrichmentSource[];
  applied_fields: string[];
  error: { code: string; message: string } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  applied_at: string | null;
}

export interface LibraryPaperMetadata {
  title: string | null;
  authors: string[];
  abstract: string | null;
  published_at: string | null;
  year: number | null;
  doi: string | null;
  source_url: string | null;
  canonical_url: string | null;
  pdf_url: string | null;
  container_title: string | null;
  volume: string | null;
  issue: string | null;
  pages: string | null;
  publisher: string | null;
  language: string | null;
  license: string | null;
  isbn: string | null;
  issn: string | null;
  arxiv_id: string | null;
  keywords: string[] | null;
  item_type: string | null;
  subtitle: string | null;
  short_title: string | null;
  series_title: string | null;
  series_number: string | null;
  edition: string | null;
  publisher_place: string | null;
  accessed_at: string | null;
  archive: string | null;
  archive_location: string | null;
  citation_key: string | null;
  format: string | null;
  call_number: string | null;
  pmid: string | null;
  pmcid: string | null;
  extra: string | null;
  selected_excerpt: string | null;
}

export interface LibraryMetadataUpdateResult {
  id: number;
  mode: "fill_missing" | "edit";
  changed_fields: string[];
  metadata: LibraryPaperMetadata;
  metadata_revision: string;
  metadata_provenance: Record<string, LibraryMetadataProvenance | null>;
}

/** One stored full text in the workspace library (GET /documents). */
export interface LibraryDocument {
  id: number;
  work_id: string;
  title: string | null;
  year: number | null;
  source: string | null;
  legal_basis: string | null;
  license: string | null;
  content_type: string | null;
  byte_size: number;
  text_status: string;
  has_file: boolean;
  url: string | null;
  doi: string | null;
  authors: string[];
  browser_capture: Record<string, unknown>;
  metadata: LibraryPaperMetadata;
  metadata_revision: string;
  metadata_provenance: Record<string, LibraryMetadataProvenance | null>;
  created_at: string;
  project_id: number | null;
  project_name: string | null;
  folder: string | null;
  run: { public_id: string; label: string } | null;
}

export type LibraryCitationFormat =
  | "apa"
  | "mla"
  | "chicago"
  | "harvard"
  | "bibtex"
  | "ris";

export interface LibraryCitationValue {
  value: string;
}

export interface LibraryDocumentCitations {
  document_id: number;
  title: string;
  metadata_revision: string;
  missing_fields: string[];
  citations: Record<LibraryCitationFormat, LibraryCitationValue>;
}

export type LibraryShareScope = "library" | "project";
export type LibraryShareRole = "viewer";

/** An owner-created, account-bound grant over a dynamic Library scope. */
export interface LibraryShare {
  id: string;
  scope: LibraryShareScope;
  project_id: number | null;
  project_name: string | null;
  role: LibraryShareRole;
  grantee: {
    email: string;
    first_name: string;
  };
  rights_statement_version: string;
  rights_confirmed_at: string;
  created_at: string;
}

/** A Library grant received by the signed-in account. */
export interface ReceivedLibraryShare {
  id: string;
  scope: LibraryShareScope;
  project_id: number | null;
  project_name: string | null;
  role: LibraryShareRole;
  owner: {
    name: string;
    email: string;
  };
  rights_statement_version: string;
  created_at: string;
}

/** Viewer-safe paper shape returned only through a received Library grant. */
export interface SharedLibraryDocument {
  id: number;
  work_id: string;
  title: string | null;
  year: number | null;
  source: string | null;
  legal_basis: string | null;
  license: string | null;
  content_type: string | null;
  byte_size: number;
  text_status: string;
  has_file: boolean;
  url: string | null;
  doi: string | null;
  authors: string[];
  metadata: LibraryPaperMetadata;
  metadata_revision: string;
  created_at: string;
  project_id: number | null;
  project_name: string | null;
  folder: string | null;
  access_role: LibraryShareRole;
  share_id: string;
  library_owner: {
    name: string;
  };
}

/** Viewer-safe saved-source shape returned only through a received grant. */
export interface SharedLibraryWebSource {
  id: string;
  title: string;
  url: string | null;
  canonical_url: string | null;
  site_name: string;
  authors: string[];
  published_at: string | null;
  description: string;
  selected_excerpt: string;
  source_kind: "paper" | "web";
  doi: string;
  project_id: number | null;
  project_name: string | null;
  created_at: string;
  updated_at: string;
  metadata: LibraryPaperMetadata & {
    site_name: string | null;
    source_kind: "paper" | "web";
  };
  access_role: LibraryShareRole;
  share_id: string;
  library_owner: {
    name: string;
  };
}

export interface BrowserCaptureRevision {
  revision: number;
}

/** One explicitly confirmed web capture, separate from stored papers. */
export interface LibraryWebSource {
  id: string;
  title: string;
  url: string;
  canonical_url: string;
  site_name: string;
  authors: string[];
  published_at: string | null;
  description: string;
  selected_excerpt: string;
  source_kind: "paper" | "web";
  doi: string;
  project_id: number | null;
  created_at: string;
  updated_at: string;
  metadata: LibraryPaperMetadata & {
    site_name: string | null;
    source_kind: "paper" | "web";
  };
  metadata_revision: string;
  metadata_provenance: Record<string, LibraryMetadataProvenance | null>;
  provenance: {
    captured_at: string | null;
    extension_version: string | null;
    metadata_fields: string[];
    bibliographic: Record<string, unknown>;
  };
}

export interface BrowserCaptureDevice {
  id: number;
  name: string | null;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface DocumentAnnotation {
  id: number;
  document_id: number;
  page: number;
  quote: string;
  note: string;
  source: "user" | "assistant";
  color: "moss" | "amber" | "rose" | "blue";
  author: string;
  created_at: string;
}

export interface DocumentTranslationStatus {
  status: "not_started" | "queued" | "running" | "rendering" | "completed" | "failed";
  language: string | null;
  page_count: number;
  completed_pages: number;
  percent: number;
  ready: boolean;
  pages_without_text: number[];
  error: string | null;
  updated_at: string | null;
}

/** Response of a PDF upload (file bytes or public link). */
export interface UploadedDocument {
  id: number;
  run_id: number | null;
  work_id: string;
  title: string;
  verified: boolean;
  text_status: string;
  byte_size: number;
}

/** One LLM-anchored passage in the paper reader (server-verified quote). */
export interface PaperHighlight {
  page: number;
  quote: string;
  note: string;
}

/** A passage the user marked in the reader to discuss with the assistant. */
export interface ChatSelection {
  document_id: number;
  page: number;
  quote: string;
  /** Display-only fields the server echoes back on the message payload. */
  work_id?: string;
  title?: string;
}

export interface WebSource {
  title: string;
  url: string;
  domain: string;
  quality: number;
  category: string;
  snippet: string;
}

export interface Citation {
  id: string;
  title: string;
}

export interface ClaimCheck {
  claim: string;
  support: "supported" | "unsupported" | "neutral";
  evidence_ids: string[];
}

export interface ChatAnswer {
  answer: string;
  reasoning?: string | null;
  citations: Citation[];
  sources_considered: number;
  tools_used?: string[];
  evidence?: EvidenceRef[];
  claims_checked: number;
  claims_supported: number;
  claims_flagged: number;
  claim_checks: ClaimCheck[];
}

export interface ChatStreamEvent {
  id: number;
  event:
    | "turn.started"
    | "plan.created"
    | "plan.updated"
    | "agent.update"
    | "checkpoint.started"
    | "checkpoint.progress"
    | "checkpoint.completed"
    | "checkpoint.failed"
    | "activity"
    | "tool.started"
    | "tool.completed"
    | "tool.failed"
    | "answer.started"
    | "answer.reset"
    | "reasoning.delta"
    | "answer.delta"
    | "answer.completed"
    | "turn.completed"
    | "turn.cancelled"
    | "turn.failed";
  turn_id?: string;
  created_at?: string;
  status?: ChatTurnStatus;
  label?: string;
  phase?:
    | "planning"
    | "retrieval"
    | "reading"
    | "library"
    | "writing"
    | "verification";
  delta?: string;
  reasoning?: string;
  tool?: ToolStepPayload["tool"];
  query?: string;
  reason?: string;
  iteration?: number;
  message_id?: number;
  result_count?: number;
  domains?: string[];
  sources?: number;
  claims_checked?: number;
  claims_flagged?: number;
  answer?: ChatAnswer;
  message?: string;
}

export type ChatTurnStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed";

/** Safe reconnect metadata; prompts, selections and request hashes stay server-only. */
export interface ChatTurnState {
  turn_id: string;
  status: ChatTurnStatus;
  last_event_id: number;
  answer: ChatAnswer | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type SpecialistAgentEventType =
  | "turn.started"
  | "context.loaded"
  | "plan.created"
  | "plan.updated"
  | "tool.started"
  | "tool.progress"
  | "tool.completed"
  | "tool.failed"
  | "checkpoint.started"
  | "checkpoint.progress"
  | "checkpoint.completed"
  | "checkpoint.failed"
  | "agent.update"
  | "change.proposed"
  | "change.completed"
  | "change.rejected"
  | "answer.completed"
  | "context.compacted"
  | "turn.completed"
  | "turn.cancelled"
  | "turn.failed";

export type AgentTurnStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed";

export type SpecialistResourceKind =
  | "manuscript"
  | "dataset"
  | "interview"
  | "interview-study"
  | "survey";

export interface AgentTurnState {
  turn_id: string;
  resource_kind: string;
  resource_id: string;
  status: AgentTurnStatus;
  last_event_id: number;
  result: unknown | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentTurnStopResult {
  turn_id: string;
  status: AgentTurnStatus;
}

/**
 * Safe, user-facing lifecycle event emitted by a specialist agent.
 *
 * Details describe observable work and validated results. They never contain
 * private chain-of-thought or provider-internal prompts.
 */
export interface SpecialistAgentEvent {
  id: number;
  event: SpecialistAgentEventType;
  turn_id?: string;
  status?: AgentTurnStatus;
  kind?: string;
  created_at?: string;
  tool?: string;
  call_id?: string;
  lifecycle?: "started" | "progress" | "completed" | "failed";
  iteration?: number;
  effect?: "read" | "staged" | "write" | "external" | string;
  label?: string;
  detail?: string;
  steps?: string[];
  input?: unknown;
  output?: unknown;
  before?: unknown;
  after?: unknown;
  operation?: string;
  applied?: boolean;
  index?: number;
  total?: number;
  result_count?: number;
  message?: string;
  error_type?: string;
  result?: unknown;
}

/** A durable user-facing output created by a specialist turn. */
export interface SpecialistArtifact {
  id: string;
  title: string;
  kind?: "pdf" | "table" | "visual" | "dataset" | "manuscript" | "file" | string;
  /** App-relative route or an absolute HTTPS URL that opens the output. */
  href: string;
  filename?: string;
  mime_type?: string;
  byte_size?: number;
}

/** An embedded MCP-UI resource rendered in a sandboxed iframe. */
export interface UiResource {
  uri: string;
  mimeType: string;
  text: string;
}

export type WorkspaceActionType =
  | "create_visual"
  | "create_survey"
  | "create_ai_interview"
  | "create_manuscript"
  | "start_review"
  | "create_project"
  | "open_data_hub"
  | "open_library"
  | "upload_interview"
  | "set_theme"
  | "set_language"
  | "update_assistant_preferences"
  | "open_settings"
  | "connect_reference_manager"
  | "manage_resource";

export type WorkspaceResourceType =
  | "project"
  | "review"
  | "manuscript"
  | "survey"
  | "dataset"
  | "interview"
  | "interview_study"
  | "visual"
  | "library_paper";

export type WorkspaceResourceOperation =
  | "open"
  | "rename"
  | "move"
  | "attach_to_manuscript"
  | "update_status"
  | "delete";

export interface WorkspaceAction {
  id: string;
  type: WorkspaceActionType;
  title: string;
  requires_confirmation: true;
  status: "proposed";
  context: {
    project_id: number | null;
    source_type: string;
    source_id: string;
    source_title: string;
    source_numeric_id: number | null;
  };
  description?: string;
  prompt?: string;
  kind?: "method" | "architecture" | "flow" | "concept" | "plot";
  aspect_ratio?: "1:1" | "4:3" | "3:2" | "16:9" | "2:3";
  resolution?: "1k" | "2k" | "4k";
  review_passes?: 0 | 1 | 2;
  grounding_mode?: "conceptual" | "quantitative" | "missing_quantitative_data";
  grounding_note?: string;
  questions?: Omit<SurveyQuestion, "id">[];
  language?: "de" | "en";
  research_goal?: string;
  sections?: VoiceGuideSection[];
  objective?: string;
  template?: string;
  question?: string;
  query?: string;
  instructions?: string;
  theme?: "light" | "dark" | "system";
  preferences?: Partial<AssistantPreferences>;
  section?:
    | "account"
    | "assistant"
    | "api-keys"
    | "integrations"
    | "team"
    | "webhooks"
    | "legal";
  provider?: "zotero" | "citavi";
  operation?: WorkspaceResourceOperation;
  resource_type?: WorkspaceResourceType;
  selector?: string;
  new_name?: string;
  destination?: string;
  resource_status?:
    | "active"
    | "paused"
    | "complete"
    | "archived"
    | "draft"
    | "live"
    | "closed"
    | "";
}

/** One agentic tool step (persisted as a role="tool" chat message payload). */
export interface ToolStepPayload {
  tool:
    | "agent_update"
    | "web_search"
    | "find_papers"
    | "show_chart"
    | "clarify"
    | "read_paper"
    | "suggest_followups"
    | "cite"
    | "show_paper"
    | "save_paper"
    | "add_document"
    | "read_webpage"
    | "citation_graph"
    | "author_lookup"
    | "search_in_document"
    | "search_library"
    | "recall_history"
    | "export_works"
    | "compare_papers"
    | "start_search"
    | "extract_data"
    | "edit_table"
    | "edit_pdf_comment"
    | "translate_passage"
    | "verify_claim"
    | "workspace_action"
    | "make_table";
  query: string;
  /** Original tool represented by an immutable lifecycle-only start row. */
  target_tool?: ToolStepPayload["tool"];
  /** Correlates an immutable completion row with its earlier start row. */
  started_message_id?: number;
  reason?: string;
  status?: "running" | "completed" | "failed";
  iteration?: number | null;
  results: Array<{
    title?: string;
    url?: string;
    domain?: string;
    /** Server-generated citation key bound to this exact result URL. */
    citation_key?: string;
    snippet?: string;
    category?: string;
    id?: string;
    doi?: string | null;
    year?: number | null;
    venue?: string | null;
    cited_by_count?: number;
    question?: string;
    options?: string[];
    chart?: string;
    error?: string;
    publisher_url?: string | null;
    highlights?: PaperHighlight[];
    page_count?: number;
    verified?: boolean;
    /** search_in_document hits + recall_history entries. */
    page?: number;
    document_id?: number;
    work_id?: string;
    public_id?: string;
    status?: string;
    mode?: string;
    created_at?: string;
    included?: number | null;
    /** search_proposal card. */
    query?: string;
    /** export_works card. */
    format?: string;
    count?: number;
    included_only?: boolean;
    /** read_webpage. */
    excerpt?: string;
    description?: string;
    content_type?: string;
    characters_read?: number;
    word_count?: number;
    headings?: string[];
    quality?: number;
    /** extract_data table. */
    columns?: string[];
    rows?: string[][];
    /** Persisted workspace mutations. */
    operation?: string;
    revision?: number;
    row_count?: number;
    column_count?: number;
    changes?: string[];
    message_id?: number;
    annotation_id?: number;
    note?: string;
    color?: "moss" | "amber" | "rose" | "blue";
    /** translate_passage card. */
    target_language?: string;
    original?: string;
    translation?: string;
    /** verify_claim evidence audit. */
    claim?: string;
    verdict?: "supported" | "contradicted" | "mixed" | "insufficient";
    confidence?: "high" | "medium" | "low";
    rationale?: string;
    evidence?: Array<{
      work_id: string;
      title: string;
      year?: number | null;
      venue?: string | null;
      stance: "supports" | "contradicts" | "context";
      reason: string;
    }>;
    /** Safe operational progress, never hidden chain of thought. */
    stage?: "plan" | "checkpoint" | "synthesis" | "complete";
    items?: string[];
    completion_reason?: string;
  }>;
  /** "ui" = iframe resource; "paper" = reader; the rest are native cards. */
  kind?:
    | "agent_work"
    | "ui"
    | "paper"
    | "export"
    | "search_proposal"
    | "runs"
    | "table"
    | "table_mutation"
    | "annotation_mutation"
    | "translation"
    | "claim_verification"
    | "workspace_action"
    | "library_save";
  /** search_proposal: where the confirmed run is filed. */
  project_id?: number | null;
  size?: "wide" | "regular" | "inline";
  resource?: UiResource;
  /** Structured data behind a table resource. Kept beside the embedded
   * rendering so the host can open large tables in the research workspace. */
  table?: {
    title?: string;
    columns: string[];
    rows: string[][];
  };
  table_original?: {
    title?: string;
    columns: string[];
    rows: string[][];
  };
  table_revision?: number;
  table_updated_at?: string;
  table_kind?: string;
  /** show_paper panel data (kind === "paper"). */
  document_id?: number;
  work_id?: string;
  title?: string;
  page_count?: number;
  highlights?: PaperHighlight[];
  legal_basis?: string | null;
  license?: string | null;
  verified?: boolean;
  workspace_actions?: WorkspaceAction[];
}

/** A verified passage reference into an attached paper: click-to-highlight. */
export interface EvidenceRef {
  work_id: string;
  document_id: number;
  page: number;
  quote: string;
}

export interface ChatMessage {
  id?: number;
  role: "user" | "assistant" | "tool";
  content: string;
  citations: string[];
  payload?: Record<string, unknown> | null;
  created_at: string;
}

export interface ReportSections {
  executive_summary: string;
  key_findings: string[];
  themes: Array<{ title: string; body: string }>;
  limitations: string;
  next_steps: string[];
}

export interface ReportWork {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  cited_by_count: number;
  reason: string | null;
}

export interface RunReport {
  /** Public generation fact; never the private provider or model route. */
  ai_generated_sections?: boolean;
  question: string;
  title: string;
  query_string: string;
  criteria: { inclusion: string[]; exclusion: string[] };
  prisma: Partial<PrismaCounts>;
  sections: ReportSections;
  included: ReportWork[];
  unsure_count: number;
  web_sources: Array<{ title: string; url: string; domain: string }>;
  methods: string;
  run: {
    id: number;
    created_at: string;
    finished_at: string | null;
    corpus_version: string | null;
  };
}

export interface ReportResponse {
  id: number;
  created_at: string;
  report: RunReport;
}

export interface Health {
  status: string;
}

export interface CoverageReport {
  method: string;
  observed: number;
  estimated_total: number;
  completeness: number;
  ci_low: number;
  ci_high: number;
  occasions: number;
  singletons: number;
  doubletons: number;
  note: string;
}

export interface CalibrationReport {
  seed_size: number;
  evaluated: number;
  human_includes: number;
  recall: number;
  agreement: number;
  missed: string[];
  verdict: string;
  note: string;
}

export interface RetractionDelta {
  checked: number;
  retracted_now: string[];
  newly_retracted: string[];
}

export type ExportFormat = "bibtex" | "ris" | "csl";

export interface ZoteroSyncRequest {
  api_key: string;
  library_type: "user" | "group";
  library_id: string;
  included_only: boolean;
}

export interface ZoteroSyncResult {
  created: number;
  failed: number;
  library: string;
}

export interface ReferenceConnector {
  id: string;
  provider: "zotero" | "citavi";
  name: string;
  status: "connected" | "error";
  library_type: string;
  library_id: string;
  collection_key: string;
  library_version: number;
  item_count: number;
  last_error: string;
  last_synced_at: string | null;
  collections: Array<{ key: string; name: string; parent: string; version: number }>;
  import_batch_id: number | null;
  created_at: string;
  sync?: {
    created: number;
    updated: number;
    deleted: number;
    item_count: number;
  };
}

export interface ReferenceConnectorPushResult {
  provider: "zotero" | "citavi";
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  library?: string;
  export_url?: string;
}

export type WriterCompileStatus = "none" | "running" | "ok" | "error";
export type WriterAccessRole = "owner" | "editor" | "reviewer" | "viewer";
export type WriterCollaboratorRole = WriterAccessRole | "none";

export interface WriterSummary {
  id: number;
  /** Opaque URL address; access stays session + org gated. */
  public_id: string;
  project_id: number | null;
  title: string;
  run_ids: number[];
  dataset_ids: string[];
  compile_status: WriterCompileStatus;
  revision: number;
  access_role: WriterAccessRole;
  created_by: number | null;
  updated_by: number | null;
  derived_from_document_id: number | null;
  target_template: string;
  retarget_report: WriterRetargetReport | null;
  /** The seeded example draft every workspace starts with. */
  is_demo?: boolean;
  created_at: string;
  updated_at: string;
}

export interface WriterDocument extends WriterSummary {
  content: string;
  compile_log: string;
  compile_errors: { path?: string | null; line: number | null; message: string }[];
}

export interface WriterProjectFile {
  id: number;
  path: string;
  content: string;
  main: boolean;
  revision: number;
  updated_by: number | null;
}

export interface WriterCollaborator {
  user_id: number;
  email: string;
  name: string;
  role: WriterCollaboratorRole;
  is_active: boolean;
  fixed: boolean;
}

export interface WriterCollaboratorCatalog {
  my_role: WriterAccessRole;
  can_manage: boolean;
  members: WriterCollaborator[];
}

export interface WriterPresence {
  user_id: number;
  email: string;
  name: string;
  path: string;
  line: number;
  mode: "source" | "preview" | "chat" | "log";
  role: WriterAccessRole;
  self: boolean;
  last_seen_at: string;
}

export interface WriterRetargetReport {
  mapped: string[];
  warnings: string[];
  section_count: number;
  citation_count: number;
  extra_files: number;
  assets: number;
  owned_sources: number;
  collaborators: number;
  original_preserved: boolean;
}

export interface WriterRetargetPreview {
  source: { id: string; title: string; revision: number };
  target_template: string;
  suggested_title: string;
  content: string;
  report: WriterRetargetReport;
}

export interface WriterCitation {
  key: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  owned?: boolean;
}

export interface WriterSource {
  id: number;
  source_document_id: number | null;
  filename: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string;
  cite_key: string;
  readable: boolean;
  byte_size: number;
  created_at: string;
}

export type WriterInterviewContextMode = "analysis" | "transcript";

export interface WriterInterviewContextItem {
  interview_id: string;
  project_id: number | null;
  title: string;
  kind: "upload" | "live";
  status: "pending" | "ready" | "error";
  duration_ms: number;
  segment_count: number;
  analysis_ready: boolean;
  methodology_ready: boolean;
  mode: WriterInterviewContextMode | null;
  include_methodology: boolean;
  linked: boolean;
  project_match: boolean;
  updated_at: string;
}

export interface WriterInterviewContextCatalog {
  items: WriterInterviewContextItem[];
  linked_count: number;
  analysis_count: number;
  transcript_count: number;
  methodology_count: number;
}

export type WriterSurveyContextMode = "summary" | "responses";

export interface WriterSurveyContextItem {
  survey_id: string;
  project_id: number | null;
  title: string;
  description: string;
  status: "draft" | "live" | "closed";
  question_count: number;
  response_count: number;
  mode: WriterSurveyContextMode | null;
  linked: boolean;
  project_match: boolean;
  updated_at: string;
}

export interface WriterSurveyContextCatalog {
  items: WriterSurveyContextItem[];
  linked_count: number;
  summary_count: number;
  responses_count: number;
}

export interface WriterShareInfo {
  shared: boolean;
  token: string | null;
  url: string | null;
  password_protected: boolean;
  comments: number;
  open_comments: number;
}

export interface WriterComment {
  id: number;
  author_label: string;
  author_key: string;
  color_index: number;
  quote: string;
  anchor_prefix: string;
  anchor_suffix: string;
  anchor_revision: string;
  page: number | null;
  content: string;
  status: "open" | "resolved";
  created_at: string;
  resolved_at: string | null;
}

export interface PublicWriterShare {
  title: string;
  password_protected: boolean;
  updated_at: string;
}

export interface RunShareInfo {
  shared: boolean;
  token: string | null;
  url: string | null;
}

export interface PublicSharedRun {
  title: string;
  question: string;
  created_at: string;
  query_string: string;
  inclusion_criteria: string[];
  exclusion_criteria: string[];
  prisma: Record<string, number>;
  methods: string;
  works: {
    rank: number;
    id: string;
    title: string;
    authors: string[];
    year: number | null;
    venue: string;
    doi: string | null;
    oa_url: string | null;
    cited_by_count: number;
    score: number;
    retracted: boolean;
    verdict: "include" | "exclude" | "unsure" | null;
    verdict_reason: string | null;
  }[];
}

export interface DatasetChatAction {
  operation: "run_analysis" | "create_chart" | "create_meta_chart" | "set_profile" | "render_visual" | string;
  applied: boolean;
  label: string;
  detail?: string;
  kind?: string;
  definition?: Record<string, unknown>;
  result?: Record<string, unknown>;
  analysis_id?: string;
  figure_id?: string;
  prompt?: string;
  x_column?: string;
  y_column?: string;
}

export interface DatasetMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: {
    actions?: DatasetChatAction[];
    agent_events?: SpecialistAgentEvent[];
    artifacts?: SpecialistArtifact[];
    workspace_actions?: WorkspaceAction[];
    dataset_updated?: boolean;
    [key: string]: unknown;
  };
  created_at: string;
}

export interface DatasetChatReply {
  message_id: number;
  answer: string;
  actions: DatasetChatAction[];
  agent_events?: SpecialistAgentEvent[];
  artifacts?: SpecialistArtifact[];
  workspace_actions?: WorkspaceAction[];
  dataset: ResearchDataset;
}

export interface DatasetVersion {
  id: number;
  version: number;
  filename: string;
  checksum: string;
  byte_size: number;
  row_count: number;
  note: string;
  created_by: number;
  created_at: string;
}

export interface ResearchDataset {
  id: number;
  public_id: string;
  project_id: number | null;
  name: string;
  filename: string;
  description: string;
  provenance: string;
  license: string;
  format: "csv" | "tsv" | "json" | "xlsx" | "";
  row_count: number;
  column_count: number;
  byte_size: number;
  columns: {
    name: string;
    type: "number" | "text";
    missing: number;
    unique: number;
    stats?: { min: number; max: number; mean: number; median: number };
    top?: { value: string; count: number }[];
  }[];
  preview: Record<string, unknown>[];
  profiled_rows?: number;
  created_at: string;
  updated_at: string;
}

export interface InterviewSegment {
  idx: number;
  speaker: string;
  start_ms: number;
  end_ms: number;
  text: string;
  edited: boolean;
}

export interface InterviewQuote {
  segment: number;
  text: string;
  verified: boolean;
  start_ms?: number;
  timestamp?: string;
  speaker?: string;
}

export interface InterviewTheme {
  name: string;
  description: string;
  quotes: InterviewQuote[];
}

export interface InterviewAnalysis {
  summary?: string;
  themes?: InterviewTheme[];
  key_findings?: string[];
  tensions?: string[];
  hypotheses?: string[];
  followups?: string[];
  quotes_total?: number;
  quotes_verified?: number;
  language?: string;
}

export interface Interview {
  id: string;
  project_id: number | null;
  title: string;
  kind: "upload" | "live";
  status: "pending" | "ready" | "error";
  error: string;
  language: "auto" | "en" | "de";
  guide: string;
  duration_ms: number;
  byte_size: number;
  audio_available: boolean;
  /** Client-reported transcripts are not independently provider-verified. */
  source_integrity?: "client_reported_unverified" | "provider_transcript" | null;
  speakers: Record<string, string>;
  analysis: InterviewAnalysis;
  pipeline: Array<{
    id: string;
    label: string;
    status: "pending" | "running" | "completed" | "failed" | "skipped";
    detail?: string;
  }>;
  active_stage: string;
  analyzing: boolean;
  segment_count: number;
  segments?: InterviewSegment[];
  created_at: string;
  updated_at: string;
}

export interface VoiceGuideSection {
  title: string;
  question: string;
  probes: string[];
  must_cover: boolean;
}

export interface VoiceSession {
  id: string;
  kind: string;
  status: "running" | "completed" | "aborted" | "error";
  participant_label: string;
  guide_version: number;
  duration_ms: number;
  interview_id: string | null;
  started_at: string;
  ended_at: string | null;
}

export interface VoiceInvite {
  id: string;
  label: string;
  passcode: string;
  passcode_required: boolean;
  max_sessions: number;
  used_sessions: number;
  active: boolean;
  expires_at: string | null;
  created_at: string;
}

export interface ParticipantInformation {
  language?: "" | "de" | "en";
  controller_name?: string;
  controller_address?: string;
  contact_email?: string;
  data_protection_contact?: string;
  purpose?: string;
  data_categories?: string;
  legal_basis?: "" | "consent" | "public_task" | "legitimate_interests";
  legal_basis_details?: string;
  retention_period?: string;
  additional_recipients?: string;
  additional_transfers?: string;
  supervisory_authority?: string;
  privacy_notice_url?: string;
  dpia_status?: "" | "completed";
  dpia_reference?: string;
  ai_interview_scope_attested?: boolean;
  spoken_processing_approved?: boolean;
  dpia_scope_fingerprint?: string;
  dpia_public_scope_token?: string;
  researcher_reviewed?: boolean;
}

export interface ParticipantNotice {
  version: string;
  language: "de" | "en";
  mode: "survey" | "text" | "live";
  core: string[];
  sections: Array<{ title: string; body: string }>;
  declaration: string;
  audio_declaration: string;
  requires_consent: boolean;
  privacy_notice_url: string;
  platform_privacy_url: string;
}

export interface VoiceStudy {
  id: string;
  project_id: number | null;
  title: string;
  language: "de" | "en";
  voice: string;
  tone: "warm" | "neutral" | "formal";
  mode: "guided" | "iterative";
  patience_ms: number;
  max_session_minutes: number;
  retention: "keep" | "transcript_only";
  consent_text: string;
  contact_line: string;
  participant_information?: ParticipantInformation;
  participant_information_ready?: boolean;
  participant_information_gaps?: string[];
  spoken_processing_ready?: boolean;
  budget_minutes: number;
  guide: { sections: VoiceGuideSection[] };
  guide_version: number;
  session_count: number;
  sessions?: VoiceSession[];
  invites?: VoiceInvite[];
  created_at: string;
  updated_at: string;
}

export interface VoiceStudyAction {
  operation: string;
  applied: boolean;
  label: string;
  detail?: string;
}

export interface VoiceStudyMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: {
    actions?: VoiceStudyAction[];
    agent_events?: SpecialistAgentEvent[];
    artifacts?: SpecialistArtifact[];
    proposals?: { operation: string; label: string; detail?: string }[];
    resolved?: boolean;
    workspace_actions?: WorkspaceAction[];
    [key: string]: unknown;
  };
  created_at: string;
}

export interface VoiceConfig {
  configured: boolean;
  /** Public invite links only expose spoken mode when the server-side launch guard allows it. */
  public_spoken_available: boolean;
  voices: string[];
  tones: string[];
  /** API-authoritative minimum for one useful AI-led interview. */
  min_live_session_minutes: number;
  /** API-authoritative current upper bound for one live AI-led interview. */
  max_live_session_minutes: number;
}

export interface PublicTalkInfo {
  state:
    | "open"
    | "inactive"
    | "expired"
    | "full"
    | "unconfigured"
    | "unavailable";
  title: string;
  language: "de" | "en";
  expected_minutes: number;
  minimum_age: 18;
  retention: "keep" | "transcript_only";
  consent_text: string;
  consent_fingerprint: string;
  contact_line: string;
  /** Human-readable provider disclosure supplied by the connected API. */
  live_provider: string;
  provider_disclosure_version: string;
  privacy_notice_url: string;
  terms_url: string;
  passcode_required: boolean;
  /** Whether the provider-backed spoken mode can currently start. */
  live_available: boolean;
  /** Modes covered by the exact consent fingerprint shown on this response. */
  available_modes: Array<"text" | "live">;
  age_declaration: string;
  participant_notices: Partial<Record<"text" | "live", ParticipantNotice>>;
}

interface VoiceSessionBaseConfig {
  id: string;
  language: "de" | "en";
  max_session_minutes: number;
  retention: "keep" | "transcript_only";
}

export interface LiveVoiceSessionConfig extends VoiceSessionBaseConfig {
  mode: "live";
  transport: "relay";
  /** One-use relay ticket, sent only in the first WebSocket frame. */
  token: string;
  ws_url: string;
  /** Legacy response metadata; the browser never configures the provider. */
  model?: string;
}

export interface TextVoiceSessionConfig extends VoiceSessionBaseConfig {
  mode: "text";
}

export type VoiceSessionConfig = LiveVoiceSessionConfig | TextVoiceSessionConfig;

export interface VoiceTurn {
  role: "interviewer" | "participant";
  text: string;
  start_ms: number;
  end_ms: number;
}

export interface InterviewMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: {
    quotes?: InterviewQuote[];
    agent_events?: SpecialistAgentEvent[];
    artifacts?: SpecialistArtifact[];
    workspace_actions?: WorkspaceAction[];
    [key: string]: unknown;
  };
  created_at: string;
}

export type SurveyQuestionType =
  | "short_text"
  | "long_text"
  | "single_choice"
  | "multiple_choice"
  | "rating"
  | "scale";

export interface SurveyQuestion {
  id: string;
  title: string;
  description: string;
  type: SurveyQuestionType;
  required: boolean;
  options: string[];
  min: number | null;
  max: number | null;
}

export interface SurveyResponse {
  id: string;
  answers: Record<string, unknown>;
  respondent_label: string;
  submitted_at: string;
}

export interface SurveyQuestionSummary {
  id: string;
  title: string;
  type: SurveyQuestionType;
  answered: number;
  missing: number;
  counts?: Array<{ option: string; count: number; percent: number }>;
  mean?: number | null;
  min?: number | null;
  max?: number | null;
  distribution?: Array<{ value: number; count: number }>;
  responses?: string[];
}

export interface Survey {
  id: number;
  public_id: string;
  project_id: number | null;
  title: string;
  description: string;
  status: "draft" | "live" | "closed";
  questions: SurveyQuestion[];
  settings: {
    collect_identity?: boolean;
    confirmation?: string;
    password_protected?: boolean;
    result_dataset_id?: string | null;
  };
  response_count: number;
  participant_information?: ParticipantInformation;
  participant_information_ready?: boolean;
  participant_information_gaps?: string[];
  summary: {
    responses: number;
    complete: number;
    completion_percent: number;
    questions: SurveyQuestionSummary[];
  };
  responses?: SurveyResponse[];
  created_at: string;
  updated_at: string;
}

export interface PublicSurvey {
  public_id: string;
  title: string;
  description: string;
  password_protected: boolean;
  questions: SurveyQuestion[];
  settings: Survey["settings"];
  participant_notice?: ParticipantNotice;
  participant_information_fingerprint?: string;
}

export interface SurveyMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: {
    response_count?: number;
    survey_updated?: boolean;
    actions?: SurveyAgentAction[];
    agent_events?: SpecialistAgentEvent[];
    artifacts?: SpecialistArtifact[];
    proposal_status?: "pending" | "applied";
    proposed_actions?: Record<string, unknown>[];
    workspace_actions?: WorkspaceAction[];
    [key: string]: unknown;
  };
  created_at: string;
}

export interface SurveyAgentAction {
  operation: string;
  applied: boolean;
  label: string;
  question_id: string;
  detail: string;
}

export interface SurveyAgentReply {
  message_id: number;
  answer: string;
  response_count: number;
  actions: SurveyAgentAction[];
  workspace_actions?: WorkspaceAction[];
  agent_events?: SpecialistAgentEvent[];
  artifacts?: SpecialistArtifact[];
  survey: Survey;
}

export interface SurveyProposalApplyResult {
  survey: Survey;
  actions: SurveyAgentAction[];
  survey_updated: boolean;
}

export interface ReviewHealth {
  score: number;
  ready: number;
  total: number;
  note: string;
  checks: {
    id: string;
    label: string;
    status: "ready" | "warning" | "action";
    detail: string;
    action: string;
  }[];
  next_actions: ReviewHealth["checks"];
}

export interface WriterCompile {
  ok: boolean;
  errors: { path?: string | null; line: number | null; message: string }[];
  log_tail: string;
}

export interface WriterSnapshot {
  id: number;
  note: string;
  chars: number;
  files: number;
  summary: string;
  changes: {
    path: string;
    state: "added" | "modified" | "removed";
    added: number;
    removed: number;
  }[];
  created_at: string;
}

export interface WriterEdit {
  path: string;
  find: string;
  replace: string;
  applicable: boolean;
  occurrences: number;
  integrity_errors?: string[];
}

export interface WriterVerification {
  status: "passed" | "failed";
  errors: { path?: string | null; line: number | null; message: string }[];
  log_tail: string;
}

/** Immutable repository grounding carried with a manually reviewed Writer proposal. */
export interface WriterRepositoryProseClaim {
  text: string;
  support: "topology" | "coverage";
  node_ids?: string[];
  edge_ids?: string[];
  evidence_ids?: string[];
}

export interface WriterRepositoryProseProvenance {
  analysis_id?: string;
  repository_access?: "public" | "private";
  repository_url?: string;
  subpath?: string | null;
  commit_sha?: string;
  grounding_sha256?: string;
  preview_request_id?: string;
  preview_sha256?: string;
  compile_input_sha256?: string;
  kind: Exclude<RepositoryManuscriptKind, "caption">;
  language: "en" | "de";
  claims: WriterRepositoryProseClaim[];
  scope_note: string;
}

export interface WriterVisualRequest {
  prompt: string;
  kind: "method" | "architecture" | "flow" | "concept" | "plot";
  resolution: "1k" | "2k" | "4k";
  aspect_ratio: "1:1" | "4:3" | "3:2" | "16:9" | "2:3";
  review_passes: 0 | 1 | 2;
}

export interface WriterMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: {
    edits?: WriterEdit[];
    /** Durable author decisions keyed by the zero-based edit index. */
    edit_decisions?: Record<string, "applied" | "rejected" | "superseded">;
    agent_events?: SpecialistAgentEvent[];
    artifacts?: SpecialistArtifact[];
    verification?: WriterVerification | null;
    visual_request?: WriterVisualRequest | null;
    workspace_actions?: WorkspaceAction[];
    /** Repository prose is always a proposal and never an automatic manuscript write. */
    source_kind?: "repository_prose";
    requires_manual_review?: true;
    expected_writer_revision?: number;
    repository_prose?: WriterRepositoryProseProvenance;
    interview_context?: Array<{
      interview_id: string;
      title: string;
      mode: WriterInterviewContextMode;
      include_methodology: boolean;
      passages: Array<{
        segment: number;
        speaker: string;
        start_ms: number;
        end_ms: number;
      }>;
    }>;
    survey_context?: Array<{
      survey_id: string;
      title: string;
      mode: WriterSurveyContextMode;
      question_count: number;
      response_count: number;
      retrieved_response_ids: string[];
    }>;
    selection?: {
      kind?: "pdf" | "source";
      page?: number | null;
      page_end?: number | null;
      line?: number | null;
      line_end?: number | null;
      path?: string | null;
      pdf_x?: number | null;
      pdf_y?: number | null;
      segments?: Array<{
        quote: string;
        page: number;
        page_end?: number | null;
      }>;
      quote?: string;
    } | null;
  };
  created_at: string;
}

export interface WriterAuditFinding {
  id: string;
  category: "claims" | "citations" | "consistency" | "research";
  severity: "error" | "warning" | "info";
  title: string;
  detail: string;
  path: string;
  line: number;
  snippet: string;
  suggestion_keys: string[];
}

export interface WriterAudit {
  score: number;
  files_checked: number;
  citation_keys: number;
  findings: WriterAuditFinding[];
  counts: Partial<Record<WriterAuditFinding["category"], number>>;
  severity_counts: Partial<Record<WriterAuditFinding["severity"], number>>;
  unused_citation_keys: string[];
}

export interface WriterResearchObject {
  id: number;
  public_id: string;
  name: string;
  kind: string;
  dataset_id: number;
  dataset_version: number;
  latest_dataset_version: number;
  result: Record<string, unknown>;
  status: string;
  latex: string;
}

export interface WriterAsset {
  id: number;
  filename: string;
  byte_size: number;
  /** True when this is a PDF whose text the editing chat can read. */
  readable?: boolean;
  created_at?: string;
}

export type RepositoryAnalysisStatus =
  | "queued"
  | "fetching"
  | "analyzing"
  | "needs_scope"
  | "ready"
  | "error"
  | "cancelled";

export type RepositoryDiagramKind =
  | "architecture"
  | "flow"
  | "deployment"
  | "module";

/** Secret-free metadata for one repository-scoped GitHub connection. */
export interface RepositoryConnection {
  id: string;
  provider: "github";
  repository_url: string;
  owner: string;
  name: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

/** Write-only credential request. The API never returns `access_token`. */
export interface GithubRepositoryConnectionCreate {
  repository_url: string;
  access_token: string;
  /** Separate, per-connection confirmation. Never persist this as a default. */
  credential_storage_confirmed: true;
}

export interface RepositoryAnalysisCreate {
  request_id: string;
  repository_url: string;
  repository_connection_id?: string;
  ref?: string;
  subpath?: string;
  goal: string;
  diagram_kind: RepositoryDiagramKind;
  language: "en" | "de";
  project_id?: number;
  /** Explicit, per-request confirmation. Never persist this as a user default. */
  rights_confirmed: true;
}

export interface RepositoryAnalysisCoverage {
  archive_entries?: number;
  files_in_scope?: number;
  eligible_files?: number;
  analyzed_files?: number;
  excluded_files?: number;
  eligible_bytes?: number;
  analyzed_bytes?: number;
  excluded_by_reason?: Record<string, number>;
  complete?: boolean;
  [key: string]: unknown;
}

export interface RepositoryDiagramNode {
  id: string;
  label: string;
  kind?: string;
  description?: string;
  group_id?: string;
  group?: string;
  evidence_ids?: string[];
  confidence?: number;
  [key: string]: unknown;
}

export interface RepositoryDiagramEdge {
  id?: string;
  source: string;
  target: string;
  label?: string;
  kind?: string;
  evidence_ids?: string[];
  confidence?: number;
  [key: string]: unknown;
}

export interface RepositoryDiagramGroup {
  id: string;
  label: string;
  description?: string;
  confidence?: number | string;
  evidence_ids?: string[];
  [key: string]: unknown;
}

export interface RepositoryDiagramSpec {
  version?: number;
  title?: string;
  summary?: string;
  brief?: string;
  kind?: RepositoryDiagramKind;
  language?: "en" | "de";
  analysis_mode?: "model_assisted" | "deterministic_fallback";
  scope_note?: string;
  nodes?: RepositoryDiagramNode[];
  edges?: RepositoryDiagramEdge[];
  groups?: RepositoryDiagramGroup[];
  [key: string]: unknown;
}

export interface RepositoryEvidence {
  id?: string;
  evidence_id?: string;
  path?: string;
  source_path?: string;
  start_line?: number;
  end_line?: number;
  line_start?: number;
  line_end?: number;
  label?: string;
  kind?: string;
  parser_id?: string;
  confidence?: number;
  file_sha256?: string;
  excerpt_sha256?: string;
  hash?: string;
  fact?: string;
  summary?: string;
  [key: string]: unknown;
}

export interface RepositoryAnalysisMetadata {
  analysis_contract_version?: number;
  analysis_mode?: "model_assisted" | "deterministic_fallback";
  map_chunks_completed?: number;
  candidate_node_count?: number;
  candidate_edge_count?: number;
  parser_ids?: string[];
  [key: string]: unknown;
}

export interface RepositoryAnalysis {
  public_id: string;
  project_id: number | null;
  repository_url: string;
  repository_access?: "public" | "private";
  owner: string;
  name: string;
  ref: string | null;
  subpath?: string | null;
  commit_sha: string | null;
  status: RepositoryAnalysisStatus;
  error: string | null;
  error_code: string | null;
  goal: string;
  diagram_kind: RepositoryDiagramKind;
  language: "en" | "de";
  coverage: RepositoryAnalysisCoverage | null;
  diagram_spec: RepositoryDiagramSpec | null;
  evidence: RepositoryEvidence[];
  analysis_metadata?: RepositoryAnalysisMetadata | null;
  created_at: string;
  updated_at: string;
}

export type RepositoryManuscriptKind = "caption" | "description" | "section";

export interface RepositoryManuscriptPreviewRequest {
  request_id: string;
  kind: RepositoryManuscriptKind;
  language: "en" | "de";
  /** Separate purpose-bound consent for the prose provider call. */
  ai_generation_confirmed: true;
}

export interface RepositoryManuscriptProvenance {
  grounding: "repository_spec";
  repository_url: string;
  commit_sha: string;
  grounding_sha256: string;
  evidence_ids: string[];
  coverage: RepositoryAnalysisCoverage;
  scope_note: string;
}

export interface RepositoryManuscriptClaim {
  text: string;
  node_ids: string[];
  edge_ids: string[];
  evidence_ids: string[];
  support: "topology" | "coverage";
}

export interface RepositoryManuscriptPreview {
  request_id: string;
  analysis_id: string;
  analysis_updated_at: string;
  commit_sha: string;
  grounding_sha256: string;
  kind: RepositoryManuscriptKind;
  language: "en" | "de";
  text: string;
  claims: RepositoryManuscriptClaim[];
  selected_node_ids: string[];
  selected_edge_ids: string[];
  evidence_ids: string[];
  described_node_count: number;
  spec_node_count: number;
  described_edge_count: number;
  spec_edge_count: number;
  scope_note: string;
  provenance: RepositoryManuscriptProvenance;
  consent: {
    ai_generation_confirmed: true;
    confirmed_at: string;
    purpose_version: "repository-manuscript-prose-v1";
  };
  generated_at: string;
  /** Hash of the complete source-bound preview contract, not only its text. */
  preview_sha256: string;
}

export interface RepositoryManuscriptProposalRequest {
  request_id: string;
  preview_request_id: string;
  preview_sha256: string;
  writer_document_id: string;
  expected_writer_revision: number;
}

export interface RepositoryManuscriptProposal {
  request_id: string;
  analysis_id: string;
  repository_access: "public" | "private";
  message_id: number;
  writer_document_id: string;
  expected_writer_revision: number;
  edits: WriterEdit[];
  verification: WriterVerification;
  requires_manual_review: true;
  idempotent: boolean;
}

export interface FigureRepositoryProvenance {
  analysis_id?: string;
  repository_id?: string;
  repository_access?: "public" | "private";
  owner?: string;
  name?: string;
  repository_url?: string;
  commit_sha?: string;
  ref?: string | null;
  subpath?: string | null;
  diagram_kind: RepositoryDiagramKind;
  verification_status: "styled_variant";
  spec_node_count: number;
  spec_edge_count: number;
}

export interface Figure {
  id: number;
  public_id: string;
  project_id: number | null;
  repository_analysis_id?: string | null;
  prompt: string;
  status: "pending" | "ok" | "error";
  error: string;
  config: {
    title?: string;
    kind?: "method" | "architecture" | "flow" | "concept" | "plot" | "refine" | "source";
    resolution?: "1k" | "2k" | "4k" | "original";
    aspect_ratio?: string;
    review_passes?: number;
    format_version?: number;
    is_example?: boolean;
    example_key?: string;
    active_stage?: string;
    pipeline?: Array<{
      id: string;
      label: string;
      status: "pending" | "running" | "completed" | "failed" | "skipped";
      detail?: string;
    }>;
    dataset_id?: string | null;
    dataset_name?: string | null;
    chart_kind?: "bar" | "line" | "scatter";
    exact?: boolean;
    writer_document_id?: string;
    writer_message_id?: number;
    writer_attachments?: Record<string, string>;
    repository?: FigureRepositoryProvenance;
    source?: {
      type: "paper_figure";
      document_id: number;
      work_id: string;
      paper_title: string;
      page: number;
      caption: string;
      url: string;
      doi: string | null;
      license: string | null;
      legal_basis: string | null;
    };
  };
  byte_size: number;
  created_at: string;
  run: { public_id: string; label: string } | null;
}

export interface WriterTemplate {
  id: number;
  name: string;
  chars: number;
  updated_at: string;
  origin: WriterTemplateOrigin | null;
}

export interface WriterTemplateShare {
  token: string;
  name: string;
  created_at: string;
}

export interface WriterTemplateSharedPreview extends WriterTemplateShare {
  chars: number;
  origin: WriterTemplateOrigin | null;
}

export interface WriterTemplateOrigin {
  provider: string;
  source_url: string;
  upstream_url: string;
  author: string;
  license_name: string;
  license_url: string;
  license_status: "open" | "confirmed" | "unverified";
  rights_confirmed: boolean;
  package_bytes: number;
  file_count: number;
  sha256: string;
  imported_at: string;
}

export interface WriterTemplateLinkPreview {
  source_url: string;
  upstream_url: string;
  provider: string;
  title: string;
  author: string;
  license_name: string;
  license_url: string;
  license_status: "open" | "unverified";
  rights_confirmation_required: boolean;
  file_count: number;
  text_file_count: number;
  asset_count: number;
  package_bytes: number;
  sha256: string;
  warnings: string[];
}

export interface WriterContributionLog {
  summary: {
    assistant_turns: number;
    edits_proposed: number;
    edits_applied: number;
    edits_applied_auto: number;
    ai_chars_added: number;
    ai_chars_removed: number;
    compiles: number;
    snapshots: number;
    figures: number;
    readable_pdfs: number;
    linked_searches: string[];
    created_at: string;
    updated_at: string;
  };
  events: {
    kind: string;
    payload: Record<string, unknown>;
    created_at: string;
  }[];
  disclosure: string;
  disclosure_tex: string;
}
