# 企业知识库搭建 — 操作手册

> 最后更新：2026-07-08
> 测试状态：33/33 passed
> 当前版本状态：后端 33 测试全绿，所有模块导入正常，JS 语法检查通过（无 BOM）

---

## 一、环境速查

| 组件 | 版本/位置 | 端口 | 状态 |
|------|----------|------|------|
| Conda 环境 | enterprise_kb, Python 3.12 | — | ✅ |
| Qdrant | .\qdrant\qdrant.exe | 6333 | 需手动启动 |
| Redis | 本地服务 | 6379 | 需运行 |
| Ollama | qwen3-embedding:4b | 11434 | 需运行 |
| DeepSeek API | deepseek-chat | 云端 | 需 API Key |

### 启动顺序

`powershell
# 1. Qdrant
.\qdrant\qdrant.exe

# 2. 确保 Redis 和 Ollama 运行中

# 3. 后端
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# 4. 浏览器
http://localhost:8001/
`

### 运行测试

`powershell
E:\anaconda3\envs\enterprise_kb\python.exe -m pytest tests/ -v --tb=short
`

---

## 二、文件地图

| 文件 | 行数 | 职责 |
|------|------|------|
| pp/main.py | ~160 | FastAPI 入口，AuthMiddleware，路由挂载 |
| pp/config.py | — | 配置（JWT、模型、预设用户） |
| pp/prompts/__init__.py | ~130 | 所有提示词集中管理 |
| pp/models/chat.py | ~50 | Pydantic 模型（ChatResponse, ToolCallEvent） |
| **RAG 核心** | | |
| pp/rag/graph.py | ~400 | 对话引擎：意图分类、agent loop、记忆集成 |
| pp/rag/tools.py | ~250 | 工具框架 + 4个内置工具（权限隔离） |
| pp/rag/memory.py | ~350 | 四层记忆系统 |
| pp/rag/retriever.py | — | 混合检索 + HyDE + 重排 |
| pp/rag/prompts.py | — | 运行时 prompt 构建 |
| pp/rag/agent_loop.py | ~70 | 独立 agent loop（流式） |
| **API 层** | | |
| pp/api/chat.py | — | /rag, /send, /stream, /history, /clear |
| pp/api/documents.py | — | 文档 CRUD |
| pp/api/kb.py | — | 知识库 CRUD |
| pp/api/auth.py | — | 登录 + /me |
| **前端** | | |
| pp/static/js/app.js | ~780 | 全部前端逻辑 |
| pp/static/css/style.css | ~500 | 样式（含工具调用卡片） |
| pp/templates/index.html | — | 主页面 |
| pp/templates/login.html | — | 登录页 |

---

## 三、本轮改动记录

### 改动 1：修复 memory.py 冗余 SUMMARY_PROMPT
- **文件**：pp/rag/memory.py
- **改了什么**：删除第 231-243 行的硬编码 SUMMARY_PROMPT 定义。现在统一使用 pp/prompts/__init__.py 中导入的版本。
- **验证**：Select-String -Path app/rag/memory.py -Pattern "SUMMARY_PROMPT" 只剩 2 处（导入 + 使用），没有重复定义。

### 改动 2：工具 Schema 增强 + 权限隔离
- **文件**：pp/rag/tools.py
- **改了什么**：
  1. 每个工具的 description 补了反例（"不要用于..."），参考 04-工具调用.md §2.1
  2. memory_type 用 enum 约束，importance 用 enum [1..10]
  3. 所有工具返回结构化 dict（{"status":"ok","data":{...}}），不再返回裸自然语言，参考 §2.3
  4. execute_tool 新增 user_id 参数，注入到 handler 调用中，校验跨用户调用
  5. 错误处理统一返回 {"status":"error","suggestion":"..."}，参考 §2.4
- **配合改动**：
  - pp/rag/graph.py：execute_tool 调用改为 execute_tool(tool_name, args, user_id=user_id)
  - pp/rag/graph.py：文件列表快速路径适配新的 _doc_stats_tool 返回格式（ile_list/doc_count 替代 文件列表/文档数量）
  - pp/rag/agent_loop.py：execute_tool 调用补 user_id="default"

