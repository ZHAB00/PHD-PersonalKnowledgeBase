"""RAG 辅助函数：父子分块展开。"""
from __future__ import annotations


def expand_parents(sources: list[dict], kb_id: str = "default") -> list[dict]:
    """将子分块展开为父分块，为 LLM 提供更丰富的上下文。"""
    from app.core import vector_store
    from qdrant_client import QdrantClient, models
    from app.config import settings as s

    parent_ids = set()
    for src in sources:
        pid = src.get("parent_id")
        if pid:
            parent_ids.add(pid)

    if not parent_ids:
        return sources

    client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, check_compatibility=False)
    col_name = f"kb_{kb_id}"
    parent_content_map = {}

    for pid in parent_ids:
        try:
            result = client.scroll(
                collection_name=col_name,
                scroll_filter=models.Filter(must=[
                    models.FieldCondition(key="doc_id", match=models.MatchValue(value=pid))
                ]),
                limit=1, with_payload=True, with_vectors=False,
            )
            if result[0]:
                parent_content_map[pid] = result[0][0].payload.get("content", "")
        except Exception:
            pass

    if not parent_content_map:
        return sources

    expanded = []
    seen_parents = set()
    for s in sources:
        pid = s.get("parent_id")
        if pid and pid in parent_content_map:
            if pid not in seen_parents:
                seen_parents.add(pid)
                expanded.append({
                    **s,
                    "content": parent_content_map[pid],
                    "score": max(s2["score"] for s2 in sources if s2.get("parent_id") == pid),
                })
        else:
            expanded.append(s)
    return expanded
