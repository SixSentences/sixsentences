(() => {
  const MAX_JSON_LD_BYTES = 100_000;
  const MAX_JSON_LD_NODES = 250;
  const MAX_PDF_NODES = 250;
  const MAX_PDF_CANDIDATES = 5;
  const MAX_TEXT_SCAN_CHARS = 16_384;
  const text = (value, limit) => String(value ?? "")
    .slice(0, Math.min(MAX_TEXT_SCAN_CHARS, Math.max(limit, limit * 4)))
    .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
  const unique = (values, limit = values.length, valueLimit = 300) => {
    const seen = new Set();
    const result = [];
    for (const raw of values) {
      const value = text(raw, valueLimit);
      const identity = value.normalize("NFKC").toLocaleLowerCase();
      if (!value || seen.has(identity)) continue;
      seen.add(identity);
      result.push(value);
      if (result.length >= limit) break;
    }
    return result;
  };
  const metaNodes = (name) => document.querySelectorAll(
    `meta[name="${CSS.escape(name)}"],meta[property="${CSS.escape(name)}"]`,
  );
  const metaValues = (names, limit = 50) => {
    const values = [];
    for (const name of names) {
      for (const node of metaNodes(name)) {
        if (node?.content) values.push(text(node.content, 2000));
        if (values.length >= limit) return values.filter(Boolean);
      }
    }
    return values.filter(Boolean);
  };
  const meta = (names) => metaValues(names, 1)[0] || "";
  const safeHttpsUrl = (value) => {
    const raw = text(value, 4000);
    if (!raw) return "";
    try {
      const parsed = new URL(raw, location.href);
      if (parsed.protocol !== "https:" || parsed.username || parsed.password) return "";
      parsed.hash = "";
      return parsed.href;
    } catch { return ""; }
  };
  const typeNames = (item) => (Array.isArray(item?.["@type"]) ? item["@type"] : [item?.["@type"]])
    .filter(Boolean)
    .map((value) => String(value).split(/[\/#]/).pop());
  const jsonLd = () => {
    let nodes = 0;
    let aggregateChars = 0;
    const walk = (value, depth = 0) => {
      if (depth > 6 || nodes++ > MAX_JSON_LD_NODES) return null;
      if (Array.isArray(value)) return value.slice(0, 40).map((item) => walk(item, depth + 1));
      if (!value || typeof value !== "object") return value;
      const out = Object.create(null);
      for (const [key, item] of Object.entries(value)) {
        if (["__proto__", "constructor", "prototype"].includes(key)) continue;
        out[key] = walk(item, depth + 1);
      }
      return out;
    };
    const candidates = [];
    let scriptCount = 0;
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      if (scriptCount++ >= 20) break;
      if ((script.textContent?.length ?? 0) > MAX_JSON_LD_BYTES) continue;
      aggregateChars += script.textContent?.length ?? 0;
      if (aggregateChars > 300_000) break;
      try {
        const parsed = walk(JSON.parse(script.textContent || "null"));
        const roots = Array.isArray(parsed) ? parsed : [parsed];
        for (const root of roots) {
          const graph = Array.isArray(root?.["@graph"]) ? root["@graph"] : [root];
          candidates.push(...graph.filter((item) => item && typeof item === "object"));
        }
      } catch { /* Untrusted markup is ignored. */ }
    }
    const score = (item) => {
      const types = typeNames(item);
      if (types.some((value) => /ScholarlyArticle$/i.test(value))) return 4;
      if (types.includes("TechArticle") || types.includes("Report")) return 3;
      if (types.includes("Article")) return 2;
      return item?.headline || item?.name ? 1 : 0;
    };
    return candidates.sort((left, right) => score(right) - score(left))[0] || null;
  };
  const ld = jsonLd();
  const ldName = (value) => text(typeof value === "string" ? value : value?.name, 300);
  const ldValues = (value, limit = 50, valueLimit = 200) => {
    const raw = Array.isArray(value) ? value : value ? [value] : [];
    return unique(raw.slice(0, limit).map(ldName), limit, valueLimit);
  };
  const firstLdContainer = () => {
    const container = Array.isArray(ld?.isPartOf) ? ld.isPartOf[0] : ld?.isPartOf;
    return ldName(container);
  };
  const TITLE_META = [
    "citation_title", "bepress_citation_title", "prism.title", "dc.title", "DC.Title", "og:title",
  ];
  const BIBLIOGRAPHIC_TITLE_META = TITLE_META.filter((name) => name !== "og:title");
  const AUTHOR_META = [
    "citation_author", "bepress_citation_author", "prism.author", "dc.creator", "DC.Creator",
  ];
  const DATE_META = [
    "citation_publication_date", "bepress_citation_date", "citation_date", "prism.publicationDate",
    "dc.date.issued", "DC.date.issued", "dc.issued", "DC.issued", "dc.date", "article:published_time",
  ];
  const ABSTRACT_META = [
    "citation_abstract", "bepress_citation_abstract", "dc.description.abstract", "DC.Description.Abstract",
    "description", "og:description",
  ];
  const PDF_META = [
    "citation_pdf_url", "bepress_citation_pdf_url", "eprints.document_url", "dc.format.pdf",
  ];
  const TYPE_META = ["citation_type", "bepress_citation_type", "prism.genre", "dc.type", "DC.Type"];
  const normalizeDoi = (value) => {
    const raw = text(value, 300)
      .replace(/^\s*doi\s*:\s*/i, "")
      .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
    const match = raw.match(/^(10\.\d{4,9}\/\S+)/i);
    return match ? match[1].replace(/[),.;]+$/g, "") : "";
  };
  const ldIdentifiers = Array.isArray(ld?.identifier) ? ld.identifier : [ld?.identifier];
  const doi = [
    ...metaValues(["citation_doi", "bepress_citation_doi", "prism.doi", "dc.identifier", "DC.Identifier"]),
    ...ldIdentifiers.map((value) => typeof value === "string" ? value : value?.value || value?.["@id"]),
  ].map(normalizeDoi).find(Boolean) || "";
  const hasValidDoi = Boolean(doi);
  const bibliographicTitle = meta(BIBLIOGRAPHIC_TITLE_META);
  const bibliographicCorroboration = Boolean(meta([
    ...AUTHOR_META, ...DATE_META, "citation_doi", "bepress_citation_doi", ...PDF_META,
  ]));
  const ldTypes = typeNames(ld);
  const scholarlyJsonLd = ldTypes.some((value) => /ScholarlyArticle$/i.test(value));
  const technicalJsonLd = ldTypes.some((value) => /^(?:TechArticle|Report)$/i.test(value));
  let activeUrl;
  try { activeUrl = new URL(location.href); } catch { activeUrl = null; }
  const arxivMatch = activeUrl?.hostname.toLowerCase() === "arxiv.org"
    ? activeUrl.pathname.match(/^\/(?:abs|pdf)\/([^/]+?)(?:\.pdf)?\/?$/i)
    : null;
  const activeHost = activeUrl?.hostname.toLowerCase().replace(/^www\./, "") || "";
  const researchGatePublicationMatch = activeHost === "researchgate.net"
    ? (activeUrl?.pathname || "").match(/^\/publication\/(\d+)(?:_|\/|$)/i)
    : null;
  const researchGatePublication = Boolean(researchGatePublicationMatch);
  const researchGatePublicationId = researchGatePublicationMatch?.[1] || "";
  const researchGateTitle = meta(TITLE_META);
  const researchGateEvidence = Boolean(
    researchGatePublication
    && researchGateTitle
    && !/(?:just a moment|access denied|security check|captcha)/i.test(researchGateTitle)
    && (
      meta(AUTHOR_META)
      || meta(DATE_META)
      || text(meta(ABSTRACT_META), 2000).length >= 80
      || ldValues(ld?.author, 1).length
    )
  );
  const declaredScholarlyType = meta(TYPE_META);
  const scholarlyTypeSignal = /(?:article|conference\s*paper|journal\s*paper|preprint|report|thesis|dissertation)/i
    .test(declaredScholarlyType);
  const structuredScholarlyEvidence = Boolean(
    (scholarlyTypeSignal || technicalJsonLd)
    && (bibliographicTitle || ld?.headline || ld?.name)
    && (meta(AUTHOR_META) || meta(DATE_META) || meta(PDF_META) || ldValues(ld?.author, 1).length)
  );
  const sourceKind = hasValidDoi || scholarlyJsonLd
    || (bibliographicTitle && bibliographicCorroboration)
    || structuredScholarlyEvidence || researchGateEvidence || arxivMatch
    ? "paper"
    : "web";

  const authors = unique([
    ...metaValues(AUTHOR_META),
    ...ldValues(ld?.author),
  ], 50, 200);
  const canonicalRaw = document.querySelector('link[rel="canonical"]')?.href || location.href;
  let canonical = safeHttpsUrl(canonicalRaw) || safeHttpsUrl(location.href) || location.href;
  try {
    const parsed = new URL(canonical);
    const arxiv = parsed.hostname.toLowerCase() === "arxiv.org" && parsed.pathname.match(/^\/abs\/([^/]+)\/?$/i);
    if (arxiv) canonical = `https://arxiv.org/abs/${arxiv[1].replace(/v\d+$/i, "")}`;
  } catch { canonical = location.href; }
  const selected = text(window.getSelection()?.toString(), 4000);
  const rawTitle = text(
    meta(TITLE_META)
      || ld?.headline || ld?.name || document.title,
    500,
  );
  const title = text(researchGatePublication ? rawTitle.replace(/^\(PDF\)\s*/i, "") : rawTitle, 500);

  const researchGateCanonicalPdfPath = /^\/profile\/([^/\\]{1,180})\/publication\/(\d+)(_[^/\\]{1,1200})?\/links\/([A-Za-z0-9_-]{6,160})\/([^/\\]{1,400}\.pdf)$/i;
  const researchGateMalformedPdfPath = /^\/publication\/profile\/([^/\\]{1,180})\/publication\/(\d+)(_[^/\\]{1,1200})?\/links\/([A-Za-z0-9_-]{6,160})\/([^/\\]{1,400}\.pdf)$/i;
  const normalizePdfCandidateUrl = (value) => {
    let raw = text(value, 4000);
    if (!raw) return "";
    if (researchGatePublication && /^profile\//i.test(raw)) raw = `/${raw}`;
    let parsed;
    try { parsed = new URL(raw, location.href); } catch { return ""; }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return "";
    parsed.hash = "";
    if (!researchGatePublication || parsed.origin !== activeUrl?.origin) return parsed.href;
    if (/%(?:2f|5c)/i.test(parsed.pathname)) return "";
    const malformed = parsed.pathname.match(researchGateMalformedPdfPath);
    if (malformed) {
      if (malformed[2] !== researchGatePublicationId) return "";
      parsed.pathname = `/profile/${malformed[1]}/publication/${malformed[2]}${malformed[3] || ""}/links/${malformed[4]}/${malformed[5]}`;
    }
    const canonicalMatch = parsed.pathname.match(researchGateCanonicalPdfPath);
    if (canonicalMatch && canonicalMatch[2] !== researchGatePublicationId) return "";
    if (parsed.pathname.startsWith("/publication/profile/") && !malformed) return "";
    if (parsed.pathname.startsWith("/profile/") && /\/publication\/\d+/i.test(parsed.pathname) && !canonicalMatch) return "";
    return parsed.href;
  };

  // A page may advertise an expired, protected or cross-origin attachment. We
  // only return a bounded public-HTTPS candidate; the privileged worker still
  // verifies bytes independently and citation persistence never depends on it.
  const pdfCandidates = new Map();
  const addPdfCandidate = (value, score, authenticatedFallback = false) => {
    const url = normalizePdfCandidateUrl(value);
    if (!url) return;
    const previous = pdfCandidates.get(url);
    pdfCandidates.set(url, {
      url,
      score: Math.max(score, previous?.score || 0),
      authenticatedFallback: Boolean(authenticatedFallback || previous?.authenticatedFallback),
    });
  };
  const excludedPdfPath = /(?:^|\/)(?:supplements?|supporting[-_ ](?:information|material)|figures?|legal)(?:\/|$)|(?:^|[._-])(?:supplement(?:ary|al)?|supporting[-_](?:information|material)|figure[-_]?\d+|terms?[-_]of[-_](?:use|service)|privacy[-_]policy|license)(?:[._-]|$)|(?:^|\/)terms?\.pdf$/i;
  for (const candidate of metaValues(PDF_META, 10)) {
    let candidatePath = "";
    try { candidatePath = decodeURIComponent(new URL(candidate, location.href).pathname); } catch { /* invalid metadata */ }
    if (!excludedPdfPath.test(candidatePath)) addPdfCandidate(candidate, 120);
  }
  const pdfSelectors = [
    'link[type="application/pdf"][href]',
    'link[rel~="alternate"][href$=".pdf"]',
    'link[rel~="enclosure"][href]',
    'a[type="application/pdf"][href]',
    'a[download][href]',
    'a[href$=".pdf"]',
    'a[href*=".pdf?"]',
    'a[href*="/pdf/"]',
    'a[href*="/pdf?"]',
    'a[href*="/fulltext/"]',
    'a[href*="/full-text/"]',
    'a[href*="/download/"]',
    'a[href$="/download"]',
    'a[href*="/download?"]',
    'iframe[type="application/pdf"][src]',
    'embed[type="application/pdf"][src]',
    'object[type="application/pdf"][data]',
  ];
  const seenPdfNodes = new Set();
  let inspectedPdfNodes = 0;
  outer: for (const selector of pdfSelectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (inspectedPdfNodes++ >= MAX_PDF_NODES) break outer;
      if (seenPdfNodes.has(node)) continue;
      seenPdfNodes.add(node);
      const tagName = String(node?.tagName || "").toUpperCase();
      const attributeName = tagName === "OBJECT" ? "data" : tagName === "IFRAME" || tagName === "EMBED" ? "src" : "href";
      const rawAttribute = node?.getAttribute?.(attributeName) || "";
      const raw = rawAttribute || node?.href || node?.src || node?.data || "";
      const url = safeHttpsUrl(raw);
      if (!url) continue;
      const normalizedUrl = normalizePdfCandidateUrl(raw);
      if (!normalizedUrl) continue;
      let parsed;
      try { parsed = new URL(normalizedUrl); } catch { continue; }
      const type = text(node?.type || node?.getAttribute?.("type"), 100).toLowerCase();
      const rel = text(node?.rel || node?.getAttribute?.("rel"), 100).toLowerCase();
      const download = text(node?.download || node?.getAttribute?.("download"), 300);
      const label = text(
        node?.getAttribute?.("aria-label") || node?.getAttribute?.("title") || node?.textContent,
        200,
      );
      const normalizedLabel = label.replace(/[()[\]{}:|·—–_.-]+/g, " ").replace(/\s+/g, " ").trim();
      const requestOnly = /\b(?:request|order|buy|purchase)\b/i.test(normalizedLabel)
        && /\b(?:pdf|full\s*text|copy)\b/i.test(normalizedLabel);
      if (requestOnly) continue;
      let decodedPath = parsed.pathname;
      try { decodedPath = decodeURIComponent(parsed.pathname); } catch { /* keep the encoded path */ }
      const excludedPrimaryLabel = /\b(?:supplement(?:ary|al)?|supporting\s+(?:information|material|document)|terms?(?:\s+of\s+(?:use|service))|privacy\s+policy|legal\s+(?:notice|document)|license|copyright|brochure|media\s+kit|press\s+kit)\b/i.test(normalizedLabel);
      const hasPdfLabel = /\bpdf\b/i.test(normalizedLabel);
      const alwaysSecondaryRole = /\b(?:appendix|figure|table|poster|slides?|presentation)\b/i.test(normalizedLabel);
      const dataSecondaryRole = /\b(?:dataset|data\s+set)\b/i.test(normalizedLabel)
        && !/\b(?:paper|article|publication|full\s*text)\b/i.test(normalizedLabel);
      const explicitSecondaryLabel = hasPdfLabel && (alwaysSecondaryRole || dataSecondaryRole);
      if (excludedPrimaryLabel || explicitSecondaryLabel || excludedPdfPath.test(decodedPath)) continue;
      const pdfPath = /\.pdf$/i.test(parsed.pathname);
      const explicitPrimaryControl = /\b(?:download|read|view|open)\b.{0,60}\b(?:full\s*text\s+)?(?:(?:article|paper|publication|file)\s+)?pdf\b/i.test(normalizedLabel)
        || /\b(?:full\s*text|article|paper|publication)\s+(?:download\s+)?pdf\b/i.test(normalizedLabel)
        || /^pdf(?:\s+download)?$/i.test(normalizedLabel);
      const visiblePrimaryControl = Boolean(explicitPrimaryControl
        && String(node?.tagName || "").toUpperCase() === "A"
        && typeof node?.getClientRects === "function"
        && node.getClientRects().length > 0
        && (() => {
          try {
            if (node.closest?.('[hidden],[inert],[aria-hidden="true"]')) return false;
            if (typeof node.checkVisibility === "function") {
              return node.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
            }
            if (typeof getComputedStyle !== "function") return false;
            const style = getComputedStyle(node);
            return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
          } catch { return false; }
        })());
      const filenameLabel = /(?:^|\s)[^/\\]{1,160}\.pdf(?:\s|$)/i.test(label);
      const downloadPdf = /\.pdf$/i.test(download);
      const titleTokens = new Set(
        text(title, 500).toLocaleLowerCase().normalize("NFKD").match(/[\p{L}\p{N}]{4,}/gu) || [],
      );
      const filenameTokens = decodedPath.split("/").pop()?.replace(/\.pdf$/i, "")
        .toLocaleLowerCase().normalize("NFKD").match(/[\p{L}\p{N}]{4,}/gu) || [];
      const titleOverlap = new Set(filenameTokens.filter((token) => titleTokens.has(token))).size;
      const titleMatchedFile = pdfPath && titleOverlap >= (titleTokens.size <= 1 ? 1 : 2);
      const namedPdf = downloadPdf
        || explicitPrimaryControl
        || filenameLabel
        || /\bfull[- ]?text\s+pdf\b/i.test(label)
        || /^pdf$/i.test(label);
      const isAnchor = tagName === "A";
      const typedPdf = type === "application/pdf"
        && (!isAnchor || namedPdf || Boolean(download) || /\b(?:alternate|enclosure)\b/.test(rel));
      const relPdf = rel.split(/\s+/).some((value) => ["alternate", "enclosure"].includes(value))
        && (pdfPath || typedPdf);
      const researchGatePathMatch = parsed.pathname.match(researchGateCanonicalPdfPath);
      const researchGateFile = activeHost === "researchgate.net"
        && parsed.origin === activeUrl?.origin
        && researchGatePathMatch?.[2] === researchGatePublicationId
        && pdfPath && (explicitPrimaryControl || filenameLabel || downloadPdf);
      const dspaceDownload = sourceKind === "paper"
        && /^\/bitstreams\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\/download\/?$/i.test(parsed.pathname)
        && /\bdownload\b/i.test(label);
      const publisherPdfPath = /\/(?:pdf|full[-_]?text|download)(?:\/|$)/i.test(parsed.pathname);
      const scholarlyNamedPdf = sourceKind === "paper"
        && (explicitPrimaryControl || downloadPdf || titleMatchedFile)
        && (pdfPath || publisherPdfPath);
      if (!typedPdf && !relPdf && !researchGateFile && !dspaceDownload && !scholarlyNamedPdf) continue;
      let score = pdfPath ? 50 : 0;
      if (typedPdf) score += 35;
      if (downloadPdf) score += 15;
      if (namedPdf) score += 10;
      if (explicitPrimaryControl) score += 55;
      if (titleMatchedFile) score += 20;
      if (relPdf) score += 15;
      if (researchGateFile) score += 35;
      if (dspaceDownload) score += 60;
      if (parsed.origin === activeUrl?.origin) score += 10;
      const authenticatedFallback = Boolean(
        sourceKind === "paper"
        && visiblePrimaryControl
        && parsed.origin === activeUrl?.origin
        && (activeHost !== "researchgate.net" || researchGateFile),
      );
      addPdfCandidate(normalizedUrl, score, authenticatedFallback);
    }
  }
  const rankedPdfCandidates = [...pdfCandidates.values()]
    .sort((left, right) => right.score - left.score || left.url.localeCompare(right.url))
    .slice(0, MAX_PDF_CANDIDATES);
  const pdfCandidate = rankedPdfCandidates[0] || null;
  const pdfUrl = pdfCandidate?.url || "";

  const firstPage = meta([
    "citation_firstpage", "bepress_citation_firstpage", "prism.startingPage",
    "dc.citation.spage", "DC.citation.spage",
  ]);
  const lastPage = meta([
    "citation_lastpage", "bepress_citation_lastpage", "prism.endingPage",
    "dc.citation.epage", "DC.citation.epage",
  ]);
  const pages = meta(["citation_pages", "bepress_citation_pages", "prism.pageRange", "dc.citation.pages"])
    || text(ld?.pagination, 100)
    || [firstPage || text(ld?.pageStart, 40), lastPage || text(ld?.pageEnd, 40)].filter(Boolean).join("–");
  const keywordValues = metaValues([
    "citation_keywords", "bepress_citation_keywords", "keywords", "dc.subject", "DC.Subject",
  ])
    .flatMap((value) => value.split(/[;,]/));
  if (typeof ld?.keywords === "string") keywordValues.push(...ld.keywords.split(/[;,]/));
  else if (Array.isArray(ld?.keywords)) keywordValues.push(...ld.keywords);
  const arxivId = arxivMatch?.[1]?.replace(/v\d+$/i, "")
    || text(meta(["citation_arxiv_id", "eprints.id"]), 80);
  const scholarlyType = ldTypes.find((value) => /^(?:ScholarlyArticle|TechArticle|Report)$/i.test(value));
  const itemType = sourceKind === "paper"
    ? scholarlyType || text(declaredScholarlyType, 80)
      || (meta(["citation_conference_title", "bepress_citation_conference_title"])
        ? "conference_paper" : "")
    : ldTypes.find((value) => value !== "Thing") || "web_page";
  const metadata = {
    container_title: text(meta([
      "citation_journal_title", "bepress_citation_journal_title", "citation_conference_title",
      "bepress_citation_conference_title", "citation_book_title", "bepress_citation_book_title",
      "prism.publicationName", "dc.source", "DC.Source", "dc.relation.ispartof",
      "DC.relation.ispartof", "dcterms.isPartOf",
    ]) || firstLdContainer(), 500) || null,
    volume: text(meta([
      "citation_volume", "bepress_citation_volume", "prism.volume", "dc.citation.volume",
    ]) || ld?.volumeNumber, 100) || null,
    issue: text(meta([
      "citation_issue", "bepress_citation_issue", "prism.number", "dc.citation.issue",
    ]) || ld?.issueNumber, 100) || null,
    pages: text(pages, 100) || null,
    publisher: text(meta([
      "citation_publisher", "bepress_citation_publisher", "prism.publisher", "dc.publisher", "DC.Publisher",
    ]) || ldName(ld?.publisher), 300) || null,
    language: text(meta(["citation_language", "dc.language", "DC.Language"]) || ld?.inLanguage || document.documentElement?.lang, 80) || null,
    license: text(meta([
      "citation_license_url", "bepress_citation_license", "dc.rights", "DC.Rights",
    ]) || ld?.license, 200) || null,
    isbn: text(meta(["citation_isbn", "bepress_citation_isbn", "prism.isbn"]) || ld?.isbn, 100) || null,
    issn: text(meta(["citation_issn", "bepress_citation_issn", "prism.issn"]) || ld?.issn, 100) || null,
    arxiv_id: text(arxivId, 80) || null,
    keywords: unique(keywordValues, 50, 100),
    item_type: text(itemType, 80) || null,
    subtitle: text(meta([
      "citation_subtitle", "bepress_citation_subtitle", "prism.subtitle", "dc.alternative",
    ]) || ld?.alternativeHeadline, 500) || null,
    short_title: text(meta(["citation_short_title", "dc.title.alternative"]), 500) || null,
    series_title: text(meta(["citation_series_title", "prism.seriesTitle"]), 500) || null,
    series_number: text(meta(["citation_series_number", "prism.seriesNumber"]), 100) || null,
    edition: text(meta(["citation_edition", "prism.edition"]) || ld?.bookEdition, 100) || null,
    publisher_place: text(meta(["citation_publisher_place", "citation_publication_place"]), 300) || null,
    accessed_at: text(meta(["citation_access_date", "dc.date.accessioned"]), 40)
      || new Date().toISOString().slice(0, 10),
    archive: text(meta(["citation_archive", "eprints.repository_name"])
      || (arxivMatch ? "arXiv" : ""), 300) || null,
    archive_location: text(meta(["citation_archive_location", "eprints.repository_url"]), 500) || null,
    citation_key: text(meta(["citation_key", "citation_citation_key"]), 200) || null,
    format: text(meta(["citation_format", "dc.format", "DC.Format"]) || ld?.encodingFormat, 200) || null,
    call_number: text(meta(["citation_call_number", "dc.identifier.callnumber"]), 200) || null,
    pmid: text(meta(["citation_pmid", "pmid", "pubmed_id"]), 80) || null,
    pmcid: text(meta(["citation_pmcid", "pmcid"]), 80) || null,
    extra: text(meta(["citation_extra", "eprints.note"]), 2000) || null,
  };
  const metadataFields = [
    title && "title",
    authors.length && "authors",
    doi && "doi",
    selected && "selected_excerpt",
    pdfUrl && "pdf_url",
    ld && "json_ld",
    ...Object.entries(metadata).filter(([, value]) => Array.isArray(value) ? value.length : value).map(([key]) => key),
  ].filter(Boolean);

  return {
    url: location.href,
    canonicalUrl: canonical,
    title,
    siteName: text(meta(["og:site_name"]), 200),
    authors,
    publishedAt: text(meta(DATE_META) || ld?.datePublished, 40),
    description: text(
      meta(ABSTRACT_META) || ld?.abstract || ld?.description,
      2000,
    ),
    selectedExcerpt: selected,
    doi,
    pdfUrl: text(pdfUrl, 4000),
    pdfCandidate: pdfCandidate ? {
      url: text(pdfCandidate.url, 4000),
      authenticatedFallback: pdfCandidate.authenticatedFallback,
    } : null,
    sourceKind,
    metadata,
    metadataFields: unique(metadataFields, 50, 80),
  };
})();
