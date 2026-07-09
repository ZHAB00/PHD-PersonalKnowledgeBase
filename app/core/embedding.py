"""Embedding wrapper v2: LangChain 1.0+ OllamaEmbeddings

Uses langchain_ollama.OllamaEmbeddings for standard interface.
Fallback to raw HTTP for compatibility.
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_embedding_model: Optional[object] = None
_embedding_dim: Optional[int] = None


def get_embeddings():
    """Get or create OllamaEmbeddings instance (LangChain 1.0+)."""
    global _embedding_model, _embedding_dim
    if _embedding_model is None:
        from app.config import settings
        from langchain_ollama import OllamaEmbeddings

        model_name = settings.embedding_model or "qwen3-embedding:4b"
        _embedding_model = OllamaEmbeddings(
            model=model_name,
            base_url=(settings.embedding_base_url or "http://localhost:11434").replace("/v1", ""),
        )
        # Probe embedding dim
        try:
            test_vec = _embedding_model.embed_query("test")
            _embedding_dim = len(test_vec)
            logger.info(f"Embedding model: {model_name}, dim={_embedding_dim}")
        except Exception:
            _embedding_dim = 2048  # qwen3-embedding:4b default
            logger.warning(f"Could not probe dim, using default {_embedding_dim}")
    return _embedding_model


def embed_text(text: str) -> list[float]:
    """Embed a single text string."""
    emb = get_embeddings()
    return emb.embed_query(text)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed multiple text strings."""
    emb = get_embeddings()
    return emb.embed_documents(texts)


def get_embedding_dim() -> int:
    """Get embedding vector dimension."""
    global _embedding_dim
    if _embedding_dim is None:
        get_embeddings()
    return _embedding_dim or 2048

# Legacy alias
embedding_dimension = get_embedding_dim
