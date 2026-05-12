from pathlib import Path

from mistralai import Mistral

from app.services.postprocess import to_wrapped_markdown


class MistralService:
    def __init__(self, api_key: str) -> None:
        self.client = Mistral(api_key=api_key)

    def convert(self, path: Path) -> str:
        data = path.read_bytes()
        uploaded = self.client.files.upload(
            file={"file_name": path.name, "content": data},
            purpose="ocr",
        )
        res = self.client.ocr.process(
            model="mistral-ocr-latest",
            document={"type": "file", "file_id": uploaded.id},
            table_format="html",
        )
        return to_wrapped_markdown(res)
