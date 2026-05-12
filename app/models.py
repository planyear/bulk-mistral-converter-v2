from pydantic import BaseModel


class ProcessFolderRequest(BaseModel):
    input_dir: str
    output_dir: str = "./out"


class FileResult(BaseModel):
    path: str
    kind: str
    status: str
    output_path: str | None = None
    reason: str | None = None


class ProcessFolderResponse(BaseModel):
    input_dir: str
    output_dir: str
    results: list[FileResult]
    graphify_hint: str