### 改动 3：ChatResponse 新增 tool_calls 字段
- **文件**：pp/models/chat.py
- **改了什么**：新增 ToolCallEvent 模型，ChatResponse 新增 	ool_calls: list[ToolCallEvent] 字段。
- **字段定义**：
  `python
  class ToolCallEvent(BaseModel):
      tool_name: str
      arguments: dict = Field(default_factory=dict)
      result: str = ""
      status: str = "ok"  # ok / error
  `

### 改动 4：graph.py 传递 tool_calls 到响应
- **文件**：pp/rag/graph.py
- **改了什么**：
  1. 新增 _build_tool_call_events() 辅助函数，将 agent loop 的 	ool_messages 转为 ToolCallEvent 列表
  2. RAG 路径：nswer, tool_msgs = await _agent_loop(...) → 构造 	ool_call_events → 传入 ChatResponse
  3. 简单聊天路径：同上处理
  4. 简单聊天路径新增记忆检索（
etrieve_long_term_memory + uild_memory_context）和 _after_response_tasks，之前这两块只在 RAG 路径有

### 改动 5：前端流式对话 + 工具调用可视化
- **文件**：pp/static/js/app.js
- **改了什么**：
  1. sendMessage 从 /api/chat/rag 改为 /api/chat/stream（SSE 流式）
  2. 用 ReadableStream + TextDecoder 解析 SSE 事件
  3. 解析 __SOURCES__ → 来源引用，__TOOL_CALL__ → 工具调用胶囊
  4. 文字逐 chunk 流式渲染到页面
  5. ddMessage 支持第三个参数 htmlContent 用于富文本
  6. state.streaming + sendBtn.disabled 用 inally 保证重置（修复卡死 bug）
  7. 补回丢失的 generateSessionId、getSessionList、saveSessionList、ddSessionToHistory、
enderSessionList
- **新增 CSS**：pp/static/css/style.css 底部 .tool-calls-block / .tool-call-item 样式

### 改动 6：favicon 404 修复
- **文件**：pp/main.py
- **改了什么**：加了 /favicon.ico 路由，文件不存在返回 204。

---

## 四、未来改动操作流程

每次改代码前先执行：

`powershell
# 1. 确认当前测试全绿
E:\anaconda3\envs\enterprise_kb\python.exe -m pytest tests/ -v --tb=short

# 2. 确认模块都能导入
E:\anaconda3\envs\enterprise_kb\python.exe -c "
for m in ['app.config','app.rag.graph','app.rag.tools','app.rag.memory','app.models.chat','app.prompts']:
    __import__(m); print('OK:', m)
"
`

改完后：

`powershell
# 3. 跑测试
E:\anaconda3\envs\enterprise_kb\python.exe -m pytest tests/ -v --tb=short

# 4. 如果改过 JS，检查语法
# 用 Node REPL 或打开浏览器 F12 看 console
`

**禁止操作**：
- 不要用 PowerShell 的 -replace 对大段 JS 做字符串替换（编码/转义易出问题）
- 如果要替换 JS 中的函数体，用 Python 写脚本，按字节偏移精确切分再拼接
- 替换完必须检查「被替换代码块引用的辅助函数」是否还在文件中
- 如果 JS 文件出现了 BOM (\ufeff)，立即用 Python 清除


---

## 2026-07-08 LangChain 1.0+ 迁移

### embedding.py
- **原因**: 手写 HTTP 调 Ollama 不够规范，LangChain 1.0 有标准封装
- **改动**: 用 OllamaEmbeddings(model=..., base_url=...) 替代手写 HTTP，加 embedding_dimension 别名兼容旧调用

### tools.py
- **原因**: 手写 
egister_tool() + dict schema 繁琐且不符合 LangChain 1.0 惯例
- **改动**: 全部改为 @tool 装饰器，Pydantic 自动生成 args_schema；保留 execute_tool() / get_tools() 兼容旧 agent loop

