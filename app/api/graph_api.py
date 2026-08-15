"""图谱可视化 API：返回用于 vis-network 的实体关系数据"""
from __future__ import annotations
import logging
from fastapi import APIRouter, Query
from app.rag.graph_rag import _get_driver, _neo4j_database, EXTRACTION_PROMPT
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/data")
async def get_graph_data(
    kb_id: str = Query("default"),
    search: str = Query(""),
    limit: int = Query(100),
):
    """返回封闭子图：返回节点集合的边是完整的。"""
    driver = _get_driver(force_check=True)
    if not driver:
        return {"nodes": [], "edges": [], "kb_id": kb_id, "total_entities": 0, "total_relations": 0, "isolated_count": 0}
    limit = max(1, min(limit, 500))
    nodes = []
    edges = []
    node_ids = []

    with driver.session(database=_neo4j_database()) as session:
        # 获取实体（节点）
        if search:
            result = session.run(
                """
                MATCH (e:Entity {kb_id: $kb_id})
                WHERE e.name CONTAINS $search
                RETURN e
                LIMIT $limit
                """,
                kb_id=kb_id, search=search, limit=limit,
            )
            ids = {record["e"]["id"] for record in result}
            if ids:
                result = session.run(
                    """
                    MATCH (e:Entity {kb_id: $kb_id}) WHERE e.id IN $ids
                    MATCH (e)-[:RELATES_TO]-(n:Entity {kb_id: $kb_id})
                    RETURN DISTINCT n.id AS id
                    LIMIT $limit
                    """,
                    kb_id=kb_id, ids=list(ids), limit=limit,
                )
                ids.update(record["id"] for record in result)
                result = session.run(
                    """
                    MATCH (e:Entity {kb_id: $kb_id}) WHERE e.id IN $ids
                    MATCH (e)-[:MENTIONED_IN]->(c:Chunk {kb_id: $kb_id})<-[:MENTIONED_IN]-(n:Entity {kb_id: $kb_id})
                    WHERE NOT n.id IN $ids
                    RETURN DISTINCT n.id AS id
                    LIMIT $limit
                    """,
                    kb_id=kb_id, ids=list(ids), limit=limit,
                )
                ids.update(record["id"] for record in result)
            node_ids = list(ids)[:limit]
        else:
            result = session.run(
                """
                MATCH (e:Entity {kb_id: $kb_id})
                OPTIONAL MATCH (e)-[:RELATES_TO]-(n:Entity {kb_id: $kb_id})
                WITH e, count(n) AS rel_deg
                OPTIONAL MATCH (e)-[:MENTIONED_IN]->(c:Chunk {kb_id: $kb_id})<-[:MENTIONED_IN]-(m:Entity {kb_id: $kb_id})
                WHERE m IS NULL OR m <> e
                WITH e, rel_deg, count(DISTINCT m) AS co_deg
                RETURN e
                ORDER BY rel_deg + co_deg DESC, e.name
                LIMIT $limit
                """,
                kb_id=kb_id, limit=limit,
            )
            node_ids = [record["e"]["id"] for record in result]

        if not node_ids:
            return {"nodes": [], "edges": [], "kb_id": kb_id, "total_entities": 0, "total_relations": 0, "isolated_count": 0}

        # 获取选定节点集合的属性。
        result = session.run(
            """
            MATCH (e:Entity {kb_id: $kb_id})
            WHERE e.id IN $ids
            RETURN e
            """,
            kb_id=kb_id, ids=node_ids,
        )
        seen_nodes = set()
        for record in result:
            e = record["e"]
            eid = e["id"]
            if eid in seen_nodes:
                continue
            seen_nodes.add(eid)
            nodes.append({
                "id": eid,
                "label": e.get("name", eid),
                "title": f"{e.get('type', '')}: {e.get('description', '')}",
                "group": e.get("type", "概念"),
                "color": _type_color(e.get("type", "")),
            })

        # 所有两端都在节点集合内的 RELATES_TO 边。
        result = session.run(
            """
            MATCH (a:Entity {kb_id: $kb_id})-[r:RELATES_TO]->(b:Entity {kb_id: $kb_id})
            WHERE a.id IN $ids AND b.id IN $ids
            RETURN a.id AS source, b.id AS target, r.type AS label, r.description AS desc
            """,
            kb_id=kb_id, ids=node_ids,
        )
        edge_ids = set()
        for record in result:
            edge_key = f"{record['source']}->{record['target']}"
            if edge_key in edge_ids:
                continue
            edge_ids.add(edge_key)
            edges.append({
                "from": record["source"],
                "to": record["target"],
                "label": record["label"],
                "title": record.get("desc", ""),
                "arrows": "to",
            })

        # 所有两端都在节点集合内的共现边。
        result = session.run(
            """
            MATCH (a:Entity {kb_id: $kb_id})-[:MENTIONED_IN]->(c:Chunk {kb_id: $kb_id})<-[:MENTIONED_IN]-(b:Entity {kb_id: $kb_id})
            WHERE a.id IN $ids AND b.id IN $ids AND a.id < b.id
            RETURN a.id AS source, b.id AS target, count(c) AS shared_chunks
            """,
            kb_id=kb_id, ids=node_ids,
        )
        for record in result:
            edge_key = f"{record['source']}~~{record['target']}"
            if edge_key in edge_ids:
                continue
            edge_ids.add(edge_key)
            edges.append({
                "from": record["source"],
                "to": record["target"],
                "label": "共现",
                "title": f"同时出现在 {record['shared_chunks']} 个片段中",
                "arrows": "",
                "dashes": True,
            })

    connected = set()
    for edge in edges:
        connected.add(edge["from"])
        connected.add(edge["to"])
    isolated_count = sum(1 for node in nodes if node["id"] not in connected)
    return {"nodes": nodes, "edges": edges, "kb_id": kb_id, "total_entities": len(nodes), "total_relations": len(edges), "isolated_count": isolated_count}


