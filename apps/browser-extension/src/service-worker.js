import { API_ORIGIN, APP_ORIGIN } from "./config.js";

const TRUSTED_CONTEXTS = new Set([chrome.runtime.id]);
const storageReady = chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
chrome.runtime.onInstalled.addListener(() => { void storageReady; });

const bytes = (size) => crypto.getRandomValues(new Uint8Array(size));
const base64url = (value) => btoa(String.fromCharCode(...value)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const sha256 = async (value) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
const MAX_PDF_BYTES = 50 * 1024 * 1024;
const MAX_METADATA_HTML_BYTES = 1024 * 1024;
const MAX_DSPACE_METADATA_BYTES = 128 * 1024;
const SOURCE_FETCH_TIMEOUT_MS = 15_000;
const PDF_PROBE_TIMEOUT_MS = 5_000;
const DSPACE_FETCH_TIMEOUT_MS = 5_000;
const PDF_FETCH_TIMEOUT_MS = 120_000;
const TAB_PDF_CHUNK_BYTES = 48 * 1024;
const MAX_TAB_PDF_CHUNKS = Math.ceil(MAX_PDF_BYTES / TAB_PDF_CHUNK_BYTES) + 1;
const TRACKING_CAPTURE_PARAM = /^(?:utm_.+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|tracking_?id|_tp)$/i;
const SECRET_CAPTURE_PARAM = /^(?:token|access_token|refresh_token|auth|authorization|auth_token|id_token|session|sessionid|sid|jwt|secret|client_secret|password|passcode|email|signature|sig|api_key|apikey|samlresponse|ticket|assertion|credential|.+_(?:token|secret|password|signature)|x-amz-.+|x-goog-.+)$/i;
const AUTH_CHALLENGE_CAPTURE_PARAM = /^(?:(?:__)?cf_chl_.+|__cf_bm|cf_clearance|cf-turnstile-response|(?:g-recaptcha|h-captcha)-response|(?:captcha|turnstile|auth_challenge|challenge)_token)$/i;
const AMBIGUOUS_SECRET_CAPTURE_PARAM = /^(?:code|state|key)$/i;
const bytesToBase64 = (value) => {
  let binary = "";
  for (let offset = 0; offset < value.length; offset += 0x8000) {
    binary += String.fromCharCode(...value.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
};
const base64ToBytes = (value) => Uint8Array.from(atob(value), (character) => character.charCodeAt(0));

const sanitizeCapturedUrl = (value) => {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error("Only a public HTTPS source can be saved.");
  }
  parsed.hash = "";
  for (const [key, queryValue] of [...parsed.searchParams.entries()]) {
    const highEntropySecret = AMBIGUOUS_SECRET_CAPTURE_PARAM.test(key)
      && queryValue.length >= 24
      && /^[A-Za-z0-9+/._~=-]+$/.test(queryValue);
    if (TRACKING_CAPTURE_PARAM.test(key) || SECRET_CAPTURE_PARAM.test(key)
        || AUTH_CHALLENGE_CAPTURE_PARAM.test(key) || highEntropySecret) {
      parsed.searchParams.delete(key);
    }
  }
  return parsed.href;
};

const boundedCaptureText = (value, limit) => String(value ?? "")
  .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, " ")
  .replace(/\s+/g, " ")
  .trim()
  .slice(0, limit);

const apiErrorMessage = (body, fallback) => {
  if (typeof body?.detail?.message === "string") return body.detail.message;
  if (typeof body?.detail === "string") return body.detail;
  return fallback;
};

const captureApiError = (status, body, fallback) => {
  const error = new Error(apiErrorMessage(body, fallback));
  error.apiStatus = status;
  error.apiCode = typeof body?.detail?.code === "string" ? body.detail.code : null;
  return error;
};

const sourceFetch = (url, options, timeout = SOURCE_FETCH_TIMEOUT_MS) => fetch(url, {
  ...options,
  signal: AbortSignal.timeout(timeout),
});

const isPublicIpv4 = (host) => {
  const parts = host.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return null;
  const octets = parts.map(Number);
  if (octets.some((part) => part > 255)) return false;
  const [a, b, c] = octets;
  return !(
    a === 0 || a === 10 || a === 127 || a >= 224 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19)) ||
    (a === 192 && b === 0 && c === 2) ||
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113)
  );
};

const isPublicPdfUrl = (value) => {
  let parsed;
  try { parsed = new URL(value); } catch { return false; }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) return false;
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!host || host.includes("%") || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return false;
  const ipv4 = isPublicIpv4(host);
  if (ipv4 !== null) return ipv4;
  if (/^[\d.]+$/.test(host) || /^(?:0x|0[0-7])/.test(host)) return false;
  if (host.includes(":")) {
    const compact = host.replace(/^0+(?=[0-9a-f])/g, "");
    if (compact === "::" || compact === "::1" || compact.startsWith("::ffff:")) return false;
    if (/^(?:fc|fd|fe[89ab]|ff)/.test(compact) || compact.startsWith("2001:db8:")) return false;
    return /^[23][0-9a-f]{0,3}:/.test(compact);
  }
  const labels = host.split(".");
  return labels.length >= 2 && labels.every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
};

const isDirectPdfUrl = (value) => {
  if (!isPublicPdfUrl(value)) return false;
  const parsed = new URL(value);
  return /\.pdf$/i.test(parsed.pathname) || (
    parsed.hostname.toLowerCase() === "arxiv.org" && /^\/pdf\/[^/]+\/?$/i.test(parsed.pathname)
  );
};

