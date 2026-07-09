import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # Embedding (Ollama + Qwen3-embedding)
    embedding_model: str = "qwen3-embedding:4b"
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_dim: int = 2560

    # Qdrant
    qdrant_host: str = '127.0.0.1'
    qdrant_port: int = 6333
    qdrant_collection: str = "enterprise_kb"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Service
    service_host: str = "0.0.0.0"
    service_port: int = 8000
    log_level: str = "INFO"

    # OCR
    tesseract_cmd: str = "tesseract"
    ocr_lang: str = "chi_sim+eng"

    # Data
    data_dir: str = "./data"

    
    # JWT Auth
    jwt_secret_key: str = "change-me-in-production-use-a-strong-random-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 hours
    # Preset users (username:password, admin only)
    preset_users: str = "admin:admin123,user:user123"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
