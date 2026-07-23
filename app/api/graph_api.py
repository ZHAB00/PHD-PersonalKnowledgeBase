"""Graph visualization API: returns entity-relation data for vis-network"""
from __future__ import annotations
import logging
from fastapi import APIRouter, Query
from app.rag.graph_rag import _get_driver
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/data")
async def get_graph_data(
    kb_id: str = Query("default"),
    search: str = Query(""),
    limit: int = Query(100),
):
    """Return nodes and edges for vis-network force-directed graph."""
    driver = _get_driver()
    if not driver: return {"nodes": [], "edges": []}
    nodes = []
    edges = []
    edge_ids = set()

    with driver.session(database=settings.neo4j_database) as session:
        # Get entities (nodes)
        if search:
            result = session.run(
                """
                MATCH (e:Entity {kb_id: $kb_id})
                WHERE e.name CONTAINS $search
                OPTIONAL MATCH (e)-[:RELATES_TO]->(related:Entity {kb_id: $kb_id})
                RETURN e, collect(DISTINCT related) AS neighbours
                LIMIT $limit
                """,
                kb_id=kb_id, search=search, limit=limit,
            )
        else:
            result = session.run(
                """
                MATCH (e:Entity {kb_id: $kb_id})
                OPTIONAL MATCH (e)-[:RELATES_TO]->(related:Entity {kb_id: $kb_id})
                RETURN e, collect(DISTINCT related) AS neighbours
                LIMIT $limit
                """,
                kb_id=kb_id, limit=limit,
            )

        seen_nodes = set()
        for record in result:
            e = record["e"]
            eid = e["id"]
            if eid not in seen_nodes:
                seen_nodes.add(eid)
                nodes.append({
                    "id": eid,
                    "label": e.get("name", eid),
                    "title": f"{e.get('type', '')}: {e.get('description', '')}",
                    "group": e.get("type", "概念"),
                    "color": _type_color(e.get("type", "")),
                })
            for neighbour in record.get("neighbours", []):
                if neighbour is None:
                    continue
                nid = neighbour.get("id")
                if nid and nid not in seen_nodes:
                    seen_nodes.add(nid)
                    nodes.append({
                        "id": nid,
                        "label": neighbour.get("name", nid),
                        "title": f"{neighbour.get('type', '')}: {neighbour.get('description', '')}",
                        "group": neighbour.get("type", "概念"),
                        "color": _type_color(neighbour.get("type", "")),
                    })

        # Get edges (relations)
        result = session.run(
            """
            MATCH (a:Entity {kb_id: $kb_id})-[r:RELATES_TO]->(b:Entity {kb_id: $kb_id})
            RETURN a.id AS source, b.id AS target, r.type AS label, r.description AS desc
            LIMIT $limit
            """,
            kb_id=kb_id, limit=limit * 2,
        )
        for record in result:
            edge_key = f"{record['source']}->{record['target']}"
            if edge_key not in edge_ids:
                edge_ids.add(edge_key)
                edges.append({
                    "from": record["source"],
                    "to": record["target"],
                    "label": record["label"],
                    "title": record.get("desc", ""),
                    "arrows": "to",
                })


        # Step 3: co-occurrence edges (entities mentioned in same chunk)
        result = session.run(
            """
            MATCH (a:Entity {kb_id: $kb_id})-[:MENTIONED_IN]->(c:Chunk {kb_id: $kb_id})<-[:MENTIONED_IN]-(b:Entity {kb_id: $kb_id})
            WHERE a.id < b.id
            RETURN a.id AS source, b.id AS target, count(c) AS shared_chunks
            LIMIT $limit
            """,
            kb_id=kb_id, limit=limit * 2,
        )
        for record in result:
            edge_key = f"{record['source']}~~{record['target']}"
            if edge_key not in edge_ids:
                edge_ids.add(edge_key)
                edges.append({
                    "from": record["source"],
                    "to": record["target"],
                    "label": "共现",
                    "title": f"同时出现在 {record['shared_chunks']} 个片段中",
                    "arrows": "",
                    "dashes": True,
                })


    return {"nodes": nodes, "edges": edges, "kb_id": kb_id, "total_entities": len(nodes), "total_relations": len(edges)}


