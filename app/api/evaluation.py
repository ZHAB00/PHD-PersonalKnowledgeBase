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
    """运行单条 RAG 查询，并用 RAGAS 指标评估。

    返回回答、来源，以及忠实度/回答相关性/上下文精度评分。
    """
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")

    # 获取 RAG 回答
    resp: ChatResponse = await chat(
        session_id="eval",
        message=req.question,
        kb_id=req.kb_id,
        top_k=5,
        rerank_strategy="mmr",
    )

    # 提取上下文分块
    context_chunks = [s.content for s in resp.sources]

    # 运行评估
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
