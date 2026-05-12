from pathlib import Path
from typing import Callable

from app.models import FileResult
from app.services.local_fs import walk
from app.services.textextract import (
    extract_csv,
    extract_docx,
    extract_text,
    extract_xlsx,
)


def _write_md(out_dir: Path, rel: Path, body: str) -> Path:
    out_path = out_dir / rel.with_suffix(".md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    return out_path


def process_directory(
    in_root: Path,
    out_root: Path,
    ocr,
    *,
    on_progress: Callable[[FileResult], None] | None = None,
) -> list[FileResult]:
    files = walk(in_root)
    results: list[FileResult] = []
    for f in files:
        try:
            if f.kind == "skip":
                r = FileResult(
                    path=str(f.rel),
                    kind=f.kind,
                    status="skipped",
                    reason=f"Unsupported extension: {f.ext}",
                )
                results.append(r)
                if on_progress:
                    on_progress(r)
                continue
            if f.kind == "ocr":
                body = ocr.convert(f.path)
            elif f.kind == "docx":
                body = extract_docx(f.path)
            elif f.kind == "xlsx":
                body = extract_xlsx(f.path)
            elif f.kind == "csv":
                body = extract_csv(f.path)
            elif f.kind == "text":
                body = extract_text(f.path)
            else:
                raise RuntimeError(f"Unknown kind: {f.kind}")
            out_path = _write_md(out_root, f.rel, body)
            r = FileResult(
                path=str(f.rel),
                kind=f.kind,
                status="processed",
                output_path=str(out_path.relative_to(out_root)),
            )
        except Exception as e:
            r = FileResult(
                path=str(f.rel),
                kind=f.kind,
                status="failed",
                reason=str(e),
            )
        results.append(r)
        if on_progress:
            on_progress(r)
    return results