### agent_loop.py
- **原因**: 手写 for 循环，LangChain 1.0 有现成的 create_agent
- **改动**: 新增 create_langchain_agent() 用 create_agent() + init_chat_model()；保留 gent_loop_stream() 兼容流式前端

### embedding.py
- **原因**: Pydantic V2 废弃 .schema() 方法
- **改动**: 改用 model_json_schema()

### requirements.txt
- **原因**: 缺少 langchain-ollama 和 langchain-qdrant
- **改动**: 补全 langchain-openai>=1.3.0, langchain-ollama>=1.1.0, langchain-qdrant>=0.2.0

### main.py
- **原因**: 
egister_builtin_tools() 已无实际逻辑（tools 是模块级 @tool 装饰器）
- **改动**: 替换为日志输出 tools 数量

### CODEX.md
- **原因**: 项目需要 Codex 自动读取的约束文件
- **改动**: 创建 CODEX.md，包含 LangChain 1.0+ 强制联网搜索规则、变更记录规则等


---

## 2026-07-08 对话优化：RAG 工具化 + 流式 + 前端交互

### graph.py
- **原因**: RAG 预取导致闲聊也走检索；意图分类失败兜底到 RAG，"hello" 也会检索
- **改动**: 
  - 删除 `_classify_intent`、`_is_file_listing_query`
  - RAG 检索改为 `search_knowledge_base` 工具，LLM 自主决定何时调用
  - `chat()` 统一 agent loop，不再预取 RAG 上下文
  - 新增 `_extract_sources()` 从工具结果提取 SourceReference
  - 新增 `_build_tool_call_events()` 构建前端展示用的工具调用事件
  - `chat_stream()` 接入 `agent_loop_stream()`，支持流式工具调用事件
  - 文件列表关键词快速通道：17个中文模式绕过 LLM 直接调 doc_stats
  - 注入 `当前知识库ID: {kb_id}` 到 system messages，防止 LLM 调工具时用错 KB

### tools.py
- **原因**: 手写 register_tool 繁琐，不符合 LangChain 1.0+ 惯例
- **改动**: 
  - 全部改为 `@tool` 装饰器 (langchain_core.tools.tool)
  - 新增 `search_knowledge_base` 工具 (Tool 0)
  - 保留 `execute_tool()`/`get_tools()` 兼容旧 agent loop
  - 所有工具返回结构化 JSON
  - doc_stats 描述加强：明确要求逐条列出 file_list
  - Pydantic V2: `schema()` -> `model_json_schema()`

### prompts/__init__.py
- **原因**: SYSTEM_PROMPT 不够强，LLM 跳过工具调用直接编造答案
- **改动**: 
  - 重写为工具优先导向："何时必须调用工具" 写死触发词
  - "有哪些文档" -> 必须调 doc_stats
  - 规则："doc_stats 返回的 file_list 必须逐条列出每个文件名"
  - "闲聊直接答" vs "知识问题调工具" 边界清晰

### agent_loop.py
- **原因**: 手写 for 循环，LangChain 1.0 有 create_agent
- **改动**: 
  - 新增 `create_langchain_agent()` 用 `create_agent()`
  - 保留 `agent_loop_stream()` 兼容流式前端

### embedding.py
- **原因**: 手写 HTTP 调 Ollama 不规范
- **改动**: 用 `OllamaEmbeddings(model=..., base_url=...)` 替代；修复 `embedding_base_url`/`ollama_base_url` 配置名不匹配

### models/chat.py
- **原因**: 前端需要展示工具调用过程
- **改动**: 新增 `ToolCallEvent` 模型，`ChatResponse` 新增 `tool_calls` 字段

### main.py
- **原因**: `register_builtin_tools()` 已无实际逻辑
- **改动**: 替换为日志输出 tools 数量；新增 `/favicon.ico` 路由

### requirements.txt
- **原因**: 缺少 langchain-ollama 和 langchain-qdrant
- **改动**: 补全 `langchain-openai>=1.3.0`, `langchain-ollama>=1.1.0`, `langchain-qdrant>=0.2.0`

