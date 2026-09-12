let capture = null;
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
document.getElementById("extensionVersion").textContent = `Version ${EXTENSION_VERSION}`;
const show = (id) => ["loading", "connect", "done", "failed"]
  .forEach((key) => { document.getElementById(key).hidden = key !== id; });
const call = (message) => chrome.runtime.sendMessage(message);
const setProgress = (title, detail) => {
  document.getElementById("loadingTitle").textContent = title;
  document.getElementById("loadingText").textContent = detail;
};
const FIXED_FAILURE_COPY = Object.freeze({
  pdf_too_large: "The PDF exceeds the 50 MiB Browser Capture limit. Download it and use Add papers in the Library instead.",
  pdf_tab_changed: "The source tab changed before the PDF could be verified. Return to the paper page and try again.",
  pdf_timeout: "The PDF transfer timed out or was interrupted. Keep the paper tab open and try again.",
  pdf_researchgate_blocked: "ResearchGate blocked the verified browser transfer. Download the visible PDF from this page, then use Add papers in the Library to upload it manually.",
  pdf_not_verified: "The advertised file could not be verified as a PDF. Open the PDF itself in a new tab and capture it there.",
  pdf_protected: "The source did not permit a verified direct transfer. Download the PDF and use Add papers in the Library instead.",
  pdf_connection: "Your Browser Capture connection needs attention. Reconnect the extension and try again.",
  pdf_storage: "The PDF could not be stored within the current Library capacity. Review your storage and try again.",
  pdf_unconfirmed: "The PDF attachment could not be confirmed. Download it and use Add papers in the Library instead.",
  capture_capacity: "There is not enough available capacity for this action. Review used and reserved capacity in SixSentences.",
  capture_concurrency: "The workspace already has the maximum number of actions running. Wait for one to finish and try again.",
  capture_resource: "This action exceeds a workspace limit. Review Library storage and usage, or choose a smaller file.",
  capture_action_limit: "This action exceeds the per-action limit. Choose a smaller file or request and try again.",
  capture_unavailable: "This action is currently unavailable. Try again or contact SixSentences support.",
  capture_forbidden: "This connected account does not have permission to perform this action.",
});
// Only structured, non-commercial API reasons select detailed copy. Raw server
// text and hosted-only capability codes are never shown to the user.
const apiCaptureFailureCategory = (error) => {
  if (error?.apiStatus === 401) return "pdf_connection";
  if (error?.apiStatus === 403) return "capture_forbidden";
  switch (error.apiCode) {
    case "capacity_exhausted": case "capacity_unavailable": return "capture_capacity";
    case "concurrency_limit": return "capture_concurrency";
    case "resource_limit": case "resource_limit_reached": return "capture_resource";
    case "action_capacity_limit": return "capture_action_limit";
    default: return "capture_unavailable";
  }
};
const captureResponseError = (response) => Object.assign(new Error(response.error || "Capture failed."), {
  apiStatus: response.apiStatus,
  apiCode: response.apiCode,
});
class SafeCaptureError extends Error {
  constructor(category) {
    super(category);
    this.safeCategory = category;
  }
}
const safeCaptureFailure = (error) => {
  const apiCategory = apiCaptureFailureCategory(error);
  if (apiCategory) return FIXED_FAILURE_COPY[apiCategory];
  if (error?.safeCategory && Object.hasOwn(FIXED_FAILURE_COPY, error.safeCategory)) {
    return FIXED_FAILURE_COPY[error.safeCategory];
  }
  const message = String(error?.message || error || "").toLowerCase();
  if (/connect|pair|account|authori[sz]|401|sign[ -]?in/.test(message)) {
    return "Your Browser Capture connection needs attention. Reconnect the extension and try again.";
  }
  if (/active (?:pdf |paper )?tab changed|preview.*again|attachment.*no longer/.test(message)) {
    return "The active tab changed during capture. Return to the source page and try again.";
  }
  if (/public https|supported public pdf|open a public/.test(message)) {
    return "Open the public HTTPS source or PDF in the active tab and try again.";
  }
  if (/did not finish|timed? ?out|interrupted/.test(message)) {
    return "The transfer timed out or was interrupted. Keep the source tab open and try again.";
  }
  return "The capture could not be confirmed. Check your connection and Library before retrying.";
};
const fail = (message) => {
  document.getElementById("error").textContent = safeCaptureFailure(message);
  show("failed");
};
const pdfAttachmentFailureCategory = (error, value) => {
  const apiCategory = apiCaptureFailureCategory(error);
  if (apiCategory) return apiCategory;
  const message = String(error?.error || error?.message || error || "").toLowerCase();
  if (/larger than (?:25|50) mib|exceeded its transfer bounds/.test(message)) {
    return "pdf_too_large";
  }
  if (/active (?:pdf |paper )?tab changed|preview.*again|attachment.*no longer/.test(message)) {
    return "pdf_tab_changed";
  }
  if (/did not finish|timed? ?out|interrupted/.test(message)) {
    return "pdf_timeout";
  }
  if (/\b401\b|authori[sz]ation|api key|account|reconnect|sign[ -]?in/.test(message)) {
    return "pdf_connection";
  }
  if (/quota|capacity|storage limit|insufficient storage/.test(message)) {
    return "pdf_storage";
  }
  let host = "";
  try { host = new URL(value?.url || "").hostname.toLowerCase().replace(/^www\./, ""); } catch { /* fixed fallback copy below */ }
  const sourceBlocked = /credentials|protected|not available|did not return|verified pdf|directly available|redirect|linked file/.test(message);
  if (host === "researchgate.net" && sourceBlocked) {
    return "pdf_researchgate_blocked";
  }
  if (/did not return a pdf|verified pdf|supported public pdf|confirmed link/.test(message)) {
    return "pdf_not_verified";
  }
  if (/credentials|protected|not available|directly available|redirect/.test(message)) {
    return "pdf_protected";
  }
  return "pdf_unconfirmed";
};
const pdfAttachmentFailure = (error, value) => FIXED_FAILURE_COPY[pdfAttachmentFailureCategory(error, value)];
const pdfFilename = (title) => {
  const clean = String(title || "paper")
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 294);
  return `${clean || "paper"}.pdf`;
};
const isDirectPdfUrl = (value) => {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && (
      /\.pdf$/i.test(parsed.pathname)
      || (parsed.hostname.toLowerCase() === "arxiv.org" && /^\/pdf\/[^/]+\/?$/i.test(parsed.pathname))
    );
  } catch { return false; }
};
async function extract() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https:/i.test(tab.url || "")) throw new Error("Open a public HTTPS web page or PDF first.");
  // Chrome's built-in PDF viewer can expose a minimal document titled
  // "content" even when the active URL has no `.pdf` suffix. Verify the
  // active response before trusting a DOM extraction so such PDFs never fall
  // through to the web-source route.
  const inspected = await call({ type: "inspectDirectPdf", url: tab.url });
  if (inspected.ok && inspected.capture) return inspected.capture;
  if (isDirectPdfUrl(tab.url)) throw new Error(inspected.error || "Could not inspect this PDF.");
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content-script.js"] });
    if (result?.title) return result;
    if (result?.sourceKind === "web") {
      const hostname = new URL(tab.url).hostname.replace(/^www\./i, "");
      return { ...result, title: hostname || "Web source" };
    }
  } catch { /* Browser PDF viewers do not expose a page DOM to content scripts. */ }
  const hostname = new URL(tab.url).hostname.replace(/^www\./i, "");
  return {
    url: tab.url,
    canonicalUrl: tab.url,
    title: tab.title || hostname || "Web source",
    siteName: hostname,
    description: "",
    selectedExcerpt: "",
    sourceKind: "web",
  };
}

