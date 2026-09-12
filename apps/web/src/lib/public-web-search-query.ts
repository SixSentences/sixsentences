/** Local-only suggestions. A suggestion is never permission to send a query. */
export const MAX_PUBLIC_WEB_QUERY_LENGTH = 400;

// Bump with changes to the exact-query disclosure; keep the Core contract aligned.
export const PUBLIC_WEB_SEARCH_NOTICE_VERSION = "public-web-query-2026-09-04.1";

type SearchContextMessage = {
  role: string;
  content: string;
  payload?: Record<string, unknown> | null;
};

const FOLLOW_UP_WORDS = new Set(
  ("schau schaue schauen such suche suchen recherchier recherchiere recherchieren " +
    "prüf prüfe prüfen überprüfe nachschauen nachschlagen googel google bitte mal noch " +
    "auch doch dazu darüber dafür das dies diese dieses dieser dem den die der im in " +
    "ins internet netz web online nach auf und zu zum zur mit mehr infos informationen " +
    "etwas es ein eine einmal nochmal erneut ebenfalls aktuell aktuelle aktuellen " +
    "aktuellsten neueste neuesten quellen quelle offizielle offiziellen dokumentation " +
    "offiziell offizieller insbesondere besonders speziell vor allem hierzu hierüber " +
    "doc docs details detailed detail genauer genaueres weiter jetzt topic subject " +
    "especially specifically particularly including preferably authoritative reliable " +
    "a an continue further searches see them these those would " +
    "check search look research browse verify find please also too again up it this " +
    "that about for on in the and some more information sources source latest current " +
    "official documentation now as well could can you du kannst könntest einmal").split(/\s+/),
);

const PRIVATE_CONTEXT_REFERENCE = /\b(?:my|our|mein\w*|unser\w*|selected|marked|markiert\w*|attached|uploaded|hochgeladen\w*)\b|\b(?:transcript|transkript|manuscript|manuskript|passage|participant|teilnehmer\w*)\b|https?:\/\/|[\w.+-]+@[\w.-]+\.[a-z]{2,}/i;

// Only an explicit, delimited public-topic declaration can cross a prior
// private-context boundary. Its body is never copied into the search query.
const EXPLICIT_PUBLIC_TOPIC_RETURN = /^(?:zurück\s+zum\s+öffentlichen\s+thema|back\s+to\s+the\s+public\s+topic)\s+([^:\r\n]+):(?=\s|$)/iu;

// A novel modifier must not turn "look it up ..." into a standalone topic.
// Only the fully recognised vocabulary above may resolve history; otherwise
// this explicit context reference asks for a manually entered public topic.
const UNRESOLVED_CONTEXT_PREFIX = /^(?:(?:kannst|könntest|würdest)\s+du\s+|(?:can|could|would)\s+you\s+)?(?:bitte\s+|please\s+)?(?:schau(?:e|en)?|such(?:e|en)?|recherchier(?:e|en)?|prüf(?:e|en)?|check|search|look|research|browse|verify|find)\s+(?:(?:bitte|mal|auch|noch|doch|please|also)\s+)*(?:dazu|darüber|hierzu|hierüber|dafür|das|dies|diese|dieses|this|that|it|these|those)\b/i;
const UNRESOLVED_CONTEXT_QUESTION = /^(?:kannst|könntest|würdest)\s+du\s+(?:(?:bitte|mal|auch|noch)\s+)*(?:dazu|darüber|hierzu|hierüber|dafür|das|dies|diese|dieses)\b/i;

// Output shape is not a new search subject. Recognise only a complete trailing
// format instruction, never an arbitrary clause that could introduce a topic.
const RESPONSE_FORMAT_SUFFIX = /\s+(?:und\s+(?:beantworte\s+(?:die|diese)\s+frage|antworte)(?:\s+bitte)?\s+(?:in|mit)\s+(?:\d{1,3}|einem|einen|einer|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn)\s+(?:(?:kurzen|knappen|klaren)\s+)?(?:satz|sätzen|saetzen|absatz|absätzen|absaetzen|punkten|stichpunkten)|and\s+(?:answer(?:\s+(?:the|this)\s+question)?|respond)(?:\s+please)?\s+in\s+(?:\d{1,3}|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:(?:short|brief|concise)\s+)?(?:sentences?|paragraphs?|bullet\s+points?))[.!?]*\s*$/iu;

/** Recognise an elliptical instruction, not a search subject. */
export function isContextualWebSearchFollowUp(message: string): boolean {
  const instruction = message.trim().replace(RESPONSE_FORMAT_SUFFIX, "");
  const words = instruction.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  return words.length > 0 && words.every((word) => FOLLOW_UP_WORDS.has(word));
}

export function validPublicWebSearchQuery(query: string): boolean {
  const canonical = query.trim();
  return canonical.length > 0 && canonical.length <= MAX_PUBLIC_WEB_QUERY_LENGTH &&
    /[\p{L}\p{N}]/u.test(canonical) && !/[\p{Cc}\p{Cf}]/u.test(canonical) &&
    !isContextualWebSearchFollowUp(canonical) && !UNRESOLVED_CONTEXT_PREFIX.test(canonical) &&
    !UNRESOLVED_CONTEXT_QUESTION.test(canonical);
}

/**
 * Resolve a follow-up only from an approved public query or an explicit public
 * topic declaration in this conversation. Never infer public status from user
 * prose or copy assistant prose, attached text, selected passages or tool output.
 * A later substantive/private turn stops lookup rather than reviving an old topic.
 * The displayed result still needs a fresh, query-bound public-data approval.
 */
export function suggestPublicWebSearchQuery(
  message: string,
  history: readonly SearchContextMessage[],
  hasSelection = false,
): string {
  const current = message.trim();
  if (!isContextualWebSearchFollowUp(current)) {
    return validPublicWebSearchQuery(current) ? current : "";
  }
  if (hasSelection) return "";
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const previous = history[index];
    if (previous.role !== "user") continue;
    const approvedQuery = previous.payload?.web_search_query;
    if (previous.payload?.web_search_public_data_confirmed === true &&
        typeof approvedQuery === "string" && validPublicWebSearchQuery(approvedQuery)) {
      return approvedQuery.trim();
    }
    const candidate = previous.content.trim();
    if (previous.payload?.selection) return "";
    if (isContextualWebSearchFollowUp(candidate)) continue;
    const publicTopicReturn = candidate.match(EXPLICIT_PUBLIC_TOPIC_RETURN);
    if (publicTopicReturn) {
      const topic = publicTopicReturn[1].trim();
      return !PRIVATE_CONTEXT_REFERENCE.test(topic) && validPublicWebSearchQuery(topic)
        ? topic
        : "";
    }
    // A short sentence without obvious private keywords is not public-data
    // approval. Do not infer a query from it or cross it to reuse an older one.
    return "";
  }
  return "";
}
