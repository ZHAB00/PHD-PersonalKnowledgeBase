
class TestGraphRAG:
    def test_neo4j_driver_connect(self):
        """Neo4j driver should connect successfully."""
        from app.rag.graph_rag import _get_driver
        driver = _get_driver()
        driver.verify_connectivity()

    def test_entity_extraction(self):
        """Entity extraction should return valid JSON from LLM."""
        import asyncio
        from app.rag.graph_rag import extract_entities_relations
        chunk = "ReAct is a framework combining reasoning and action. It uses Chain-of-Thought prompting."
        entities, relations = asyncio.run(extract_entities_relations(chunk, "test_kb"))
        assert isinstance(entities, list)
        assert isinstance(relations, list)

    def test_store_and_retrieve(self):
        """Store entities then retrieve graph evidence."""
        import asyncio
        from app.rag.graph_rag import (
            extract_entities_relations, store_entities_relations,
            retrieve_graph_evidence, format_graph_evidence
        )
        chunk = "LangChain is an AI framework for building LLM applications. It supports ReAct agents."
        entities, relations = asyncio.run(extract_entities_relations(chunk, "test_kb"))
        if entities:
            store_entities_relations(entities, relations, chunk, "chunk_test", "doc_test", "test_kb")
            evidence = retrieve_graph_evidence("LangChain", "test_kb")
            formatted = format_graph_evidence(evidence)
            assert len(formatted) > 0
            assert "LangChain" in formatted

    def test_graph_stats(self):
        """Graph stats should return entity and relation counts."""
        from app.rag.graph_rag import graph_stats
        stats = graph_stats("test_kb")
        assert "entities" in stats
        assert "relations" in stats
        assert isinstance(stats["entities"], int)
        assert isinstance(stats["relations"], int)