const exactResearchGatePdfForPage = (pageUrl, pdfUrl) => {
  let page;
  let pdf;
  try {
    page = new URL(pageUrl);
    pdf = new URL(pdfUrl);
  } catch { return false; }
  const pageHost = page.hostname.toLowerCase().replace(/^www\./, "");
  if (pageHost !== "researchgate.net" || pdf.origin !== page.origin) return false;
  if (/%(?:2f|5c)/i.test(pdf.pathname)) return false;
  const pageMatch = page.pathname.match(/^\/publication\/(\d+)(?:_|\/|$)/i);
  const pdfMatch = pdf.pathname.match(/^\/profile\/[^/\\]{1,180}\/publication\/(\d+)(?:_[^/\\]{1,1200})?\/links\/[A-Za-z0-9_-]{6,160}\/[^/\\]{1,400}\.pdf$/i);
  return Boolean(pageMatch && pdfMatch && pageMatch[1] === pdfMatch[1]);
};

const directPdfTitle = (value, responseFilename = "") => {
  try {
    const raw = responseFilename || new URL(value).pathname.split("/").filter(Boolean).pop() || "paper";
    try { return decodeURIComponent(raw).replace(/\.pdf$/i, ""); } catch { return raw.replace(/\.pdf$/i, ""); }
  } catch { return "paper"; }
};

const arxivLandingUrl = (value) => {
  if (!isPublicPdfUrl(value)) return "";
  const parsed = new URL(value);
  if (parsed.hostname.toLowerCase() !== "arxiv.org") return "";
  const match = parsed.pathname.match(/^\/pdf\/([^/]+?)\/?$/i);
  if (!match) return "";
  return `https://arxiv.org/abs/${match[1].replace(/\.pdf$/i, "")}`;
};

const arxivCanonicalUrl = (value) => {
  const landing = arxivLandingUrl(value) || value;
  try {
    const parsed = new URL(landing);
    if (parsed.hostname.toLowerCase() !== "arxiv.org") return landing;
    const match = parsed.pathname.match(/^\/abs\/([^/]+)\/?$/i);
    if (!match) return landing;
    return `https://arxiv.org/abs/${match[1].replace(/v\d+$/i, "")}`;
  } catch { return landing; }
};

