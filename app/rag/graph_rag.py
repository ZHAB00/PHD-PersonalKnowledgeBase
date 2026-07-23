"""GraphRAG: Knowledge Graph construction + graph-enhanced retrieval

Builds entity-relationship graph from document chunks via LLM extraction,
stores in Neo4j, and enriches retrieval with graph-evidence subgraphs.

References:
  RAG Best Practices Section 8.1: GraphRAG
"""
from __future__ import annotations
import json
import logging
import re
from typing import Optional

from neo4j import GraphDatabase

from app.config import settings
from app.models.chat import SourceReference

logger = logging.getLogger(__name__)

# ---- singleton Neo4j driver ----
_driver: Optional[GraphDatabase.driver] = None


_neo4j_ok = None
_neo4j_ok = None  # None=untested, True=connected, False=failed

def _get_driver():
    global _driver, _neo4j_ok
    if _neo4j_ok is False:
        return None
    if _driver is None:
        try:
            _driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            _driver.verify_connectivity()
            _neo4j_ok = True
            logger.info("Neo4j connected: %s", settings.neo4j_uri)
        except Exception as e:
            logger.warning("Neo4j unavailable (%s), graph disabled", e)
            _neo4j_ok = False
            if _driver:
                try:
                    _driver.close()
                except Exception:
                    pass
                _driver = None
            return None
    return _driver

def _close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


# ================================================================
# Schema init
# ================================================================

SCHEMA_CYPHER = """
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;
CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name);
CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type);
CREATE INDEX entity_kb IF NOT EXISTS FOR (e:Entity) ON (e.kb_id);
"""


def init_schema():
    driver = _get_driver()
    if not driver: return
    with driver.session(database=settings.neo4j_database) as session:
        for stmt in SCHEMA_CYPHER.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    session.run(stmt)
                except Exception as e:
                    logger.debug("Schema init (may already exist): %s", e)
    logger.info("Neo4j schema ready")


# ================================================================
# Entity & Relation Extraction (LLM)
# ================================================================

EXTRACTION_PROMPT = """你是一个知识图谱构建专家。从以下文档片段中提取实体和关系。

请以 JSON 格式返回，格式如下：
{
  "entities": [
    {"name": "实体名", "type": "概念/技术/人物/组织/工具/模型/架构", "description": "一句话描述"}
  ],
  "relations": [
    {"source": "源实体名", "target": "目标实体名", "relation": "关系类型(如:包含/使用/依赖/实现/对比/属于/改进)", "description": "关系描述"}
  ]
}

要求：
1. 只提取有意义的、技术相关的实体
2. 实体名必须精确，使用文档中出现的标准名称
3. 关系必须有明确依据，不要臆造
4. 每个片段最多提取 5 个实体和 5 条关系，宁缺毋滥
5. 如果片段中没有明确的可提取实体，返回 {"entities": [], "relations": []}

文档片段：
{chunk_text}
"""


async def extract_entities_relations(chunk_text: str, kb_id: str) -> tuple[list[dict], list[dict]]:
    """Extract entities and relations from a single chunk via LLM (non-blocking)."""
    import asyncio

    prompt = EXTRACTION_PROMPT.replace("{chunk_text}", chunk_text[:3000])

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, _call_extraction_llm, prompt)
    except Exception as e:
        logger.warning("GraphRAG extraction LLM error: %s", e)
        return [], []

    # Parse JSON from LLM output (may have markdown fences)
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return [], []
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        logger.warning("GraphRAG extraction JSON parse failed: %s", raw[:200])
        return [], []

    entities = data.get("entities", [])
    relations = data.get("relations", [])
    logger.debug("GraphRAG extracted: %d entities, %d relations from chunk", len(entities), len(relations))
    return entities, relations


