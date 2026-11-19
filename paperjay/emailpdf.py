"""An email as a PDF: for bills and statements that arrive in the body of an email, not as an attachment.

The PDF shows who it's from, to whom, when and the subject, then the text of the email (the plain-text
part, or the HTML part turned into text: no pictures, scripts or links are fetched), and the names of its
attachments. It has a real text layer, so it is searchable straight away and OCR leaves it as it is.
Characters the font lacks are replaced; with DejaVu Sans installed (fonts-dejavu-core) every common script
shows.
"""
import email.utils
import html
import io
import re
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/local/share/fonts")
MAX_CHARS = 200_000
_BLOCK = re.compile(r"</?(?:p|div|br|tr|li|h[1-6]|table|ul|ol|blockquote|section|article|header|footer)\b[^>]*>", re.I)


def _font() -> tuple[str, str] | None:
    for d in FONT_DIRS:
        regular, bold = Path(d, "DejaVuSans.ttf"), Path(d, "DejaVuSans-Bold.ttf")
        if regular.exists():
            return str(regular), str(bold if bold.exists() else regular)
    return None


def html_to_text(markup: str) -> str:
    """Readable text from an email's HTML: block elements become line breaks, everything else is dropped."""
    markup = re.sub(r"(?is)<(script|style|head|title)\b.*?</\1\s*>", " ", markup)
    markup = re.sub(r"(?is)<!--.*?-->", " ", markup)
    markup = re.sub(r"(?i)</t[dh]\s*>", "  ", markup)
    markup = _BLOCK.sub("\n", markup)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", "", markup))
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    out, blank = [], 0
    for line in lines:
        blank = blank + 1 if not line else 0
        if blank <= 1:
            out.append(line)
    return "\n".join(out).strip()


def body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        content = html_to_text(content)
    return content.replace("\r\n", "\n").strip()[:MAX_CHARS]


def attachment_names(msg) -> list[str]:
    return [p.get_filename() for p in msg.walk() if not p.is_multipart() and p.get_filename()]


def sent_at(msg) -> datetime | None:
    try:
        d = email.utils.parsedate_to_datetime(str(msg.get("Date", "")))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def render(msg) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(18, 18, 18)
    font = _font()
    if font:
        pdf.add_font("body", "", font[0])
        pdf.add_font("body", "B", font[1])
        family = "body"
        clean = lambda s: s  # noqa: E731
    else:
        family = "helvetica"
        clean = lambda s: (s.replace("’", "'").replace("‘", "'").replace("“", '"')  # noqa: E731
                           .replace("”", '"').replace("–", "-").replace("—", "-")
                           .encode("latin-1", "replace").decode("latin-1"))
    when = sent_at(msg)
    if when:
        pdf.set_creation_date(when)
    pdf.set_title(clean(str(msg.get("Subject", "")) or "Email"))
    pdf.set_creator("PaperJay")
    pdf.add_page()
    pdf.set_font(family, "B", 15)
    pdf.multi_cell(0, 7, new_x="LMARGIN", new_y="NEXT", text=clean(str(msg.get("Subject", "")).strip() or "(no subject)"))
    pdf.ln(2)
    pdf.set_font(family, "", 9.5)
    pdf.set_text_color(90, 90, 90)
    for label, value in (("From", msg.get("From")), ("To", msg.get("To")), ("Cc", msg.get("Cc")),
                         ("Date", when.strftime("%Y-%m-%d %H:%M %Z").strip() if when else msg.get("Date"))):
        if value:
            pdf.multi_cell(0, 5, new_x="LMARGIN", new_y="NEXT", text=clean(f"{label}: {value}"))
    names = attachment_names(msg)
    if names:
        pdf.multi_cell(0, 5, new_x="LMARGIN", new_y="NEXT", text=clean("Attachments: " + ", ".join(names)))
    pdf.ln(3)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_text_color(20, 20, 20)
    pdf.set_font(family, "", 10.5)
    text = body_text(msg) or "(this email has no text)"
    for para in text.split("\n"):
        pdf.multi_cell(0, 5.2, new_x="LMARGIN", new_y="NEXT", text=clean(para) or " ", wrapmode="CHAR")
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def file_name(msg) -> str:
    subject = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(msg.get("Subject", "")).strip())
    subject = " ".join(subject.split())[:80].strip(" .") or "Email"
    return f"{subject}.pdf"
