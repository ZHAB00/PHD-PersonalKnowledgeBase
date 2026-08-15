# PDH-PKG — 操作手册

> 最后更新：2026-08-13
> 测试状态：57 passed（另有 2 个 Neo4j 集成测试需本地 Neo4j 运行）
> 当前版本状态：PDH-PKG 0.1.0 桌面应用，内置向量模型可选安装，打包与安装器已验证

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
python -m pytest tests/ -v --tb=short
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
| pp/rag/tools.py | ~250 | 工具框架 + 4个内置工具（用户隔离） |
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

### 2026-08-15 debug.ps1 优先使用当前 Conda 环境的 Python
- **文件**：packaging/debug.ps1
- **原因**：从 `(enterprise_kb)` 提示符运行 `packaging\debug.ps1` 时，脚本仍可能解析到 base/WindowsApps 的 `python`，导致 `No module named neo4j`。
- **改动**：启动前先校验候选 Python 是否能导入 `neo4j` 和 `uvicorn`（校验用 try/catch 包裹，避免 stderr 触发 NativeCommandError）；选择顺序为 `PDH_PKG_PYTHON` -> `CONDA_PREFIX\python.exe` -> 本地 `enterprise_kb` 兜底路径 -> 全局 `python`，并打印实际选中的 Python 路径；`.ps1` 改为 UTF-8 BOM 编码避免中文乱码。

### 2026-08-13 联网搜索工具（无需 API Key）
- **文件**：app/rag/web_search.py、app/rag/tools.py、app/prompts/__init__.py、app/core/user_settings.py、app/api/settings.py、app/templates/index.html、app/static/js/app.js、app/static/css/style.css、tests/test_web_search.py
- **改动**：
  1. 新增 `web_search` 工具，支持 `auto / tavily / searxng / bing / duckduckgo`。
  2. 默认 `auto` 无需 API Key：有 Tavily Key 用 Tavily；有 Base URL 用 SearXNG；否则用 Bing，失败自动切 DuckDuckGo。
  3. Bing 跳转链接自动解码为真实 URL，并补齐浏览器请求头降低反爬拦截。
  4. 系统提示词新增规则 8：web_search 结果必须逐条引用标题和 URL。
  5. 设置页新增“联网搜索”组和“测试连接”；搜索 API Key 保存后脱敏回显。
  6. 工具气泡支持 web_search 结果卡片展示。
- **验证**：10 个联网搜索单元测试通过；真实 Bing 无 Key 搜索返回 3 条含真实 URL 的结果；全量测试 67 passed。

