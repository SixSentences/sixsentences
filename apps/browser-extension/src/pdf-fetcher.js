(() => {
  "use strict";

  if (globalThis.__sixSentencesPdfFetcherInstalled) return;
  globalThis.__sixSentencesPdfFetcherInstalled = true;

  const MAX_PDF_BYTES = 50 * 1024 * 1024;
  const PDF_FETCH_TIMEOUT_MS = 120_000;
  const STREAM_CHUNK_BYTES = 48 * 1024;
  const PORT_NAME = "six-sentences-pdf-fetch";

  const encodeChunk = (value) => {
    let binary = "";
    for (let offset = 0; offset < value.length; offset += 0x8000) {
      binary += String.fromCharCode(...value.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  };

  chrome.runtime.onConnect.addListener((port) => {
    if (port.name !== PORT_NAME) return;
    let started = false;
    port.onMessage.addListener((message) => {
      if (started || message?.type !== "fetchPdf") return;
      started = true;
      const requestId = String(message.requestId || "");
      void (async () => {
        const transferAbort = new AbortController();
        const abortTransfer = () => transferAbort.abort();
        port.onDisconnect.addListener(abortTransfer);
        const timeout = setTimeout(abortTransfer, PDF_FETCH_TIMEOUT_MS);
        try {
          const pageUrl = new URL(String(message.pageUrl || ""));
          const pdfUrl = new URL(String(message.url || ""));
          const activeUrl = new URL(location.href);
          if (!requestId || activeUrl.href !== pageUrl.href) {
            throw new Error("The active paper page changed before the PDF transfer.");
          }
          if (pageUrl.protocol !== "https:" || pdfUrl.protocol !== "https:"
              || pageUrl.username || pageUrl.password || pdfUrl.username || pdfUrl.password
              || pdfUrl.origin !== pageUrl.origin) {
            throw new Error("The protected PDF is not on the active paper origin.");
          }
          const response = await fetch(pdfUrl.href, {
            method: "GET",
            credentials: "same-origin",
            redirect: "error",
            referrerPolicy: "no-referrer",
            signal: transferAbort.signal,
          });
          if (!response.ok || !response.body) {
            throw new Error("The protected PDF is not available from the active paper page.");
          }
          const declared = Number(response.headers.get("content-length") || 0);
          if (declared > MAX_PDF_BYTES) throw new Error("This PDF is larger than 50 MiB.");

          const reader = response.body.getReader();
          let totalBytes = 0;
          let magicVerified = false;
          let pending = new Uint8Array(0);
          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              totalBytes += value.length;
              if (totalBytes > MAX_PDF_BYTES) {
                throw new Error("This PDF is larger than 50 MiB.");
              }
              let transferable = value;
              if (!magicVerified) {
                const combined = new Uint8Array(pending.length + value.length);
                combined.set(pending);
                combined.set(value, pending.length);
                if (combined.length < 5) {
                  pending = combined;
                  continue;
                }
                if (new TextDecoder().decode(combined.subarray(0, 5)) !== "%PDF-") {
                  await reader.cancel().catch(() => undefined);
                  throw new Error("The protected link did not return a PDF.");
                }
                magicVerified = true;
                pending = new Uint8Array(0);
                transferable = combined;
              }
              for (let offset = 0; offset < transferable.length; offset += STREAM_CHUNK_BYTES) {
                const chunk = transferable.subarray(offset, offset + STREAM_CHUNK_BYTES);
                port.postMessage({
                  type: "chunk",
                  requestId,
                  data: encodeChunk(chunk),
                  byteLength: chunk.length,
                });
              }
            }
          } catch (error) {
            await reader.cancel().catch(() => undefined);
            throw error;
          }
          if (!magicVerified) {
            throw new Error("The protected link did not return a PDF.");
          }
          port.postMessage({ type: "done", requestId, totalBytes });
        } finally {
          clearTimeout(timeout);
          port.onDisconnect.removeListener(abortTransfer);
        }
      })().catch((error) => {
        try {
          port.postMessage({
            type: "error",
            requestId,
            error: error?.name === "TimeoutError" || error?.name === "AbortError"
              ? "The PDF transfer did not finish within 2 minutes. Try again."
              : String(error?.message || "Could not read the protected PDF."),
          });
        } catch { /* The worker disconnected and already discarded the transfer. */ }
      });
    });
  });
})();
