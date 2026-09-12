import {
  Braces,
  ClipboardCheck,
  FileDown,
  Globe,
  Medal,
  Microscope,
  NotebookPen,
  ScanSearch,
  ShieldCheck,
  Telescope,
  Waypoints,
} from "lucide-react";

import type { RunConfig, RunEvent } from "@/lib/types";
import { coveragePresentation, recallPresentation } from "@/lib/review-quality-presentation";

/** Pipeline stages in execution order, with display metadata. */
export const STAGE_ORDER = [
  "protocol_synthesis",
  "query_compilation",
  "retrieval",
  "integrity",
  "ranking",
  "screening_title_abstract",
  "snowball",
  "acquisition",
  "screening_full_text",
  "web_search",
  "report",
] as const;

export type StageId = (typeof STAGE_ORDER)[number];

export const STAGE_META: Record<
  StageId,
  { label: string; icon: React.ComponentType<{ className?: string }> }
> = {
  protocol_synthesis: { label: "Protocol", icon: NotebookPen },
  query_compilation: { label: "Query compilation", icon: Braces },
  retrieval: { label: "Exhaustive retrieval", icon: Telescope },
  integrity: { label: "Integrity checks", icon: ShieldCheck },
  ranking: { label: "Ranking", icon: Medal },
  screening_title_abstract: { label: "Screening", icon: ScanSearch },
  snowball: { label: "Citation snowballing", icon: Waypoints },
  acquisition: { label: "Full-text acquisition", icon: FileDown },
  screening_full_text: { label: "Full-text screening", icon: Microscope },
  web_search: { label: "Web sources", icon: Globe },
  report: { label: "PRISMA report", icon: ClipboardCheck },
};

/** Stages a run will actually visit, given its configuration. */
export function expectedStages(config: Partial<RunConfig>): StageId[] {
  return STAGE_ORDER.filter((stage) => {
    if (stage === "screening_title_abstract") return Boolean(config.screen);
    if (stage === "snowball") return Boolean(config.snowball && config.screen);
    if (stage === "acquisition") return Boolean(config.acquire || config.full_text);
    if (stage === "screening_full_text") return Boolean(config.full_text);
    if (stage === "web_search") return Boolean(config.web_search);
    return true;
  });
}

/** Every SSE event name the stream can emit (EventSource needs them upfront). */
export const EVENT_NAMES = [
  "protocol_created",
  "protocol_approved",
  "protocol_gate_opened",
  "queries_compiled",
  "query_calibration",
  "canary_check",
  "corpus_search_done",
  "expansion_round_done",
  "saturation_check",
  "coverage_estimated",
  "live_search_page",
  "live_search_done",
  "live_search_partial",
  "live_search_unavailable",
  "dedup_done",
  "retraction_check_done",
  "integrity_signals_done",
  "peer_review_filter",
  "retraction_recheck",
  "ranking_done",
  "working_set_finalized",
  "output_set_finalized",
  "screening_started",
  "screening_progress",
  "screening_paused",
  "screening_done",
  "screening_recall_certified",
  "snowball_round_done",
  "snowball_screening_started",
  "snowball_screening_progress",
  "semantic_sweep_done",
  "imports_merged",
  "extraction_done",
  "extraction_started",
  "extraction_progress",
  "extraction_schema_updated",
  "extraction_cell_reviewed",
  "budget_exhausted",
  "provider_unavailable",
  "run_control",
  "human_decisions_recorded",
  "acquisition_progress",
  "acquisition_done",
  "acquisition_skipped",
  "fulltext_screening_started",
  "fulltext_screening_progress",
  "fulltext_screening_paused",
  "fulltext_screening_done",
  "web_search_done",
  "web_search_partial",
  "web_search_failed",
  "web_search_skipped",
  "web_harvest_done",
  "ask_queued",
  "ask_planned",
  "ask_retrieval_done",
  "ask_reader_started",
  "ask_library_started",
  "ask_answer_started",
  "ask_answer_delta",
  "ask_reasoning_delta",
  "ask_answer_finalizing",
  "ask_answer_verifying",
  "run_completed",
  "run_paused",
  "run_cancelled",
  "living_settings_updated",
  "living_refresh_started",
] as const;

const number = (value: unknown): number => (typeof value === "number" ? value : 0);
const text = (value: unknown): string => (typeof value === "string" ? value : "");
const fmt = (value: unknown): string => Intl.NumberFormat("en-US").format(number(value));

/**
 * One human sentence per audit event, the timeline line. Returns null for
 * events that shouldn't appear as a line (they're rendered elsewhere).
 */
