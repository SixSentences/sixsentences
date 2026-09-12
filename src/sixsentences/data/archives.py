"""Fail-closed ZIP preflight and bounded member reads.

ZIP metadata is attacker-controlled. The central-directory entry count is
checked directly from the end-of-central-directory record before ``ZipFile``
allocates one ``ZipInfo`` per entry. Declared sizes, compression ratios,
encryption, ambiguous names and symbolic links are then rejected before a
member can be decompressed.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_MIN_BYTES = 22
_EOCD_SEARCH_BYTES = 65_535 + _EOCD_MIN_BYTES
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_CENTRAL_MIN_BYTES = 46
_ZIP32_SENTINEL = 0xFFFFFFFF
_SUPPORTED_COMPRESSION = {ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA}


class UnsafeArchiveError(ValueError):
    """A ZIP is malformed, ambiguous or outside an explicit resource bound."""


@dataclass(frozen=True, slots=True)
class ZipSafetyLimits:
    """Bounds checked before and while a ZIP member is decompressed."""

    max_archive_bytes: int
    max_entries: int
    max_uncompressed_bytes: int
    max_member_bytes: int
    max_compression_ratio: int = 200

    def __post_init__(self) -> None:
        """Validate the safety envelope itself."""

        if (
            min(
                self.max_archive_bytes,
                self.max_entries,
                self.max_uncompressed_bytes,
                self.max_member_bytes,
                self.max_compression_ratio,
            )
            < 1
        ):
            raise ValueError("ZIP safety limits must be positive")
        if self.max_member_bytes > self.max_uncompressed_bytes:
            raise ValueError("max_member_bytes cannot exceed max_uncompressed_bytes")


@dataclass(frozen=True, slots=True)
class ZipMember:
    """Validated central-directory metadata for one regular member."""

    name: str
    compressed_bytes: int
    uncompressed_bytes: int
    crc32: int


def _preflight_central_directory(blob: bytes, *, max_entries: int) -> None:
    """Validate and count central records before ``ZipFile`` allocates them."""

    search_start = max(0, len(blob) - _EOCD_SEARCH_BYTES)
    offset = blob.rfind(_EOCD_SIGNATURE, search_start)
    if offset < 0 or offset + _EOCD_MIN_BYTES > len(blob):
        raise UnsafeArchiveError("not a readable ZIP archive")

    comment_length = int.from_bytes(blob[offset + 20 : offset + 22], "little")
    if offset + _EOCD_MIN_BYTES + comment_length != len(blob):
        raise UnsafeArchiveError("ZIP archive has an invalid central directory")

    disk_number = int.from_bytes(blob[offset + 4 : offset + 6], "little")
    directory_disk = int.from_bytes(blob[offset + 6 : offset + 8], "little")
    entries_on_disk = int.from_bytes(blob[offset + 8 : offset + 10], "little")
    total_entries = int.from_bytes(blob[offset + 10 : offset + 12], "little")
    if disk_number or directory_disk or entries_on_disk != total_entries:
        raise UnsafeArchiveError("multi-disk ZIP archives are not supported")
    if total_entries == 0xFFFF or total_entries > max_entries:
        raise UnsafeArchiveError(f"archive contains more than {max_entries} entries")

    directory_size = int.from_bytes(blob[offset + 12 : offset + 16], "little")
    directory_offset = int.from_bytes(blob[offset + 16 : offset + 20], "little")
    if directory_size == _ZIP32_SENTINEL or directory_offset == _ZIP32_SENTINEL:
        raise UnsafeArchiveError("ZIP64 archives are not supported")
    directory_end = directory_offset + directory_size
    if directory_offset > offset or directory_end != offset:
        raise UnsafeArchiveError("ZIP archive has an invalid central-directory extent")

    cursor = directory_offset
    counted = 0
    while cursor < directory_end:
        if cursor + _CENTRAL_MIN_BYTES > directory_end:
            raise UnsafeArchiveError("ZIP archive has a truncated central-directory record")
        if blob[cursor : cursor + 4] != _CENTRAL_SIGNATURE:
            raise UnsafeArchiveError("ZIP archive has an invalid central-directory record")
        filename_length = int.from_bytes(blob[cursor + 28 : cursor + 30], "little")
        extra_length = int.from_bytes(blob[cursor + 30 : cursor + 32], "little")
        member_comment_length = int.from_bytes(blob[cursor + 32 : cursor + 34], "little")
        member_disk = int.from_bytes(blob[cursor + 34 : cursor + 36], "little")
        compressed_size = int.from_bytes(blob[cursor + 20 : cursor + 24], "little")
        uncompressed_size = int.from_bytes(blob[cursor + 24 : cursor + 28], "little")
        local_offset = int.from_bytes(blob[cursor + 42 : cursor + 46], "little")
        if member_disk:
            raise UnsafeArchiveError("multi-disk ZIP archives are not supported")
        if _ZIP32_SENTINEL in {compressed_size, uncompressed_size, local_offset}:
            raise UnsafeArchiveError("ZIP64 members are not supported")
        cursor += _CENTRAL_MIN_BYTES + filename_length + extra_length + member_comment_length
        if cursor > directory_end:
            raise UnsafeArchiveError("ZIP archive has a truncated central-directory record")
        counted += 1
        if counted > max_entries:
            raise UnsafeArchiveError(f"archive contains more than {max_entries} entries")
    if cursor != directory_end or counted != total_entries:
        raise UnsafeArchiveError("ZIP central-directory entry count is inconsistent")


def _safe_member_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    trimmed = name[:-1] if name.endswith("/") else name
    raw_parts = trimmed.split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        return False
    path = PurePosixPath(trimmed)
    return bool(path.parts) and all(":" not in part for part in path.parts)


def _validate_archive(archive: ZipFile, limits: ZipSafetyLimits) -> tuple[ZipMember, ...]:
    entries = archive.infolist()
    if len(entries) > limits.max_entries:
        raise UnsafeArchiveError(f"archive contains more than {limits.max_entries} entries")

    names: set[str] = set()
    total = 0
    members: list[ZipMember] = []
    for item in entries:
        if not _safe_member_name(item.filename):
            raise UnsafeArchiveError("archive contains an unsafe member name")
        if item.filename in names:
            raise UnsafeArchiveError("archive contains duplicate member names")
        names.add(item.filename)

        unix_mode = item.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            raise UnsafeArchiveError("archive contains a symbolic link")
        file_type = stat.S_IFMT(unix_mode)
        if file_type and not (stat.S_ISREG(unix_mode) or stat.S_ISDIR(unix_mode)):
            raise UnsafeArchiveError("archive contains a special file")
        if item.flag_bits & 0x1:
            raise UnsafeArchiveError("encrypted archives are not supported")
        if item.compress_type not in _SUPPORTED_COMPRESSION:
            raise UnsafeArchiveError("archive uses an unsupported compression method")
        if item.is_dir():
            continue
        if item.file_size < 0 or item.compress_size < 0:
            raise UnsafeArchiveError("archive contains invalid member sizes")
        if item.file_size > limits.max_member_bytes:
            raise UnsafeArchiveError("an archived file exceeds the size limit")
        total += item.file_size
        if total > limits.max_uncompressed_bytes:
            raise UnsafeArchiveError("archive expands beyond the size limit")
        if item.file_size:
            if item.compress_size <= 0:
                raise UnsafeArchiveError("archive has an invalid compressed member")
            if item.file_size / item.compress_size > limits.max_compression_ratio:
                raise UnsafeArchiveError("archive compression ratio is unsafe")
        members.append(
            ZipMember(
                name=item.filename,
                compressed_bytes=item.compress_size,
                uncompressed_bytes=item.file_size,
                crc32=item.CRC,
            )
        )
    return tuple(members)


def open_safe_zip(blob: bytes, *, limits: ZipSafetyLimits) -> ZipFile:
    """Open a ZIP after validating declared resource and path metadata.

    Callers must close the returned archive and must use
    :func:`read_zip_member` for every decompression.
    """

    if len(blob) > limits.max_archive_bytes:
        raise UnsafeArchiveError("ZIP archive exceeds the encoded size limit")
    _preflight_central_directory(blob, max_entries=limits.max_entries)
    try:
        archive = ZipFile(BytesIO(blob))
    except (BadZipFile, ValueError) as exc:
        raise UnsafeArchiveError("not a readable ZIP archive") from exc
    try:
        _validate_archive(archive, limits)
    except Exception:
        archive.close()
        raise
    return archive


def preflight_zip(blob: bytes, *, limits: ZipSafetyLimits) -> tuple[ZipMember, ...]:
    """Return validated member metadata without decompressing file contents."""

    with open_safe_zip(blob, limits=limits) as archive:
        return _validate_archive(archive, limits)


def read_zip_member(archive: ZipFile, item: str | ZipInfo, *, max_bytes: int) -> bytes:
    """Read exactly one member with an actual decompressed-byte cap."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        info = archive.getinfo(item) if isinstance(item, str) else item
        if info.is_dir():
            raise UnsafeArchiveError("archive member is a directory")
        if info.file_size > max_bytes:
            raise UnsafeArchiveError("an archived file exceeds the size limit")
        with archive.open(info) as source:
            payload = source.read(max_bytes + 1)
    except UnsafeArchiveError:
        raise
    except (BadZipFile, KeyError, NotImplementedError, RuntimeError, ValueError) as exc:
        raise UnsafeArchiveError("archive member could not be read safely") from exc
    if len(payload) > max_bytes:
        raise UnsafeArchiveError("an archived file exceeds the size limit")
    if len(payload) != info.file_size:
        raise UnsafeArchiveError("archive member size does not match its metadata")
    return payload
