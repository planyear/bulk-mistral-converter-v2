import zipfile
from pathlib import Path


def zip_directory(src: Path, dest: Path) -> Path:
    src = Path(src)
    dest = Path(dest)
    if not src.is_dir():
        raise ValueError(f"Not a directory: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            zf.write(p, arcname=str(p.relative_to(src)))
    return dest
