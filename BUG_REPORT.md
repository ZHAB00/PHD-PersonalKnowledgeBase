# Bug 报告：前端重构时 JS 文件中文编码损坏导致页面卡死

## 概述

2026-07-09 进行前端全面重构（HTML 从 base64 迁移至独立模板），在将 UI 文本中文化时，
使用错误的字符串编码方式生成 JavaScript 文件，导致浏览器解析 JS 失败，整个页面卡死（所有按钮无响应）。

## 影响范围

- **时间**: 2026-07-09 全天，至少 4-5 轮修复迭代
- **影响**: 前端完全不可用 — 新建对话、文档管理、知识库切换、退出登录等所有按钮点击无反应
- **用户可见现象**: 页面白屏或"连接中..."卡住，控制台报 `Uncaught SyntaxError: Invalid or unexpected token`

## 根因分析

### 错误的做法

在 Python 脚本中生成 JS 文件时，使用 Python 的 `\uXXXX` unicode-escape 语法写入中文字符：

```python
# ❌ 错误写法 — Python 源码中的 \uXXXX 转义
JS = u"""
  $("statusText").textContent = "\u5df2\u8fde\u63a5";
  d.innerHTML = '<span>\u5220\u9664</span>';
"""
with open("app.js", "w", encoding="utf-8") as f:
    f.write(JS)
```

**问题**: Python 的 `open("w", encoding="utf-8")` 将 `\u5df2\u8fde\u63a5` 当作字面 18 个 ASCII 字符写入文件，
而不是将这 6 个字节解释为 Unicode 码点再编码为 UTF-8。

### 文件里实际写入的内容

```
# 预期（正确UTF-8）:
$("statusText").textContent = "已连接";

# 实际（乱码字节）:
$("statusText").textContent = "å·²è¿žæŽ¥";
```

浏览器加载该 JS 时，乱码字节破坏了 JavaScript 字符串字面量的边界，导致语法错误，
整个 IIFE 无法执行，后续所有 `on()` 事件绑定全部失效。

### 正确的做法

使用 `json.dumps(s, ensure_ascii=False)` 来生成 JavaScript 字符串字面量：

```python
# ✅ 正确写法
import json

status_text = json.dumps("已连接", ensure_ascii=False)  # 输出: "已连接"（真正的UTF-8字节）

JS = f"""
  $("statusText").textContent = {status_text};
"""
with open("app.js", "w", encoding="utf-8") as f:
    f.write(JS)
```

`json.dumps` 会将中文字符以正确的 UTF-8 多字节序列写入文件目标字符串，
再通过 `open("w", encoding="utf-8")` 写入磁盘。

## 为什么这个问题拖了一整天

1. **后端测试 33/33 全绿** — 所有 API、RAG、chunker 测试都通过，让人误以为没问题
2. **JS 括号平衡检查通过** — `(` `)` `{` `}` 计数正确，没有静态语法错误提示
3. **浏览器缓存干扰** — 多次修改 JS 后浏览器仍加载旧版本（`v=2` 不变），
   修改版本号为 `v=3` 后才真正加载到损坏的文件
4. **PowerShell 编码问题混淆** — 之前用 PowerShell `-replace` 修改 JS 也出过编码问题，
   让人以为是同类问题，实际根因不同

## 经验教训

| 教训 | 具体做法 |
|------|---------|
| **永远不要手写 unicode-escape** | 用 `json.dumps(ensure_ascii=False)` 或直接写 UTF-8 源码文件 |
| **前端改动后必须清除缓存验证** | 改 version hash 或 Ctrl+Shift+R 强制刷新 |
| **不要只信后端测试** | 前端 JS 语法错误不会在 pytest 中暴露，需要浏览器 Console 检查 |
| **生成代码时用结构化方式** | 避免在 Python 字符串中嵌套大量 JavaScript 代码，考虑模板引擎 |
| **编码问题优先怀疑写入链** | Python `open("w")` 写入和 `\u` 转义交互是最容易出错的环节 |

## 修复记录

