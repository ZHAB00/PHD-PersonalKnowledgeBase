"""agent_eval 数据模型：评测集、执行轨迹、判定结果、指标报告。

对应《agent评估指标体系.md》：
- EvalSet / EvalTask   -> §6 评测集设计规范
- Trace / TraceStep    -> §5.3 结构化轨迹格式（Trace Evaluation）
- Judgment             -> §5.1 三通道判定机制的结果载体
- MetricsReport        -> §3 指标定义 + §4 综合评分模型

纯 pydantic 模型，不依赖 app，可独立单测。
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------
# 评测集（EvalSet）
# ---------------------------------------------------------------

TaskCategory = Literal[
    "factual_qa",          # 事实问答
    "multi_hop",           # 多跳推理
    "retrieval_synthesis", # 检索综合
    "tool_use",            # 工具调用
    "open_generation",     # 开放生成
    "refusal_boundary",    # 拒答/边界
    "adversarial",         # 对抗/安全
    "misc",
]

Difficulty = Literal["L1", "L2", "L3", "L4"]


class SubRequirement(BaseModel):
    """任务的关键子要求。任务可拆分时用于计算完成度 CR（M4）。"""
    id: str
    description: str
    required: bool = True


class Assertion(BaseModel):
    """可执行断言（判定通道之一，§5.1）。

    target: answer | tool_result | source_count
    type:
      contains      目标文本包含 value
      contains_any  value 用 | 分隔，命中任意一个
      not_contains  目标文本不包含 value
      equals        目标文本(去首尾空白) 等于 value
      regex         value 为正则，目标文本匹配
    """
    id: str
    type: Literal["contains", "contains_any", "not_contains", "equals", "regex"] = "contains"
    target: Literal["answer", "tool_result", "source_count"] = "answer"
    value: str = ""
    negate: bool = False


class EvalTask(BaseModel):
    id: str
    category: TaskCategory = "factual_qa"
    difficulty: Difficulty = "L2"
    question: str
    kb_id: str = "default"
    gold_answer: Optional[str] = None          # 黄金参考答案（软性任务可为空）
    gold_doc_ids: list[str] = Field(default_factory=list)  # 相关文档 id/文件名，用于 Hit@k/MRR
    sub_requirements: list[SubRequirement] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)
    tools_allowed: Optional[list[str]] = None  # None=不检查；[]=禁止任何工具；非空=白名单（M22 越权）
    should_refuse: bool = False                # 应拒答（用于拒答准确率 M21）
    variants: list[str] = Field(default_factory=list)       # 扰动变体文本（鲁棒性 M17）
    rubric_hints: dict = Field(default_factory=dict)
    max_steps_hint: Optional[int] = None
    # 多轮对话（M28）：同一 session_group 的任务按列表顺序在同一个会话里连续提问
    session_group: Optional[str] = None
    turn_index: Optional[int] = None           # 组内轮次（0 起），用于逐轮正确率
    judge_question: Optional[str] = None       # 判卷用自包含问题（agent 仍看到原 question，如指代句"那生成部分呢？"）


class EvalSet(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    tasks: list[EvalTask]


# ---------------------------------------------------------------
# 执行轨迹（Trace）
# ---------------------------------------------------------------


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    status: Literal["ok", "error", "timeout"] = "ok"
    result_preview: str = ""   # 截断结果（前端展示用）
    result: str = ""           # 完整结果（用于断言/复现）


class TraceStep(BaseModel):
    index: int
    kind: Literal["tool_call", "reasoning", "final_answer"]
    tool: Optional[ToolCallRecord] = None
    content: str = ""


class Trace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    task_id: str
    run_index: int = 0          # 同任务第几次运行（一致性 M16 / 一次性通过 M3）
    question: str
    final_answer: str = ""
    steps: list[TraceStep] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)   # SourceReference.dump()
    citations: list[dict] = Field(default_factory=list) # [{marker, text}]
    token_usage: dict = Field(default_factory=dict)     # {prompt_tokens, completion_tokens, total_tokens}
    latency_ms: int = 0
    status: Literal["completed", "timeout", "error", "max_steps"] = "completed"
    error: str = ""

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        return [s.tool for s in self.steps if s.kind == "tool_call" and s.tool]

    @property
    def n_tool_calls(self) -> int:
        return len(self.tool_calls)

    @property
    def n_steps(self) -> int:
        return len(self.steps)


# ---------------------------------------------------------------
# 判定（Judgment）
# ---------------------------------------------------------------


class SubRequirementJudgment(BaseModel):
    id: str
    satisfied: bool = False


class Judgment(BaseModel):
    task_id: str
    run_index: int = 0
    overall: Literal["success", "partial", "fail"] = "fail"
    correct: bool = False
    sub_requirements: list[SubRequirementJudgment] = Field(default_factory=list)
    # 幻觉（M7）
    hallucination: bool = False
    hallucination_items: list[str] = Field(default_factory=list)
    # 软性分数（0-1，LLM-as-judge）
    faithfulness_score: Optional[float] = None    # 忠实度
    relevancy_score: Optional[float] = None       # 相关性
    completeness_score: Optional[float] = None    # 完整性
    instruction_following: Optional[bool] = None  # 指令遵循（M24）
    citation_accuracy: Optional[float] = None     # 引用准确率（M8），无引用时为 None
    # 安全（D5）
    safety_flags: list[str] = Field(default_factory=list)  # harmful/pii/prompt_leak/jailbreak/unauthorized
    refusal_appropriate: Optional[bool] = None    # 应拒答时是否正确拒答（M21）
    # 元信息
    method: str = "assertion"   # assertion | llm | hybrid | manual
    judge_model: str = ""
    reason: str = ""
    raw: dict = Field(default_factory=dict)


# ---------------------------------------------------------------
# 指标报告（MetricsReport）
# ---------------------------------------------------------------


class GatingItem(BaseModel):
    metric: str
    threshold: str
    value: float
    passed: bool


class MetricsReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    eval_set_name: str = ""
    generated_at: str = ""
    num_tasks: int = 0
    num_runs: int = 0
    # dimensions: {"D1": {"M1_tsr": {"label","value","unit","note"}, ...}, ...}
    dimensions: dict[str, dict] = Field(default_factory=dict)
    composite: dict = Field(default_factory=dict)   # {weights, dimension_scores, composite_score}
    gating: list[GatingItem] = Field(default_factory=list)
    verdict: str = "NOT_RUN"   # PASS / REVIEW / FAIL / FAIL_GATING
    unavailable: list[dict] = Field(default_factory=list)  # [{metric_id, reason}]
