"""Shared hard resource limits for future community server input paths."""

MIB = 1024**2

# A single user-supplied research document. A deployment may enforce a lower
# storage allowance separately.
MAX_DOCUMENT_UPLOAD_BYTES = 100 * MIB

# Browser Capture keeps a tighter in-browser transfer bound than the regular
# upload path. Base64 expands a 50 MiB PDF to at most this many characters.
MAX_BROWSER_CAPTURE_PDF_BYTES = 50 * MIB
MAX_BROWSER_CAPTURE_PDF_BASE64_CHARS = ((MAX_BROWSER_CAPTURE_PDF_BYTES + 2) // 3) * 4

# The largest JSON requests contain a base64-encoded 100 MB recording or
# document. This cap includes encoding overhead and stops oversized bodies at
# the application/proxy boundary before Pydantic or a decoder allocates again.
MAX_REQUEST_BODY_BYTES = 160 * MIB