### 2026-08-13 项目介绍页（landing）
- **文件**：landing/index.html、landing/assets/screens/*.jpg、landing/assets/favicon.ico、favicon.ico、output/landing-preview-*.png（预览图）
- **原因**：需要一个对外介绍界面，展示产品能力、真实界面、下载安装包与开源计划
- **改动**：
  - 新增浅色专业风单页介绍页，定位为个人知识库，含 Hero、产品能力、界面预览、3D 图谱设计、下载、GitHub 区块
  - 用 Playwright 抓取应用真实界面（对话 / 文档 / 设置）作为视觉素材
  - GitHub 地址留配置占位 `GITHUB_URL`，上传仓库后填入即自动点亮"查看源码"按钮
  - 移除云服务器板块，不展示服务器 IP；导航左侧新增 GitHub 图标入口
  - 新增四套配色切换（翡翠绿 / 靛蓝 / 黑金 / 珊瑚橙），支持 `?scheme=` 预览
  - 修复桌面导航重复的"下载安装包"按钮；替换 favicon.ico 为知识图谱风格图标
  - 导航与页脚 Logo 改用用户原版图标（packaging/resources/icon.ico 转 PNG），根目录 favicon.ico 与 landing favicon 均恢复为用户原版
  - 本地已通过 Playwright 桌面 / 移动端及四套配色检查：无横向溢出、无坏图、无控制台错误

### 2026-08-13 移除角色隔离
- **文件**：app/core/auth.py、app/api/auth.py、app/api/kb.py、app/api/documents.py、app/api/graph_api.py、app/main.py、app/static/js/app.js、app/templates/index.html、app/templates/login.html、API_DOCS.md、启动说明.md、landing/index.html
- **改动**：
  1. 删除 get_admin_user 依赖，知识库/文档/图谱操作不再要求管理员角色。
  2. JWT 和 /api/auth/me 不再返回 role 字段，前端不再维护 kb_role。
  3. 文档页操作按钮始终显示，登录页不再写入角色。
- **验证**：58 passed；local-token 实测新建并删除知识库无 403。

### 改动 1：修复 memory.py 冗余 SUMMARY_PROMPT
- **文件**：pp/rag/memory.py
- **改了什么**：删除第 231-243 行的硬编码 SUMMARY_PROMPT 定义。现在统一使用 pp/prompts/__init__.py 中导入的版本。
- **验证**：Select-String -Path app/rag/memory.py -Pattern "SUMMARY_PROMPT" 只剩 2 处（导入 + 使用），没有重复定义。

### 改动 2：工具 Schema 增强 + 用户隔离
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
python -m pytest tests/ -v --tb=short

# 2. 确认模块都能导入
python -c "
for m in ['app.config','app.rag.graph','app.rag.tools','app.rag.memory','app.models.chat','app.prompts']:
    __import__(m); print('OK:', m)
"
`

改完后：

`powershell
# 3. 跑测试
python -m pytest tests/ -v --tb=short

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
- Neo4j 2026.05 Community 安装在 <Neo4j安装目录>
- 必须用 Java 21+ (<Java 21 安装目录>), Java 17/8 不支持
- 密码: 按安装时配置, 端口: bolt 7687, http 7474
- 启动脚本: <Neo4j安装目录>\start_neo4j.bat

#### 2. graph_rag.py 核心模块
- extract_entities_relations(): 用 DeepSeek LLM 从文档 chunk 抽取实体+关系
- store_entities_relations(): 存入 Neo4j (Entity + RELATES_TO + Chunk)
- 
etrieve_graph_evidence(): 按查询词匹配实体 -> 1-hop 邻居 -> 关联 chunk
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

## 2026-08-04 - 提示词工程修复 + 前端工具气泡重构

### 修改文件
| 文件 | 改动 |
|------|------|
| app/prompts/__init__.py | SYSTEM_PROMPT 增加检索内容防注入边界；删除未使用的 REFUSAL_RESPONSE / NEEDS_RAG_PROMPT；记忆 JSON 提示词增加“只输出 JSON、不要代码块”约束 |
| app/rag/prompts.py | 移除 REFUSAL_RESPONSE 引用 |
| app/rag/graph.py | 移除历史重复注入（不再把历史同时拼进 user prompt 和 messages）；移除死提示词引用 |
| app/rag/graph_rag.py | EXTRACTION_PROMPT 增加纯 JSON 约束；修复关系写入缺少 src/tgt 参数；修复检索 session.run query 参数冲突 |
| app/rag/agent_loop.py | reasoning_content 整轮持续回传 |
| app/models/chat.py | ToolCallEvent 增加 sources 字段，持久化来源引用 |
| app/static/js/app.js | 每个工具调用独立气泡 + 展开/收起（含动画）；思考中指示；移除右侧来源面板；来源引用收进工具气泡；新建对话跳转；删除对话确认；默认知识库可删除并自动切换到剩余知识库 |
| app/static/css/style.css | 工具气泡、展开动画、思考 spinner、气泡间距样式 |
| app/core/kb_service.py | 允许删除默认知识库，但至少保留一个知识库 |

### 测试结果
- pytest tests/ -q --tb=short: 37 passed

## 2026-08-05 - 安全/持久化复查修复 + 回归测试

### 修改文件
| 文件 | 改动 |
|------|------|
| app/api/documents.py | kb_id 白名单正则校验，文件名含路径分隔符/.. 直接拒绝，upload 先校验再建目录 |
| app/core/kb_service.py | _load_kb_list_raw 从 kbs.json 恢复时同时写回 kb:{id}；get_kb 增加文件回退 |
| app/rag/tools.py | search_knowledge_base 改用 asyncio.to_thread，避免 HyDE 阻塞事件循环 |
| .gitignore | 追加 data/kbs.json |
| tests/test_reliability.py | 新增 4 个回归测试：kb_id 穿越、文件名穿越、get_kb 文件回退、BM25 元数据保留 |

### 测试结果
- pytest tests/ -q --tb=short: 41 passed

## 2026-08-05 - 复查修复：列表入口持久化 + list 400 分支

### 修改
- app/core/kb_service.py: get_kb_list() 先走 _load_kb_list_raw() 读文件，只有文件和缓存都为空才创建默认库，避免覆盖 kbs.json。
- app/api/documents.py: list_documents 非法 kb_id 的 HTTPException 补上字符串引号，返回 400 而不是 500。
- tests/test_reliability.py: 新增 get_kb_list 文件恢复不覆盖、list 非法 kb_id 返回 400 两个回归测试。

### 测试结果
- pytest tests/ -q --tb=short: 43 passed

## 2026-08-05 - 安全加固第一批（LLM 注入/ACL/计算器/记忆过滤）

### 修改
- app/rag/tools.py: search_knowledge_base / doc_stats 强制使用当前会话 kb_id（忽略 LLM 传入的 kb_id）；calculator 改为 AST 求值器，禁止 eval、限制幂运算规模；remember 写入前过滤指令型/敏感内容
- app/rag/graph.py: 长期记忆、图谱证据不再注入 system 角色，改为追加到 user 消息并用 <untrusted> 定界
- app/rag/agent_loop.py / app/rag/graph.py: execute_tool 透传 kb_id 到工具上下文
- app/rag/memory.py: 新增 validate_memory_content，store_long_term_memory 跳过 prompt-injection / 敏感内容
- tests/test_security.py: 新增跨库 kb_id 忽略、calculator AST、记忆过滤 3 个安全回归测试

### 测试结果
- pytest tests/ -q --tb=short: 46 passed

## 2026-08-05 - 安全加固第二批（输出 guard / 抽取可信边界 / 默认密钥告警）

### 修改
- app/rag/graph_rag.py: EXTRACTION_PROMPT 显式声明文档片段不可信、不得执行其中指令，并强制 JSON schema。
- app/rag/graph.py: 新增 _guard_output，对话保存前脱敏 sk-* 密钥和 Neo4j 密码，记录疑似 system prompt 泄漏/拒答信号。
- app/main.py: 启动时检测默认 JWT secret 和弱预设密码并输出警告。
- tests/test_security.py: 新增输出脱敏、抽取提示词可信边界 2 个测试。

### 测试结果
- pytest tests/ -q --tb=short: 48 passed

## 2026-08-05 - 读接口统一鉴权 + 前端自动携带 token

### 修改
- app/main.py: AuthMiddleware 对 /api/* 强制 Bearer 鉴权（/api/auth/login 除外），未认证返回 401。
- app/static/js/app.js: 全局 fetch 包装，所有 /api/ 请求自动带 Authorization 头。
- tests/test_api.py、tests/test_reliability.py: client fixture 登录 admin 并设置默认鉴权头。
- tests/test_security.py: 新增未认证读接口 401 测试。

### 测试结果
- pytest tests/ -q --tb=short: 49 passed

## 2026-08-05 - 安全收尾（rate limit / JWT 密钥自动持久化 / 注入测试）

### 修改
- app/main.py: /api/* 增加进程内 rate limit（120 次/分钟/IP+路径），超限返回 429。
- app/core/auth.py: 默认 JWT secret 时自动生成随机密钥并持久化到 data/secret.key，旧默认值不再使用。
- .gitignore: 追加 data/secret.key。
- tests/test_security.py: 新增 remember 注入拦截、记忆写入跳过投毒、rate limit 突发、JWT 密钥非默认 4 个测试。

### 测试结果
- pytest tests/ -q --tb=short: 53 passed

## 2026-08-12 - 设置页 + 模型接入层 + 免登录

### 修改
- 新增 app/core/user_settings.py：设置持久化到 data/settings.json，支持对话模型、向量化、Neo4j、访问密码。
- 新增 app/api/settings.py：设置读取/保存/测试连接接口，密钥脱敏。
- app/core/embedding.py：支持内置 ONNX（fastembed）、Ollama、OpenAI 兼容三种向量化方式，设置变更热切换。
- app/rag/graph.py / agent_loop.py：聊天模型改为读取用户设置，支持 DeepSeek、OpenAI 兼容、Ollama、LM Studio。
- app/api/auth.py：无密码模式下提供本机 local-token，默认免登录；可开启访问密码。
- 前端新增“设置”视图与首次配置引导，包含对话/向量化/Neo4j/访问密码四组配置和测试按钮。
- requirements.txt 增加 fastembed。

### Review 修复
- mask_settings 不再返回 password_hash；开启访问密码必须设置密码。
- LM Studio 默认地址 http://localhost:1234/v1；测试聊天接口按需传 extra_body。
- 设置保存后重置向量模型缓存，支持热切换。
- local-token 仅允许本机获取，防止局域网无密码提权。

### 测试结果
- pytest tests/ -q --tb=short: 54 passed
- 2 个 Neo4j 集成测试因 Neo4j 未启动失败，与本次改动无关

## 2026-08-12 - 访问密码登录页适配

### 修改
- app/api/settings.py: 新增 /api/settings/public，登录页无需令牌即可判断是否启用访问密码。
- app/api/auth.py: 新增 /api/auth/login-local，校验单密码后发放本地令牌。
- app/main.py: 放行 login-local 与 settings/public。
- app/templates/login.html: 无密码模式自动获取本地令牌免登录；开启密码时隐藏用户名，只输密码。
- tests/test_settings.py: 新增单密码登录流程测试。

### 测试结果
- pytest tests/ -q --tb=short: 55 passed
- 2 个 Neo4j 集成测试因 Neo4j 未启动失败，与本次改动无关

## 2026-08-12 - 打包脚本（PyInstaller + Inno Setup 7）

### 新增文件
| 文件 | 作用 |
|------|------|
| packaging/run.py | PDH-PKG 启动器：设置 %LOCALAPPDATA%\PDH-PKG 数据目录、启动后端、自动打开浏览器 |
| packaging/build.ps1 | 安装依赖、可选下载模型、PyInstaller 单目录打包、拷贝 Qdrant、调用 ISCC |
| packaging/installer.iss | Inno Setup 7 安装器：x64、组件可选（内置模型 / Neo4j）、快捷方式、卸载保留/删除数据 |

### 使用
```powershell
.\packaging\build.ps1 -Version 0.1.0 -BundleModel
```
- 不加 -BundleModel 则不下载模型，安装包更小，首次启动由用户在设置页选择下载。
- 若已安装 Inno Setup 7，会自动找到 ISCC.exe；也可用 -ISCC 指定路径。
- 输出：output/PDH-PKG-Setup-0.1.0.exe，便携版在 dist2/PDH-PKG/。

### 构建验证
- PyInstaller 打包成功，OpenSSL DLL 已随包携带，修复 _ssl 加载失败。
- 打包版启动冒烟测试通过：/health 返回 200。
- Neo4j 默认关闭：app/main.py 与 graph_rag 读取用户设置，未启用时不启动、不阻塞。
- 当前机器使用本机安装的 Inno Setup 7 `ISCC.exe` 已成功生成安装器。

## 2026-08-13 - 内置向量模型与打包收尾

### 修改
- packaging/build.ps1：构建统一输出到 dist2/PDH-PKG，固定使用项目 Conda Python；内置模型改为下载 Qdrant 的 ONNX 版 bge-small-zh-v1.5（HF 镜像，禁用 Xet）。
- app/core/embedding.py：打包版优先加载 exe 同级或上级目录中的 models 内置模型缓存，命中时设置 HF_HUB_OFFLINE=1，真正离线可用；源码运行或自定义模型仍使用数据目录缓存。
- packaging/installer.iss：内置向量模型作为可选组件（约 91MB）打进安装包，未选择时安装包仍可用，首次启动在设置页选择下载。
- 安装类型说明：完整安装 = 核心 + 内置模型；简洁安装 = 仅核心；自定义安装 = 核心固定，可单独勾选/取消内置模型。
- packaging/run.py：桌面版改为无控制台窗口启动；日志写入用户数据目录下的 run.log，启动失败可直接查日志。
- app/main.py：Qdrant 数据目录改为用户数据目录下的 qdrant，不再写入安装目录；打包前也会清理 storage/snapshots，避免把个人运行数据带进安装包。
- packaging/installer.iss：安装类型页新增文字说明；卸载时通过弹窗询问是否删除个人数据，安装阶段不再出现“卸载选项”。
- 安装目录与数据目录分离：程序默认安装到 `{autopf}\PDH-PKG`（非管理员下为 `%LOCALAPPDATA%\Programs\PDH-PKG`），个人数据仍单独放在 `%LOCALAPPDATA%\PDH-PKG`。
- 前端设置页：改为独立页面布局，顶部带返回按钮与保存按钮；进入设置时立即显示加载转圈；测试连接按钮点击后先显示“测试中...”转圈；侧栏移除 Connected 状态与退出按钮。
- 调试模式：设置 `PDH_PKG_DEBUG=1` 后固定使用内置调试默认值（DeepSeek + Ollama），读取 `.env`，不读 `settings.json` / `settings.debug.json`，设置页保存不影响运行；正式/打包模式只读 `settings.json`，不会读调试值。一键脚本 `packaging/debug.ps1`（需先 cd 到项目根目录或使用完整路径），手动示例：`$env:PDH_PKG_DEBUG='1'; python -m uvicorn app.main:app --port 8001 --reload`，打包版可用 `PDH-PKG.exe --debug`。
- Redis 降级：`app/core/cache.py` 在 Redis 运行中异常（超时/断连）时自动切回内存存储，`kb_service.py` 缓存读取失败时回退 `kbs.json`，知识库接口不再因 Redis 超时返回 500。

### 产物
- 安装器：output/PDH-PKG-Setup-0.1.0.exe
- 便携目录：output/PDH-PKG-0.1.0-portable/
- 便携压缩包：output/PDH-PKG-0.1.0-desktop.zip

### 验证
- 打包版无窗口冒烟测试：/health 返回 200。
- 打包版 /api/settings/test/embedding：本地 ONNX 模型加载成功，维度 512，约 0.24s。
- pytest tests/ -q --tb=short：55 passed；2 个 Neo4j 集成测试因本地 Neo4j 未启动失败。

## 2026-08-12 - 桌面应用窗口（pywebview）

### 修改
- packaging/run.py：后台启动 FastAPI 服务，等待 /health 就绪后用 pywebview 打开原生桌面窗口；关窗后停止服务。
- 支持 PDH_PKG_NO_WINDOW=1 无窗口模式（用于冒烟测试/服务器模式）。
- requirements.txt 增加 pywebview；build.ps1 增加 --hidden-import webview。

### 产物
- 便携版：output/PDH-PKG-0.1.0-desktop.zip（约 290MB，含内置模型）
- 可运行目录：dist2/PDH-PKG/，双击 PDH-PKG.exe 即为桌面应用。

### 验证
- 无窗口模式冒烟测试：/health 返回 200。

## 2026-08-12 - 安装器生成

### 产物
- 安装器：output/PDH-PKG-Setup-0.1.0.exe（约 213MB，含内置模型）
- Inno Setup 7.0.2 编译成功，x64，默认安装到 %LOCALAPPDATA%\PDH-PKG。
- 模型/Neo4j 为可选组件：仅在 packaging/resources 下存在对应目录时出现在安装向导。
- 图标使用 favicon.ico。

### 使用
运行 output/PDH-PKG-Setup-0.1.0.exe 即可安装，安装后可勾选“立即启动 PDH-PKG”。

## 2026-08-12 - 全项目英文注释中文化

### 修改
- 将 app/ 下 Python、JavaScript、CSS、HTML 中的英文注释与 docstring 全部转为中文。
- 覆盖：RAG 引擎、记忆系统、GraphRAG、检索器、工具框架、API、core 服务、worker、前端 JS/CSS/HTML。
- 保留英文提示词字符串、URL 引用、代码标识符和测试文件中的英文，仅转换注释。

### 验证
- compileall 全量通过
- node --check app/static/js/app.js 通过
- pytest tests/ -q --tb=short: 51 passed
- 2 个 Neo4j 集成测试因本地 Neo4j 未启动而失败，与注释改动无关

## 2026-08-04 - 账号权限调整（全部账号设为 OP）

### 修改
- app/config.py: user/user123 角色从 user 改为 admin，所有预设账号均为 OP。
- app/core/auth.py: 旧 token 缺少 role 时按用户名回查当前角色，老登录态自动升级。
- app/static/js/app.js: 启动时通过 /api/auth/local-token 刷新本地 token，角色隔离已移除。

### 验证
- user/user123 登录返回 role=admin，可创建并删除知识库。

## 2026-08-04 - 个人数据库可靠性第一批（安全/持久化/检索修复）

### 修改文件
| 文件 | 改动 |
|------|------|
| app/api/documents.py | 上传/删除/重索引统一走 _safe_doc_path，拒绝路径穿越 |
| app/core/kb_service.py | KB 元数据新增 data/kbs.json 文件持久化，Redis/内存丢失后重启可恢复 |
| app/rag/hybrid_retriever.py | BM25 保留 filename/page_number/chunk_index 元数据，不再丢失来源信息 |
| app/rag/retriever.py | rebuild_bm25_index 修正 kb_id/tenant_id 传参错误；HyDE 在 async 调用链中也能触发（线程内运行独立事件循环） |

### 测试结果
- pytest tests/ -q --tb=short: 37 passed
- Playwright 真浏览器验证: 每工具一个气泡、展开/收起状态与动画、图谱检索内容单独展示、删除对话确认、新建对话跳转

## 2026-08-04 - 效率优化第一批（GraphRAG 采样 + parent-child 停用 + 可观测日志）

### 修改文件
| 文件 | 改动 |
|------|------|
| app/config.py | 新增 graph_ingest_max_chunks=50 |
| app/workers/ingestion.py | 自动入库 GraphRAG 默认采样 50 个 chunk，4 并发；入库分阶段计时日志 |
| app/core/chunker.py | enable_parent_child 默认改为 False（原实现未将 parent_id 写入 Qdrant，纯浪费 embedding/存储） |
| app/rag/graph.py | chat_stream 增加总耗时/工具数/字符数日志 |

### 说明
- 自动入库不再全量串行调用 DeepSeek；需要全量图谱时仍可点击“构建图谱”执行完整构建。
- parent-child 当前实现只建不用且查询逻辑错误，先停用避免 25-33% 的无效索引成本。
- 后续待办：持久化任务队列、检索去重/缓存、BM25 增量维护、异步阻塞 I/O 清理、指标面板。

### 测试结果
- pytest tests/ -q --tb=short: 37 passed

## 2026-08-13 - 默认知识库可编辑 + 图谱刷新修复

### 修改
- app/static/js/app.js：默认知识库允许编辑，编辑弹窗从接口加载名称和描述；新增/编辑按钮在个人版中始终显示；修复保存按钮同时触发创建和更新的重复逻辑。
- 前端图谱：2D 与 3D 共用“星际星云 / 全息晶体 / 霓虹电路 / 浅色极简”四种设计，设计选择器在 2D 下也始终可见，并同步调整节点、连线、图例和控制按钮样式。
- app/core/user_settings.py：调试模式读取 `.env` 中的 Neo4j 配置，不再硬编码默认密码；正式模式无设置文件时沿用 `.env`/默认 Neo4j 配置。
- app/rag/graph_rag.py：Neo4j 连接改为读取当前用户设置，支持刷新时重新检测连接；图谱抽取 LLM 改用设置页配置的对话模型。
- app/api/graph_api.py：图谱 /data 与 /stats 每次请求可重连 Neo4j；构建前先确认 Neo4j 可用并试调一次抽取模型，模型不可用时不删除旧图谱。
- app/api/documents.py、app/workers/ingestion.py：图谱清理与自动入库同样按当前 Neo4j 设置执行。

### 验证
- pytest tests/ -q -p no:cacheprovider --basetemp=.pytest_tmp：57 passed。
- Playwright 真浏览器验证：新增/编辑/删除按钮可见，默认知识库可打开编辑弹窗并显示“默认知识库 / 系统默认知识库”。
- 图谱页选择 agent 知识库后点击刷新：显示 180 个实体、331 条关系，画布正常渲染。