最终使用 `_gen_final.py` 脚本，通过 `json.dumps(..., ensure_ascii=False)` 生成所有中文字符串，
确保 JS 文件中的中文以正确 UTF-8 编码写入。修复后页面恢复正常，所有按钮可点击。

---

*报告生成时间: 2026-07-09*
*关联文件: app/static/js/app.js, app/templates/index.html, OPERATIONS.md*

---

# Bug 报告：Qdrant 大批量 upsert 导致 WinError 10053 连接重置

## 概述

2026-07-10，上传大文件"aigent开发面试问答集.md"（1210 chunks）时，embedding 完成后向 Qdrant upsert 全部 1210 个 2560 维向量点时，Windows TCP 层抛出 `[WinError 10053]`，导致文档处理失败。

## 根因

`vector_store.upsert_chunks()` 将所有 1210 个 points 一次性 `client.upsert()` 写入 Qdrant。Qdrant 处理不过来，Windows TCP 连接被 RST。

### 排查过程

| 假设 | 测试 | 结论 |
|------|------|------|
| Ollama embedding 超时 | 200 texts 一次请求成功 | 不是 |
| TCP 端口耗尽 | Session 复用连接仍失败 | 不是 |
| 特定 chunk 内容 | 分批 50 chunks 全成功 | 不是 |
| Qdrant 维度不匹配 | 2560 维，匹配 | 不是 |
| **Qdrant 大批量 upsert** | 1210 随机向量复现 10053 | **根因** |

## 修复方案

分批 upsert 到 Qdrant，每批 200 个 points：

`upsert_chunks` 分批 upsert，每批 200 个 points。

## 教训

- 错误提示"软件中止连接"的软件可能是 Qdrant 而非 Ollama
- 大向量批量写入 Qdrant 需分批
- 用独立最小复现代码绕过 embedding 直接测 Qdrant

## 关键代码

### 修复前（问题代码）

`app/core/vector_store.py` — `upsert_chunks()`:

```python
vectors = embed_texts(texts)
points = []
for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
    points.append(PointStruct(...))
client.upsert(collection_name=col_name, points=points)  # 1210 points 一次写入
return len(points)
```

### 修复后

```python
vectors = embed_texts(texts)
points = []
for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
    points.append(PointStruct(...))
# Batch upsert to avoid Qdrant connection reset
BATCH = 200
for i in range(0, len(points), BATCH):
    batch = points[i:i + BATCH]
    client.upsert(collection_name=col_name, points=batch)
return len(points)
```

### 验证代码

```python
# 复现问题：1210 个点一次性 upsert -> 10053
client.upsert(collection_name="kb_4274f071-de4", points=points)

# 修复后：分批 200 个 -> 全部成功
for i in range(0, len(points), 200):
    client.upsert(collection_name="kb_4274f071-de4", points=points[i:i+200])
```

*2026-07-10 | app/core/vector_store.py*


---

# Bug 报告：对话历史丢失、Session标题???、图谱按钮无响应

## 概述

2026-07-12，多个前端功能失效的连锁 bug，根因是 JS 编码问题和后端逻辑遗漏。

## Bug 1: lch() 缺少 async → 对话历史加载静默失败

### 影响
- 切换到历史会话时对话记录不显示
- 刷新页面后对话记录消失

### 根因
app/static/js/app.js 中 lch() 函数体内使用了 await fetch() 但声明不是 async function。在非严格模式下不报错但 await 不等待 Promise。

### 修复
function lch() -> async function lch()

### 代码位置
app/static/js/app.js — lch() 函数声明

---

## Bug 2: synSessions() 从未被调用 → Session 标题不回显

### 影响
- 后端 SQLite 中已保存的 session 标题不会同步到前端 localStorage
- 所有历史对话标题显示为时间戳或 ???

### 根因
synSessions() 函数定义存在，但初始化链中从未调用它。

### 修复
在 lkl().then(...) 前添加 synSessions() 调用

### 代码位置
app/static/js/app.js — 初始化链（IIFE 底部）

---

## Bug 3: 标题 API 不保存到 SQLite

### 影响
生成标题后仅保存在浏览器 localStorage，后端 sessions 表为空。换浏览器/清除缓存后标题丢失。

