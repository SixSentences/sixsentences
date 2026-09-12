import type {
  SpecialistAgentEvent,
  WriterEdit,
  WriterMessage,
} from "@/lib/types";

export type WriterEditDecision =
  | "pending"
  | "applied"
  | "rejected"
  | "superseded"
  | "blocked";

export type WriterEditReview = {
  id: string;
  messageId: number;
  index: number;
  edit: WriterEdit;
  decision: WriterEditDecision;
};

type PersistedEditDecision = "applied" | "rejected" | "superseded";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function writerEditReviewId(messageId: number, index: number): string {
  return `${messageId}:${index}`;
}

/** Match the backend's exact, unique find-anchor rule without changing text. */
export function locateWriterEditAnchor(
  source: string,
  find: string,
): { from: number; to: number } | null {
  if (!find) return null;
  const from = source.indexOf(find);
  if (from < 0 || source.indexOf(find, from + find.length) >= 0) return null;
  return { from, to: from + find.length };
}

function completedEventMatchesEdit(
  event: SpecialistAgentEvent,
  edit: WriterEdit,
  index: number,
): boolean {
  if (
    event.event !== "change.completed"
    || event.tool !== "manuscript.apply_change"
    || event.applied === false
    || event.before !== edit.find
    || event.after !== edit.replace
  ) {
    return false;
  }
  const input = isRecord(event.input) ? event.input : {};
  const output = isRecord(event.output) ? event.output : {};
  const eventPath = String(input.path ?? output.path ?? "");
  const proposalIndex = input.proposal_index ?? output.proposal_index;
  if (
    proposalIndex !== undefined
    && Number(proposalIndex) !== index
  ) {
    return false;
  }
  return eventPath === edit.path;
}

function persistedDecision(
  message: WriterMessage,
  index: number,
  edit: WriterEdit,
): PersistedEditDecision | null {
  const decisions = message.payload.edit_decisions;
  if (isRecord(decisions)) {
    const decision = decisions[String(index)];
    if (
      decision === "applied"
      || decision === "rejected"
      || decision === "superseded"
    ) {
      return decision;
    }
  }
  if (
    (message.payload.edits ?? []).filter(
      (candidate) =>
        candidate.path === edit.path
        && candidate.find === edit.find
        && candidate.replace === edit.replace,
    ).length === 1
    && (message.payload.agent_events ?? []).some((event) =>
      completedEventMatchesEdit(event, edit, index),
    )
  ) {
    return "applied";
  }
  if (
    (message.payload.agent_events ?? []).some((event) => {
      const input = isRecord(event.input) ? event.input : {};
      const output = isRecord(event.output) ? event.output : {};
      return (
        (input.proposal_index !== undefined || output.proposal_index !== undefined)
        && completedEventMatchesEdit(event, edit, index)
      );
    })
  ) {
    return "applied";
  }
  return null;
}

/**
 * Project the durable chat proposals into one review queue.
 *
 * Message order and edit order are preserved deliberately: later anchors may
 * only exist after an earlier proposal has landed. The UI therefore exposes
 * the first pending proposal instead of pretending independent edits can be
 * approved safely in parallel.
 */
export function collectWriterEditReviews(
  messages: WriterMessage[],
  optimisticApplied: ReadonlySet<string> = new Set(),
): WriterEditReview[] {
  return messages.flatMap((message) => {
    if (message.role !== "assistant") return [];
    return (message.payload.edits ?? []).map((edit, index) => {
      const id = writerEditReviewId(message.id, index);
      const durable = persistedDecision(message, index, edit);
      const decision: WriterEditDecision =
        durable === "applied" || optimisticApplied.has(id)
          ? "applied"
          : durable === "rejected"
            ? "rejected"
            : durable === "superseded"
              ? "superseded"
            : !edit.applicable
              ? "blocked"
              : "pending";
      return { id, messageId: message.id, index, edit, decision };
    });
  });
}

/**
 * Return the newest message-scoped proposal set that still needs a decision.
 * Dependencies are ordered inside one assistant turn; an abandoned older
 * turn must not block a newer turn prepared against the latest manuscript.
 */
export function activeWriterEditReviewSet(
  reviews: WriterEditReview[],
): WriterEditReview[] {
  const newestProposal = reviews.at(-1);
  if (!newestProposal) return [];
  const newestSet = reviews.filter(
    (review) => review.messageId === newestProposal.messageId,
  );
  return newestSet.some((review) => review.decision === "pending")
    ? newestSet
    : [];
}

/** Only the first pending edit in the newest proposal set is reviewable. */
export function nextWriterEditReview(
  reviews: WriterEditReview[],
): WriterEditReview | null {
  return (
    activeWriterEditReviewSet(reviews).find(
      (review) => review.decision === "pending",
    ) ?? null
  );
}
