"""What kind of file is this? Decided from the file's first bytes, never from its name or the browser's word.

Only documents PaperJay can OCR are accepted: PDF, and JPEG, PNG, TIFF, HEIC/HEIF (iPhone photos), WebP.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileType:
    mime: str
    ext: str
    kind: str  # "pdf" or "image"


PDF = FileType("application/pdf", ".pdf", "pdf")
JPEG = FileType("image/jpeg", ".jpg", "image")
PNG = FileType("image/png", ".png", "image")
TIFF = FileType("image/tiff", ".tif", "image")
HEIC = FileType("image/heic", ".heic", "image")
WEBP = FileType("image/webp", ".webp", "image")
BY_MIME = {t.mime: t for t in (PDF, JPEG, PNG, TIFF, HEIC, WEBP)}

# ISO base media "brands" that mean a HEIF still image (not e.g. an mp4 video)
_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif"}
SUPPORTED_TEXT = "PDF, JPEG, PNG, TIFF, HEIC or WebP"


def sniff(path: Path) -> FileType | None:
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" in head:  # the spec allows junk before the header; readers accept it within the first 1 KB
        return PDF
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return TIFF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head[4:8] == b"ftyp":
        brands = {head[8:12]} | {head[i:i + 4] for i in range(16, min(len(head), int.from_bytes(head[:4], "big")), 4)}
        if brands & _HEIF_BRANDS and b"avif" not in brands:
            return HEIC
    return None