## 2026-07-08 前端交互修复

### app.js
- **原因**: 多项前端交互问题
- **改动**:
  - `sendMessage` 改为 SSE 流式 (`/api/chat/stream`)，用 ReadableStream 解析
  - 解析 `__TOOL_CALL__`/`__TOOL_RESULT__` 事件，渲染工具调用胶囊
  - "思考中" 三点跳动动画，收到首个 token/工具调用时自动消失
  - `addMessage` 支持 htmlContent 参数
  - `state.streaming` + `finally` 防止卡死
  - 补回丢失的 `generateSessionId`/`getSessionList`/`renderSessionList` 等
  - **历史对话加载修复**: 前端读 `data.history` 而非直接遍历 response 对象（API 返回 `{history: [...]}`）
  - **KB 切换不再清空聊天**: `switchKb()` 只更新 state + 同步 select，不碰 chatMessages
  - **文档管理页 KB 选择器只读**: `kbSelect` 移到 view-header，不可切换（pointer-events: none）
  - **对话页 KB 选择器**: chat-kb-bar 里独立 `chatKbSelect`，唯一切换入口
  - 初始化时把当前 session 加入列表

### index.html
- **原因**: KB 选择器位置不合理
- **改动**:
  - sidebar 删除 kb-selector
  - 文档管理页 view-header 内嵌 kb-selector（只读显示）
  - 对话页 chat-input-area 上方新增 chat-kb-bar

### style.css
- **原因**: 新增 UI 元素需要样式
- **改动**:
  - `.tool-calls-block` / `.tool-call-item` 工具调用胶囊样式
  - `.thinking-indicator` 三点跳动动画
  - `.chat-kb-bar` 对话页 KB 切换条
  - `.view-header .kb-selector` 文档页只读 KB 选择器

### CODEX.md
- **原因**: 项目需要 Codex 自动读取的约束文件
- **改动**: 创建 CODEX.md，包含 LangChain 1.0+ 强制联网搜索规则、变更记录规则


---

## 2026-07-08 工具调用可视化 + 历史持久化修复

### app/static/js/app.js
- **原因**: 工具调用显示在 AI 气泡内部混乱，`[DONE]` 后卡住，刷新后工具调用消失
- **改动**:
  - 工具调用改为独立灰色气泡 `.message-tool`：显示在 AI 气泡上方，"正在调用 xxx..." → "已调用 xxx ✓"
  - `const thinkingInd` 改为 `let`（后续赋 null 会报错）
  - `[DONE]` 用 `done` 标志位联动 break 外层 while 循环（之前只 break 内层 for 导致卡住）
  - 历史加载时根据 `m.tool_calls` 还原工具调用气泡

### app/rag/graph.py
- **原因**: 历史加载时工具调用信息丢失
- **改动**: `chat()` 保存历史时把 `tool_calls` 序列化到 assistant 消息的 `tool_calls` 字段

### app/static/css/style.css
- **原因**: 工具调用需要独立样式
- **改动**: 新增 `.message-tool` 样式（灰色小字，完成后变绿）

### app/prompts/__init__.py
- **原因**: LLM 拿到 doc_stats 结果后不列文件名，自己概括
- **改动**: 规则明确 "doc_stats 返回的 file_list 必须逐条列出每个文件名"；工具描述加 "你必须逐条列出 file_list"

### app/rag/graph.py
- **原因**: LLM 调 doc_stats 时不知道当前 KB ID，用了 default
- **改动**: 在 system messages 中注入 `当前知识库ID: {kb_id}。调用 doc_stats 或 search_knowledge_base 时必须使用此 kb_id。`


### 2026-07-08 BOM JS parsing fix + SSE read loop
- app/static/js/app.js
- Removed BOM (U+FEFF) before sendMessage, removed early chunk break, simplified buf/loop exit
- Tests: 31/31