def _type_color(entity_type: str) -> str:
    """将实体类型映射为图谱颜色。"""
    type_lower = entity_type.lower()
    if any(k in type_lower for k in ["模型", "model", "llm"]):
        return "#3b82f6"  # blue
    if any(k in type_lower for k in ["技术", "技术架构", "架构", "framework"]):
        return "#10b981"  # green
    if any(k in type_lower for k in ["概念", "概念/方法", "方法"]):
        return "#f59e0b"  # amber
    if any(k in type_lower for k in ["工具", "tool", "平台"]):
        return "#8b5cf6"  # purple
    if any(k in type_lower for k in ["人物", "组织", "公司"]):
        return "#ef4444"  # red
    if any(k in type_lower for k in ["算法", "algorithm"]):
        return "#ec4899"  # pink
    return "#6b7280"  # gray


@router.get("/stats")
async def get_graph_stats(kb_id: str = Query("default")):
    """返回图谱统计信息。"""
    from app.rag.graph_rag import graph_stats
    return graph_stats(kb_id)



@router.post("/build")
async def build_graph(
    kb_id: str = Query("default"),
    max_chunks: int = Query(50),
):
    """根据指定知识库已有的 Qdrant 分块构建知识图谱。"""
    import asyncio, concurrent.futures
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _build_graph_task_sync, kb_id, max_chunks)
    return {"status": "started", "kb_id": kb_id, "message": "后台构建中，前端可正常使用"}




def _build_graph_task_sync(kb_id: str, max_chunks: int):
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_build_graph_task(kb_id, max_chunks))
    finally:
        loop.close()

