"""Fake phone photos of a page: perspective, a table behind it, uneven light and a shadow."""
import cv2
import numpy as np

import docgen


def photo(lines=docgen.LINES, corners=((210, 155), (1260, 210), (1350, 1780), (130, 1710)), size=(1512, 2016),
          seed=1, shadow=True):
    """A 3-megapixel photo (big enough to read, small enough for a test machine)."""
    rng = np.random.default_rng(seed)
    page = np.asarray(docgen.page_image(lines=lines).resize((1275, 1650)))
    w, h = size
    # wood-ish table: brown with streaks and noise
    table = np.zeros((h, w, 3), np.float32)
    table[:] = (96, 68, 44)
    streaks = cv2.resize(rng.normal(0, 18, (h // 40, 1)).astype(np.float32), (w, h))
    table += streaks[..., None] + rng.normal(0, 6, (h, w, 1))
    src = np.float32([[0, 0], [page.shape[1], 0], [page.shape[1], page.shape[0]], [0, page.shape[0]]])
    m = cv2.getPerspectiveTransform(src, np.float32(corners))
    warped = cv2.warpPerspective(page, m, (w, h)).astype(np.float32)
    mask = cv2.warpPerspective(np.ones(page.shape[:2], np.float32), m, (w, h))[..., None]
    img = table * (1 - mask) + warped * mask
    # light falls off to one side; a soft shadow crosses the page
    light = np.linspace(1.0, 0.72, w, dtype=np.float32)[None, :, None]
    img *= light
    if shadow:
        yy, xx = np.mgrid[0:h, 0:w]
        band = np.exp(-(((xx + yy * 0.6) - 1300) / 190.0) ** 2).astype(np.float32)[..., None]
        img *= 1 - 0.35 * band
    img += rng.normal(0, 3, img.shape)
    return img.clip(0, 255).astype(np.uint8), corners