const decodeHtml = (value) => String(value || "")
  .replace(/&#(\d+);/g, (_match, code) => String.fromCodePoint(Number(code)))
  .replace(/&#x([0-9a-f]+);/gi, (_match, code) => String.fromCodePoint(Number.parseInt(code, 16)))
  .replace(/&(?:amp|#38);/gi, "&")
  .replace(/&(?:quot|#34);/gi, '"')
  .replace(/&(?:apos|#39);/gi, "'")
  .replace(/&(?:lt|#60);/gi, "<")
  .replace(/&(?:gt|#62);/gi, ">");

const cleanMetadataText = (value, limit) => decodeHtml(value)
  .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, " ")
  .replace(/\s+/g, " ")
  .trim()
  .slice(0, limit);

const contentDispositionFilename = (value) => {
  const header = String(value || "").slice(0, 2000);
  const encoded = header.match(/(?:^|;)\s*filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1];
  const quoted = header.match(/(?:^|;)\s*filename\s*=\s*"([^"]*)"/i)?.[1];
  const bare = header.match(/(?:^|;)\s*filename\s*=\s*([^;]+)/i)?.[1];
  let candidate = encoded || quoted || bare || "";
  if (encoded) {
    try { candidate = decodeURIComponent(encoded); } catch { candidate = ""; }
  }
  return cleanMetadataText(candidate, 300)
    .replace(/[\\/]/g, " ")
    .replace(/^\.+/, "")
    .trim();
};

const tagAttributes = (tag) => {
  const attributes = Object.create(null);
  const pattern = /([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g;
  for (const match of tag.matchAll(pattern)) {
    attributes[match[1].toLowerCase()] = match[2] ?? match[3] ?? match[4] ?? "";
  }
  return attributes;
};

const metadataFromHtml = (html, landingUrl, pdfUrl) => {
  const values = new Map();
  for (const tag of html.match(/<meta\b[^>]{0,4000}>/gi)?.slice(0, 500) || []) {
    const attributes = tagAttributes(tag);
    const key = String(attributes.name || attributes.property || "").toLowerCase();
    if (!key || !("content" in attributes)) continue;
    const current = values.get(key) || [];
    if (current.length < 50) current.push(attributes.content);
    values.set(key, current);
  }
  const first = (...keys) => {
    for (const key of keys) {
      const value = values.get(key)?.[0];
      if (value) return value;
    }
    return "";
  };
  const authors = (values.get("citation_author") || values.get("dc.creator") || [])
    .map((value) => cleanMetadataText(value, 200)).filter(Boolean).slice(0, 50);
  const title = cleanMetadataText(first("citation_title", "dc.title", "og:title"), 500) || directPdfTitle(pdfUrl);
  const firstPage = first("citation_firstpage", "prism.startingpage");
  const lastPage = first("citation_lastpage", "prism.endingpage");
  const keywords = [...new Set((values.get("citation_keywords") || values.get("keywords") || [])
    .flatMap((value) => value.split(/[;,]/))
    .map((value) => cleanMetadataText(value, 100))
    .filter(Boolean))].slice(0, 50);
  const arxivId = (() => {
    try {
      const match = new URL(landingUrl).pathname.match(/^\/abs\/([^/]+)\/?$/i);
      return match?.[1]?.replace(/v\d+$/i, "") || "";
    } catch { return ""; }
  })();
  const metadata = {
    container_title: cleanMetadataText(first("citation_journal_title", "citation_conference_title", "citation_book_title", "prism.publicationname", "dc.source"), 500) || null,
    volume: cleanMetadataText(first("citation_volume", "prism.volume"), 100) || null,
    issue: cleanMetadataText(first("citation_issue", "prism.number"), 100) || null,
    pages: cleanMetadataText(first("citation_pages", "prism.pagerange") || [firstPage, lastPage].filter(Boolean).join("–"), 100) || null,
    publisher: cleanMetadataText(first("citation_publisher", "dc.publisher"), 300) || null,
    language: cleanMetadataText(first("citation_language", "dc.language"), 80) || null,
    license: cleanMetadataText(first("citation_license_url", "dc.rights"), 200) || null,
    isbn: cleanMetadataText(first("citation_isbn", "prism.isbn"), 100) || null,
    issn: cleanMetadataText(first("citation_issn", "prism.issn"), 100) || null,
    arxiv_id: cleanMetadataText(arxivId || first("citation_arxiv_id", "eprints.id"), 80) || null,
    keywords,
    item_type: "ScholarlyArticle",
    subtitle: cleanMetadataText(first("citation_subtitle", "prism.subtitle", "dc.alternative"), 500) || null,
    short_title: cleanMetadataText(first("citation_short_title", "dc.title.alternative"), 500) || null,
    series_title: cleanMetadataText(first("citation_series_title", "prism.seriestitle"), 500) || null,
    series_number: cleanMetadataText(first("citation_series_number", "prism.seriesnumber"), 100) || null,
    edition: cleanMetadataText(first("citation_edition", "prism.edition"), 100) || null,
    publisher_place: cleanMetadataText(first("citation_publisher_place", "citation_publication_place"), 300) || null,
    accessed_at: cleanMetadataText(first("citation_access_date", "dc.date.accessioned"), 40)
      || new Date().toISOString().slice(0, 10),
    archive: cleanMetadataText(first("citation_archive", "eprints.repository_name") || (arxivId ? "arXiv" : ""), 300) || null,
    archive_location: cleanMetadataText(first("citation_archive_location", "eprints.repository_url"), 500) || null,
    citation_key: cleanMetadataText(first("citation_key", "citation_citation_key"), 200) || null,
    format: cleanMetadataText(first("citation_format", "dc.format"), 200) || null,
    call_number: cleanMetadataText(first("citation_call_number", "dc.identifier.callnumber"), 200) || null,
    pmid: cleanMetadataText(first("citation_pmid", "pmid", "pubmed_id"), 80) || null,
    pmcid: cleanMetadataText(first("citation_pmcid", "pmcid"), 80) || null,
    extra: cleanMetadataText(first("citation_extra", "eprints.note"), 2000) || null,
  };
  const metadataFields = [
    title && "citation_title",
    authors.length && "citation_author",
    first("citation_publication_date", "citation_date", "dc.date") && "citation_date",
    first("citation_doi", "dc.identifier") && "citation_doi",
    first("citation_abstract", "description", "og:description") && "citation_abstract",
    "pdf_url",
  ].filter(Boolean);
  for (const [key, value] of Object.entries(metadata)) {
    if (Array.isArray(value) ? value.length : value) metadataFields.push(key);
  }
  return {
    url: pdfUrl,
    landingUrl,
    canonicalUrl: arxivCanonicalUrl(landingUrl),
    title,
    siteName: "arXiv",
    authors,
    publishedAt: cleanMetadataText(first("citation_publication_date", "citation_date", "dc.date"), 40) || null,
    description: cleanMetadataText(first("citation_abstract", "description", "og:description"), 2000),
    selectedExcerpt: "",
    doi: cleanMetadataText(first("citation_doi", "dc.identifier"), 300),
    pdfUrl,
    sourceKind: "paper",
    metadata,
    metadataFields,
  };
};

async function readBoundedResponse(response, maximum, tooLargeMessage) {
  if (!response.ok || !response.body) throw new Error("The source is not directly available without browser credentials.");
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > maximum) throw new Error(tooLargeMessage);
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.length;
    if (length > maximum) { await reader.cancel(); throw new Error(tooLargeMessage); }
    chunks.push(value);
  }
  const content = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.length; }
  return content;
}

const DSPACE_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";

const dspaceBitstreamRoute = (value) => {
  try {
    const parsed = new URL(value);
    const match = parsed.pathname.match(new RegExp(`^/server/api/core/bitstreams/(${DSPACE_UUID})/content/?$`, "i"));
    if (!match) return null;
    return { origin: parsed.origin, uuid: match[1].toLowerCase() };
  } catch { return null; }
};

const dspaceDownloadContentUrl = (value) => {
  try {
    const parsed = new URL(value);
    const match = parsed.pathname.match(new RegExp(`^/bitstreams/(${DSPACE_UUID})/download/?$`, "i"));
    if (!match || parsed.search || parsed.hash) return "";
    return `${parsed.origin}/server/api/core/bitstreams/${match[1].toLowerCase()}/content`;
  } catch { return ""; }
};

const exactDspaceLink = (value, origin, pathname) => {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:"
      && parsed.origin === origin
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash
      && parsed.pathname === pathname
      ? parsed.href
      : "";
  } catch { return ""; }
};

async function dspaceJson(url) {
  const response = await sourceFetch(
    url,
    {
      headers: { Accept: "application/hal+json, application/json" },
      credentials: "omit",
      referrerPolicy: "no-referrer",
      redirect: "error",
    },
    DSPACE_FETCH_TIMEOUT_MS,
  );
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (!contentType.includes("json")) throw new Error("The repository metadata endpoint did not return JSON.");
  const content = await readBoundedResponse(
    response,
    MAX_DSPACE_METADATA_BYTES,
    "The repository metadata response is too large.",
  );
  let parsed;
  try { parsed = JSON.parse(new TextDecoder().decode(content)); } catch {
    throw new Error("The repository metadata response is invalid.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("The repository metadata response is invalid.");
  }
  return parsed;
}

const dspaceValues = (item, key, limit = 50, valueLimit = 2000) => {
  const values = Array.isArray(item?.metadata?.[key]) ? item.metadata[key] : [];
  return values.slice(0, limit)
    .map((entry) => typeof entry?.value === "string" ? cleanMetadataText(entry.value, valueLimit) : "")
    .filter(Boolean);
};

const dspaceDoi = (item) => {
  for (const value of dspaceValues(item, "dc.identifier.doi", 10, 300)) {
    const normalized = value
      .replace(/^\s*doi\s*:\s*/i, "")
      .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
      .replace(/[.,;:]+$/g, "");
    if (/^10\.\d{4,9}\/\S+$/i.test(normalized)) return normalized;
  }
  return "";
};

async function dspacePaperMetadata(direct, responseFilename) {
  const route = dspaceBitstreamRoute(direct.href);
  if (!route) return null;
  const bitstreamPath = `/server/api/core/bitstreams/${route.uuid}`;
  const bitstream = await dspaceJson(`${route.origin}${bitstreamPath}`);
  if (String(bitstream.uuid || "").toLowerCase() !== route.uuid
      || bitstream.type !== "bitstream"
      || bitstream.bundleName !== "ORIGINAL") {
    throw new Error("The repository bitstream metadata did not match the active PDF.");
  }
  const bundleUrl = exactDspaceLink(bitstream?._links?.bundle?.href, route.origin, `${bitstreamPath}/bundle`);
  if (!bundleUrl) throw new Error("The repository bundle link was not trusted.");
  const bundle = await dspaceJson(bundleUrl);
  const bundleUuid = String(bundle.uuid || "").toLowerCase();
  if (!new RegExp(`^${DSPACE_UUID}$`, "i").test(bundleUuid) || bundle.type !== "bundle") {
    throw new Error("The repository bundle metadata was invalid.");
  }
  const itemPath = `/server/api/core/bundles/${bundleUuid}/item`;
  const itemUrl = exactDspaceLink(bundle?._links?.item?.href, route.origin, itemPath);
  if (!itemUrl) throw new Error("The repository item link was not trusted.");
  const item = await dspaceJson(itemUrl);
  const itemUuid = String(item.uuid || item.id || "").toLowerCase();
  if (!new RegExp(`^${DSPACE_UUID}$`, "i").test(itemUuid)
      || item.type !== "item"
      || item.inArchive !== true
      || item.discoverable !== true
      || item.withdrawn !== false) {
    throw new Error("The repository item is not an available archived work.");
  }
  const selfPath = `/server/api/core/items/${itemUuid}`;
  if (!exactDspaceLink(item?._links?.self?.href, route.origin, selfPath)) {
    throw new Error("The repository item identity was not trusted.");
  }

  const title = dspaceValues(item, "dc.title", 1, 500)[0] || "";
  const authors = dspaceValues(item, "dc.contributor.author", 50, 200);
  const publishedAt = dspaceValues(item, "dc.date.issued", 1, 40)[0] || null;
  const description = dspaceValues(item, "dc.description.abstract", 1, 2000)[0] || "";
  const itemType = dspaceValues(item, "dc.type", 1, 80)[0] || "";
  const doi = dspaceDoi(item);
  const scholarlyType = /^(?:scholarly\s+article|journal\s+article|article|conference\s+paper|conference\s+article|preprint|report|technical\s+report|thesis|doctoral\s+thesis|master'?s\s+thesis|dissertation|working\s+paper|book\s+chapter)$/i
    .test(itemType.trim());
  if (!title || !scholarlyType) return null;

  const firstPage = dspaceValues(item, "prism.startingpage", 1, 40)[0] || "";
  const lastPage = dspaceValues(item, "prism.endingpage", 1, 40)[0] || "";
  const identifierUri = dspaceValues(item, "dc.identifier.uri", 1, 500)[0] || "";
  const publicIdentifier = isPublicPdfUrl(identifierUri) ? sanitizeCapturedUrl(identifierUri) : "";
  const landingUrl = `${route.origin}/items/${itemUuid}`;
  const metadata = {
    container_title: dspaceValues(item, "dc.relation.ispartof", 1, 500)[0] || null,
    volume: dspaceValues(item, "dc.citation.volume", 1, 100)[0] || null,
    issue: dspaceValues(item, "dc.citation.issue", 1, 100)[0] || null,
    pages: [firstPage, lastPage].filter(Boolean).join("–") || null,
    publisher: dspaceValues(item, "dc.publisher", 1, 300)[0] || null,
    language: dspaceValues(item, "dc.language.iso", 1, 80)[0] || null,
    license: dspaceValues(item, "dc.rights.uri", 1, 200)[0]
      || dspaceValues(item, "dc.rights", 1, 200)[0] || null,
    isbn: dspaceValues(item, "dc.identifier.isbn", 1, 100)[0] || null,
    issn: dspaceValues(item, "dc.identifier.issn", 1, 100)[0] || null,
    arxiv_id: null,
    keywords: [...new Set(dspaceValues(item, "dc.subject", 50, 100))],
    item_type: itemType || null,
    subtitle: dspaceValues(item, "dc.title.alternative", 1, 500)[0] || null,
    short_title: null,
    series_title: null,
    series_number: null,
    edition: null,
    publisher_place: null,
    accessed_at: new Date().toISOString().slice(0, 10),
    archive: direct.hostname,
    archive_location: publicIdentifier || landingUrl,
    citation_key: null,
    format: "PDF",
    call_number: null,
    pmid: null,
    pmcid: null,
    extra: null,
  };
  const metadataFields = [
    "pdf_url", "dspace.dc.title",
    authors.length && "dspace.dc.contributor.author",
    publishedAt && "dspace.dc.date.issued",
    description && "dspace.dc.description.abstract",
    doi && "dspace.dc.identifier.doi",
    itemType && "dspace.dc.type",
    metadata.container_title && "dspace.dc.relation.ispartof",
    metadata.pages && "dspace.prism.pages",
    metadata.license && "dspace.dc.rights",
    metadata.keywords.length && "dspace.dc.subject",
  ].filter(Boolean);
  return {
    url: direct.href,
    landingUrl,
    canonicalUrl: landingUrl,
    title,
    siteName: direct.hostname,
    authors,
    publishedAt,
    description,
    selectedExcerpt: "",
    doi,
    pdfUrl: direct.href,
    sourceKind: "paper",
    metadata,
    metadataFields,
  };
}

async function probePdfResponse(url, credentials) {
  if (!isPublicPdfUrl(url)) return { verified: false, filename: "" };
  try {
    const options = {
      method: "GET",
      headers: { Range: "bytes=0-4" },
      credentials,
      referrerPolicy: "no-referrer",
      redirect: "error",
    };
    let response;
    try { response = await sourceFetch(url, options, PDF_PROBE_TIMEOUT_MS); } catch (error) {
      if (!isDirectPdfUrl(url)) throw error;
    }
    if ((!response?.ok || !response.body) && isDirectPdfUrl(url)) {
      try { await response?.body?.cancel?.(); } catch { /* best-effort disposal before retry */ }
      response = await sourceFetch(url, { ...options, headers: {} }, PDF_PROBE_TIMEOUT_MS);
    }
    if (!response.ok || !response.body) return { verified: false, filename: "" };
    const reader = response.body.getReader();
    let prefix = "";
    while (prefix.length < 5) {
      const { done, value } = await reader.read();
      if (done) break;
      prefix += new TextDecoder().decode(value.subarray(0, 5 - prefix.length));
    }
    await reader.cancel().catch(() => undefined);
    return {
      verified: prefix === "%PDF-",
      filename: contentDispositionFilename(response.headers.get("content-disposition")),
    };
  } catch {
    return { verified: false, filename: "" };
  }
}

async function probePublicPdf(url) {
  return probePdfResponse(url, "omit");
}

async function probeExactActivePdf(url, activeTabUrl) {
  let direct;
  let active;
  try {
    direct = new URL(url);
    active = new URL(activeTabUrl);
  } catch { return { verified: false, filename: "" }; }
  if (direct.href !== active.href || !isDirectPdfUrl(direct.href)) {
    return { verified: false, filename: "" };
  }
  // This is the only worker request that may use browser credentials. It is
  // bound to the exact, user-reviewed active PDF URL and never follows a
  // redirect, so credentials cannot move to a publisher IdP or CDN.
  return probePdfResponse(direct.href, "include");
}

async function inspectDirectPdf(url, activeTabUrl) {
  const direct = new URL(url);
  if (direct.href !== new URL(activeTabUrl).href || !isPublicPdfUrl(direct.href)) {
    throw new Error("The active tab is not a supported public PDF.");
  }
  let probe = await probePublicPdf(direct.href);
  if (!probe.verified) probe = await probeExactActivePdf(direct.href, activeTabUrl);
  if (!probe.verified) {
    throw new Error("The active tab is not a supported public PDF.");
  }
  const landingUrl = arxivLandingUrl(direct.href);
  if (landingUrl) {
    try {
      const response = await sourceFetch(landingUrl, { credentials: "omit", referrerPolicy: "no-referrer", redirect: "error" });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.toLowerCase().includes("html")) throw new Error("The paper metadata page did not return HTML.");
      const html = new TextDecoder().decode(await readBoundedResponse(response, MAX_METADATA_HTML_BYTES, "The paper metadata page is too large."));
      return metadataFromHtml(html, landingUrl, direct.href);
    } catch { /* keep an honest editable PDF-only preview */ }
  }
  try {
    const dspaceCapture = await dspacePaperMetadata(direct, probe.filename);
    if (dspaceCapture) return dspaceCapture;
  } catch { /* keep an honest PDF-only preview if repository metadata is unavailable */ }
  return {
    url: direct.href,
    landingUrl,
    canonicalUrl: landingUrl ? arxivCanonicalUrl(landingUrl) : direct.href,
    title: directPdfTitle(direct.href, probe.filename),
    siteName: direct.hostname,
    authors: [],
    publishedAt: null,
    description: "",
    selectedExcerpt: "",
    doi: "",
    pdfUrl: direct.href,
    sourceKind: landingUrl ? "paper" : "document",
    metadata: {
      container_title: null,
      volume: null,
      issue: null,
      pages: null,
      publisher: null,
      language: null,
      license: null,
      isbn: null,
      issn: null,
      arxiv_id: null,
      keywords: [],
      item_type: landingUrl ? "ScholarlyArticle" : "document",
      subtitle: null,
      short_title: null,
      series_title: null,
      series_number: null,
      edition: null,
      publisher_place: null,
      accessed_at: new Date().toISOString().slice(0, 10),
      archive: landingUrl ? "arXiv" : null,
      archive_location: null,
      citation_key: null,
      format: "PDF",
      call_number: null,
      pmid: null,
      pmcid: null,
      extra: null,
    },
    metadataFields: ["pdf_url"],
  };
}

async function verifiedPdfTarget(payload, activeTab) {
  if (!activeTab?.id || !activeTab.url) throw new Error("The active paper tab is unavailable.");
  const activeUrl = new URL(activeTab.url);
  const pageUrl = new URL(payload.page_url || "");
  const pdfUrl = new URL(payload.pdf_url || payload.source_url || "");
  if (activeUrl.href !== pageUrl.href) throw new Error("The active tab changed after the preview. Review it again before uploading.");
  if (!isPublicPdfUrl(pdfUrl.href)) throw new Error("Only a public HTTPS PDF can be uploaded.");
  if (pdfUrl.href === activeUrl.href) {
    // The following bounded full fetch verifies `%PDF-` before any upload.
    return {
      url: pdfUrl.href,
      linked: false,
      activeDirect: isDirectPdfUrl(pdfUrl.href),
      authenticatedFallback: false,
      tabId: activeTab.id,
      pageUrl: activeUrl.href,
    };
  }
  if (!isPublicPdfUrl(activeUrl.href)) throw new Error("The active landing page is not public HTTPS.");
  if (pdfUrl.origin !== activeUrl.origin) {
    throw new Error("Open the detected PDF in its own tab and confirm it there before uploading.");
  }
  const activeHost = activeUrl.hostname.toLowerCase().replace(/^www\./, "");
  if (activeHost === "researchgate.net" && !exactResearchGatePdfForPage(activeUrl.href, pdfUrl.href)) {
    throw new Error("This ResearchGate PDF does not match the active publication.");
  }
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: activeTab.id }, files: ["content-script.js"] });
  if (payload.capture_kind !== "paper"
      || result?.sourceKind !== "paper"
      || !result?.pdfUrl
      || !result?.pdfCandidate?.url
      || new URL(result.url).href !== activeUrl.href
      || new URL(result.pdfUrl).href !== pdfUrl.href
      || new URL(result.pdfCandidate.url).href !== pdfUrl.href) {
    throw new Error("This PDF is no longer the attachment detected on the active paper page.");
  }
  if (activeHost === "researchgate.net"
      && (!exactResearchGatePdfForPage(activeUrl.href, result.pdfUrl)
        || !exactResearchGatePdfForPage(activeUrl.href, result.pdfCandidate.url))) {
    throw new Error("This ResearchGate PDF does not match the active publication.");
  }
  const authenticatedFallback = Boolean(
    result.pdfCandidate.authenticatedFallback === true,
  );
  const resolvedUrl = dspaceDownloadContentUrl(pdfUrl.href) || pdfUrl.href;
  return {
    url: resolvedUrl,
    advertisedUrl: pdfUrl.href,
    linked: true,
    authenticatedFallback,
    tabId: activeTab.id,
    pageUrl: activeUrl.href,
  };
}

async function assertActivePaperTab(tabId, pageUrl) {
  const tab = await chrome.tabs.get(tabId);
  if (tab?.active !== true || !tab?.url || new URL(tab.url).href !== new URL(pageUrl).href) {
    throw new Error("The active paper tab changed during the PDF transfer. Try again from the paper page.");
  }
}

async function probeLinkedPdf(url) {
  try {
    const response = await sourceFetch(url, {
      method: "GET",
      headers: { Range: "bytes=0-4" },
      credentials: "omit",
      referrerPolicy: "no-referrer",
      redirect: "error",
    }, PDF_PROBE_TIMEOUT_MS);
    const status = Number(response.status || 0);
    if (!response.ok || !response.body) {
      try { await response.body?.cancel?.(); } catch { /* best-effort response disposal */ }
      return { verified: false, status };
    }
    const reader = response.body.getReader();
    let prefix = new Uint8Array(0);
    while (prefix.length < 5) {
      const { done, value } = await reader.read();
      if (done) break;
      const required = Math.min(5 - prefix.length, value.length);
      const next = new Uint8Array(prefix.length + required);
      next.set(prefix);
      next.set(value.subarray(0, required), prefix.length);
      prefix = next;
    }
    await reader.cancel().catch(() => undefined);
    return { verified: new TextDecoder().decode(prefix) === "%PDF-", status };
  } catch {
    return { verified: false, status: 0 };
  }
}

async function fetchPdf(url, credentials = "omit") {
  if (credentials !== "omit" && credentials !== "include") {
    throw new Error("Unsupported PDF credential mode.");
  }
  const response = await sourceFetch(
    url,
    { credentials, referrerPolicy: "no-referrer", redirect: "error" },
    PDF_FETCH_TIMEOUT_MS,
  );
  const status = Number(response.status || 0);
  if (!response.ok || !response.body) {
    try { await response.body?.cancel?.(); } catch { /* best-effort response disposal */ }
    return { content: null, status };
  }
  const content = await readBoundedResponse(response, MAX_PDF_BYTES, "This PDF is larger than 50 MiB.");
  if (new TextDecoder().decode(content.subarray(0, 5)) !== "%PDF-") {
    throw new Error("The confirmed link did not return a PDF.");
  }
  return { content, status };
}

async function fetchPdfFromActiveTab(target) {
  await assertActivePaperTab(target.tabId, target.pageUrl);
  await chrome.scripting.executeScript({ target: { tabId: target.tabId }, files: ["pdf-fetcher.js"] });
  await assertActivePaperTab(target.tabId, target.pageUrl);
  const requestId = crypto.randomUUID();
  const content = await new Promise((resolve, reject) => {
    const port = chrome.tabs.connect(target.tabId, { name: "six-sentences-pdf-fetch" });
    const chunks = [];
    let totalBytes = 0;
    let finished = false;
    const timeout = setTimeout(() => finish(new Error("The PDF transfer did not finish within 2 minutes. Try again.")), PDF_FETCH_TIMEOUT_MS + 2_000);
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      try { port.disconnect(); } catch { /* already disconnected */ }
      if (error) reject(error);
      else resolve(value);
    };
    port.onMessage.addListener((message) => {
      if (finished || message?.requestId !== requestId) return;
      if (message.type === "error") {
        finish(new Error(boundedCaptureText(message.error, 500) || "Could not read the protected PDF."));
        return;
      }
      if (message.type === "chunk") {
        if (chunks.length >= MAX_TAB_PDF_CHUNKS
            || !Number.isInteger(message.byteLength)
            || message.byteLength < 1
            || message.byteLength > TAB_PDF_CHUNK_BYTES
            || typeof message.data !== "string"
            || message.data.length > Math.ceil(TAB_PDF_CHUNK_BYTES / 3) * 4 + 4) {
          finish(new Error("The protected PDF stream exceeded its transfer bounds."));
          return;
        }
        let chunk;
        try { chunk = base64ToBytes(message.data); } catch {
          finish(new Error("The protected PDF stream was invalid."));
          return;
        }
        if (chunk.length !== message.byteLength || totalBytes + chunk.length > MAX_PDF_BYTES) {
          finish(new Error("The protected PDF stream exceeded its transfer bounds."));
          return;
        }
        chunks.push(chunk);
        totalBytes += chunk.length;
        return;
      }
      if (message.type === "done") {
        if (!Number.isInteger(message.totalBytes) || message.totalBytes !== totalBytes) {
          finish(new Error("The protected PDF stream ended unexpectedly."));
          return;
        }
        const combined = new Uint8Array(totalBytes);
        let offset = 0;
        for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.length; }
        if (new TextDecoder().decode(combined.subarray(0, 5)) !== "%PDF-") {
          finish(new Error("The protected link did not return a PDF."));
          return;
        }
        finish(null, combined);
      }
    });
    port.onDisconnect.addListener(() => {
      if (!finished) finish(new Error("The protected PDF transfer was interrupted. Try again."));
    });
    port.postMessage({
      type: "fetchPdf",
      requestId,
      url: target.url,
      pageUrl: target.pageUrl,
    });
  });
  await assertActivePaperTab(target.tabId, target.pageUrl);
  return content;
}

async function confirmedPdf(payload, activeTab) {
  const verified = await verifiedPdfTarget(payload, activeTab);
  try {
    let content = null;
    if (verified.linked) {
      await assertActivePaperTab(verified.tabId, verified.pageUrl);
      const probe = await probeLinkedPdf(verified.url);
      if (!probe.verified && [0, 401, 403].includes(probe.status) && verified.authenticatedFallback) {
        content = await fetchPdfFromActiveTab(verified);
      } else if (!probe.verified && ![400, 405, 416].includes(probe.status)) {
        throw new Error("The linked file did not return a verified PDF.");
      }
    }
    if (!content) {
      let fetched;
      try {
        fetched = await fetchPdf(verified.url);
      } catch (error) {
        if (error?.name === "TimeoutError"
            || error?.name === "AbortError"
            || /larger than 50 MiB|exceeded its transfer bounds/i.test(String(error?.message || ""))
            || !verified.activeDirect) {
          throw error;
        }
        fetched = { content: null, status: 0 };
      }
      if (!fetched.content && [401, 403].includes(fetched.status) && verified.authenticatedFallback) {
        content = await fetchPdfFromActiveTab(verified);
      } else if (!fetched.content && verified.activeDirect) {
        await assertActivePaperTab(verified.tabId, verified.pageUrl);
        const credentialed = await fetchPdf(verified.url, "include");
        if (!credentialed.content) {
          throw new Error("The source is not directly available from the active PDF tab.");
        }
        content = credentialed.content;
      } else if (!fetched.content) {
        throw new Error("The source is not directly available without browser credentials.");
      } else {
        content = fetched.content;
      }
    }
    if (verified.linked || verified.activeDirect) {
      await assertActivePaperTab(verified.tabId, verified.pageUrl);
    }
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", content))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return { content_base64: bytesToBase64(content), sha256: digest, resolved_url: verified.url };
  } catch (error) {
    if (error?.name === "TimeoutError" || error?.name === "AbortError") {
      throw new Error("The PDF transfer did not finish within 2 minutes. Try again.");
    }
    throw error;
  }
}

async function connectBrowser() {
  const verifier = base64url(bytes(32));
  const state = base64url(bytes(24));
  const challenge = base64url(await sha256(verifier));
  const redirectUri = chrome.identity.getRedirectURL();
  const query = new URLSearchParams({
    connect: "browser-capture", code_challenge: challenge, state,
    redirect_uri: redirectUri, device_name: `Chrome · ${navigator.platform || "Browser"}`
  });
  const callback = await chrome.identity.launchWebAuthFlow({
    url: `${APP_ORIGIN}/library?${query}`,
    interactive: true
  });
  if (!callback) throw new Error("Browser connection was cancelled.");
  const returned = new URL(callback);
  if (returned.origin + returned.pathname !== new URL(redirectUri).origin + new URL(redirectUri).pathname) throw new Error("Unexpected pairing callback.");
  if (returned.searchParams.get("state") !== state) throw new Error("Pairing state did not match.");
  const code = returned.searchParams.get("code");
  if (!code) throw new Error("Pairing code is missing.");
  const response = await fetch(`${API_ORIGIN}/browser-capture/pair/exchange`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, code_verifier: verifier, state })
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.api_key) throw new Error(body?.detail || "Could not finish Browser Capture pairing.");
  await chrome.storage.local.set({ apiKey: body.api_key });
  return { ok: true, connected: true };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!TRUSTED_CONTEXTS.has(sender.id) || sender.tab || !sender.url?.startsWith(chrome.runtime.getURL(""))) {
    sendResponse({ ok: false, error: "Untrusted extension context." });
    return;
  }
  (async () => {
    await storageReady;
    const stored = await chrome.storage.local.get(["apiKey"]);
    const apiBase = API_ORIGIN;
    if (message.type === "status") return { ok: true, connected: Boolean(stored.apiKey) };
    if (message.type === "connect") return connectBrowser();
    if (message.type === "inspectDirectPdf") {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!activeTab?.url) throw new Error("The active PDF tab is unavailable.");
      return { ok: true, capture: await inspectDirectPdf(message.url, activeTab.url) };
    }
    if (message.type === "saveWeb") {
      const apiPayload = {
        url: sanitizeCapturedUrl(message.payload.url),
        canonical_url: sanitizeCapturedUrl(message.payload.canonical_url || message.payload.url),
        title: boundedCaptureText(message.payload.title, 500),
        site_name: boundedCaptureText(message.payload.site_name, 200),
        description: boundedCaptureText(message.payload.description, 2000),
        selected_excerpt: boundedCaptureText(message.payload.selected_excerpt, 4000),
        source_kind: "web",
        extension_version: boundedCaptureText(message.payload.extension_version, 32),
      };
      if (!apiPayload.title) throw new Error("This page does not expose a usable title.");
      const payloadDigest = base64url(await sha256(JSON.stringify(apiPayload)));
      const receiptKey = `pending:${payloadDigest}`;
      const pending = await chrome.storage.session.get([receiptKey]);
      const pendingReceipt = pending[receiptKey];
      const captureId = typeof pendingReceipt === "string"
        ? pendingReceipt
        : pendingReceipt?.captureId || crypto.randomUUID();
      const capturedAt = typeof pendingReceipt === "object" && pendingReceipt?.capturedAt
        ? pendingReceipt.capturedAt
        : new Date().toISOString();
      await chrome.storage.session.set({ [receiptKey]: { captureId, capturedAt } });
      const response = await fetch(`${apiBase}/browser-capture/web`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${stored.apiKey}` },
        body: JSON.stringify({
          ...apiPayload,
          capture_id: captureId,
          captured_at: capturedAt,
        })
      });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) await chrome.storage.local.remove(["apiKey"]);
      if (!response.ok) {
        if (response.status === 409) await chrome.storage.session.remove([receiptKey]);
        throw captureApiError(response.status, body, "Could not save this source.");
      }
      await chrome.storage.session.remove([receiptKey]);
      return { ok: true, result: body };
    }
    if (message.type === "saveCitation") {
      const apiPayload = {
        ...message.payload,
        source_url: sanitizeCapturedUrl(message.payload.source_url),
        canonical_url: sanitizeCapturedUrl(message.payload.canonical_url || message.payload.source_url),
      };
      const { capture_id: _ignoredCaptureId, captured_at: _ignoredCapturedAt, ...stablePayload } = apiPayload;
      const payloadDigest = base64url(await sha256(JSON.stringify(stablePayload)));
      const receiptKey = `pending-citation:${payloadDigest}`;
      const pending = await chrome.storage.session.get([receiptKey]);
      const pendingReceipt = pending[receiptKey];
      const captureId = typeof pendingReceipt === "string"
        ? pendingReceipt
        : pendingReceipt?.captureId || crypto.randomUUID();
      const capturedAt = typeof pendingReceipt === "object" && pendingReceipt?.capturedAt
        ? pendingReceipt.capturedAt
        : apiPayload.captured_at || new Date().toISOString();
      await chrome.storage.session.set({ [receiptKey]: { captureId, capturedAt } });
      const response = await fetch(`${apiBase}/browser-capture/citations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${stored.apiKey}` },
        body: JSON.stringify({ ...apiPayload, capture_id: captureId, captured_at: capturedAt })
      });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) await chrome.storage.local.remove(["apiKey"]);
      if (!response.ok) {
        if (response.status === 409) await chrome.storage.session.remove([receiptKey]);
        throw captureApiError(response.status, body, "Could not save this paper citation.");
      }
      await chrome.storage.session.remove([receiptKey]);
      return { ok: true, result: body };
    }
    if (message.type === "savePaper") {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const pdf = await confirmedPdf(message.payload, activeTab);
      const { resolved_url: resolvedPdfUrl, ...pdfUpload } = pdf;
      const apiPayload = {
        ...message.payload,
        page_url: undefined,
        capture_kind: undefined,
        source_url: sanitizeCapturedUrl(message.payload.source_url),
        canonical_url: sanitizeCapturedUrl(message.payload.canonical_url || message.payload.source_url),
        pdf_url: sanitizeCapturedUrl(resolvedPdfUrl || message.payload.pdf_url),
      };
      const { capture_id: _ignoredCaptureId, captured_at: _ignoredCapturedAt, ...stablePayload } = apiPayload;
      const payloadDigest = base64url(await sha256(JSON.stringify({ ...stablePayload, sha256: pdf.sha256 })));
      const receiptKey = `pending-pdf:${payloadDigest}`;
      const pending = await chrome.storage.session.get([receiptKey]);
      const pendingReceipt = pending[receiptKey];
      apiPayload.capture_id = typeof pendingReceipt === "string" ? pendingReceipt : pendingReceipt?.captureId || crypto.randomUUID();
      apiPayload.captured_at = typeof pendingReceipt === "object" && pendingReceipt?.capturedAt
        ? pendingReceipt.capturedAt
        : apiPayload.captured_at || new Date().toISOString();
      await chrome.storage.session.set({ [receiptKey]: {
        captureId: apiPayload.capture_id,
        capturedAt: apiPayload.captured_at,
      } });
      const response = await fetch(`${apiBase}/browser-capture/papers`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${stored.apiKey}` },
        body: JSON.stringify({
          ...apiPayload,
          ...pdfUpload,
        })
      });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) await chrome.storage.local.remove(["apiKey"]);
      if (!response.ok) {
        if (response.status === 409) await chrome.storage.session.remove([receiptKey]);
        throw captureApiError(response.status, body, "Could not save this PDF.");
      }
      await chrome.storage.session.remove([receiptKey]);
      return { ok: true, result: body };
    }
    if (message.type === "disconnect") {
      if (stored.apiKey) {
        const response = await fetch(`${apiBase}/browser-capture/device/revoke`, {
          method: "POST",
          headers: { Authorization: `Bearer ${stored.apiKey}` }
        });
        if (!response.ok && response.status !== 401) throw new Error("Could not revoke this Browser Capture device.");
      }
      await chrome.storage.local.remove(["apiKey"]);
      return { ok: true };
    }
    throw new Error("Unknown extension action.");
  })().then(sendResponse).catch((error) => sendResponse({
    ok: false,
    error: error.message,
    apiStatus: error.apiStatus ?? null,
    apiCode: error.apiCode ?? null,
  }));
  return true;
});
