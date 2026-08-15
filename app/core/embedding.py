"""向量化封装：支持本地 ONNX、Ollama、OpenAI 兼容接口。"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from app.config import settings as env
from app.core.user_settings import embedding_config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_embedding_model: Optional[object] = None
_embedding_dim: Optional[int] = None


def _bundled_model_dir() -> Optional[Path]:
    """定位安装包内置的向量模型缓存目录。"""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    for candidate in (exe_dir / "models", exe_dir.parent / "models"):
        if (candidate / "models--Qdrant--bge-small-zh-v1.5").exists():
            return candidate
    return None


def _build_local_model():
    from fastembed import TextEmbedding
    cfg = embedding_config()
    model_name = cfg["model"] or "BAAI/bge-small-zh-v1.5"
    bundled = _bundled_model_dir()
    if bundled is not None and model_name == "BAAI/bge-small-zh-v1.5":
        cache_dir = str(bundled)
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    else:
        cache_dir = str(Path(env.data_dir) / "models")
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return TextEmbedding(
        model_name=model_name,
        cache_dir=cache_dir,
    )


def get_embeddings():
    global _embedding_model
    with _lock:
        if _embedding_model is None:
            cfg = embedding_config()
            if cfg["provider"] == "local":
                _embedding_model = _build_local_model()
            elif cfg["provider"] == "ollama":
                from langchain_ollama import OllamaEmbeddings
                base = cfg["base_url"] or env.embedding_base_url
                _embedding_model = OllamaEmbeddings(
                    model=cfg["model"] or env.embedding_model,
                    base_url=base.replace("/v1", ""),
                )
            else:
                from openai import OpenAI
                _embedding_model = OpenAI(
                    base_url=cfg["base_url"] or env.embedding_base_url,
                    api_key=cfg["api_key"] or env.deepseek_api_key,
                    timeout=60.0,
                )
        return _embedding_model


def embed_text(text: str) -> list[float]:
    """对单个文本进行向量化。"""
    cfg = embedding_config()
    if cfg["provider"] == "local":
        model = get_embeddings()
        return [float(x) for x in next(model.embed([text]))]
    if cfg["provider"] == "ollama":
        return get_embeddings().embed_query(text)
    client = get_embeddings()
    model = cfg["model"] or "text-embedding-3-small"
    resp = client.embeddings.create(model=model, input=text)
    return resp.data[0].embedding


def embed_texts(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """批量向量化文本。"""
    cfg = embedding_config()
    if cfg["provider"] == "local":
        model = get_embeddings()
        return [[float(x) for x in vec] for vec in model.embed(texts, batch_size=batch_size)]
    if cfg["provider"] == "ollama":
        return [embed_text(t) for t in texts]
    client = get_embeddings()
    model = cfg["model"] or "text-embedding-3-small"
    all_vectors = []
    for i in range(0, len(texts), batch_size):
        resp = client.embeddings.create(model=model, input=texts[i:i + batch_size])
        all_vectors.extend([d.embedding for d in resp.data])
    return all_vectors


def get_embedding_dim() -> int:
    """获取向量维度。"""
    global _embedding_dim
    if _embedding_dim is None:
        try:
            _embedding_dim = len(embed_text("测试"))
        except Exception:
            _embedding_dim = 512
    return _embedding_dim


def reset_embedding():
    """清空向量模型缓存，设置变更后热切换。"""
    global _embedding_model, _embedding_dim
    with _lock:
        _embedding_model = None
        _embedding_dim = None


# 旧版兼容别名
embedding_dimension = get_embedding_dim
