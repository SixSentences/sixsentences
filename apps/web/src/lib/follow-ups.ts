export interface FollowUpSuggestion {
  label: string;
  prompt: string;
}

interface RunFollowUpContext {
  question: string;
  answer: string;
  askMode: boolean;
  paperCount: number;
  evidencePassageCount: number;
  evidenceSourceCount: number;
}

interface WriterFollowUpContext {
  question: string;
  answer: string;
  editCount: number;
  hasLinkedSources: boolean;
}

function compactTopic(question: string): string {
  return question
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[?.!]+$/, "")
    .slice(0, 180);
}

function addSuggestion(
  suggestions: FollowUpSuggestion[],
  label: string,
  prompt: string,
): void {
  if (suggestions.some((item) => item.label === label)) return;
  suggestions.push({ label, prompt });
}

/**
 * Suggest the next useful research actions without spending another model call.
 * The visible labels teach capabilities while the prompts stay anchored to the
 * user's current question.
 */
export function buildRunFollowUps({
  question,
  answer,
  askMode,
  paperCount,
  evidencePassageCount,
  evidenceSourceCount,
}: RunFollowUpContext): FollowUpSuggestion[] {
  if (!question.trim() || !answer.trim()) return [];

  const topic = compactTopic(question);
  const context = `${question} ${answer}`.toLowerCase();
  const suggestions: FollowUpSuggestion[] = [];
  const hasNumbers = /\b(\d+(?:[.,]\d+)?%?|effect|rate|ratio|correlation|increase|decrease|trend|outcome)\b/i.test(context);
  const isComparative = /\b(compare|comparison|versus|vs\.?|difference|differ|alternative)\b/i.test(context);
  const isCurrent = /\b(latest|recent|current|today|new evidence|state of the art)\b/i.test(context);
  const namesSinglePaper = /\b(?:this|that|the|original|dies(?:e[snm]?|em)|das|dem|der|originale[nrms]?)\s+(?:paper|article|study|papier|aufsatz)\b/i.test(question);
  const actsOnSinglePaper =
    /\b(?:paper|article|study|papier|aufsatz)\b/i.test(question)
    && /\b(?:open|show|save|download|highlight|mark|read|explain|öffn|oeffn|zeig|lad|speicher|markier|lies|les|erklär|erklaer)\w*/i.test(question);
  const isSinglePaperTask = !isComparative && (namesSinglePaper || actsOnSinglePaper);

  if (evidencePassageCount > 0 || /\[(?:W\d+|[a-z0-9.-]+\.[a-z]{2,})\]/i.test(answer)) {
    addSuggestion(
      suggestions,
      "Verify the main claim",
      `Verify the main factual claim in the answer to “${topic}”. Separate supporting, contradicting and insufficient academic evidence, and show the source-level verdicts.`,
    );
    addSuggestion(
      suggestions,
      "Open source passage",
      `Open the strongest available full-text source behind the answer to “${topic}” and highlight the exact passage that supports the main conclusion.`,
    );
  }

  if (askMode) {
    if (isSinglePaperTask) {
      addSuggestion(
        suggestions,
        "Explain this paper",
        `Explain the named paper in “${topic}” through its research question, method, main result and limitations, using the opened full text where available.`,
      );
      addSuggestion(
        suggestions,
        "Trace later work",
        `Find the most important later work that directly builds on or challenges the named paper in “${topic}”, and explain the connection before offering a comparison.`,
      );
    }
    addSuggestion(
      suggestions,
      "Start a full review",
      `Turn “${topic}” into a documented systematic literature search with explicit eligibility criteria and screening.`,
    );
  }

  if (isCurrent) {
    addSuggestion(
      suggestions,
      "Check newer evidence",
      `Check current academic and high-quality web sources for evidence that updates or challenges the answer to “${topic}”.`,
    );
  }

  if (hasNumbers) {
    addSuggestion(
      suggestions,
      "Visualize the finding",
      `Turn the most decision-relevant quantitative finding in the answer to “${topic}” into a clear chart and explain what it does and does not show.`,
    );
  }

  // A comparative wording alone is not evidence. When retrieval produced no
  // usable papers, offering a table teaches a path that must immediately fail
  // or tempt the model to invent rows. Only expose it once the chat actually
  // has at least two sources (or an existing paper set from a review run).
  if (!isSinglePaperTask && (evidenceSourceCount >= 2 || paperCount >= 2)) {
    addSuggestion(
      suggestions,
      isComparative ? "Build comparison table" : "Compare key studies",
      `Compare the most relevant studies for “${topic}” in a compact table covering method, sample, outcome, main result and limitations.`,
    );
  }

  if (!askMode) {
    addSuggestion(
      suggestions,
      "Extract study data",
      `Extract the key study characteristics and outcome values relevant to “${topic}” into a reusable evidence table.`,
    );
  }

  addSuggestion(
    suggestions,
    "Stress-test the answer",
    `Act as a skeptical reviewer and identify the strongest counter-evidence, uncertainty and methodological limitation in the answer to “${topic}”.`,
  );

  return suggestions.slice(0, 3);
}

/** Suggest manuscript actions that match the latest assistant turn. */
export function buildWriterFollowUps({
  question,
  answer,
  editCount,
  hasLinkedSources,
}: WriterFollowUpContext): FollowUpSuggestion[] {
  if (!question.trim() || !answer.trim()) return [];

  const topic = compactTopic(question);
  const context = `${question} ${answer}`.toLowerCase();
  const suggestions: FollowUpSuggestion[] = [];
  const concernsCitations = /\b(cit|source|evidence|reference|literature|claim)\w*/i.test(context);
  const concernsResults = /\b(result|data|finding|outcome|effect|table|figure|plot|chart|analysis)\w*/i.test(context);
  const concernsStructure = /\b(section|structure|outline|introduction|method|discussion|conclusion|paragraph)\w*/i.test(context);

  if (editCount > 0) {
    addSuggestion(
      suggestions,
      "Check the revision",
      `Review the proposed revision for “${topic}” for factual accuracy, internal consistency and unintended changes before I apply it.`,
    );
  }

  if (concernsCitations || hasLinkedSources) {
    addSuggestion(
      suggestions,
      "Verify citation support",
      `Check which claims affected by “${topic}” still need evidence and propose only citations available in my linked searches or uploaded sources.`,
    );
  }

  if (concernsResults) {
    addSuggestion(
      suggestions,
      "Create a paper figure",
      `Identify the most useful figure or table for the material discussed in “${topic}” and propose the exact LaTeX plus a publication-ready caption.`,
    );
  }

  if (concernsStructure) {
    addSuggestion(
      suggestions,
      "Connect the next section",
      `Use the outcome of “${topic}” to propose a strong transition and the most useful next section without repeating existing text.`,
    );
  }

  addSuggestion(
    suggestions,
    "Run a reviewer pass",
    `Review the manuscript around “${topic}” as a critical peer reviewer and propose the two highest-impact improvements with precise edits.`,
  );
  addSuggestion(
    suggestions,
    "Strengthen the argument",
    `Strengthen the reasoning around “${topic}”, clearly separating evidence, interpretation and limitations, and return precise edit proposals.`,
  );

  return suggestions.slice(0, 3);
}
