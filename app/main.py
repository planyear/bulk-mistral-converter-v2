from dotenv import load_dotenv

load_dotenv()

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.models import FileResult, ProcessFolderResponse
from app.services.jobs import JobStore
from app.services.runner import process_directory

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _make_ocr_backend():
    backend = settings.OCR_BACKEND.lower()
    if backend == "docling":
        from app.services.docling_ocr import DoclingService

        return DoclingService()
    if backend == "mistral":
        if not settings.MISTRAL_API_KEY:
            raise RuntimeError("OCR_BACKEND=mistral but MISTRAL_API_KEY is empty")
        from app.services.mistral import MistralService

        return MistralService(settings.MISTRAL_API_KEY)
    raise RuntimeError(f"Unknown OCR_BACKEND: {backend}")


ocr = _make_ocr_backend()

job_store = JobStore(Path(settings.DATA_DIR), settings.JOB_TTL_SECONDS)

limiter = Limiter(key_func=get_remote_address)


def _sweeper_loop(stop_event: threading.Event) -> None:
    interval = max(60, settings.JOB_TTL_SECONDS // 6)
    while not stop_event.is_set():
        try:
            job_store.sweep()
        except Exception:
            log.exception("sweeper tick failed")
        stop_event.wait(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = threading.Event()
    t = threading.Thread(target=_sweeper_loop, args=(stop_event,), daemon=True)
    t.start()
    try:
        yield
    finally:
        stop_event.set()


app = FastAPI(
    title="Bulk Doc Converter",
    version="0.3.0",
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
    lifespan=lifespan,
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "ocr_backend": settings.OCR_BACKEND,
        "version": app.version,
    }


@app.post("/convert", response_model=ProcessFolderResponse)
def process_folder(
    input_dir: str = Form(...),
    output_dir: str = Form("./out"),
) -> ProcessFolderResponse:
    in_root = Path(input_dir).expanduser().resolve()
    if not in_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {in_root}")
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    results = process_directory(in_root, out_root, ocr)
    log.info("FILES FOUND: %d · OCR_BACKEND=%s", len(results), settings.OCR_BACKEND)
    hint = f"Run `/graphify {out_root} --obsidian --wiki` to build the knowledge graph + Obsidian vault."
    return ProcessFolderResponse(
        input_dir=str(in_root),
        output_dir=str(out_root),
        results=results,
        graphify_hint=hint,
    )


@app.post("/upload")
@limiter.limit(settings.UPLOAD_RATE_LIMIT)
async def upload(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")
    if len(files) > settings.MAX_FILES_PER_JOB:
        raise HTTPException(
            status_code=413,
            detail=f"too many files (max {settings.MAX_FILES_PER_JOB})",
        )
    job = job_store.create()
    total = 0
    count = 0
    try:
        for up in files:
            data = await up.read()
            total += len(data)
            if total > settings.MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds {settings.MAX_UPLOAD_BYTES} bytes",
                )
            job_store.write_upload(job, up.filename or "upload", data)
            count += 1
    except HTTPException:
        import shutil

        shutil.rmtree(job_store._job_root(job.id), ignore_errors=True)
        with job_store._lock:
            job_store._jobs.pop(job.id, None)
        raise

    background_tasks.add_task(job_store.run, job.id, ocr)
    return JSONResponse(
        status_code=202,
        content={"job_id": job.id, "status": "queued", "files_received": count},
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    download_url = f"/jobs/{job.id}/download" if job.status == "completed" else None
    return {
        "job_id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "results": [r.model_dump() for r in job.results],
        "error": job.error,
        "download_url": download_url,
    }


@app.get("/jobs/{job_id}/download")
def download_job(job_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail=f"job status is {job.status}")
    if not job.zip_path.exists():
        raise HTTPException(status_code=410, detail="zip artifact swept")
    return FileResponse(
        path=str(job.zip_path),
        media_type="application/zip",
        filename=f"{job.id}.zip",
    )
