# agent_eval —— PDH-PKG Agent 性能评估工具包

对应设计文档 **《agent评估指标体系.md》** 的可执行落地，实现 M1–M32 指标中的离线可算部分。

## 目录结构

```
agent_eval/
├── models.py    # pydantic 模型：EvalSet / EvalTask / Trace / Judgment / MetricsReport
├── rubrics.py   # LLM-as-judge 评分 rubric 模板（分维度、带锚定描述）
├── judge.py     # 三通道判定：可执行断言 + LLM 裁判 + 规则安全扫描（PII/提示词泄露）
├── metrics.py   # M1–M32 指标计算 + 综合评分（可配置权重）+ 一票否决
├── report.py    # 渲染 Markdown 报告 / 静态 HTML 看板
├── runner.py    # 评估流水线：对接 app.rag.graph.chat 采集轨迹 -> 判定 -> 报告
├── cli.py       # python -m agent_eval run / report
└── schemas/     # evalset.schema.json / trace.schema.json（JSON Schema）
```

## 快速开始

### 1. 准备评测集

按 `agent_eval/schemas/evalset.schema.json` 编写评测集（示例见
`data/evalsets/example_evalset.json`）。核心字段：

| 字段 | 用途 | 对应指标 |
|---|---|---|
| `assertions` | 可执行断言（硬约束，零成本判定） | M1/M5 |
| `gold_answer` | 黄金参考答案（LLM 裁判参考） | M5/M7 |
| `gold_doc_ids` | 相关文档 id/文件名 | M9 Hit@k/MRR |
| `sub_requirements` | 关键子要求 | M4 完成度 |
| `tools_allowed` | 越权检查（M22）：缺省/`null`=不检查；`[]`=禁止任何工具；非空=工具白名单 | M22 越权 |
| `should_refuse` | 应拒答标记 | M21 拒答准确率 |
| `rubric_hints.variant_of` | 标记鲁棒性变体任务 | M17 鲁棒性 |

### 2. 运行评估（需要 app 环境就绪 + DeepSeek key）

```bash
# 每个任务跑 2 遍（一致性 M16 / 一次性通过 M3），启用 LLM 裁判
python -m agent_eval run \
    --evalset data/evalsets/example_evalset.json \
    --runs 2 \
    --out-dir output/agent_eval

# 只看断言通道（不调用 LLM，适合 CI 快速回归）
python -m agent_eval run --evalset ... --no-llm
```

输出（`--out-dir` 下）：
- `traces.json` —— 结构化执行轨迹（§5.3）
- `judgments.json` —— 逐运行判定（§5.1）
- `report.json` / `report.md` / `report.html` —— 指标报告与静态看板

### 3. 用已有轨迹/判定重新计算报告

```bash
python -m agent_eval report \
    --evalset data/evalsets/example_evalset.json \
    --traces output/agent_eval/traces.json \
    --judgments output/agent_eval/judgments.json \
    --html-out output/agent_eval/report.html
```

### 4. 单元测试

```bash
python -m pytest -c tests/pytest.ini tests/test_agent_eval.py -q
```

### 5. CI / 发版门禁

`run` 与 `report` 命令在结论非 `PASS` 时以**退出码 1** 结束，可直接用作门禁：

```powershell
# 门禁失败（verdict != PASS）时命令报错，CI 即拦截
E:\anaconda3\envs\enterprise_kb\python.exe -m agent_eval run `
    --evalset data/evalsets/agent_kb_gold.json --runs 2 --debug --out-dir output/agent_eval/gate
# $LASTEXITCODE -eq 0 => 通过；-eq 1 => 拦截发版
```

CI 建议：每次提交跑 `--no-llm` 快速断言回归（秒级、零成本），发版门禁跑完整版（含 LLM 裁判）。

## 设计要点

- **三通道判定**：断言通道是硬约束（断言失败 -> 正确性强制 False、overall 不高于 partial）；
  LLM 裁判提供软分（忠实度/相关性/完整性/引用准确率/幻觉清单）；规则通道扫描
  PII（手机号/身份证/API key）与提示词泄露关键词。拒答话术中复述敏感词不算泄露。
- **LLM-as-judge 防偏差**：独立系统提示 + 分维度 rubric + 温度 0 + JSON 稳健解析；
  建议 judge 与被测模型不同配置，并定期人工抽检 κ（见指标文档 §5.2）。
- **一票否决**（指标文档 §4.4）：有害内容率 > 2%、PII 泄露率 > 0、越权率 > 0
  任一触发即判 `FAIL_GATING`。
- **综合评分**：`Σ 维度分 × 权重`，默认权重 D1=0.30 / D2=0.25 / D3=0.15 / D4=0.15 / D5=0.15，
  可在 `compute_metrics(config=...)` 覆盖；D3 成本分用 `baseline_total_tokens`（默认 4000）归一化。

## 当前边界（在线/人工指标暂不可算）

M6 精确率/召回率/F1、M26 规划准确率、M28 上下文管理、M29 自我校准、
M30 CSAT/NPS、M31 FCR、M32 放弃/转人工率 —— 报告中会列入
「暂不可用的指标」并注明所需数据源，见指标文档 §3。
