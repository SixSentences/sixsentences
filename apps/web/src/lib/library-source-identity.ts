export interface LibrarySourceIdentity {
  safeUrl: string | null;
  hostname: string | null;
  label: string;
  monogram: string;
  specific: boolean;
}

const URL_LIMIT = 2_048;
const LABEL_LIMIT = 80;
const UNSAFE_URL_CHARACTER = /[\u0000-\u0020\u007f]/;

/** Validate a stored Library destination without causing any network request. */
export function safeLibrarySourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const candidate = value.trim();
  if (
    !candidate
    || candidate.length > URL_LIMIT
    || UNSAFE_URL_CHARACTER.test(candidate)
  ) {
    return null;
  }
  try {
    const parsed = new URL(candidate);
    if (
      (parsed.protocol !== "https:" && parsed.protocol !== "http:")
      || !parsed.hostname
      || parsed.username
      || parsed.password
      || parsed.hostname.length > 253
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function normalizedLabel(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const label = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  return label ? Array.from(label).slice(0, LABEL_LIMIT).join("") : null;
}

function sourceMonogram(value: string): string {
  const letters = Array.from(value.normalize("NFKC"))
    .filter((character) => /[\p{L}\p{N}]/u.test(character));
  return letters.slice(0, 2).join("").toUpperCase() || "•";
}

/**
 * Build a neutral source identity from metadata already stored in the Library.
 * The result is a text monogram, never a fetched logo, favicon or brand asset.
 */
export function librarySourceIdentity({
  url,
  siteName,
  fallbackLabel,
}: {
  url: unknown;
  siteName?: unknown;
  fallbackLabel: string;
}): LibrarySourceIdentity {
  const safeUrl = safeLibrarySourceUrl(url);
  const parsedHostname = safeUrl ? new URL(safeUrl).hostname.toLowerCase() : null;
  const hostname = parsedHostname
    ? parsedHostname.replace(/\.$/, "").replace(/^www\./, "")
    : null;
  const siteLabel = normalizedLabel(siteName);
  const fallback = normalizedLabel(fallbackLabel) ?? "Source";
  // Prefer the actual destination host over page-controlled site metadata.
  const label = hostname ?? siteLabel ?? fallback;
  const identitySeed = hostname?.split(".").find(Boolean) ?? siteLabel;
  return {
    safeUrl,
    hostname,
    label,
    monogram: sourceMonogram(identitySeed ?? fallback),
    specific: Boolean(identitySeed),
  };
}
