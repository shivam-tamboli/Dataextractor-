from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "PDF Data Extractor"
    app_version: str = "1.0.0"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/dataextractor"

    # Upload
    upload_dir: str = "/tmp/pdf_uploads"
    max_file_size_mb: int = 50

    # OCR
    tesseract_cmd: str = "/usr/bin/tesseract"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
