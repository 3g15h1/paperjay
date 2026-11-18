"""Turn an original file into a searchable PDF, its text, a page count and a thumbnail.

    photo/scan ──(img2pdf, or Pillow for HEIC/WebP/transparency)──▶ PDF
    PDF ──(OCRmyPDF + Tesseract: rotate, deskew, clean, PDF/A)──▶ archive PDF ──(pdftotext)──▶ text
                                                                         └──(pdftoppm)──▶ thumbnail

Every external program runs in its own process group with a time limit and a lower CPU priority, so a
stuck or heavy OCR job can be killed cleanly and never starves the web app. Pages that already contain
text (PDFs made on a computer) are not OCRed again.
"""
import logging
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .filetypes import FileType

log = logging.getLogger("paperjay")

TOOLS = ["tesseract", "gs", "unpaper", "pdftotext", "pdftoppm"]
THUMB_WIDTH = 600
MAX_IMAGE_PIXELS = 150_000_000  # Pillow refuses bigger images (decompression-bomb guard)


class OcrFailed(Exception):
    """The file can't be processed and retrying won't help (password-protected, damaged, too many pages…)."""


@dataclass
class Options:
    language: str = "eng"
    jobs: int = 1
    timeout: int = 1800
    max_pages: int = 500
    # How sure Tesseract must be before turning a page. OCRmyPDF's default (14) leaves many sideways and
    # upside-down phone photos as they are: pages with a few lines of text score about 6, dense ones 11.
    # Upright pages are never turned, whatever this is, so a low value only affects pages that look turned.
    rotate_threshold: float = 4.0


@dataclass
class Result:
    archive: Path | None
    text: str
    page_count: int
    thumbnail: Path | None
    warnings: list[str] = field(default_factory=list)


def missing_tools() -> list[str]:
    missing = [t for t in TOOLS if not shutil.which(t)]
    try:
        import ocrmypdf  # noqa: F401  (run as `python -m ocrmypdf` so it needn't be on PATH)
    except ImportError:
        missing.insert(0, "ocrmypdf")
    return missing


def tesseract_languages() -> set[str]:
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.strip() for line in out.splitlines()[1:] if line.strip()}


def _lower_priority():
    os.nice(10)


def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run a tool; on timeout kill it and everything it started."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                         preexec_fn=_lower_priority)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise TimeoutError(f"{cmd[0]} took longer than {int(timeout)} s")
    return subprocess.CompletedProcess(cmd, p.returncode, out.decode("utf-8", "replace"),
                                       err.decode("utf-8", "replace"))


# ---- images to PDF ----

def _dpi_for(size: tuple[int, int], info_dpi) -> float:
    """Resolution to give an image. Scanners record a real DPI; phones write 72 or nothing. Then assume the
    long side of the photo is a letter/A4 page (about 11 inches), which is what Tesseract does best with."""
    try:
        dpi = float(info_dpi[0]) if info_dpi else 0.0
    except (TypeError, ValueError, IndexError):
        dpi = 0.0
    if 150 <= dpi <= 1200:
        return dpi
    return max(100.0, min(600.0, max(size) / 11.0))


def image_to_pdf(src: Path, ftype: FileType, out: Path) -> Path:
    import img2pdf
    from PIL import Image, ImageOps, ImageSequence
    import pillow_heif

    pillow_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(src) as im:
            dpi = _dpi_for(im.size, im.info.get("dpi"))
            has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    except Exception as e:
        raise OcrFailed(f"the image can't be read ({type(e).__name__}: {e})") from e

    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    if ftype.ext in (".jpg", ".png", ".tif") and not has_alpha:
        # img2pdf embeds JPEGs without re-compressing them and applies the EXIF rotation itself
        try:
            out.write_bytes(img2pdf.convert(str(src), layout_fun=layout, rotation=img2pdf.Rotation.ifvalid))
            return out
        except Exception as e:
            log.info("img2pdf couldn't take %s directly (%s); converting with Pillow", src.name, e)

    # HEIC, WebP, transparent or unusual images: flatten each frame onto white, upright, then embed
    pages = []
    with Image.open(src) as im:
        for i, frame in enumerate(ImageSequence.Iterator(im)):
            f = ImageOps.exif_transpose(frame.copy())
            if f.mode in ("RGBA", "LA", "PA", "P"):
                f = f.convert("RGBA")
                bg = Image.new("RGB", f.size, "white")
                bg.paste(f, mask=f.getchannel("A"))
                f = bg
            elif f.mode not in ("RGB", "L", "1"):
                f = f.convert("RGB")
            page = out.with_name(f"{out.stem}-p{i}" + (".jpg" if ftype.ext in (".heic", ".webp", ".jpg") else ".png"))
            if page.suffix == ".jpg":
                f.convert("RGB").save(page, "JPEG", quality=92)
            else:
                f.save(page, "PNG")
            pages.append(str(page))
    out.write_bytes(img2pdf.convert(pages, layout_fun=layout))
    for p in pages:
        os.unlink(p)
    return out


# ---- PDF inspection ----

def inspect_pdf(path: Path) -> int:
    """Page count; OcrFailed for files that can't be opened or are locked with a password."""
    import pikepdf
    try:
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except pikepdf.PasswordError as e:
        raise OcrFailed("the PDF is protected with a password; remove the password and upload it again") from e
    except pikepdf.PdfError as e:
        raise OcrFailed(f"the PDF is damaged and can't be opened ({e})") from e


