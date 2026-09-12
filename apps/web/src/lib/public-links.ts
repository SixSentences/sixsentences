const LOCAL_APP_ORIGIN = "http://localhost:3000";

function normalizedBaseUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (url.username || url.password || url.search || url.hash) return null;
    url.pathname = url.pathname.replace(/\/+$/, "");
    return url.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}

export const publicAppBaseUrl =
  normalizedBaseUrl(process.env.NEXT_PUBLIC_APP_URL) ?? LOCAL_APP_ORIGIN;

export const publicLegalBaseUrl =
  normalizedBaseUrl(process.env.NEXT_PUBLIC_LEGAL_BASE_URL);

/** Build an operator-configured legal link without inventing local documents. */
export function publicLegalUrl(path: string): string | null {
  if (!publicLegalBaseUrl) return null;
  const relativePath = path.replace(/^\/+/, "");
  return relativePath ? `${publicLegalBaseUrl}/${relativePath}` : publicLegalBaseUrl;
}
