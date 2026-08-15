"""agent_eval CLI。

    python -m agent_eval run \\
        --evalset data/evalsets/example_evalset.json \\
        --runs 2 [--no-llm] [--out-dir output/agent_eval]

    python -m agent_eval report \\
        --evalset data/evalsets/example_evalset.json \\
        --traces output/agent_eval/traces.json \\
        --judgments output/agent_eval/judgments.json \\
        --out output/agent_eval/report.md
"""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from agent_eval.__init__ import __version__  # noqa: F401  (re-export check)

console = Console()


def _gate(console: Console, verdict: str):
    """CI 门禁：结论非 PASS 时以退出码 1 结束（可直接用作发版/CI 检查）。"""
    if verdict != "PASS":
        console.print("[bold red]✗ 评估门禁未通过（verdict != PASS），退出码 1[/bold red]")
        raise SystemExit(1)


@click.group()
def main():
    """PDH-PKG Agent 性能评估工具（对应《agent评估指标体系.md》）。"""


@main.command()
@click.option("--evalset", required=True, type=click.Path(exists=True), help="评测集 JSON 路径")
@click.option("--runs", default=1, show_default=True, help="每个任务重复运行次数（一致性/一次性通过率）")
@click.option("--use-llm/--no-llm", default=True, show_default=True, help="是否启用 LLM 裁判通道")
@click.option("--timeout", default=120.0, show_default=True, help="单任务超时（秒）")
@click.option("--out-dir", default="output/agent_eval", show_default=True, help="输出目录")
@click.option("--debug/--no-debug", default=False, show_default=True, help="以调试模式运行（与调试库的 Ollama 嵌入维度保持一致）")
def run(evalset: str, runs: int, use_llm: bool, timeout: float, out_dir: str, debug: bool):
    """对评测集逐任务运行 Agent，采集轨迹并评估。"""
    if debug:
        import os
        os.environ["PDH_PKG_DEBUG"] = "1"
    from agent_eval.runner import run_evalset

    console.print(f"[bold]▶ 开始评估[/bold] evalset={evalset} runs={runs} llm_judge={use_llm}")
    result = run_evalset(evalset, out_dir, runs=runs, use_llm=use_llm, timeout_s=timeout)

    table = Table(title="评估结果")
    table.add_column("产物", style="cyan")
    table.add_column("路径", style="green")
    for key, label in [
        ("traces", "轨迹 traces.json"), ("judgments", "判定 judgments.json"),
        ("report_json", "指标 JSON"), ("report_md", "报告 Markdown"),
        ("report_html", "报告 HTML（看板）"),
    ]:
        table.add_row(label, result[key])
    console.print(table)
    console.print(
        f"[bold]结论：[/bold]{result['verdict']}　"
        f"综合质量分={result['composite_score']}"
    )
    from agent_eval.history import append_history
    append_history(None, result["report"], note=out_dir)
    console.print("[dim]已写入历史基线（python -m agent_eval history 查看趋势）[/dim]")
    _gate(console, result["verdict"])


@main.command()
@click.option("--evalset", required=True, type=click.Path(exists=True), help="评测集 JSON 路径")
@click.option("--traces", required=True, type=click.Path(exists=True), help="traces.json 路径")
@click.option("--judgments", required=True, type=click.Path(exists=True), help="judgments.json 路径")
@click.option("--out", default=None, help="输出 Markdown 报告路径（可选）")
@click.option("--html-out", default=None, help="输出 HTML 看板路径（可选）")
def report(evalset: str, traces: str, judgments: str, out: str, html_out: str):
    """用已有的轨迹与判定数据重新计算指标与报告。"""
    from agent_eval.report import render_html, render_markdown
    from agent_eval.runner import report_from_files

    rep = report_from_files(evalset, traces, judgments, out_path=out)
    if html_out:
        from pathlib import Path
        Path(html_out).write_text(render_html(rep), encoding="utf-8")
    console.print(render_markdown(rep))
    console.print(f"[bold]结论：[/bold]{rep.verdict}　综合质量分={rep.composite.get('composite_score')}")
    from agent_eval.history import append_history
    append_history(None, rep, note="report")
    _gate(console, rep.verdict)


@main.command()
@click.option("--path", default=None, help="历史文件路径（默认 output/agent_eval/history.jsonl）")
@click.option("--evalset", default=None, help="只看某个评测集")
def history(path: str, evalset: str):
    """查看历史评估趋势（每次 run/report 自动追加）。"""
    from agent_eval.history import load_history, trend_lines

    records = load_history(path)
    if not records:
        console.print("[yellow]暂无历史记录[/yellow]")
        return
    for line in trend_lines(records, evalset=evalset):
        console.print(line)


if __name__ == "__main__":
    main()
