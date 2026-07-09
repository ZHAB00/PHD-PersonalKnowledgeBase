"""RAG evaluation metrics (lightweight, no ragas dependency)

Reference (RAG Best Practices Section 9):
  - Faithfulness: Is the answer grounded in retrieved documents?
  - Answer Relevancy: Does the answer address the question?
  - Context Precision: Are retrieved chunks actually relevant?

All metrics use LLM-as-judge with DeepSeek.
"""
from __future__ import annotations
import json
import re
from typing import Optional

from openai import OpenAI
import httpx

from app.config import settings

# Prompt templates for each metric

FAITHFULNESS_PROMPT = """You are an evaluator. Given a question, retrieved context, and an AI answer, determine if the answer is FAITHFUL to the context (only uses information from the context, no fabrication).

Question: {question}
Context: {context}
Answer: {answer}

Score the faithfulness on a scale of 0-10:
- 10: Every claim is directly supported by context
- 7-9: Mostly faithful, minor expansions
- 4-6: Some unsupported claims
- 0-3: Mostly fabricated

Return JSON: {{"score": <int>, "reason": "<brief explanation>"}}"""

ANSWER_RELEVANCY_PROMPT = """You are an evaluator. Given a question and an AI answer, determine if the answer RELEVANT to the question. Consider: does it address the question, stay on topic, avoid tangents?

Question: {question}
Answer: {answer}

Score the relevancy on a scale of 0-10:
- 10: Directly and thoroughly answers the question
- 7-9: Relevant but could be more precise
- 4-6: Partially relevant, some off-topic
- 0-3: Irrelevant or non-responsive

Return JSON: {{"score": <int>, "reason": "<brief explanation>"}}"""

CONTEXT_PRECISION_PROMPT = """You are an evaluator. Given a question and a list of retrieved document chunks, determine the PRECISION of the retrieval - what proportion of chunks are actually relevant to answering the question?

Question: {question}
Contexts: {contexts}

Score precision on a scale of 0-10 (10 = all chunks highly relevant, 0 = none relevant).
Consider: Is each chunk needed? Would removing any hurt the answer?

Return JSON: {{"score": <int>, "reason": "<brief explanation>"}}"""


def _get_client() -> OpenAI:
    return OpenAI(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )


def _extract_json(text: str) -> Optional[dict]:
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def evaluate_faithfulness(question: str, context: str, answer: str) -> dict:
    """Evaluate if answer is grounded in retrieved context. Returns {score: 0-10, reason: str}."""
    client = _get_client()
    prompt = FAITHFULNESS_PROMPT.format(question=question, context=context[:3000], answer=answer[:2000])
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=300,
        )
        result = _extract_json(resp.choices[0].message.content or "")
        if result:
            return {"score": result.get("score", 5), "reason": result.get("reason", "")}
    except Exception as e:
        return {"score": 0, "reason": f"Eval failed: {e}"}
    return {"score": 5, "reason": "Could not parse eval result"}


def evaluate_answer_relevancy(question: str, answer: str) -> dict:
    """Evaluate if answer is relevant to question. Returns {score: 0-10, reason: str}."""
    client = _get_client()
    prompt = ANSWER_RELEVANCY_PROMPT.format(question=question, answer=answer[:2000])
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=300,
        )
        result = _extract_json(resp.choices[0].message.content or "")
        if result:
            return {"score": result.get("score", 5), "reason": result.get("reason", "")}
    except Exception as e:
        return {"score": 0, "reason": f"Eval failed: {e}"}
    return {"score": 5, "reason": "Could not parse eval result"}


def evaluate_context_precision(question: str, contexts: list[str]) -> dict:
    """Evaluate retrieval precision. Returns {score: 0-10, reason: str}."""
    client = _get_client()
    ctx_str = "\n---\n".join(f"[{i}] {c[:500]}" for i, c in enumerate(contexts[:10]))
    prompt = CONTEXT_PRECISION_PROMPT.format(question=question, contexts=ctx_str)
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=300,
        )
        result = _extract_json(resp.choices[0].message.content or "")
        if result:
            return {"score": result.get("score", 5), "reason": result.get("reason", "")}
    except Exception as e:
        return {"score": 0, "reason": f"Eval failed: {e}"}
    return {"score": 5, "reason": "Could not parse eval result"}


def run_ragas_eval(question: str, answer: str, context_chunks: list[str]) -> dict:
    """Run all three RAGAS metrics and return summary.

    Args:
        question: User question
        answer: LLM generated answer
        context_chunks: List of retrieved context strings

    Returns:
        {
            "faithfulness": {"score": 8, "reason": "..."},
            "answer_relevancy": {"score": 7, "reason": "..."},
            "context_precision": {"score": 9, "reason": "..."},
            "overall": 8.0
        }
    """
    context_joined = "\n\n".join(context_chunks[:5])
    faithfulness = evaluate_faithfulness(question, context_joined, answer)
    relevancy = evaluate_answer_relevancy(question, answer)
    precision = evaluate_context_precision(question, context_chunks)

    scores = [
        faithfulness.get("score", 0),
        relevancy.get("score", 0),
        precision.get("score", 0),
    ]
    overall = round(sum(scores) / len(scores), 1)

    return {
        "faithfulness": faithfulness,
        "answer_relevancy": relevancy,
        "context_precision": precision,
        "overall": overall,
    }
