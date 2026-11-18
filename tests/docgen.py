"""Make realistic test documents: text rendered as a 'scanned' page image, optionally rotated or skewed."""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = [
    "ACME POWER COMPANY",
    "Statement Date: August 14, 2025",
    "Account Number: 4471-2290-18",
    "Amount Due: $142.37",
    "Please pay by September 5, 2025.",
    "Thank you for being a valued customer.",
]


def _font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def page_image(lines=LINES, width=2550, height=3300, rotate=0, skew=0.0) -> Image.Image:
    """A letter page at 300 dpi. rotate: 0/90/180/270 (as if the phone was turned). skew: degrees."""
    im = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(im)
    f = _font(64)
    y = 250
    for line in lines:
        d.text((220, y), line, fill="black", font=f)
        y += 140
    if skew:
        im = im.rotate(skew, expand=False, fillcolor="white", resample=Image.BICUBIC)
    if rotate:
        im = im.rotate(rotate, expand=True)
    return im


def text_pdf(path: Path, lines=LINES) -> Path:
    """A 'born-digital' PDF that already has real text, made with Ghostscript from PostScript."""
    ps = path.with_suffix(".ps")
    body = "".join(f"72 {720 - i * 24} moveto ({line}) show\n" for i, line in enumerate(lines))
    ps.write_text(f"%!PS\n/Helvetica findfont 14 scalefont setfont\n{body}showpage\n")
    subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite", f"-sOutputFile={path}", str(ps)],
                   check=True)
    ps.unlink()
    return path
