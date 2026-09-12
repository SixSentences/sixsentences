"""Bounded decoding helpers for tenant-controlled files.

Compressed Office documents and images are parsed in-process. Their encoded
byte size alone is not a useful resource bound: a tiny archive can expand to
gigabytes and a small image can declare an enormous canvas. These helpers
validate archive metadata before any member is read and image dimensions
before pixel buffers are allocated.
"""

from __future__ import annotations

import stat
import warnings
from io import BytesIO
from zipfile import BadZipFile, ZipFile, ZipInfo

from PIL import Image

MAX_IMAGE_PIXELS = 40_000_000
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_EOCD_MIN_BYTES = 22
_ZIP_EOCD_SEARCH_BYTES = 65_535 + _ZIP_EOCD_MIN_BYTES


class UnsafeArchiveError(ValueError):
    """The compressed upload exceeds a resource or file-system safety bound."""


class UnsafeImageError(ValueError):
    """The uploaded image cannot be decoded within the configured bounds."""


def _preflight_zip_entry_count(blob: bytes, *, max_entries: int) -> None:
    """Bound the central-directory member count before ZipFile allocates it."""

    search_start = max(0, len(blob) - _ZIP_EOCD_SEARCH_BYTES)
    offset = blob.rfind(_ZIP_EOCD_SIGNATURE, search_start)
    if offset < 0 or offset + _ZIP_EOCD_MIN_BYTES > len(blob):
        raise UnsafeArchiveError("not a readable ZIP archive")
    comment_length = int.from_bytes(blob[offset + 20 : offset + 22], "little")
    if offset + _ZIP_EOCD_MIN_BYTES + comment_length != len(blob):
        raise UnsafeArchiveError("ZIP archive has an invalid central directory")
    disk_number = int.from_bytes(blob[offset + 4 : offset + 6], "little")
    directory_disk = int.from_bytes(blob[offset + 6 : offset + 8], "little")
    entries_on_disk = int.from_bytes(blob[offset + 8 : offset + 10], "little")
    total_entries = int.from_bytes(blob[offset + 10 : offset + 12], "little")
    if disk_number or directory_disk or entries_on_disk != total_entries:
        raise UnsafeArchiveError("multi-disk ZIP archives are not supported")
    # ZIP64 is only required at 65,535 entries, far beyond every supported
    # product envelope. Rejecting its sentinel keeps this preflight small and
    # fail-closed instead of allocating an attacker-sized ZipInfo list first.
    if total_entries == 0xFFFF:
        raise UnsafeArchiveError(f"archive contains more than {max_entries} entries")
    if total_entries > max_entries:
        raise UnsafeArchiveError(f"archive contains more than {max_entries} entries")


def open_safe_zip(
    blob: bytes,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
    max_member_bytes: int | None = None,
    max_compression_ratio: int = 200,
) -> ZipFile:
    """Open a ZIP only after validating every declared member.

    The returned ``ZipFile`` owns its in-memory buffer. Callers must still read
    members through :func:`read_zip_member`, which enforces the bound against
    the actual decompressed stream rather than trusting metadata alone.
    """

    _preflight_zip_entry_count(blob, max_entries=max_files)
    try:
        archive = ZipFile(BytesIO(blob))
    except BadZipFile as exc:
        raise UnsafeArchiveError("not a readable ZIP archive") from exc

    entries = archive.infolist()
    if len(entries) > max_files:
        archive.close()
        raise UnsafeArchiveError(f"archive contains more than {max_files} entries")
    files = [item for item in entries if not item.is_dir()]
    if len(files) > max_files:
        archive.close()
        raise UnsafeArchiveError(f"archive contains more than {max_files} files")

    total = 0
    per_member = max_member_bytes or max_uncompressed_bytes
    for item in files:
        unix_mode = item.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            archive.close()
            raise UnsafeArchiveError("archive contains a symbolic link")
        if item.flag_bits & 0x1:
            archive.close()
            raise UnsafeArchiveError("encrypted archives are not supported")
        if item.file_size > per_member:
            archive.close()
            raise UnsafeArchiveError("an archived file exceeds the size limit")
        total += item.file_size
        if total > max_uncompressed_bytes:
            archive.close()
            raise UnsafeArchiveError("archive expands beyond the size limit")
        if item.file_size:
            if item.compress_size <= 0:
                archive.close()
                raise UnsafeArchiveError("archive has an invalid compressed member")
            if item.file_size / item.compress_size > max_compression_ratio:
                archive.close()
                raise UnsafeArchiveError("archive compression ratio is unsafe")
    return archive


def read_zip_member(archive: ZipFile, item: str | ZipInfo, *, max_bytes: int) -> bytes:
    """Read one member with an actual decompressed-byte cap."""

    try:
        with archive.open(item) as source:
            payload = source.read(max_bytes + 1)
    except (BadZipFile, KeyError, RuntimeError) as exc:
        raise UnsafeArchiveError("archive member could not be read safely") from exc
    if len(payload) > max_bytes:
        raise UnsafeArchiveError("an archived file exceeds the size limit")
    return payload


def decode_image(
    raw: bytes,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> tuple[Image.Image, str | None]:
    """Decode the first image frame after bounding its canvas.

    A detached image copy is returned so callers do not retain the upload
    buffer or a lazy decoder after this function exits.
    """

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as decoded:
                width, height = decoded.size
                if width < 1 or height < 1 or width * height > max_pixels:
                    raise UnsafeImageError(f"image exceeds the {max_pixels:,}-pixel safety limit")
                image_format = decoded.format
                decoded.load()
                return decoded.copy(), image_format
    except UnsafeImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UnsafeImageError("image dimensions exceed the safety limit") from exc
    except Exception as exc:
        raise UnsafeImageError("the uploaded file is not a readable image") from exc
