"""agent_eval：PDH-PKG Agent 性能评估工具包。

对应设计文档《agent评估指标体系.md》，提供：
- models   —— 评测集 / 轨迹 / 判定 / 报告的数据模型（pydantic）
- rubrics  —— LLM-as-judge 评分 rubric 模板
- judge    —— 断言 + LLM 裁判 + 安全扫描三通道判定
- metrics  —— M1–M32 指标计算与综合评分、一票否决
- report   —— Markdown / 静态 HTML 看板渲染
- runner   —— 对接 app.rag.graph.chat 的完整评估流水线
- cli      —— python -m agent_eval run / report

依赖：pydantic>=2、openai、httpx、click、rich（均已在项目 requirements.txt 中）。
"""
__version__ = "0.1.0"
