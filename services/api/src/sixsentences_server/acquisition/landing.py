"""Resolve a direct PDF link from an open-access landing page.

Most publishers and repositories emit the Highwire ``<meta
name="citation_pdf_url">`` tag — the same signal Google Scholar, Zotero and
Unpaywall follow. Following it on an *open-access* landing page yields the OA
PDF; no paywall is bypassed, because the resolver only ever hands us open-access
locations. Stdlib only.
"""

from html.parser import HTMLParser
from urllib.parse import urljoin


class _CitationPdfFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pdf_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "link":
            a = dict(attrs)
            if (
                (a.get("type") or "").lower() == "application/pdf"
                and (a.get("rel") or "").lower() in {"alternate", "related"}
                and a.get("href")
            ):
                self.pdf_urls.append(str(a["href"]))
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if str(href).lower().split("?", 1)[0].endswith(".pdf"):
                self.pdf_urls.append(str(href))
            return
        if tag != "meta":
            return
        a = dict(attrs)
        name = (a.get("name") or a.get("property") or "").lower()
        content = a.get("content")
        if (
            name
            in {
                "citation_pdf_url",
                "eprints.document_url",
                "wkhealth_pdf_url",
                "pdf_url",
            }
            and content
        ):
            self.pdf_urls.append(content)


def pdf_urls_from_html(content: bytes, base_url: str) -> list[str]:
    """Return unique, absolute PDF candidates advertised by a landing page."""
    finder = _CitationPdfFinder()
    finder.feed(content.decode("utf-8", errors="ignore"))
    return list(dict.fromkeys(urljoin(base_url, url) for url in finder.pdf_urls))


def pdf_url_from_html(content: bytes, base_url: str) -> str | None:
    """Return the absolute citation_pdf_url from a landing page, or None."""
    urls = pdf_urls_from_html(content, base_url)
    return urls[0] if urls else None
