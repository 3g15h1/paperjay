"""Startup settings from environment variables. Everything else is set in the app and kept in the database
(OCR_LANGUAGE, DATE_ORDER and the AI_* variables only give settings.py their first values).

DATA_DIR        where PaperJay keeps everything (default ./data): paperjay.db, media/, backups/
CONSUME_DIR     folder watched for new files (default DATA_DIR/consume; "off" to disable)
OCR_WORKERS     how many documents are processed at once (default 1)
OCR_LANGUAGE    Tesseract language(s), e.g. "eng" or "deu+eng" (default eng)
DATE_ORDER      how to read dates like 03/04/2025: MDY (default) or DMY
MAX_UPLOAD_MB   largest file accepted (default 200)
MAX_PAGES       most pages per document (default 500)
OCR_TIMEOUT     seconds before one document's OCR is stopped (default 1800)
HTTPS_PORT      PaperJay's own HTTPS, for the live camera scanner (default 8443; "off" to disable, e.g. behind
                a reverse proxy that does HTTPS)
HTTPS_NAMES     extra names or addresses for its certificate, comma-separated (e.g. "nas.lan, 192.168.1.20")
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    workers: int = 1
    consume_dir: Path | None = None
    max_upload_mb: int = 200
    max_pages: int = 500
    ocr_timeout: int = 1800
    https_port: int | None = None
    https_names: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paperjay.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def ocr_jobs(self) -> int:
        """CPU cores each OCR run may use, so all workers together use the whole machine."""
        return max(1, (os.cpu_count() or 1) // self.workers)

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        # STATE_DIRECTORY is what systemd's StateDirectory= sets
        data = Path(env.get("DATA_DIR") or env.get("STATE_DIRECTORY", "").split(":")[0] or "data").resolve()
        consume = env.get("CONSUME_DIR", "").strip()
        consume_dir = None if consume.lower() == "off" else Path(consume).resolve() if consume else data / "consume"
        https = (env.get("HTTPS_PORT") or "8443").strip().lower()
        c = cls(data_dir=data, workers=int(env.get("OCR_WORKERS") or 1), consume_dir=consume_dir,
                max_upload_mb=int(env.get("MAX_UPLOAD_MB") or 200), max_pages=int(env.get("MAX_PAGES") or 500),
                ocr_timeout=int(env.get("OCR_TIMEOUT") or 1800),
                https_port=None if https in ("off", "0", "no", "false") else int(https),
                https_names=env.get("HTTPS_NAMES", ""))
        if c.workers < 1 or c.max_upload_mb < 1 or c.max_pages < 1 or c.ocr_timeout < 30:
            raise ValueError("OCR_WORKERS, MAX_UPLOAD_MB and MAX_PAGES must be 1 or more, OCR_TIMEOUT 30 or more")
        return c