async function saveCapture(value) {
  const sourcePageUrl = value.landingUrl || value.url;
  const policy = SixSentencesCapturePolicy.captureRoute(value);
  const uploadPdf = policy.uploadPdf;
  const metadata = value.metadata || {};
  const common = {
    source_url: sourcePageUrl,
    canonical_url: value.canonicalUrl || sourcePageUrl,
    title: value.title,
    authors: value.authors || [],
    published_at: value.publishedAt || null,
    description: value.description || "",
    selected_excerpt: value.selectedExcerpt || "",
    doi: value.doi || "",
    extension_version: EXTENSION_VERSION,
    metadata_fields: value.metadataFields || [],
    metadata,
  };
  if (value.sourceKind === "paper") {
    setProgress("Saving paper details…", "Securing the available citation details before attaching the PDF.");
    const citationResponse = await call({ type: "saveCitation", payload: common });
    if (!citationResponse.ok) throw captureResponseError(citationResponse);
    if (uploadPdf) {
      setProgress("Checking paper PDF…", "Verifying the exact file before adding it to the saved citation.");
      const attachmentResponse = await call({
        type: "savePaper",
        payload: {
          ...common,
          capture_id: crypto.randomUUID(),
          capture_kind: "paper",
          filename: pdfFilename(value.title),
          page_url: value.url,
          pdf_url: value.pdfUrl,
          captured_at: new Date().toISOString(),
        },
      });
      if (!attachmentResponse.ok) {
        const savedCopy = SixSentencesCaptureResult.captureResultCopy(citationResponse.result, "paper");
        renderDone(savedCopy, ` The paper details are safe. ${pdfAttachmentFailure(attachmentResponse, value)}`);
        return;
      }
      const copy = SixSentencesCaptureResult.captureResultCopy(
        attachmentResponse.result,
        "paper",
        { pdfRequested: true },
      );
      renderDone(copy);
      return;
    }
    const copy = SixSentencesCaptureResult.captureResultCopy(citationResponse.result, "paper");
    const attachmentNote = policy.unattachedPdf
      ? " The citation is saved; its linked PDF was not attached because it is hosted on another origin."
      : "";
    renderDone(copy, attachmentNote);
    return;
  }

  if (policy.route === "document") {
    setProgress("Checking PDF…", "Verifying the exact document before saving it to your Library.");
    const response = await call({
      type: "savePaper",
      payload: {
        ...common,
        capture_id: crypto.randomUUID(),
        capture_kind: "document",
        filename: pdfFilename(value.title),
        page_url: value.url,
        pdf_url: value.pdfUrl,
        captured_at: new Date().toISOString(),
      },
    });
    if (!response.ok) throw new SafeCaptureError(pdfAttachmentFailureCategory(response, value));
    renderDone(SixSentencesCaptureResult.captureResultCopy(
      response.result,
      "document",
      { pdfRequested: true },
    ));
    return;
  }

  setProgress("Saving web source…", "Adding the available source details and your selected passage.");
  const response = await call({
    type: "saveWeb",
    payload: {
      url: sourcePageUrl,
      canonical_url: value.canonicalUrl || sourcePageUrl,
      title: value.title,
      site_name: value.siteName || "",
      description: value.description || "",
      selected_excerpt: value.selectedExcerpt || "",
      source_kind: "web",
      extension_version: EXTENSION_VERSION,
    },
  });
  if (!response.ok) throw captureResponseError(response);
  renderDone(SixSentencesCaptureResult.captureResultCopy(response.result, "web"));
}

