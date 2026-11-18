"""Document scanner: turn a phone photo of a page into a clean "scanned" page.

    find the page's four corners (detect)  →  cut it out and straighten it (flatten)
    →  even out shadows and lighting, then colour, grayscale or black & white (clean_up)

Detection looks for the largest four-sided shape that stands out from the background: the photo is
shrunk, blurred and closed (so the text on the page doesn't count as edges), then edges are traced and the
biggest contour that simplifies to four corners wins. When nothing convincing is found, the whole photo is
used and the person adjusts the corners by hand.
"""
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

LOOKS = ("bw", "gray", "color")
WORK_SIZE = 1000          # detection runs on a copy this big (longest side)
MAX_SIZE = 4000           # photos are kept at most this big


@dataclass
class Detected:
    corners: list[list[float]]   # [[x, y] ×4] in photo pixels: top-left, top-right, bottom-right, bottom-left
    found: bool                  # False: no page edge found, the corners are the photo's own


def load(path) -> np.ndarray:
    """A photo as an RGB array, turned the way the camera held it (EXIF), HEIC included."""
    import pillow_heif
    pillow_heif.register_heif_opener()
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((MAX_SIZE, MAX_SIZE), Image.LANCZOS)
        return np.asarray(im).copy()


def order(pts) -> np.ndarray:
    """Four points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def detect(img: np.ndarray) -> Detected:
    h, w = img.shape[:2]
    scale = WORK_SIZE / max(h, w)
    small = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img
    scale = min(scale, 1.0)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    # wipe out the text: a page is a big light area, its writing is small dark detail
    gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)), iterations=3)
    v = float(np.median(gray))
    edges = cv2.Canny(gray, int(max(0, 0.66 * v)), int(min(255, 1.33 * v)))
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area_min = 0.2 * small.shape[0] * small.shape[1]
    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        hull = cv2.convexHull(c)
        if cv2.contourArea(hull) < area_min:
            break
        peri = cv2.arcLength(hull, True)
        for eps in (0.02, 0.03, 0.05):
            approx = cv2.approxPolyDP(hull, eps * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                best = approx
                break
        if best is not None:
            break
    if best is None:
        return Detected([[0, 0], [w, 0], [w, h], [0, h]], False)
    pts = order(best) / scale
    pts[:, 0] = pts[:, 0].clip(0, w)
    pts[:, 1] = pts[:, 1].clip(0, h)
    return Detected(pts.round(1).tolist(), True)


def flatten(img: np.ndarray, corners) -> np.ndarray:
    """Cut out the four-cornered page and make it a straight rectangle."""
    tl, tr, br, bl = order(corners)
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    width, height = max(width, 50), max(height, 50)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype=np.float32), dst)
    return cv2.warpPerspective(img, m, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _even_out(channel: np.ndarray) -> np.ndarray:
    """Divide by the page's own lighting (a blurred, text-free copy): shadows and dim corners go white."""
    k = max(15, (min(channel.shape) // 25) | 1)
    background = cv2.medianBlur(cv2.dilate(channel, np.ones((7, 7), np.uint8)), k)
    out = cv2.divide(channel, background, scale=255)
    return cv2.normalize(out, None, 0, 255, cv2.NORM_MINMAX)


def clean_up(page: np.ndarray, look: str = "bw") -> Image.Image:
    if look not in LOOKS:
        raise ValueError(f"look must be one of {', '.join(LOOKS)}")
    if look == "color":
        return Image.fromarray(cv2.merge([_even_out(c) for c in cv2.split(page)]))
    gray = _even_out(cv2.cvtColor(page, cv2.COLOR_RGB2GRAY))
    if look == "gray":
        return Image.fromarray(gray)
    block = max(15, (min(gray.shape) // 40) | 1)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 12)
    bw = cv2.medianBlur(bw, 3)  # specks
    return Image.fromarray(bw).convert("1")


def scan(img: np.ndarray, corners, look: str = "bw", rotate: int = 0) -> Image.Image:
    """The whole thing: flatten, clean up, and turn by 0/90/180/270 degrees (clockwise)."""
    page = flatten(img, corners)
    m = max(2, round(min(page.shape[:2]) * 0.006))  # trim a hair: no sliver of table along the edges
    out = clean_up(page[m:-m, m:-m], look)
    if rotate % 360:
        out = out.rotate(-(rotate % 360), expand=True)
    return out
