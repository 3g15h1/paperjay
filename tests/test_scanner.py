"""The camera scanner: finding the page, straightening and cleaning it, and the API."""
import io

import cv2
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from paperjay import ocr, scanner
from paperjay.app import create_app
from paperjay.config import Config

import photogen
from conftest import sign_in_client

needs_ocr = pytest.mark.skipif(bool(ocr.missing_tools()), reason="OCR tools missing")


def test_finds_the_page_in_a_photo():
    img, truth = photogen.photo()
    d = scanner.detect(img)
    assert d.found and np.abs(np.array(d.corners) - np.array(truth)).max() < 25


def test_no_page_means_the_whole_photo():
    flat = np.full((800, 600, 3), 128, np.uint8)
    d = scanner.detect(flat)
    assert not d.found and d.corners == [[0, 0], [600, 0], [600, 800], [0, 800]]


def test_corner_order_doesnt_matter():
    pts = [[600, 20], [30, 700], [10, 40], [580, 760]]
    assert scanner.order(pts).tolist() == [[10, 40], [600, 20], [580, 760], [30, 700]]


@needs_ocr
@pytest.mark.parametrize("look,mode", [("bw", "1"), ("gray", "L"), ("color", "RGB")])
def test_scan_is_straight_clean_and_readable(look, mode, tmp_path):
    img, truth = photogen.photo()
    out = scanner.scan(img, truth, look)
    assert out.mode == mode and out.width < out.height
    # the shadow is gone: the empty lower part of the page is evenly light
    gray = np.asarray(out.convert("L"), np.float32)
    blank = gray[int(gray.shape[0] * 0.6):int(gray.shape[0] * 0.9), int(gray.shape[1] * 0.1):int(gray.shape[1] * 0.9)]
    assert blank.mean() > 235 and blank.std() < 12
    out.save(tmp_path / "s.png")
    text = subprocess.run(["tesseract", str(tmp_path / "s.png"), "-"], capture_output=True, text=True).stdout
    assert "Account Number: 4471-2290-18" in text and "$142.37" in text


def test_rotate():
    img, truth = photogen.photo()
    assert scanner.scan(img, truth, "gray", rotate=90).width > scanner.scan(img, truth, "gray").width


@needs_ocr
def test_scan_api_end_to_end(tmp_path):
    img, _ = photogen.photo()
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=88)
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None))) as c:
        sign_in_client(c)
        p1 = c.post("/api/scan/pages", files={"file": ("IMG_1.jpg", buf.getvalue())}).json()
        assert p1["found"] and p1["width"] == 1512
        assert c.get(f"/api/scan/pages/{p1['id']}/photo", params={"w": 600}).content[8:12] == b"WEBP"
        corners = ",".join(str(round(v)) for pt in p1["corners"] for v in pt)
        r = c.get(f"/api/scan/pages/{p1['id']}/preview", params={"corners": corners, "look": "bw"})
        assert r.status_code == 200 and r.content[8:12] == b"WEBP"
        assert c.get(f"/api/scan/pages/{p1['id']}/preview", params={"corners": "1,2,3"}).status_code == 400
        assert c.post("/api/scan/pages", files={"file": ("x.pdf", b"%PDF-1.4 nope")}).status_code == 415
        p2 = c.post("/api/scan/pages", files={"file": ("IMG_2.jpg", buf.getvalue())}).json()
        r = c.post("/api/scan/finish", json={"pages": [
            {"id": p1["id"], "corners": p1["corners"], "look": "bw"},
            {"id": p2["id"], "corners": p2["corners"], "look": "gray", "rotate": 0}]}).json()
        up = r["uploads"][0]
        assert up["outcome"] == "accepted" and up["filename"].startswith("Scan ")
        import time
        for _ in range(240):
            d = c.get(f"/api/documents/{up['document_id']}").json()
            if d["status"] in ("ready", "failed"):
                break
            time.sleep(0.5)
        assert d["status"] == "ready" and d["page_count"] == 2 and "Account Number" in d["content"]
        assert c.get(f"/api/scan/pages/{p1['id']}/photo").status_code == 404   # cleaned up
        assert c.get("/api/scan/pages/../../etc/photo").status_code == 404


def test_live_camera_detect_and_hint(tmp_path):
    img, corners = photogen.photo()
    small = cv2.resize(img, (360, 480), interpolation=cv2.INTER_AREA)
    frame = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes()
    blank = np.full((1600, 1200, 3), 128, np.uint8)
    buf = io.BytesIO()
    Image.fromarray(blank).save(buf, "JPEG")
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        d = c.post("/api/scan/detect", content=frame, headers={"Content-Type": "image/jpeg"}).json()
        assert d["found"]
        want = np.array(corners) / np.array([1512, 2016])
        assert np.abs(np.array(d["corners"]) - want).max() < 0.02          # within 2% of the frame
        assert c.post("/api/scan/detect", content=b"not a picture").status_code == 400
        # the page couldn't be found in the photo itself: the live camera's outline is used instead
        hint = "0.1,0.1,0.9,0.1,0.9,0.9,0.1,0.9"
        p = c.post("/api/scan/pages", files={"file": ("f.jpg", buf.getvalue())}, data={"hint": hint}).json()
        assert p["found"] and p["corners"][0] == [120.0, 160.0] and p["corners"][2] == [1080.0, 1440.0]
        p = c.post("/api/scan/pages", files={"file": ("f.jpg", buf.getvalue())}).json()
        assert not p["found"] and p["corners"][2] == [1200, 1600]


@needs_ocr
def test_scan_finish_separate_makes_one_document_per_page(tmp_path):
    img, _ = photogen.photo()
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=88)
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None))) as c:
        sign_in_client(c)
        ps = [c.post("/api/scan/pages", files={"file": (f"IMG_{i}.jpg", buf.getvalue() + bytes([i]))}).json() for i in range(2)]
        pages = [{"id": p["id"], "corners": p["corners"], "look": "gray"} for p in ps]
        assert c.post("/api/scan/finish", json={"pages": pages, "mode": "sideways"}).status_code == 400
        r = c.post("/api/scan/finish", json={"pages": pages, "mode": "separate"}).json()
        assert len(r["uploads"]) == 2
        assert len({u["document_id"] for u in r["uploads"]}) == 2
        assert [u["filename"].endswith(f"({i + 1}).pdf") for i, u in enumerate(r["uploads"])] == [True, True]
        for p in ps:
            assert c.get(f"/api/scan/pages/{p['id']}/photo").status_code == 404   # cleaned up