### 2026-07-09 Global BOM cleanup
- Stripped BOM from 28 .py + 7 .js/.css/.html/.md files
- Fixed SSE read loop: removed early exit, simplified loop condition
- Cache bust: dynamic timestamp version in index.html
- Tests: 31/31

### 2026-07-09 var state hoisting + tool_calls format fix
- **app/static/js/app.js**: var state -> var lineState, prevent hoisting from shadowing const state
- **app/rag/graph.py**: strip custom ToolCallEvent format from history before sending to DeepSeek (requires id field)
- Cleared 5 corrupted chat history keys from Redis
- Tests: 31/31

### 2026-07-09 Fix deleted memory_ctx in chat_stream
- Restored memory_ctx = build_memory_context() and cache.set_json() accidentally removed during dedup
- Tests: 31/31

### 2026-07-09 Stream agent loop + user_id pass-through
- **app/rag/agent_loop.py**: rewritten to use stream=True, real token-by-token output, tool call stream accumulation
- **app/rag/graph.py**: pass user_id through to agent_loop_stream
- Fixed: agent answers no longer truncated, tool calls use correct user isolation
- Tests: 31/31

### 2026-07-09 Fix chat history not loading on page init
- **app/static/js/app.js**: extracted loadChatHistory(), called at init to auto-load current session history
- renderSessionList now delegates to loadChatHistory instead of inline fetch
- Tests: 31/31

### 2026-07-09 Fix interaction logic gaps
- btnSessionAdd now creates new session + clears chat UI
- switchKb now refreshes doc list for the new KB
- Page init auto-loads chat history via loadChatHistory()

### 2026-07-09 DeepSeek-style session management
- **HTML**: redesigned sidebar with btnNewChat + session list, documents as secondary view
- **JS**: new session system - auto title generation, session switching preserves KB, page init loads last session
- **CSS**: new sidebar styles (btn-new-chat, session-items, nav-item-side)
- **API**: POST /api/chat/title - AI-generated conversation titles
- **graph.py**: agent_loop_stream now real token-by-token streaming with stream=True
- Tests: 31/31

### 2026-07-09 Review fixes
- graph.py: save partial history on stream error (was losing conversation on crash)
- JS: fixed missing brace (}})();) for IIFE closure
- Tests: 31/31

### 2026-07-09 Fix null element crash + UI builder
- JS: buildUI() dynamically creates all HTML (Chinese via chr() to survive PowerShell pipe)
- JS: removed null refs to rerankToggle/clearChatBtn (not in buildUI)
- Tests: 31/31

### 2026-07-09 Review: all clear
- Python syntax: 11/11 files OK
- JS syntax: OK (node --check)
- Backend routes: 16/16 present
- DOM elements: all present in buildUI
- Tests: 31/31 passed
---

## 2026-07-09 17:14 - 修复"连接中"问题 + cache busting

### 修改文件
- pp/templates/index.html: 更新 JS/CSS 版本号为实际文件 MD5 (js: 9cfc4004, css: 418ec627)
- pp/static/js/app.js: checkHealth 函数修改

### 具体改动
1. **checkHealth 添加 null guard**: 检查 statusDot/statusText 存在再操作
2. **checkHealth 成功后启用输入**: chatInput.disabled = false; sendBtn.disabled = false
3. **Cache busting**: HTML 中版本号从 ac7316f9 更新为 9cfc4004 (匹配文件 MD5)

### 原因分析
- 版本号不匹配导致浏览器可能使用缓存的旧 JS
- checkHealth 异常未处理可能导致后续初始化代码不执行
- 连接成功后输入框仍 disabled，用户无法交互


## 2026-07-09 17:30 - 修复 app.js 语法错误（孤儿 try/catch）

### 问题
- 之前的 try-catch 包装操作留下了孤儿 `try {` 和 `} catch` 块
- 导致 JS 语法错误: "Missing catch or finally after try"
- 整个 app.js 无法执行，前端显示"连接中"且所有按钮无效

### 修改
- 删除第 4 行的孤儿 `try {`
- 删除第 997-1001 行的孤儿 `} catch(_initErr) { ... }`
- 清理 index.html 的 debug 标记
- 版本号: js=a860564d, css=418ec627