def _call_extraction_llm(prompt: str) -> str:
    """Synchronous LLM call for entity extraction (runs in thread pool executor)."""
    from openai import OpenAI
    import httpx

    client = OpenAI(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    resp = client.chat.completions.create(
        model=settings.graphrag_llm_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1024,
    )
    return resp.choices[0].message.content.strip()


# ================================================================
# Neo4j storage
# ================================================================

def _safe_neo4j_value(val: str) -> str:
    """Escape single quotes in Neo4j string values."""
    return val.replace("\\", "\\\\").replace("'", "\\'")


def store_entities_relations(
    entities: list[dict],
    relations: list[dict],
    chunk_text: str,
    chunk_id: str,
    doc_id: str,
    kb_id: str,
):
    """Store extracted entities and relations into Neo4j."""
    if not entities and not relations:
        return

    driver = _get_driver()
    if not driver: return
    with driver.session(database=settings.neo4j_database) as session:
        # Merge entities
        for ent in entities:
            name = _safe_neo4j_value(ent.get("name", ""))
            etype = _safe_neo4j_value(ent.get("type", "概念"))
            desc = _safe_neo4j_value(ent.get("description", ""))
            eid = f"{kb_id}:{name}"
            session.run(
                """
                MERGE (e:Entity {id: $eid})
                SET e.name = $name, e.type = $type, e.description = $desc,
                    e.kb_id = $kb_id, e.updated_at = timestamp()
                MERGE (e)-[:MENTIONED_IN]->(c:Chunk {id: $chunk_id})
                SET c.doc_id = $doc_id, c.text = $chunk_text, c.kb_id = $kb_id
                """,
                eid=eid, name=name, type=etype, desc=desc, kb_id=kb_id,
                chunk_id=chunk_id, doc_id=doc_id, chunk_text=chunk_text[:500],
            )

        # Merge relations
        for rel in relations:
            src = _safe_neo4j_value(rel.get("source", ""))
            tgt = _safe_neo4j_value(rel.get("target", ""))
            rtype = _safe_neo4j_value(rel.get("relation", "相关"))
            rdesc = _safe_neo4j_value(rel.get("description", ""))
            if not src or not tgt:
                continue
            src_id = f"{kb_id}:{src}"
            tgt_id = f"{kb_id}:{tgt}"
            session.run(
                """
                MATCH (a:Entity {id: $src_id}), (b:Entity {id: $tgt_id})
                MERGE (a)-[r:RELATES_TO {type: $rtype}]->(b)
                SET r.description = $rdesc, r.kb_id = $kb_id, r.updated_at = timestamp()
                """,
                src_id=src_id, tgt_id=tgt_id, rtype=rtype, rdesc=rdesc, kb_id=kb_id,
            )

    logger.info("GraphRAG stored: %d entities, %d relations to Neo4j (kb=%s)", len(entities), len(relations), kb_id)


# ================================================================
# Graph retrieval: subgraph evidence
# ================================================================

def retrieve_graph_evidence(query: str, kb_id: str, max_evidence: int = 8) -> list[dict]:
    """Search Neo4j for entities matching the query and return subgraph evidence.

    Returns list of dicts with evidence from the graph:
      {entity, type, relation, related_entity, chunk_snippet}
    """
    driver = _get_driver()
    if not driver: return []
    evidence = []

    with driver.session(database=settings.neo4j_database) as session:
        # Step 1: jieba keyword extraction + bidirectional CONTAINS match
        import jieba
        keywords = list(set(jieba.cut(query)))
        keywords = [k.strip() for k in keywords if len(k.strip()) >= 2][:15]
        # Primary: entity name appears in query
        # Secondary: keyword from jieba appears in entity name
        conditions = ["$query CONTAINS e.name"]
        params = {"kb_id": kb_id, "query": query, "max_ev": max_evidence}
        for i, kw in enumerate(keywords):
            pname = f"kw{i}"
            conditions.append(f"e.name CONTAINS ${pname}")
            params[pname] = kw
        where_clause = " OR ".join(conditions)
        result = session.run(
            f"""
            MATCH (e:Entity {{kb_id: $kb_id}})
            WHERE {where_clause}
            RETURN e.name AS entity, e.type AS type, e.description AS desc
            LIMIT $max_ev
            """,
            **params,
        )
        matched_entities = [r.data() for r in result]

        if not matched_entities:
            return evidence

        entity_names = [e["entity"] for e in matched_entities]

        # Record matched entities
        for e in matched_entities:
            evidence.append({
                "source": "graph",
                "entity": e["entity"],
                "type": e["type"],
                "relation": "匹配查询",
                "related_entity": "",
                "description": e.get("desc", ""),
            })

        # Step 2: get 1-hop neighbours (both directions)
        result = session.run(
            """
            MATCH (a:Entity {kb_id: $kb_id})-[r:RELATES_TO]->(b:Entity {kb_id: $kb_id})
            WHERE a.name IN $names OR b.name IN $names
            RETURN a.name AS entity, r.type AS relation, b.name AS related, r.description AS desc
            LIMIT $max_ev
            """,
            kb_id=kb_id, names=entity_names, max_ev=max_evidence,
        )
        for rec in result:
            evidence.append({
                "source": "graph",
                "entity": rec["entity"],
                "type": "",
                "relation": rec["relation"],
                "related_entity": rec["related"],
                "description": rec.get("desc", ""),
            })

        # Step 3: get chunk text associated with matched entities
        result = session.run(
            """
            MATCH (e:Entity {kb_id: $kb_id})-[:MENTIONED_IN]->(c:Chunk {kb_id: $kb_id})
            WHERE e.name IN $names
            RETURN e.name AS entity, c.text AS chunk_text
            LIMIT $max_ev
            """,
            kb_id=kb_id, names=entity_names, max_ev=max_evidence,
        )
        for rec in result:
            evidence.append({
                "source": "graph_chunk",
                "entity": rec["entity"],
                "type": "文档片段",
                "relation": "提及",
                "related_entity": "",
                "description": (rec.get("chunk_text") or "")[:300],
            })

    logger.debug("GraphRAG evidence: %d items for query '%s' in kb=%s", len(evidence), query[:40], kb_id)
    return evidence


def format_graph_evidence(evidence: list[dict]) -> str:
    """Format graph evidence as a readable context string for the LLM."""
    if not evidence:
        return ""

    lines = ["【图谱证据 · 知识图谱检索结果】"]
    seen_pairs = set()
    for e in evidence:
        key = (e.get("entity", ""), e.get("relation", ""), e.get("related_entity", ""))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        if e["source"] == "graph_chunk":
            lines.append(f"  · 实体「{e['entity']}」相关片段: {e['description'][:200]}")
        elif e.get("relation") == "匹配查询":
            lines.append(f"  · 实体: {e['entity']} ({e.get('type', '')}): {e.get('description', '')[:150]}")
        elif e.get("related_entity"):
            lines.append(f"  · {e['entity']} —[{e.get('relation', '相关')}]→ {e['related_entity']}: {e.get('description', '')[:150]}")

    return "\n".join(lines)


# ================================================================
# Incremental ingestion hook: called per chunk during doc processing
# ================================================================

async def ingest_chunk_to_graph(chunk_text: str, chunk_id: str, doc_id: str, kb_id: str):
    """Extract entities from a chunk and store in Neo4j. Called during document ingestion."""
    if not settings.neo4j_enabled:
        return
    try:
        entities, relations = await extract_entities_relations(chunk_text, kb_id)
        store_entities_relations(entities, relations, chunk_text, chunk_id, doc_id, kb_id)
    except Exception as e:
        logger.warning("GraphRAG chunk ingest failed for %s: %s", chunk_id, e)


# ================================================================
# KB cleanup: remove graph data for a knowledge base
# ================================================================

def delete_kb_graph(kb_id: str):
    """Delete all entities, chunks, and relations for a given kb_id."""
    driver = _get_driver()
    if not driver: return
    with driver.session(database=settings.neo4j_database) as session:
        session.run("MATCH (c:Chunk {kb_id: $kb_id}) DETACH DELETE c", kb_id=kb_id)
        session.run("MATCH ()-[r:RELATES_TO {kb_id: $kb_id}]->() DELETE r", kb_id=kb_id)
        session.run("MATCH (e:Entity {kb_id: $kb_id}) DETACH DELETE e", kb_id=kb_id)
    logger.info("GraphRAG: deleted graph data for kb=%s", kb_id)


# ================================================================
# Graph stats
# ================================================================

def graph_stats(kb_id: str = None) -> dict:
    """Return graph statistics for a knowledge base."""
    driver = _get_driver()
    if not driver: return {"entities": 0, "relations": 0, "chunks": 0}
    with driver.session(database=settings.neo4j_database) as session:
        if kb_id:
            ent_count = session.run(
                "MATCH (e:Entity {kb_id: $kb_id}) RETURN count(e) AS c", kb_id=kb_id
            ).single()["c"]
            rel_count = session.run(
                "MATCH ()-[r:RELATES_TO {kb_id: $kb_id}]->() RETURN count(r) AS c", kb_id=kb_id
            ).single()["c"]
        else:
            ent_count = session.run("MATCH (e:Entity) RETURN count(e) AS c").single()["c"]
            rel_count = session.run("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS c").single()["c"]
    return {"entities": ent_count, "relations": rel_count}