def pdf_text(path: Path, timeout: float = 300) -> str:
    r = run(["pdftotext", "-enc", "UTF-8", str(path), "-"], timeout)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {r.stderr.strip()[-500:]}")
    # pdftotext ends each page with a form feed; keep one between pages, they mark page boundaries for citations
    pages = []
    for page in r.stdout.replace("\r", "").rstrip("\f").split("\f"):
        text = "\n".join(" ".join(line.split()) for line in page.split("\n"))
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        pages.append(text.strip())
    return "\n\f".join(pages).strip()


def thumbnail(pdf: Path, out: Path, timeout: float = 120) -> Path:
    from PIL import Image
    prefix = out.with_suffix("")
    r = run(["pdftoppm", "-f", "1", "-l", "1", "-scale-to", str(THUMB_WIDTH), "-png", "-singlefile",
             str(pdf), str(prefix)], timeout)
    png = prefix.with_suffix(".png")
    if r.returncode != 0 or not png.exists():
        raise RuntimeError(f"pdftoppm failed: {r.stderr.strip()[-500:]}")
    with Image.open(png) as im:
        im.convert("RGB").save(out, "WEBP", quality=80, method=4)
    png.unlink()
    return out


# ---- OCR ----

# ocrmypdf exit codes (https://ocrmypdf.readthedocs.io/en/latest/advanced.html#return-code-policy)
_EXIT_INPUT, _EXIT_OUTPUT_INVALID, _EXIT_ENCRYPTED, _EXIT_PDFA = 2, 4, 8, 10


def ocrmypdf(src: Path, out: Path, opts: Options, pdfa: bool = True, rgb: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ocrmypdf", "--mode", "skip", "--rotate-pages",
           "--rotate-pages-threshold", str(opts.rotate_threshold), "--deskew", "--clean",
           "--output-type", "pdfa" if pdfa else "pdf", "--language", opts.language, "--jobs", str(opts.jobs),
           "--optimize", "1", "--tesseract-timeout", "300", "--max-image-mpixels", str(MAX_IMAGE_PIXELS // 1_000_000),
           "--quiet", str(src), str(out)]
    if rgb:  # PDF/A can't keep print-shop spot colours (Separation/DeviceN); convert them
        cmd[-3:-3] = ["--color-conversion-strategy", "RGB"]
    return run(cmd, opts.timeout)


def _needs_rgb(r: subprocess.CompletedProcess) -> bool:
    return r.returncode != 0 and ("ColorConversionNeeded" in r.stderr or "color space" in r.stderr.lower())


def process(original: Path, ftype: FileType, workdir: Path, opts: Options) -> Result:
    """The whole pipeline for one document. Raises OcrFailed when retrying can't help."""
    warnings = []
    if ftype.kind == "image":
        pdf_in = image_to_pdf(original, ftype, workdir / "input.pdf")
    else:
        pdf_in = original
    pages = inspect_pdf(pdf_in)
    if pages == 0:
        raise OcrFailed("the PDF has no pages")
    if pages > opts.max_pages:
        raise OcrFailed(f"the PDF has {pages} pages; the limit is {opts.max_pages}")

    archive = workdir / "archive.pdf"
    r = ocrmypdf(pdf_in, archive, opts)
    if _needs_rgb(r):
        # e.g. bank statements printed with spot colours: found on 2 of 33 real documents
        warnings.append("colours converted to RGB to make the archive PDF")
        r = ocrmypdf(pdf_in, archive, opts, rgb=True)
    if r.returncode in (_EXIT_PDFA, _EXIT_OUTPUT_INVALID) or _needs_rgb(r):
        # Some PDFs can't be turned into PDF/A; a normal searchable PDF is just as good to read and search
        warnings.append("saved as a standard PDF because it couldn't be converted to PDF/A")
        r = ocrmypdf(pdf_in, archive, opts, pdfa=False)
    if r.returncode != 0:
        detail = (r.stderr.strip().splitlines() or ["no details"])[-1][:500]
        if r.returncode == _EXIT_ENCRYPTED:
            raise OcrFailed("the PDF is encrypted and can't be read")
        if r.returncode == _EXIT_INPUT:
            if ftype.kind == "pdf":
                # OCR failed, but a PDF made on a computer may still have readable text: keep it searchable
                text = pdf_text(pdf_in)
                if text:
                    warnings.append(f"OCR failed ({detail}); using the text already in the PDF")
                    thumb = _try_thumbnail(pdf_in, workdir, warnings)
                    return Result(None, text, pages, thumb, warnings)
            raise OcrFailed(f"OCR failed: {detail}")
        raise RuntimeError(f"ocrmypdf exited with {r.returncode}: {detail}")

    text = pdf_text(archive)
    if not text:
        warnings.append("no text was found in this document")
    return Result(archive, text, pages, _try_thumbnail(archive, workdir, warnings), warnings)


def _try_thumbnail(pdf: Path, workdir: Path, warnings: list) -> Path | None:
    try:
        return thumbnail(pdf, workdir / "thumb.webp")
    except Exception as e:  # a missing thumbnail shouldn't fail the document
        warnings.append(f"no thumbnail: {e}")
        return None