### 修复
在 /api/chat/title 端点生成标题后调用 save_session()

### 代码位置
app/api/chat.py — generate_title() 函数

---

## Bug 4: 图谱按钮 window 绑定缺失 → onclick 报错

### 影响
图谱刷新和构建按钮点击后报 Uncaught ReferenceError

### 修复
添加 window.refreshGraph/buildGraph/renderGraphView 全局绑定

### 代码位置
app/static/js/app.js — IIFE 结尾

---

## Bug 5: encodeURIComponent 被截断

### 影响
图谱 API 请求 URL 编码失败

### 修复
全局替换 encodeURIComponen 为完整函数名

### 代码位置
app/static/js/app.js — buildGraph() 和 renderGraphView()

---

## Bug 6: 图谱 nav 互斥缺失

### 影响
点击图谱按钮后不亮，视图切换不正确

### 修复
在 switchView() 中添加 graph 分支

### 代码位置
app/static/js/app.js — switchView() 函数

---

*报告时间: 2026-07-12*
*关联文件: app/static/js/app.js, app/api/chat.py, app/rag/graph.py*

---

## Bug 7: 知识图谱持续出现游离点

### 影响
图谱中大量实体节点没有任何可见连线，重建后依然存在，图观感破碎。

### 根因
1. 每个实体都会被入库，但可见边只有 `RELATES_TO` 和同 chunk 共现边；没有这两种边的实体必然显示为孤点。
2. LLM 抽取关系少且 `relations` 的 source/target 与 `entities` 名称不完全一致时，旧逻辑 `MATCH` 不到端点就直接丢弃关系，实体却仍保留。
3. `/api/graph/data` 先取实体、再分别截断取边（`LIMIT limit*2`），节点集和边集不是封闭子图，可能漏掉已存在的边。
4. 前端不过滤 degree 0 节点，所有入库实体全部画出来。

### 修复
1. `/api/graph/data` 改为封闭子图：先按连通度排序取实体，再只返回两端都在节点集内的 `RELATES_TO` 和共现边，不再截断边；返回 `isolated_count`。
2. 前端只渲染出现在边里的节点，隐藏游离点，并在统计栏显示隐藏数量。
3. 构建侧新增 `normalize_entity_name()`：实体 ID 按“去空格/统一大小写”归一，关系端点自动 `MERGE` 补齐，不再静默丢关系。
4. 抽取提示词要求 `relations.source/target` 必须原样等于 `entities.name`，实体/关系上限提高，`max_tokens` 提到 2048。

### 代码位置
- app/api/graph_api.py — get_graph_data()、_flush_graph_batch()
- app/rag/graph_rag.py — normalize_entity_name()、store_entities_relations()、EXTRACTION_PROMPT
- app/static/js/app.js — renderGraphView()

### 生效方式
需启动 Neo4j 后点击“构建图谱”全量重建（`max_chunks=0`），新实体 ID 和关系补齐逻辑才会写入 Neo4j。

*报告时间: 2026-08-02*
*关联文件: app/api/graph_api.py, app/rag/graph_rag.py, app/static/js/app.js*

---

## Bug 8: vis-network improvedLayout 配置位置错误

### 影响
图谱渲染时控制台报 `This network could not be positioned by this version of the improved layout algorithm`，
布局算法退化且不稳定；旧配置把 `improvedLayout: false` 放进了 `physics`，实际应放在 `layout`。

### 修复
1. `improvedLayout: false` 移到 `layout` 配置，并设置固定 `randomSeed`。
2. 图谱 UI 同步重做：深色网格画布、实体类型图例、度大小自适应、节点标签描边、显式关系/片段共现线型区分、加载态、适应画布和缩放控制。

### 代码位置
app/static/js/app.js — renderGraphView()、buildGraphLegend()、fitGraph()、zoomGraph()
app/static/css/style.css — Graph View 区块
app/templates/index.html — #view-graph 区块

*报告时间: 2026-08-02*
*关联文件: app/static/js/app.js, app/static/css/style.css, app/templates/index.html*