def _type_color(entity_type: str) -> str:
    """Map entity type to a color for the graph."""
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
    """Return graph statistics."""
    from app.rag.graph_rag import graph_stats
    return graph_stats(kb_id)



@router.post("/build")
async def build_graph(
    kb_id: str = Query("default"),
    max_chunks: int = Query(50),
):
    """Build knowledge graph from existing Qdrant chunks for a given kb_id."""
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
    """Full graph build: clear old, process all chunks, batch-flush to Neo4j."""
    import asyncio, json as _json, re as _re, httpx as _httpx
    from openai import OpenAI
    from qdrant_client import QdrantClient
    from app.rag.graph_rag import delete_kb_graph, graph_stats, _get_driver, _safe_neo4j_value

    delete_kb_graph(kb_id)
    logger.info("Graph build: cleared kb=%s", kb_id)

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

    llm = OpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key, timeout=_httpx.Timeout(60.0, connect=10.0))

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
            prompt = ("你是知识图谱专家。从文档提取实体和关系。\n"
                "JSON格式: {\"entities\":[{\"name\":\"实体\",\"type\":\"概念/技术/模型/工具/框架/算法/架构\",\"description\":\"描述\"}],"
                "\"relations\":[{\"source\":\"源\",\"target\":\"目标\",\"relation\":\"包含/使用/依赖/实现/对比/属于/改进\",\"description\":\"描述\"}]}\n"
                "规则: 提取所有技术实体, 不限数量。关系有明确依据。无实体返回空。\n"
                "片段:\n" + txt[:3000])
            resp = llm.chat.completions.create(model=settings.graphrag_llm_model, messages=[{"role":"user","content":prompt}], temperature=0.3, max_tokens=1536)
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
    """Batch flush entities+relations to Neo4j."""
    from app.rag.graph_rag import _get_driver, _safe_neo4j_value as _sv
    driver = _get_driver()
    if not driver: return
    with driver.session(database=settings.neo4j_database) as session:
        seen = {}
        for ent, ct, cid, did in ent_buf:
            nm = _sv(ent.get("name", ""))
            if not nm: continue
            eid = f"{kb_id}:{nm}"
            if eid not in seen:
                seen[eid] = (nm, _sv(ent.get("type", "概念")), _sv(ent.get("description", "")), ct, cid, did)
        for eid, (nm, tp, ds, ct, cid, did) in seen.items():
            session.run("MERGE (e:Entity {id:$eid}) SET e.name=$nm,e.type=$tp,e.description=$ds,e.kb_id=$kb,e.updated_at=timestamp() MERGE (e)-[:MENTIONED_IN]->(c:Chunk {id:$cid}) SET c.doc_id=$did,c.text=$ct,c.kb_id=$kb", eid=eid, nm=nm, tp=tp, ds=ds, kb=kb_id, cid=cid, did=did, ct=ct[:500])
        sr = set()
        for rel, ct, cid, did in rel_buf:
            s = _sv(rel.get("source", ""))
            t = _sv(rel.get("target", ""))
            rt = _sv(rel.get("relation", "相关"))
            rd = _sv(rel.get("description", ""))
            if not s or not t: continue
            rk = (f"{kb_id}:{s}", f"{kb_id}:{t}", rt)
            if rk not in sr:
                sr.add(rk)
                session.run("MATCH (a:Entity {id:$s}),(b:Entity {id:$t}) MERGE (a)-[r:RELATES_TO {type:$rt}]->(b) SET r.description=$rd,r.kb_id=$kb,r.updated_at=timestamp()", s=rk[0], t=rk[1], rt=rt, rd=rd, kb=kb_id)