"""评估流水线：对接 app.rag.graph.chat 采集轨迹 -> 判定 -> 指标 -> 报告。

用法：
    python -m agent_eval run --evalset data/evalsets/example_evalset.json

注意：run 需要 app 运行环境（Qdrant/Redis/Ollama 等）就绪；use_llm 判定另需 DeepSeek key。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from agent_eval.judge import extract_citations, judge_hybrid
from agent_eval.metrics import compute_metrics
from agent_eval.models import EvalSet, EvalTask, Judgment, Trace, TraceStep, ToolCallRecord
from agent_eval.report import render_html, render_markdown, touch_timestamp


def _load_evalset(path: str | Path) -> EvalSet:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvalSet.model_validate(data)


async def _run_task(
    task: EvalTask,
    run_index: int,
    timeout_s: float,
    session_id: str | None = None,
    user_id: str = "default",
) -> Trace:
    """调用 app 的 chat 入口执行单任务，采集轨迹。app 导入失败时返回 error 轨迹。"""
    try:
        from app.rag.graph import chat
    except Exception as e:  # noqa: BLE001
        return Trace(
            task_id=task.id, run_index=run_index, question=task.question,
            status="error", error=f"无法导入 app.rag.graph.chat：{e}",
        )

    session_id = session_id or f"eval-{task.id}-{run_index}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    try:
        resp = await asyncio.wait_for(
            chat(
                session_id=session_id,
                message=task.question,
                kb_id=task.kb_id,
                top_k=5,
                rerank_strategy="mmr",
                user_id=user_id,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return Trace(
            task_id=task.id, run_index=run_index, question=task.question,
            status="timeout", error=f"超过 {timeout_s}s",
            latency_ms=int(timeout_s * 1000),
        )
    except Exception as e:  # noqa: BLE001
        return Trace(
            task_id=task.id, run_index=run_index, question=task.question,
            status="error", error=str(e),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    steps: list[TraceStep] = []
    for i, tc in enumerate(resp.tool_calls):
        steps.append(TraceStep(
            index=i, kind="tool_call",
            tool=ToolCallRecord(
                tool_name=tc.tool_name,
                arguments=tc.arguments,
                status="ok" if tc.status == "ok" else "error",
                result_preview=(tc.result or "")[:500],
                result=tc.result or "",
            ),
        ))
    steps.append(TraceStep(index=len(steps), kind="final_answer", content=resp.answer))

    sources = [s.model_dump() for s in resp.sources]
    trace = Trace(
        task_id=task.id,
        run_index=run_index,
        question=task.question,
        final_answer=resp.answer or "",
        steps=steps,
        sources=sources,
        citations=extract_citations(resp.answer or ""),
        token_usage=resp.token_usage or {},
        latency_ms=latency_ms,
        status="completed",
    )
    return trace


async def _run_all(evalset: EvalSet, runs: int, timeout_s: float, batch_id: str) -> list[Trace]:
    """按运行轮次驱动；多轮任务（session_group）在组内按列表顺序共享同一会话。

    user_id 带批次 id：组内各轮共享（记忆连续性是被测对象），
    批次间/运行间隔离（避免上次运行的长期记忆污染本次）。
    """
    traces: list[Trace] = []
    sessions: dict[tuple[str, int], str] = {}
    for ri in range(runs):
        for task in evalset.tasks:
            if task.session_group:
                key = (task.session_group, ri)
                session_id = sessions.get(key)
                if session_id is None:
                    session_id = f"eval-mt-{task.session_group}-{ri}-{uuid.uuid4().hex[:8]}"
                    sessions[key] = session_id
                user_id = f"eval-{task.session_group}-{ri}-{batch_id}"
            else:
                session_id = f"eval-{task.id}-{ri}-{uuid.uuid4().hex[:8]}"
                user_id = f"eval-{task.id}-{ri}-{batch_id}"
            traces.append(await _run_task(task, ri, timeout_s, session_id=session_id, user_id=user_id))
    return traces


def run_evalset(
    evalset_path: str | Path,
    out_dir: str | Path = "output/agent_eval",
    runs: int = 1,
    use_llm: bool = True,
    timeout_s: float = 120.0,
) -> dict:
    """完整离线评估。返回 {out_dir, traces, judgments, report} 路径集合。"""
    evalset = _load_evalset(evalset_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    traces = asyncio.run(_run_all(evalset, runs, timeout_s, batch_id=uuid.uuid4().hex[:8]))
    judgments: list[Judgment] = [
        judge_hybrid(task, trace, use_llm=use_llm)
        for task, trace in zip(
            [t for t in evalset.tasks for _ in range(runs)], traces
        )
    ]

    report = compute_metrics(evalset, traces, judgments)
    touch_timestamp(report)

    traces_path = out / "traces.json"
    judgments_path = out / "judgments.json"
    report_json_path = out / "report.json"
    report_md_path = out / "report.md"
    report_html_path = out / "report.html"

    traces_path.write_text(
        json.dumps([t.model_dump() for t in traces], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    judgments_path.write_text(
        json.dumps([j.model_dump() for j in judgments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_json_path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    report_html_path.write_text(render_html(report), encoding="utf-8")

    return {
        "out_dir": str(out),
        "traces": str(traces_path),
        "judgments": str(judgments_path),
        "report_json": str(report_json_path),
        "report_md": str(report_md_path),
        "report_html": str(report_html_path),
        "verdict": report.verdict,
        "composite_score": report.composite.get("composite_score"),
        "report": report,
    }


def report_from_files(
    evalset_path: str | Path,
    traces_path: str | Path,
    judgments_path: str | Path,
    out_path: Optional[str | Path] = None,
) -> MetricsReport:
    """从已有的 traces/judgments JSON 重新计算指标与报告。"""
    evalset = _load_evalset(evalset_path)
    traces = [Trace.model_validate(t) for t in json.loads(Path(traces_path).read_text(encoding="utf-8"))]
    judgments = [Judgment.model_validate(j) for j in json.loads(Path(judgments_path).read_text(encoding="utf-8"))]
    report = compute_metrics(evalset, traces, judgments)
    touch_timestamp(report)
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(report), encoding="utf-8")
    return report
