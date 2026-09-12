const INTERNAL_AGENT_CONTROL_TEXT = new RegExp(
  String.raw`(?:\b(?:iteration(?:s)?[\s_-]*(?:\d+(?:\s*\/\s*\d+)?|count|limit|ceiling|cap|budget|remaining|left)|(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[\s_-]+iterations?|\d+[\s_-]*iteration[\s_-]*(?:ceiling|cap|limit)|(?:current|next|final)[\s_-]*iteration|tool[\s_-]*calls?|work[\s_-]*limit|safe[\s_-]*guard|safety[\s_-]*limit|\d+[\s_-]+(?:distinct[\s_-]+)?search[\s_-]*passes?|search[\s_-]*passes?[\s_-]+(?:remain|required|left)|remaining[\s_-]*(?:budget|calls?|tools?))\b|\b(?:iteration(?:en)?[\s_-]*(?:\d+|anzahl|limit|obergrenze|budget|verbleibend)|(?:ein(?:e)?|zwei|drei|vier|f(?:ü|ue)nf|sechs|sieben|acht|neun|zehn)[\s_-]+iteration(?:en)?|(?:aktuelle|n(?:ä|ae)chste|letzte)[\s_-]*iteration|tool[\s_-]*aufrufe?|arbeitslimit|sicherheitslimit|\d+[\s_-]+suchl(?:auf|äufe)|verbleibende[\s_-]*(?:aufrufe?|tools?|budget))\b)`,
  "i",
);
const DOMAIN_ITERATION_CONTEXT =
  /\b(?:algorithm|solver|optimization|optimisation|training|model|method|experiment|simulation|converg\w*|epoch|gradient|verfahren|algorithmus|optimierung|training|modell|methode|experiment|simulation)\w*\b/i;
const HARD_RUNTIME_CONTEXT =
  /\b(?:agent|turn|tool[\s_-]*calls?|budget|limit|ceiling|cap|remaining|left|guard|review[\s_-]*round|search[\s_-]*passes?|aufrufe?|arbeitslimit|sicherheitslimit|obergrenze|verbleibend)\b/i;

const NORMAL_PROGRESS_REWRITES: Array<[RegExp, string]> = [
  [
    /(?:skipped a duplicate agent action|identical .+ call already ran|duplicate agent action|reused an earlier tool result)/i,
    "Reviewed the available results",
  ],
  [
    /(?:\bend of file\b|\beof\b|reached (?:the )?end of|beyond (?:the )?(?:end|linked source)|no more (?:source )?(?:content|text)|offset (?:was )?beyond)/i,
    "Finished reviewing the source",
  ],
  [
    /(?:compacted|truncated|replayed) (?:the )?(?:working |conversation )?context/i,
    "Reviewed the latest workspace state",
  ],
  [
    /using the stored interview as evidence,? not creating a new interview stud(?:y|ie)?/i,
    "Reviewed the saved interview transcript",
  ],
  [
    /working only inside the open survey and its validated editor actions/i,
    "Reviewed the current survey draft",
  ],
  [
    /(?:working|reviewing) (?:only )?(?:inside|with) the (?:open|current) interview study.*do not create a separate study/i,
    "Reviewed the current interview study",
  ],
];
const INTERNAL_WORKFLOW_HISTORY_TEXT =
  /\b(?:temporary workaround|known bug|bug history|regression|legacy fallback|fallback path|internal (?:controller|guard|state|prompt)|previous attempt failed|earlier attempt failed|retrying because|transport projection)\b/i;
const UNNECESSARY_NEGATIVE_ACTION_TEXT =
  /^(?:(?:no changes? (?:were|was|have been) made|nothing (?:was|has been) (?:changed|edited|deleted|removed|written|applied|published)|did not (?:change|edit|delete|remove|write|apply|publish)(?: anything)?|without (?:changing|editing|deleting|removing|writing|applying|publishing)(?: anything)?)(?: (?:in|to) the (?:workspace|file|manuscript|dataset|survey|table|source))?|(?:keine? (?:Ä|Ae|ä|ae)nderungen? (?:wurden|wurde)(?: (?:im|in der|an der) (?:Workspace|Datei|Manuskript|Datensatz|Umfrage|Tabelle|Quelle))?|nichts (?:wurde|ist) (?:ge(?:ä|ae)ndert|bearbeitet|gel(?:ö|oe)scht|entfernt|geschrieben|angewendet|ver(?:ö|oe)ffentlicht)|nicht (?:ge(?:ä|ae)ndert|bearbeitet|gel(?:ö|oe)scht|entfernt|geschrieben|angewendet|ver(?:ö|oe)ffentlicht)|ohne (?:etwas )?(?:zu )?(?:ändern|bearbeiten|löschen|entfernen|schreiben|anwenden|veröffentlichen)))\.?$/i;

