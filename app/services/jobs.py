import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.models import FileResult
from app.services.runner import process_directory
from app.services.zip_out import zip_directory

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "completed", "failed"]


@dataclass
class Job:
    id: str
    created_at: float
    updated_at: float
    status: JobStatus
    input_dir: Path
    output_dir: Path
    zip_path: Path
    results: list[FileResult] = field(default_factory=list)
    error: str | None = None


_CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _safe_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = _CTRL_CHARS.sub("", name)
    name = name.lstrip(".")
    if not name:
        name = "upload"
    if len(name.encode("utf-8")) > 200:
        stem = Path(name).stem
        suffix = Path(name).suffix
        while len(name.encode("utf-8")) > 200 and len(stem) > 1:
            stem = stem[:-1]
            name = stem + suffix
    return name


class JobStore:
    def __init__(self, data_dir: Path, ttl_seconds: int) -> None:
        self.data_dir = Path(data_dir)
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        (self.data_dir / "jobs").mkdir(parents=True, exist_ok=True)

    def _job_root(self, job_id: str) -> Path:
        return self.data_dir / "jobs" / job_id

    def create(self) -> Job:
        job_id = uuid.uuid4().hex
        root = self._job_root(job_id)
        in_dir = root / "in"
        out_dir = root / "out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        job = Job(
            id=job_id,
            created_at=now,
            updated_at=now,
            status="queued",
            input_dir=in_dir,
            output_dir=out_dir,
            zip_path=root / "out.zip",
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def write_upload(self, job: Job, filename: str, data: bytes) -> Path:
        safe = _safe_filename(filename)
        dest = job.input_dir / safe
        i = 1
        stem = dest.stem
        suffix = dest.suffix
        while dest.exists():
            dest = job.input_dir / f"{stem}_{i}{suffix}"
            i += 1
        resolved = dest.resolve()
        root = job.input_dir.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("path traversal blocked")
        dest.write_bytes(data)
        return dest

    def _set_status(self, job: Job, status: JobStatus, error: str | None = None) -> None:
        with self._lock:
            job.status = status
            job.updated_at = time.time()
            if error is not None:
                job.error = error

    def _append_result(self, job: Job, r: FileResult) -> None:
        with self._lock:
            job.results.append(r)
            job.updated_at = time.time()

    def run(self, job_id: str, ocr) -> None:
        job = self.get(job_id)
        if job is None:
            return
        self._set_status(job, "running")
        try:
            process_directory(
                job.input_dir,
                job.output_dir,
                ocr,
                on_progress=lambda r: self._append_result(job, r),
            )
            zip_directory(job.output_dir, job.zip_path)
            self._set_status(job, "completed")
        except Exception as e:
            log.exception("job %s failed", job_id)
            self._set_status(job, "failed", error=str(e))

    def sweep(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            ids = list(self._jobs.keys())
        for jid in ids:
            with self._lock:
                job = self._jobs.get(jid)
                if job is None:
                    continue
                if job.status not in ("completed", "failed"):
                    continue
                if now - job.updated_at < self.ttl_seconds:
                    continue
                del self._jobs[jid]
            root = self._job_root(jid)
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            removed += 1
        return removed
