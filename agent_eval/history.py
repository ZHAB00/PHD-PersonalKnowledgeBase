"""历史基线：每次评测自动追加一行到 history.jsonl，支持趋势对比。

用于回答「这次发版比上次好还是差」，而非只看单次 PASS/FAIL。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_eval.models import MetricsReport

DEFAULT_HISTORY_PATH = os.getenv("AGENT_EVAL_HISTORY", "output/agent_eval/history.jsonl")


def _metric(report: MetricsReport, dim: str, mid: str):
    item = report.dimensions.get(dim, {}).get(mid)
    return item["value"] if item else None


def build_record(report: MetricsReport, note: str = "") -> dict:
    """从 MetricsReport 提取一行历史记录。"""
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "run_id": report.run_id,
        "evalset": report.eval_set_name,
        "tasks": report.num_tasks,
        "runs": report.num_runs,
        "verdict": report.verdict,
        "composite": report.composite.get("composite_score"),
        "tsr": _metric(report, "D1", "M1_tsr"),
        "accuracy": _metric(report, "D2", "M5_accuracy"),
        "hallucination_rate": _metric(report, "D2", "M7_hallucination_rate"),
        "latency_p50_ms": _metric(report, "D3", "M11_latency_p50"),
        "note": note,
    }


def append_history(path: str | Path | None, report: MetricsReport, note: str = "") -> dict:
    """追加一条历史记录，返回记录本身。"""
    p = Path(path or DEFAULT_HISTORY_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = build_record(report, note=note)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_history(path: str | Path | None = None) -> list[dict]:
    p = Path(path or DEFAULT_HISTORY_PATH)
    if not p.exists():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def trend_lines(records: list[dict], evalset: str | None = None) -> list[str]:
    """渲染文本趋势：按评测集分组，逐行给出较上一次的差值。"""
    lines: list[str] = []
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r.get("evalset", "?"), []).append(r)
    for name in sorted(groups):
        if evalset and name != evalset:
            continue
        lines.append(f"【{name}】")
        prev = None
        for r in groups[name]:
            base = (f"  {r['ts']}  verdict={r['verdict']} composite={_f(r['composite'])} "
                    f"TSR={_f(r['tsr'])} acc={_f(r['accuracy'])} 幻觉={_f(r['hallucination_rate'])} "
                    f"runs={r['runs']}")
            if prev is None:
                lines.append(base + "  (基线)")
            else:
                delta = _delta(prev, r)
                lines.append(base + f"  Δcomposite={delta}")
            prev = r
    return lines


def _f(v) -> str:
    return "N/A" if v is None else f"{v:.3f}"


def _delta(prev: dict, cur: dict) -> str:
    parts = []
    for key, label in (("composite", "综合"), ("tsr", "TSR"), ("accuracy", "正确率")):
        p, c = prev.get(key), cur.get(key)
        if p is None or c is None:
            continue
        parts.append(f"{label}{c - p:+.3f}")
    h_p, h_c = prev.get("hallucination_rate"), cur.get("hallucination_rate")
    if h_p is not None and h_c is not None:
        parts.append(f"幻觉{h_c - h_p:+.3f}")
    return " ".join(parts) if parts else "-"