/** Keep internal controller counters and ceilings out of user-facing text. */
export function safeAgentDisplayText(value: unknown, fallback = "") {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return fallback;
  if (!INTERNAL_AGENT_CONTROL_TEXT.test(text)) return text;
  const visible = text
    .split(/(?<=[.!?])\s+|[\r\n]+/)
    .filter((part) => {
      if (!INTERNAL_AGENT_CONTROL_TEXT.test(part)) return true;
      return DOMAIN_ITERATION_CONTEXT.test(part) && !HARD_RUNTIME_CONTEXT.test(part);
    })
    .join(" ")
    .trim();
  return visible || fallback;
}

/**
 * Turn ordinary progress copy into a concise user-facing update.
 *
 * Unlike `safeAgentDisplayText`, this helper is only for successful or active
 * progress. Error renderers intentionally keep using the base helper so a real
 * failure or an unapplied change remains visible to the user.
 */
export function safeAgentProgressText(value: unknown, fallback = "") {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return fallback;
  const visible = text
    .split(/(?<=[.!?])\s+|[\r\n]+/)
    .flatMap((part) => {
      const rewrite = NORMAL_PROGRESS_REWRITES.find(([pattern]) => pattern.test(part));
      if (rewrite) return [rewrite[1]];
      const safe = safeAgentDisplayText(part);
      if (!safe) return [];
      if (INTERNAL_WORKFLOW_HISTORY_TEXT.test(safe)) return [];
      if (UNNECESSARY_NEGATIVE_ACTION_TEXT.test(safe)) return [];
      return [safe];
    })
    .filter((part, index, parts) => parts.indexOf(part) === index)
    .join(" ")
    .trim();
  return visible || fallback;
}

/** Recursively remove internal controller strings from inspectable tool data. */
export function safeAgentDisplayValue(value: unknown): unknown {
  if (typeof value === "string") {
    return safeAgentDisplayText(value) || undefined;
  }
  if (Array.isArray(value)) {
    return value
      .map((entry) => safeAgentDisplayValue(entry))
      .filter((entry) => entry !== undefined);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).flatMap(([key, entry]) => {
        const safe = safeAgentDisplayValue(entry);
        return safe === undefined ? [] : [[key, safe]];
      }),
    );
  }
  return value;
}

/** Allow only explicit HTTP(S) destinations for external research links. */
export function safeExternalHttpUrl(value: unknown) {
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.toString()
      : "";
  } catch {
    return "";
  }
}

/** Link plain URLs only when the research tools actually observed that page. */
export function observedSourceTextParts(text: string, observedUrls: Iterable<string>) {
  const observed = new Set(Array.from(observedUrls, safeExternalHttpUrl).filter(Boolean));
  const parts: Array<{ text: string; href?: string }> = [];
  let cursor = 0;
  for (const match of text.matchAll(/https?:\/\/[^\s<>"`]+/gi)) {
    let candidate = match[0];
    // Sentence punctuation is not part of a URL; balanced URL parentheses are.
    candidate = candidate.replace(/[.,;:!?]+$/, "");
    while (candidate.endsWith(")") && candidate.split(")").length > candidate.split("(").length) {
      candidate = candidate.slice(0, -1);
    }
    const href = safeExternalHttpUrl(candidate);
    if (!href || !observed.has(href)) continue;
    const start = match.index!;
    if (start > cursor) parts.push({ text: text.slice(cursor, start) });
    parts.push({ text: candidate, href });
    cursor = start + candidate.length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) });
  return parts;
}

/** Keep generated-output links on safe app routes or explicit HTTPS origins. */
export function safeAgentArtifactHref(value: unknown) {
  if (typeof value !== "string") return "";
  const href = value.trim();
  if (href.startsWith("/") && !href.startsWith("//")) {
    try {
      const decoded = decodeURIComponent(href);
      const pathname = decoded.split(/[?#]/, 1)[0];
      const segments = pathname.split("/");
      if (
        decoded.startsWith("//") ||
        decoded.includes("\\") ||
        /[\u0000-\u001F\u007F]/.test(decoded) ||
        segments.some((segment) => segment === "." || segment === "..")
      ) {
        return "";
      }
      return href;
    } catch {
      return "";
    }
  }
  try {
    const url = new URL(href);
    return url.protocol === "https:" ? url.toString() : "";
  } catch {
    return "";
  }
}
