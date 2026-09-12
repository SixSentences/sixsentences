const URL_IN_MESSAGE = /https?:\/\/[^\s<>]+/i;
// A web-related noun is not a request: an online workshop, an existing website
// or a documentation summary must remain an ordinary workspace conversation.
const WEB_RESEARCH_ACTION = String.raw`\b(?:search(?:ing)?|research(?:ing)?|look(?:ing)?|check(?:ing)?|verify|consult|read|find|finding|finde|such\w*|recherchier\w*|schau\w*|prüf\w*|ueberpruef\w*|überprüf\w*|kontrollier\w*|lies|nachschlag\w*)\b`;
const WEB_RESEARCH_TARGET = String.raw`\b(?:internet|web|online|netz|official|offiziell\w*|documentation|docs?|dokumentation|website|webseite|vendor|hersteller)\b`;
const EXPLICIT_WEB_RESEARCH = new RegExp(
  WEB_RESEARCH_ACTION + String.raw`[^.!?;\n]{0,100}` + WEB_RESEARCH_TARGET +
    String.raw`|(?:^|[.!?;\n])\s*(?:(?:kannst|könntest|koenntest)\s+du\s+)?(?:bitte\s+)?(?:im\s+)?` + WEB_RESEARCH_TARGET + String.raw`[^.!?;\n]{0,60}\b(?:such\w*|recherchier\w*|nachschlag\w*)\b` +
    String.raw`|(?:^|[.!?;\n])\s*(?:please\s+|bitte\s+)?(?:(?:web|online|internet)\s+(?:search|research)|websuche|internetrecherche|googel\w*|google|browse)\b`,
  "i",
);
// Same existing no-new-research constraint as the server's tool policy.
const NO_NEW_RESEARCH = new RegExp(
  String.raw`\b(?:` +
    String.raw`(?:do\s+not|don['’]?t|without|no)\s+(?:new|another|further|more)?\s*` +
    String.raw`(?:(?:web|internet|online)\s+)?(?:search(?:ing)?|research|lookup|brows\w*|googl\w*|look(?:ing)?(?:\s+(?:it|this|that))?\s+(?:up|online)|(?:check|consult|read)\s+(?:the\s+)?(?:web|internet|online|official))|` +
    String.raw`nothing\s+(?:new|else)\s+to\s+(?:search|look\s+up)|` +
    String.raw`(?:nichts|nix|nichts\s+mehr|nicht|keine)\s+(?:neu\w*|weiter\w*|nochmal|` +
    String.raw`erneut)?\s*(?:(?:im\s+(?:internet|web|netz)|online)\s+)?(?:such\w*|recherch\w*|nachschlag\w*|(?:web|internet)(?:suche|recherche)|googel\w*)|` +
    String.raw`ohne\s+(?:(?:neue|weitere|erneute)\s+)?(?:suche|recherche|(?:web|internet)(?:suche|recherche))|` +
    String.raw`ohne\s+(?:(?:erneut|nochmal|weiter)\s+)?zu\s+(?:such\w*|recherchier\w*|nachschlag\w*)|` +
    String.raw`(?:such\w*|recherchier\w*|schau\w*)\s+(?:bitte\s+)?nicht\s+(?:(?:im|in\s+the)\s+)?(?:internet|web|netz|online)|` +
    String.raw`(?:nur|only)\s+(?:das|dieses|the|this|current|open|offene|vorhandene)\s+` +
    String.raw`(?:paper|papier|document|dokument|material|materialien)\s+(?:nutzen|verwenden|use)` +
    String.raw`)\b`,
  "i",
);

/**
 * Mirrors the API's deliberately narrow Sonar-intent boundary. A pasted URL
 * is excluded because it is opened by the separately constrained page reader
 * and must not grant a general web-search capability.
 */
export function explicitWebResearchRequested(message: string): boolean {
  return (
    !URL_IN_MESSAGE.test(message) &&
    !NO_NEW_RESEARCH.test(message) &&
    EXPLICIT_WEB_RESEARCH.test(message)
  );
}
