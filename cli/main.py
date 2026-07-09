
import asyncio
import sys
from pathlib import Path
import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.panel import Panel

console = Console()


@click.group()
def cli():
    pass


@cli.command()
@click.argument("filepath", type=click.Path(exists=True))
@click.option("--tenant", default="default", help="租户 ID")
def upload(filepath, tenant):
    from app.workers.ingestion import ingest_document, get_task_info
    path = Path(filepath)
    console.print(f"上传文档: [bold]{path.name}[/bold]")

    async def _run():
        result = await ingest_document(path, path.name, tenant)
        console.print(f"任务已提交: {result.task_id}")
        # 轮询等结果
        for _ in range(30):
            info = await get_task_info(result.task_id)
            if info and info.status.value != "processing":
                return info
            await asyncio.sleep(1)
        return None

    info = asyncio.run(_run())
    if info and info.status.value == "ready":
        console.print(f"[green]处理完成！共 {info.total_chunks} 个分块[/green]")
    elif info:
        console.print(f"[red]处理失败: {info.error_message}[/red]")
    else:
        console.print("[red]任务超时[/red]")


@cli.command()
@click.option("--tenant", default="default", help="租户 ID")
def list_docs(tenant):
    from app.workers.ingestion import list_tasks
    tasks = asyncio.run(list_tasks(tenant))
    if not tasks:
        console.print("暂无文档")
        return
    table = Table(title=f"文档列表 ({len(tasks)} 份)")
    table.add_column("文件名", style="cyan")
    table.add_column("类型", style="magenta")
    table.add_column("状态", style="green")
    table.add_column("分块数", justify="right")
    table.add_column("页数", justify="right")
    for t in tasks:
        status_style = {"ready": "green", "processing": "yellow", "failed": "red", "pending": "white"}
        table.add_row(t.filename, t.doc_type.value, f"[{status_style.get(t.status.value, 'white')}]{t.status.value}[/]", str(t.total_chunks), str(t.total_pages) if t.total_pages else "-")
    console.print(table)


@cli.command()
@click.argument("query")
@click.option("--top-k", default=5, help="检索数量")
@click.option("--tenant", default="default", help="租户 ID")
def chat(query, top_k, tenant):
    from app.rag.graph import chat as do_chat
    result = asyncio.run(do_chat("cli-session", query, tenant, top_k))
    console.print(Panel(result.answer, title="回答", border_style="blue"))
    if result.sources:
        console.print("\n[bold]来源引用:[/bold]")
        for s in result.sources:
            score_str = f"{s.score * 100:.0f}%"
            console.print(f"  {s.filename} p.{s.page_number or '-'}  (相关度: {score_str})")


@cli.command()
def status():
    from app.core import vector_store
    try:
        count = vector_store.count_documents()
        console.print(f"向量库总文档分块数: [bold]{count}[/bold]")
        console.print("Qdrant 连接正常")
    except Exception as e:
        console.print(f"Qdrant 连接失败: {e}")