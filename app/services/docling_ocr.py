from pathlib import Path


class DoclingService:
    def __init__(self) -> None:
        from docling.document_converter import DocumentConverter

        self._converter = DocumentConverter()

    def convert(self, path: Path) -> str:
        result = self._converter.convert(str(path))
        return result.document.export_to_markdown()
