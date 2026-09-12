export const BROWSER_CAPTURE_STORE_ITEM_ID =
  "nniehinpekncehhibpmpfmkhfoednhia";

export const BROWSER_CAPTURE_PREVIEW_URL =
  "/downloads/SixSentences-Browser-Capture-0.1.9-preview.zip";

/**
 * Accept only the canonical Chrome Web Store origin and this product's exact
 * item ID. The public environment value is an operator-controlled release
 * switch, but validating it prevents a typo or copied listing URL from sending
 * users to a different extension.
 *
 * @param {string | null | undefined} value
 * @returns {string | null}
 */
export function verifiedBrowserCaptureStoreUrl(value) {
  const candidate = typeof value === "string" ? value.trim() : "";
  if (!candidate) return null;

  try {
    const url = new URL(candidate);
    const pathSegments = url.pathname.split("/").filter(Boolean);
    const itemId = pathSegments.at(-1);
    const isStoreDetail =
      pathSegments[0] === "detail" &&
      (pathSegments.length === 2 || pathSegments.length === 3);

    if (
      url.protocol !== "https:" ||
      url.hostname !== "chromewebstore.google.com" ||
      url.port ||
      url.username ||
      url.password ||
      !isStoreDetail ||
      itemId !== BROWSER_CAPTURE_STORE_ITEM_ID
    ) {
      return null;
    }

    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}

/**
 * Resolve the install surface fail-closed. Production never exposes the
 * developer-mode preview; it gains an install CTA only after the exact Store
 * listing URL is deliberately configured at build time.
 *
 * @param {{ nodeEnv: string | undefined; storeUrl: string | undefined }} input
 * @returns {{ kind: "store" | "preview"; href: string } | null}
 */
export function resolveBrowserCaptureDistribution({ nodeEnv, storeUrl }) {
  const verifiedStoreUrl = verifiedBrowserCaptureStoreUrl(storeUrl);
  if (verifiedStoreUrl) return { kind: "store", href: verifiedStoreUrl };
  if (nodeEnv === "development") {
    return { kind: "preview", href: BROWSER_CAPTURE_PREVIEW_URL };
  }
  return null;
}
