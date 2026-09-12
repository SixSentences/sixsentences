"""Polite, open-access-only document fetcher.

Sends a mailto-identified User-Agent, resolves redirects itself, caps the
download size, and retries transient failures. It has NO credential parameter,
cookie jar, or auth path — it is structurally incapable of the credential replay
principle 7 forbids. A failure (4xx, oversize, transient after retries) returns
None so the service tries the next open-access candidate or records an honest
"not retrieved". It is never handed a paywalled URL: the resolver only produces
OA locations.

SSRF guard: the candidate and every redirect hop must point at a public host
(``core.net.is_public_http_url``). Candidate URLs come from third-party metadata
(OpenAlex/corpus) and landing pages scrape a ``citation_pdf_url``, so an attacker
who seeds a work could otherwise aim the fetch at ``169.254.169.254`` or an
internal service — and the fetched body flows back into screening quotes and
chat evidence, making it a *readable* SSRF. Redirects are followed manually
(the client does not auto-follow) precisely so each hop can be revalidated.
"""

import time
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urljoin

import httpx

from sixsentences_server.acquisition.models import FetchedBlob
from sixsentences_server.core.net import is_public_http_url

_MAX_RETRIES = 3
_MAX_REDIRECTS = 5
_TRANSIENT_STATUS = (429, 500, 502, 503, 504)
_REDIRECT_STATUS = (301, 302, 303, 307, 308)
# Figure-heavy arXiv papers can legitimately exceed the 30 MB upload ceiling
# (PaperBanana is about 42.6 MB). OA acquisition remains strictly bounded, but
# gets enough headroom for those primary sources; per-workspace storage quotas
# still apply before the downloaded blob is attached to a run or Library.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

UrlGuard = Callable[[str], bool]


class DocumentFetcher(Protocol):
    def fetch(self, url: str) -> FetchedBlob | None: ...


class HttpxFetcher:
    def __init__(
        self,
        *,
        mailto: str = "",
        max_bytes: int = DEFAULT_MAX_BYTES,
        http: httpx.Client | None = None,
        guard: UrlGuard | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self._guard = guard or is_public_http_url
        ua = (
            f"SixSentences/0.1 (+full-text acquisition; mailto:{mailto})"
            if mailto
            else "SixSentences/0.1 (+full-text acquisition)"
        )
        # follow_redirects=False: we resolve hops ourselves so each is guarded.
        self.http = http or httpx.Client(
            follow_redirects=False,
            timeout=60,
            headers={
                "User-Agent": ua,
                "Accept": "application/pdf, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
                "Accept-Language": "en-US,en;q=0.8",
            },
        )

    def fetch(self, url: str) -> FetchedBlob | None:
        current = url
        redirects = 0
        attempt = 0
        while True:
            if not self._guard(current):
                return None  # SSRF guard: refuse a non-public target / hop
            transient = False
            try:
                with self.http.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_STATUS:
                        location = response.headers.get("location")
                        if not location or redirects >= _MAX_REDIRECTS:
                            return None
                        current = urljoin(current, location)
                        redirects += 1
                        continue
                    if response.status_code in _TRANSIENT_STATUS:
                        transient = True
                    elif response.status_code >= 400:
                        return None  # 404/403/…: not available here, try next candidate
                    else:
                        chunks = bytearray()
                        for chunk in response.iter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > self.max_bytes:
                                return None  # oversize: refuse, try next candidate
                        ctype = response.headers.get("content-type", "").split(";")[0].strip()
                        return FetchedBlob(bytes(chunks), ctype, str(response.url))
            except httpx.TransportError:
                transient = True
            if not transient:
                return None
            attempt += 1
            if attempt >= _MAX_RETRIES:
                return None
            time.sleep(min(2 ** (attempt - 1), 5))