### 验证
- node -c: PASS
- Brace balance: {=232, }=232
- 所有关键函数存在: buildUI, checkHealth, sendMessage, generateSessionId, loadKbList, loadChatHistory
## 2026-07-09 18:00 - 删除文档确认弹窗改为自定义 Modal

### 修改文件
- app/static/js/app.js
- app/templates/index.html (版本号)

### 具体改动
1. **dld 函数**: 将浏览器原生 confirm() 替换为自定义 confirmModal 弹窗
   - 使用 Promise 等待用户点击确定/取消
   - _confirmResolve 变量管理回调
2. **新增事件绑定**: confirmCancel 和 confirmOk 按钮的 click 事件
3. **版本号**: app.js?v=13 → v=14

### 原因
- 浏览器原生 confirm 对话框样式不可控，无法统一 UI 风格
- 已有 confirmModal HTML 结构，复用即可
## 2026-07-09 18:30 - 修复文档删除弹窗 + Ghost 删除逻辑

### 修改文件
- app/static/js/app.js
- app/templates/index.html (v=18)
- app/api/documents.py
- app/core/vector_store.py

### 具体改动
1. **删除弹窗**: 浏览器原生 confirm() 替换为自定义 confirmModal，用回调模式（非 Promise）
2. **Ghost 删除修复**: 原逻辑从 tasks 表查找任务再删向量，但 ghost 不在 tasks 里
   - 改为 delete_by_filename() 直接用 filename 匹配 Qdrant 向量删除
   - vector_store.py 新增 delete_by_filename 方法
3. **前端事件**: confirmOk/confirmCancel 通过 on() 绑定 click 事件

### 原因
- Ghost 文档的向量数据残留，删除点击看似无反应（实际 API 返回 200 但向量没删，刷新后重新出现在列表）
- 弹窗确认需要统一 UI 风格

## 2026-07-10 17:00 - GraphRAG 知识图谱集成

### 修改文件
| 文件 | 改动 |
|------|------|
| pp/config.py | 新增 Neo4j + GraphRAG 配置项 (neo4j_uri, neo4j_password, ne4j_enabled, graphrag_*) |
| pp/rag/graph_rag.py | **新建** - 实体关系抽取、Neo4j 存储、图谱检索、证据格式化 |
| pp/rag/retriever.py | 集成 graph_rag import, enable_graphrag 参数, _graph_retrieve 函数 |
| pp/workers/ingestion.py | 集成 _ingest_graph_async: 文档上传后自动抽取实体关系 |
| 	ests/test_graph_rag.py | **新建** - 4 个测试 (连接/抽取/存储检索/统计) |

### 具体改动

#### 1. Neo4j 环境 (Java 21)
- Neo4j 2026.05 Community 安装在 E:\neo4j-chs-community-2026.05.0-windows
- 必须用 Java 21+ (D:\Program Files\Java\jdk-21), Java 17/8 不支持
- 密码: neo4j/kb123456, 端口: bolt 7687, http 7474
- 启动脚本: E:\neo4j-chs-community-2026.05.0-windows\start_neo4j.bat

#### 2. graph_rag.py 核心模块
- extract_entities_relations(): 用 DeepSeek LLM 从文档 chunk 抽取实体+关系
- store_entities_relations(): 存入 Neo4j (Entity + RELATES_TO + Chunk)
- etrieve_graph_evidence(): 按查询词匹配实体 -> 1-hop 邻居 -> 关联 chunk
- ormat_graph_evidence(): 格式化为可读上下文
- ingest_chunk_to_graph(): per-chunk 异步入库钩子
- delete_kb_graph() / graph_stats(): 清理/统计

#### 3. 检索增强
- retriever.py 在 Rerank 后调用 _graph_retrieve() 注入图谱证据
- 通过 enable_graphrag (默认 True) 控制开关
- 图谱结果作为 SourceReference(filename="[知识图谱]") 合并到检索结果

