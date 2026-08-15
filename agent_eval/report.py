"""报告渲染：MetricsReport -> Markdown / 静态 HTML（轻量看板）。"""
from __future__ import annotations

from datetime import datetime, timezone
import html

from agent_eval.models import MetricsReport

DIM_NAMES = {
    "D1": "任务结果 Outcome",
    "D2": "正确性与质量 Correctness",
    "D3": "效率与成本 Efficiency",
    "D4": "可靠性与鲁棒性 Reliability",
    "D5": "安全与对齐 Safety",
    "D6": "过程与行为 Process",
    "D7": "用户体验 UX",
}


def _fmt(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_markdown(report: MetricsReport) -> str:
    lines: list[str] = []
    lines.append(f"# Agent 评估报告：{report.eval_set_name or report.run_id}")
    lines.append("")
    lines.append(f"- 生成时间：{report.generated_at or '-'}")
    lines.append(f"- 评测集任务数：{report.num_tasks}　运行数：{report.num_runs}")
    lines.append(f"- **结论：{report.verdict}**")
    lines.append("")

    if report.composite:
        comp = report.composite
        lines.append("## 综合评分")
        lines.append("")
        lines.append(f"- 综合质量分：**{_fmt(comp.get('composite_score'))}**")
        lines.append("- 维度得分：" + "　".join(
            f"{d}={_fmt(s)}" for d, s in comp.get("dimension_scores", {}).items()
        ))
        lines.append("- 权重：" + "　".join(f"{k}:{v}" for k, v in comp.get("weights", {}).items()))
        lines.append("")

    if report.gating:
        lines.append("## 一票否决项")
        lines.append("")
        lines.append("| 指标 | 阈值 | 实际值 | 是否通过 |")
        lines.append("|---|---|---|---|")
        for g in report.gating:
            lines.append(f"| {g.metric} | {g.threshold} | {_fmt(g.value)} | {'✅' if g.passed else '❌'} |")
        lines.append("")

    for dim in sorted(report.dimensions, key=lambda d: d in DIM_NAMES and list(DIM_NAMES).index(d) or 99):
        items = report.dimensions[dim]
        if not items:
            continue
        lines.append(f"## {dim}　{DIM_NAMES.get(dim, '')}")
        lines.append("")
        lines.append("| 指标 | 名称 | 值 | 备注 |")
        lines.append("|---|---|---|---|")
        for mid, m in sorted(items.items()):
            val = _fmt(m["value"])
            val += (" " + m["unit"]) if m.get("unit") else ""
            note = m.get("note", "")
            lines.append(f"| {mid} | {m['label']} | {val} | {note} |")
        lines.append("")

    if report.unavailable:
        lines.append("## 暂不可用的指标（在线/人工）")
        lines.append("")
        for u in report.unavailable:
            lines.append(f"- **{u['metric_id']}**：{u['reason']}")
        lines.append("")

    return "\n".join(lines)


def render_html(report: MetricsReport) -> str:
    """单文件静态 HTML 看板（无外部依赖）。"""
    rows = ""
    for dim in sorted(report.dimensions, key=lambda d: d in DIM_NAMES and list(DIM_NAMES).index(d) or 99):
        items = report.dimensions[dim]
        if not items:
            continue
        body = ""
        for mid, m in sorted(items.items()):
            val = _fmt(m["value"])
            val += (" " + m["unit"]) if m.get("unit") else ""
            body += f"<tr><td class='mid'>{html.escape(mid)}</td><td>{html.escape(m['label'])}</td><td class='val'>{html.escape(val)}</td><td class='note'>{html.escape(m.get('note',''))}</td></tr>"
        rows += f"<section><h2>{html.escape(dim)}　{DIM_NAMES.get(dim,'')}</h2><table>{body}</table></section>"

    gating_rows = "".join(
        f"<tr><td>{html.escape(g.metric)}</td><td>{html.escape(g.threshold)}</td>"
        f"<td class='val'>{_fmt(g.value)}</td><td>{'✅ 通过' if g.passed else '❌ 未通过'}</td></tr>"
        for g in report.gating
    )
    comp = report.composite
    dim_scores = "　".join(
        f"<span class='pill'>{html.escape(d)}: {_fmt(s)}</span>"
        for d, s in comp.get("dimension_scores", {}).items()
    )
    unavailable = "".join(
        f"<li><b>{html.escape(u['metric_id'])}</b> — {html.escape(u['reason'])}</li>"
        for u in report.unavailable
    )
    verdict_color = {"PASS": "#16a34a", "REVIEW": "#d97706", "FAIL": "#dc2626", "FAIL_GATING": "#dc2626"}.get(report.verdict, "#6b7280")

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Agent 评估报告 — {html.escape(report.eval_set_name or report.run_id)}</title>
<style>
 body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;margin:0;background:#f5f6f8;color:#1f2937}}
 header{{background:#111827;color:#fff;padding:20px 32px}}
 header h1{{margin:0 0 8px;font-size:22px}}
 .meta{{color:#9ca3af;font-size:13px}}
 .verdict{{display:inline-block;margin-left:12px;padding:2px 12px;border-radius:12px;color:#fff;font-weight:600}}
 main{{max-width:1100px;margin:24px auto;padding:0 16px}}
 section{{background:#fff;border-radius:10px;padding:16px 20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 h2{{margin:4px 0 12px;font-size:17px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 td,th{{border-bottom:1px solid #e5e7eb;padding:7px 8px;text-align:left}}
 th{{color:#6b7280;font-weight:600}}
 .mid{{font-family:Consolas,monospace;font-size:12.5px;color:#4b5563;white-space:nowrap}}
 .val{{font-weight:600;white-space:nowrap}}
 .note{{color:#9ca3af;font-size:12.5px}}
 .pill{{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:10px;padding:2px 10px;margin:2px;font-size:13px}}
 ul{{color:#6b7280;font-size:13px}}
</style>
</head>
<body>
<header>
  <h1>Agent 评估报告 — {html.escape(report.eval_set_name or report.run_id)}
    <span class="verdict" style="background:{verdict_color}">{html.escape(report.verdict)}</span></h1>
  <div class="meta">生成时间：{html.escape(report.generated_at or '-')}　|　任务数：{report.num_tasks}　|　运行数：{report.num_runs}
    　|　综合质量分：<b style="color:#fff">{_fmt(comp.get('composite_score'))}</b></div>
  <div style="margin-top:8px">{dim_scores}</div>
</header>
<main>
  <section><h2>一票否决项 Gating</h2><table>{gating_rows}</table></section>
  {rows}
  <section><h2>暂不可用的指标（在线/人工）</h2><ul>{unavailable}</ul></section>
</main>
</body>
</html>"""


def touch_timestamp(report: MetricsReport) -> MetricsReport:
    if not report.generated_at:
        report.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return report