function renderDone(copy, note = "") {
  document.getElementById("doneIcon").textContent = copy.symbol;
  document.getElementById("doneIcon").dataset.tone = copy.tone;
  document.getElementById("doneEyebrow").textContent = copy.eyebrow;
  document.getElementById("doneTitle").textContent = copy.title;
  document.getElementById("doneText").textContent = `${copy.detail}${note}`;
  show("done");
}

async function init() {
  show("loading");
  const status = await call({ type: "status" });
  document.getElementById("disconnectButton").hidden = !status.connected;
  if (!status.connected) { show("connect"); return; }
  setProgress("Reading this tab…", "Collecting the available source details.");
  capture = await extract();
  await saveCapture(capture);
}

document.getElementById("connectButton").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  event.currentTarget.textContent = "Connecting…";
  const response = await call({ type: "connect" });
  if (!response.ok) {
    fail(response.error);
    event.currentTarget.disabled = false;
    event.currentTarget.textContent = "Connect account";
    return;
  }
  init().catch((error) => fail(error));
});

document.getElementById("disconnectButton").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  const response = await call({ type: "disconnect" });
  if (!response.ok) {
    event.currentTarget.disabled = false;
    fail(response.error || "Could not disconnect this browser.");
    return;
  }
  event.currentTarget.hidden = true;
  show("connect");
});

init().catch((error) => fail(error));