#### 4. Ingestion 流程
- 文档上传后, _ingest_graph_async 自动抽取前 20 个 chunk 的实体
- fire-and-forget 模式, 不阻塞 embedding/upsert

### 测试结果
- 37/37 passed (33 original + 4 graph_rag)

## 2026-07-10 19:30 - GraphRAG 前端开关

### 修改文件
| 文件 | 改动 |
|------|------|
| pp/rag/graph.py | chat_sync/chat_stream 新增 enable_graphrag 参数，传递给 retrieve() |
| pp/api/chat.py | send_message_stream 接收 enable_graphrag Form 参数 |
| pp/static/js/app.js | state.graphRagEnabled + toggleGraphRag() + sendMessage 传参 |
| pp/templates/index.html | chat-kb-bar 新增图谱增强 toggle 按钮 + CSS |

### 具体改动
1. **后端**: enable_graphrag (默认 True) 贯穿 chat_stream → retrieve → _graph_retrieve
2. **前端**: 聊天输入栏上方知识库选择器旁边出现 sun icon 按钮，点击切换开关/关
3. **默认开启**, 用户可随时关闭以跳过图谱检索, 提升速度

### 测试结果
- 37/37 passed

## 2026-07-10 20:00 - 知识图谱可视化页面 (vis-network)

### 修改文件
| 文件 | 改动 |
|------|------|
| pp/api/graph_api.py | **新建** - GET /api/graph/data 返回 nodes+edges, GET /api/graph/stats |
| pp/main.py | 注册 graph_router |
| pp/templates/index.html | 新增导航按钮(知识图谱) + vis-network CDN + view-graph 区域 |
| pp/static/js/app.js | renderGraphView() + refreshGraph() + 事件绑定 (v=30) |
| pp/static/css/style.css | graph-toolbar, graph-container, graph-empty 样式 |

### 具体改动
1. **导航**: 侧边栏新增"知识图谱"按钮(节点图标), 点击切换到全屏力导向图
2. **工具栏**: 知识库选择 + 实体搜索框 + 刷新按钮 + 实体/关系统计
3. **力导向图**: vis-network 渲染, 节点按类型着色(模型蓝色/技术绿色/概念橙色...), 连线显示关系类型, 支持拖拽缩放, 双击节点搜索关联实体
4. **API**: /api/graph/data?kb_id=xxx&search=xxx 返回 {nodes, edges}, 支持搜索过滤

### 测试结果
- 37/37 passed
- Neo4j 数据: 5 entities + 3 relations (test_kb) 验证通过

## 2026-08-03 - 3D 图谱多设计模式

### 修改文件
| 文件 | 改动 |
|------|------|
| app/templates/index.html | 新增 3D 设计下拉框（星际星云/全息晶体/霓虹电路/浅色极简），版本号 CSS v=28、JS v=54 |
| app/static/js/app.js | state.graphDesign + setGraphDesign()；renderGraph3D 重构为共享渲染管线 + buildDesignScene3D()；新增屏幕空间标签层，节点布局改为带径向扰动的自然簇状 |
| app/static/css/style.css | 新增设计下拉框样式与 .graph3d-label 屏幕标签样式（含四种设计的差异化标签） |

### 具体改动
1. **四套设计**: 星际星云（星空粒子+光晕）、全息晶体（网格地面+八面体节点+轨道环）、霓虹电路（霓虹节点+脉冲光+线框外壳）、浅色极简（浅色背景+无装饰节点+白底标签）。
2. **名字可见性**: 原 Three.js Sprite 标签尺寸过小导致 3D 下看不到名字，改为 DOM 屏幕空间标签，180 个实体全部清晰显示，悬停节点时标签同步高亮。
3. **自然布局**: 正球壳排列改为按节点度数调整半径 + 稳定哈希抖动，避免规整的“水晶球”观感。

### 测试结果
- node --check app/static/js/app.js 通过
- Playwright 真浏览器验证: 180 labels 全部可见、四套设计切换无 pageerror、页面无滚动溢出
