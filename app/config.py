from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    OCR_BACKEND: str = "docling"
    MISTRAL_API_KEY: str = ""
    DATA_DIR: str = "/tmp/bulk_doc_converter"
    JOB_TTL_SECONDS: int = 3600
    MAX_UPLOAD_BYTES: int = 209715200
    MAX_FILES_PER_JOB: int = 50
    UPLOAD_RATE_LIMIT: str = "10/minute"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
