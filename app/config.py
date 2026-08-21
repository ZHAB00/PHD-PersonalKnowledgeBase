import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # DeepSeek 对话模型
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"

    # 向量化模型（Ollama + Qwen3-embedding）
    embedding_model: str = "qwen3-embedding:4b"
    embedding_base_url: str = "http://127.0.0.1:11434/v1"
    embedding_dim: int = 2560

    # Qdrant 向量库
    qdrant_host: str = '127.0.0.1'
    qdrant_port: int = 6333
    qdrant_collection: str = "pdh_pkg"

    # Redis 缓存
    redis_url: str = "redis://localhost:6379/0"

    # 服务配置
    service_host: str = "0.0.0.0"
    service_port: int = 8000
    log_level: str = "INFO"

    # OCR 配置
    tesseract_cmd: str = "tesseract"
    ocr_lang: str = "chi_sim+eng"

    # 数据目录
    data_dir: str = "./data"
    # Neo4j 知识图谱
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_enabled: bool = True
    neo4j_bin: str = ""
    neo4j_java_home: str = ""
    # GraphRAG 实体抽取模型（未设置时回退到 deepseek_model）
    graphrag_llm_model: str = "deepseek-v4-flash"
    # GraphRAG 自动入库采样数（0 表示全量，较慢且成本高）
    graph_ingest_max_chunks: int = 50
    # GraphRAG 检索最大图证据节点数
    graphrag_max_evidence: int = 8


    
    # JWT 鉴权
    jwt_secret_key: str = "change-me-in-production-use-a-strong-random-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 小时
    # 预设用户（用户名:密码，仅开发默认值；生产必须通过 PRESET_USERS 覆盖）
    preset_users: str = "admin:admin123:admin,user:user123:admin"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
