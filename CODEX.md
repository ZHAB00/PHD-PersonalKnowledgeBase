# 企业知识库搭建 — Codex 项目指令

## 技术栈

- **后端**: Python 3.12 + FastAPI (端口 8001)
- **Conda 环境**: enterprise_kb
- **向量数据库**: Qdrant (本地二进制, 端口 6333, 非 Docker)
- **Embedding**: Ollama qwen3-embedding:4b (端口 11434) — 用 langchain_ollama.OllamaEmbeddings
- **聊天模型**: DeepSeek API (deepseek-chat) — 用 OpenAI 兼容接口
- **缓存/会话**: Redis (端口 6379)
- **前端**: 原生 HTML/CSS/JS (在 pp/static/ 和 pp/templates/)
- **无 Docker** — 用户 BIOS 不支持虚拟化

## 关键规则

### LangChain 1.0+ 必须
- **所有 LangChain 相关代码必须先联网搜索最新 API 再写**
- 当前版本: langchain==1.3.11, langchain-core==1.4.8
- 工具用 @tool 装饰器 (langchain_core.tools.tool)
- Agent 用 create_agent() (langchain.agents.create_agent)
- Embedding 用 OllamaEmbeddings (langchain_ollama)
- 向量存储用原生 qdrant_client 封装, 不要换 langchain_qdrant.QdrantVectorStore（现有调用方太多）
- 分块用 langchain_text_splitters.RecursiveCharacterTextSplitter

### 代码规范
- **改 JS 文件必须用 Python 脚本精确替换**, 禁止 PowerShell -replace 对 JS 做大段替换（编码/转义问题）
- 每次改动后跑 pytest tests/ -v --tb=short, 必须全绿（当前 33 个测试）
- 改 JS 后检查括号平衡、BOM 字符
- 优先参考项目内文档: docs/01-面试八股文/ 下的 *.md 文件

### RAG 架构（当前 v4: 工具驱动）
`
用户 → LLM（带 5 个工具） → 自主决定
  ├── 闲聊 → 直接答, 不调工具
  ├── 知识问题 → 调 search_knowledge_base → 基于结果回答
  ├── 文件列表 → 调 doc_stats
  └── 记忆查询 → 调 memory_search
`

### 用户隔离
- JWT 登录 (admin/admin123, user/user123)
- AuthMiddleware 注入 
equest.state.user_id
- 对话/记忆/tool 调用全部按 user_id 隔离
- 文档上传按 kb_id 隔离

## 启动命令

`powershell
# Qdrant
.\qdrant\qdrant.exe

# 后端
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# 测试
E:\anaconda3\envs\enterprise_kb\python.exe -m pytest tests/ -v --tb=short
`

## 文件地图

| 文件 | 职责 |
|------|------|
| pp/main.py | FastAPI 入口 |
| pp/rag/graph.py | 对话引擎 (chat, chat_stream) |
| pp/rag/tools.py | 5 个 @tool 工具 |
| pp/rag/memory.py | 四层记忆系统 |
| pp/rag/retriever.py | 混合检索 + HyDE + 重排 |
| pp/core/embedding.py | OllamaEmbeddings 封装 |
| pp/core/vector_store.py | Qdrant 封装 |
| pp/prompts/__init__.py | 所有提示词 |
| pp/static/js/app.js | 前端逻辑 |
| OPERATIONS.md | 操作手册 |

## 变更记录规则（强制）

**每次修改任何代码文件后，必须在 OPERATIONS.md 末尾追加变更记录。这是硬性要求，不可跳过。**

在最终回答前，检查是否已更新 OPERATIONS.md。如果没有，先更新再回复。

格式：
```
### YYYY-MM-DD 变更标题
- **文件**: xxx.py
- **原因**: 为什么改
- **改动**: 具体改了什么
```

OPERATIONS.md 结构：
1. 环境速查 + 启动顺序
2. 文件地图
3. 历史变更记录（按时间倒序，最新在最上面）
4. 未来改动操作流程

**禁止只改代码不记录。每次改动后检查 OPERATIONS.md 是否已更新。**