export function describeEvent(event: RunEvent): string | null {
  const p = event.payload ?? {};
  switch (event.event) {
    case "protocol_created":
      return p.synthesized_by === "user"
        ? "Using your boolean query verbatim"
        : "Review protocol synthesized";
    case "protocol_gate_opened":
      return "Paused at the gate: the protocol is waiting for your approval";
    case "protocol_approved":
      return "Protocol approved, continuing the run";
    case "queries_compiled":
      return "Search strategy compiled for scholarly retrieval";
    case "query_calibration": {
      const verdict = text(p.verdict);
      return verdict ? `Hit-count calibration: ${verdict} (${fmt(p.unique)} works)` : null;
    }
    case "canary_check":
      return `Canary recall: ${fmt(p.found)}/${fmt(p.total)} known must-hits found`;
    case "corpus_search_done":
      return `Base query returned ${fmt(p.new_unique)} unique works`;
    case "expansion_round_done":
      return `Expansion round ${fmt(p.round)}: ${
        (p.variants as string[] | undefined)?.length ?? 0
      } variants, +${fmt(p.new_unique_works)} new works (novelty ${(
        number(p.novelty) * 100
      ).toFixed(1)}%)`;
    case "saturation_check": {
      const reason = text(p.stopped_because).replaceAll("_", " ");
      return `Search saturated after ${fmt(p.queries_executed)} queries: ${fmt(
        p.total_unique,
      )} unique works (${reason})`;
    }
    case "coverage_estimated":
      return coveragePresentation(p).description;
    case "live_search_page":
      return `Live index page ${fmt(p.page)}: +${fmt(p.new_unique)} new works · ${(
        number(p.relevant_share) * 100
      ).toFixed(0)}% direct topic matches`;
    case "live_search_done": {
      const reason = text(p.stopped_because).replaceAll("_", " ");
      return `The live index added ${fmt(p.new_unique)} fresh works${
        reason ? ` · stopped after ${reason}` : ""
      }`;
    }
    case "live_search_partial":
      return `The live index became unavailable after ${fmt(
        p.records_returned,
      )} records; the review continues with the retrieved evidence`;
    case "live_search_unavailable":
      return "The live index is temporarily unavailable; the review continues with the local corpus";
    case "dedup_done":
      return `Deduplicated: ${fmt(p.identified)} records down to ${fmt(p.unique)} unique works`;
    case "retraction_check_done":
      return number(p.flagged) > 0
        ? `${fmt(p.flagged)} retracted works flagged (kept visible, ranked down)`
        : "No retracted works found";
    case "integrity_signals_done":
      return `Integrity: ${fmt(p.flagged)} of ${fmt(p.assessed)} works flagged${
        number(p.critical) > 0 ? ` (${fmt(p.critical)} critical)` : ""
      }`;
    case "peer_review_filter":
      return `Peer-review filter excluded ${fmt(p.excluded)} works, ${fmt(p.kept)} kept`;
    case "ranking_done":
      return `${fmt(p.ranked)} works ranked by relevance · impact · recency`;
    case "working_set_finalized":
      return `${fmt(p.selected)} most relevant papers selected from ${fmt(
        p.identified_unique,
      )} identified`;
    case "output_set_finalized":
      return `${fmt(p.selected)} eligible papers selected from ${fmt(
        p.screened,
      )} screened records`;
    case "screening_done":
      return `Screened ${fmt(p.screened)}: ${fmt(p.included)} include · ${fmt(
        p.excluded,
      )} exclude · ${fmt(p.unsure)} unsure`;
    case "screening_started":
      return `Screening started: ${fmt(p.pending)} records pending`;
    case "screening_progress":
      return `Screening ${fmt(p.completed)}/${fmt(p.total)}: ${fmt(
        p.included,
      )} include · ${fmt(p.excluded)} exclude · ${fmt(p.unsure)} unsure`;
    case "screening_paused":
      return `Screening paused safely: ${fmt(p.screened)}/${fmt(
        p.of_total,
      )} records completed · ${fmt(p.pending)} pending`;
    case "semantic_sweep_done":
      return `Semantic sweep: ${fmt(p.returned)} meaning-level candidates across ${fmt(
        p.sweeps,
      )} phrasings (${fmt(p.new_unique)} new)`;
    case "snowball_round_done":
      return `Snowball round ${fmt(p.round)}: ${fmt(p.new_records)} new records via citations, ${fmt(
        p.new_includes,
      )} included`;
    case "snowball_screening_started":
      return `Snowball round ${fmt(p.round)}: screening ${fmt(p.candidates)} citation candidates`;
    case "snowball_screening_progress":
      return `Snowball round ${fmt(p.round)}: ${fmt(p.completed)}/${fmt(
        p.total,
      )} candidates screened`;
    case "imports_merged":
      return `${fmt(p.records)} uploaded records joined identification (${fmt(
        p.new_unique,
      )} new)`;
    case "extraction_done":
      return `Evidence table filled for ${fmt(p.works)} works (${fmt(
        p.verified_cells,
      )} verified quotes)`;
    case "extraction_started":
      return `Evidence extraction started for ${fmt(p.works)} works`;
    case "extraction_progress":
      return `Evidence extraction ${fmt(p.completed)}/${fmt(p.total)}`;
    case "extraction_schema_updated":
      return `Extraction contract updated (${(p.fields as string[] | undefined)?.length ?? 0} fields)`;
    case "extraction_cell_reviewed":
      return `Extraction review saved for ${text(p.field).replaceAll("_", " ")}`;
    case "screening_recall_certified":
      return recallPresentation(p).description;
    case "budget_exhausted":
      return `Processing limit reached after ${fmt(p.screened)} works: honest pause, ${fmt(
        p.pending,
      )} pending`;
    case "provider_unavailable":
      return `Model routes unavailable after retries: run paused safely with ${fmt(
        p.pending,
      )} records pending`;
    case "run_control":
      return text(p.signal) === "pause"
        ? `Pause requested, stopping at the next checkpoint (${fmt(p.pending)} pending)`
        : `Cancelling the active worker now`;
    case "human_decisions_recorded":
      return `${fmt(p.count)} human decision${number(p.count) === 1 ? "" : "s"} recorded`;
    case "acquisition_done":
      return `Full texts: ${fmt(p.retrieved)} of ${fmt(p.sought)} retrieved (${fmt(
        p.parsed,
      )} parsed)`;
    case "acquisition_progress":
      return `Full-text retrieval ${fmt(p.completed)}/${fmt(p.total)}: ${fmt(
        p.retrieved,
      )} retrieved · ${fmt(p.not_retrieved)} unavailable`;
    case "acquisition_skipped":
      return "Full-text acquisition skipped: no title/abstract candidates";
    case "fulltext_screening_started":
      return `Full-text screening started: ${fmt(p.pending)} reports pending`;
    case "fulltext_screening_progress":
      return `Full-text screening ${fmt(p.completed)}/${fmt(p.total)}: ${fmt(
        p.included,
      )} include · ${fmt(p.excluded)} exclude · ${fmt(p.unsure)} unsure`;
    case "fulltext_screening_paused":
      return "Full-text screening paused safely; completed decisions were preserved";
    case "fulltext_screening_done":
      return `Full-text pass: ${fmt(p.studies_included)} studies included of ${fmt(
        p.assessed,
      )} assessed`;
    case "web_search_done": {
      const collected = number(p.grey_literature ?? p.found ?? p.returned);
      return `${fmt(collected)} web source${collected === 1 ? "" : "s"} collected`;
    }
    case "web_search_partial": {
      const collected = number(p.grey_literature ?? p.found ?? p.returned);
      return `${fmt(collected)} web source${
        collected === 1 ? "" : "s"
      } collected; ${fmt(p.failed_queries)} search attempt${
        number(p.failed_queries) === 1 ? " was" : "s were"
      } unavailable`;
    }
    case "web_search_failed":
      return "Web source search was temporarily unavailable; no web sources were added";
    case "web_search_skipped":
      return "Web source search was unavailable for this run";
    case "web_harvest_done":
      return number(p.new_unique_works) > 0
        ? `${fmt(p.new_unique_works)} scholarly work${
            number(p.new_unique_works) === 1 ? "" : "s"
          } recovered from web sources`
        : null;
    case "retraction_recheck":
      return `Retraction re-check: ${
        (p.newly_retracted as string[] | undefined)?.length ?? 0
      } newly retracted`;
    case "run_completed":
      return "Run completed. Every number in the results is reconstructable from this log.";
    case "run_paused":
      return "Run paused. The work so far is saved.";
    case "run_cancelled":
      return "Run cancelled";
    case "living_settings_updated":
      return "Living research monitor updated";
    case "living_refresh_started":
      return "A versioned evidence refresh was started";
    default:
      return null;
  }
}

/** The stage a live run is currently in, judged by its last event. */
export function activeStage(events: RunEvent[]): StageId | null {
  const last = events[events.length - 1];
  if (!last) return "protocol_synthesis";
  const stage = last.stage as StageId;
  return STAGE_ORDER.includes(stage) ? stage : null;
}
