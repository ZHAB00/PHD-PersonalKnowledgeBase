from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.rag.evaluation import run_ragas_eval
from app.rag.graph import chat
from app.models.chat import ChatResponse

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


class EvalRequest(BaseModel):
    question: str
    kb_id: str = "default"


class EvalResponse(BaseModel):
    question: str
    answer: str
    sources: list[dict]
    metrics: dict


@router.post("/run", response_model=EvalResponse)
async def run_evaluation(req: EvalRequest):
    """Run a single RAG query and evaluate with RAGAS metrics.

    Returns the answer, sources, and faithfulness/answer_relevancy/context_precision scores.
    """
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")

    # Get RAG response
    resp: ChatResponse = await chat(
        session_id="eval",
        message=req.question,
        kb_id=req.kb_id,
        top_k=5,
        rerank_strategy="mmr",
    )

    # Extract context chunks
    context_chunks = [s.content for s in resp.sources]

    # Run evaluation
    metrics = run_ragas_eval(
        question=req.question,
        answer=resp.answer,
        context_chunks=context_chunks,
    )

    return EvalResponse(
        question=req.question,
        answer=resp.answer,
        sources=[s.model_dump() for s in resp.sources],
        metrics=metrics,
    )
