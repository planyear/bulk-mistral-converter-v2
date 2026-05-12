from dataclasses import dataclass
from pathlib import Path

OCR_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}
DOCX_EXTS = {".docx"}
XLSX_EXTS = {".xlsx", ".xlsm"}
CSV_EXTS = {".csv"}
TEXT_EXTS = {".txt", ".md", ".markdown"}


@dataclass
class LocalFile:
    path: Path
    rel: Path
    ext: str
    kind: str


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in OCR_EXTS:
        return "ocr"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in XLSX_EXTS:
        return "xlsx"
    if ext in CSV_EXTS:
        return "csv"
    if ext in TEXT_EXTS:
        return "text"
    return "skip"


def walk(root: Path) -> list[LocalFile]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    out: list[LocalFile] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        ext = p.suffix.lower()
        out.append(LocalFile(path=p, rel=rel, ext=ext, kind=classify(p)))
    return out