async def _build_graph_task(kb_id: str, max_chunks: int):
    """完整图谱构建：清空旧数据、处理全部分块、批量写入 Neo4j。"""
    import asyncio, json as _json, re as _re, httpx as _httpx
    from openai import OpenAI
    from qdrant_client import QdrantClient
    from app.rag.graph_rag import delete_kb_graph, graph_stats, _get_driver, _safe_neo4j_value
    from app.core.user_settings import chat_config



    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, check_compatibility=False)
    all_chunks = []
    offset = None
    while True:
        result = client.scroll(collection_name=f"kb_{kb_id}", limit=200, offset=offset, with_payload=True, with_vectors=False)
        points, next_offset = result[0], result[1]
        if not points: break
        all_chunks.extend(points)
        if next_offset is None: break
        offset = next_offset

    if not all_chunks:
        logger.warning("Graph build: no chunks for kb=%s", kb_id)
        return

    meaningful = [p for p in all_chunks if len(p.payload.get("content", "")) > 80] or all_chunks
    to_process = meaningful if max_chunks <= 0 else meaningful[:max_chunks]
    total = len(to_process)
    logger.info("Graph build: %d chunks, kb=%s", total, kb_id)

    cfg = chat_config()
    llm = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=_httpx.Timeout(60.0, connect=10.0))
    # Verify the extraction model before clearing the old graph.
    try:
        probe_txt = to_process[0].payload.get("content", "")[:3000]
        probe_prompt = EXTRACTION_PROMPT.replace("{chunk_text}", probe_txt or "test")
        llm.chat.completions.create(model=cfg["model"], messages=[{"role":"user","content":probe_prompt}], temperature=0.3, max_tokens=64)
    except Exception as e:
        logger.warning("Graph build aborted before clearing old graph: %s", e)
        return

    delete_kb_graph(kb_id)
    logger.info("Graph build: cleared kb=%s", kb_id)

    BATCH = 25
    ent_buf = []
    rel_buf = []
    ok = 0
    fail = 0

    for idx, p in enumerate(to_process):
        txt = p.payload.get("content", "")
        did = p.payload.get("doc_id", str(p.id))
        cidx = p.payload.get("chunk_index", 0)
        cid = f"{did}:{cidx}"

        try:
            prompt = EXTRACTION_PROMPT.replace("{chunk_text}", txt[:3000])
            resp = llm.chat.completions.create(model=cfg["model"], messages=[{"role":"user","content":prompt}], temperature=0.3, max_tokens=2048)
            raw = resp.choices[0].message.content.strip()
            m = _re.search(r"\{[\s\S]*\}", raw)
            if m:
                data = _json.loads(m.group())
                for e in data.get("entities", []):
                    ent_buf.append((e, txt, cid, did))
                for r in data.get("relations", []):
                    rel_buf.append((r, txt, cid, did))
            ok += 1
        except Exception as e:
            fail += 1
            if fail <= 3: logger.warning("Graph chunk %d fail: %s", idx+1, e)

        if len(ent_buf) >= BATCH * 8 or (idx == total-1 and (ent_buf or rel_buf)):
            _flush_graph_batch(ent_buf, rel_buf, kb_id)
            ent_buf.clear()
            rel_buf.clear()

        if (idx+1) % 50 == 0:
            logger.info("Graph build: %d/%d (fail:%d)", idx+1, total, fail)

    stats = graph_stats(kb_id)
    logger.info("Graph DONE kb=%s: %d chunks, %dE/%dR (fail:%d)", kb_id, ok, stats["entities"], stats["relations"], fail)


def _flush_graph_batch(ent_buf, rel_buf, kb_id):
    """批量将实体和关系写入 Neo4j。"""
    from app.rag.graph_rag import _get_driver, _safe_neo4j_value as _sv, normalize_entity_name as _norm
    driver = _get_driver(force_check=True)
    if not driver: return
    with driver.session(database=_neo4j_database()) as session:
        seen = {}
        for ent, ct, cid, did in ent_buf:
            raw_nm = ent.get("name", "")
            if not raw_nm.strip(): continue
            nm = _sv(raw_nm.strip())
            eid = f"{kb_id}:{_norm(raw_nm)}"
            if eid not in seen:
                seen[eid] = (nm, _sv(ent.get("type", "概念")), _sv(ent.get("description", "")), ct, cid, did)
        for eid, (nm, tp, ds, ct, cid, did) in seen.items():
            session.run("MERGE (e:Entity {id:$eid}) SET e.name=$nm,e.type=$tp,e.description=$ds,e.kb_id=$kb,e.updated_at=timestamp() MERGE (e)-[:MENTIONED_IN]->(c:Chunk {id:$cid}) SET c.doc_id=$did,c.text=$ct,c.kb_id=$kb", eid=eid, nm=nm, tp=tp, ds=ds, kb=kb_id, cid=cid, did=did, ct=ct[:500])
        sr = set()
        for rel, ct, cid, did in rel_buf:
            raw_s = rel.get("source", "")
            raw_t = rel.get("target", "")
            s = _sv(raw_s.strip())
            t = _sv(raw_t.strip())
            rt = _sv(rel.get("relation", "相关"))
            rd = _sv(rel.get("description", ""))
            if not s or not t or s == t: continue
            rk = (f"{kb_id}:{_norm(raw_s)}", f"{kb_id}:{_norm(raw_t)}", rt)
            if rk not in sr:
                sr.add(rk)
                session.run(
                    "MERGE (a:Entity {id:$s}) SET a.name=$sn,a.type=coalesce(a.type,'概念'),a.kb_id=$kb,a.updated_at=timestamp() "
                    "MERGE (b:Entity {id:$t}) SET b.name=$tn,b.type=coalesce(b.type,'概念'),b.kb_id=$kb,b.updated_at=timestamp() "
                    "MERGE (a)-[r:RELATES_TO {type:$rt}]->(b) SET r.description=$rd,r.kb_id=$kb,r.updated_at=timestamp()",
                    s=rk[0], sn=s, t=rk[1], tn=t, rt=rt, rd=rd, kb=kb_id,
                )
