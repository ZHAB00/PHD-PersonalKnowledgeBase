# PDH-PKG API 接口文档

> 版本 v2.0.0 | Base URL: `http://localhost:8001`

---

## 认证说明

除 `/health` 和 `/api/auth/login` 外，所有接口均需在请求头携带 JWT Token：

```
Authorization: Bearer <access_token>
```

未登录或 token 过期时，后端自动降级为 `user_id="default"` 匿名用户。预设账号（仅开发默认，生产必须通过 `PRESET_USERS` 修改）：

| 用户名 | 密码 |
|--------|------|
| admin | admin123 |
| user | user123 |

可在 `.env` 中通过 `preset_users` 配置修改。

---

## 1. 健康检查

### GET /health

无需认证。

**响应 200:**
```json
{"status": "ok", "service": "PDH-PKG"}
```

---

## 2. 认证

### POST /api/auth/login

**请求体:**
```json
{
  "username": "admin",
  "password": "<your-password>"
}
```

**响应 200:**
```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "username": "admin",
  "user_id": "admin"
}
```

**错误:**
| 状态码 | 说明 |
|--------|------|
| 400 | 用户名或密码为空 |
| 401 | 用户名或密码错误 |

### GET /api/auth/me

需要认证。

**响应 200:**
```json
{"user_id": "admin", "username": "admin"}
```

---

## 3. 知识库管理

### GET /api/kb/list

获取所有知识库列表（含实时文档数）。

**响应 200:**
```json
[
  {
    "id": "default",
    "name": "默认知识库",
    "description": "系统默认知识库",
    "doc_count": 3,
    "created_at": "2026-07-07T12:00:00Z",
    "updated_at": "2026-07-07T12:00:00Z"
  }
]
```

### GET /api/kb/{kb_id}

获取单个知识库详情。

**响应 200:** 同上单条 KnowledgeBase
**错误:** 404 — 知识库不存在

### POST /api/kb/create

创建知识库（名称不可重复）。

**请求体:**
```json
{
  "name": "技术文档",
  "description": "公司内部技术文档集合"
}
```

| 字段 | 类型 | 必填 | 限制 |
|------|------|------|------|
| name | string | 是 | 1-64 字符 |
| description | string | 否 | 最长 256 字符 |

**响应 200:** KnowledgeBase 对象
**错误:** 400 — 名称已存在

### PUT /api/kb/{kb_id}

编辑知识库名称/描述。两个字段均为可选，只更新传入的字段。

**请求体:**
```json
{
  "name": "技术文档 V2",
  "description": "更新后的描述"
}
```

**响应 200:** KnowledgeBase 对象
**错误:** 400 — 名称重复 | 404 — 知识库不存在

### DELETE /api/kb/{kb_id}

删除知识库。**默认知识库不可删除。** 需传入确认码。

**查询参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| confirmation | string | 是 | 固定值 `A1B2C3D4` |

**响应 200:**
```json
{"status": "deleted", "kb_id": "abc123"}
```

**错误:**
| 状态码 | 说明 |
|--------|------|
| 400 | 确认码错误 / 不能删除默认知识库 |
| 404 | 知识库不存在 |

---

## 4. 文档管理

### POST /api/documents/upload

上传文档，自动触发异步解析入库。

**请求格式:** multipart/form-data

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | 是 | 文件，支持 .pdf / .docx / .md / .txt |
| kb_id | string | 否 | 目标知识库 ID，默认 "default" |
| tenant_id | string | 否 | 租户 ID，默认 "default" |

**响应 200:**
```json
{
  "task_id": "a1b2c3d4-e5f6-...",
  "filename": "report.pdf",
  "status": "processing",
  "message": "Document submitted"
}
```

**错误:** 400 — 文件名空 / 不支持的格式

### GET /api/documents/status/{task_id}

查询文档处理进度。

**查询参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| kb_id | string | 否 | 知识库 ID，默认 "default" |

**响应 200:**
```json
{
  "task_id": "a1b2c3d4-...",
  "status": "ready",
  "progress": "15 个分块",
  "doc_info": {
    "id": "a1b2c3d4-...",
    "filename": "report.pdf",
    "doc_type": "pdf",
    "status": "ready",
    "total_chunks": 15,
    "total_pages": 3,
    "kb_id": "default",
    "tenant_id": "default"
  }
}
```

status 可能值: `pending` | `processing` | `ready` | `failed`

**错误:** 404 — 任务不存在

### GET /api/documents/list

获取指定知识库的文档列表，包含磁盘 orphan 文件（Redis 记录丢失但文件仍在）。

**查询参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| kb_id | string | 否 | 默认 "default" |
| tenant_id | string | 否 | 默认 "default" |

**响应 200:**
```json
{
  "documents": [
    {
      "id": "a1b2c3d4-...",
      "filename": "report.pdf",
      "doc_type": "pdf",
      "status": "ready",
      "total_chunks": 15,
      "total_pages": 3,
      "kb_id": "default",
      "tenant_id": "default"
    }
  ],
  "total": 1,
  "orphan_count": 0
}
```

### DELETE /api/documents/{task_id}

删除文档（向量数据 + 磁盘文件 + Redis 记录）。

**查询参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| kb_id | string | 否 | 默认 "default" |
| tenant_id | string | 否 | 默认 "default" |

**响应 200:**
```json
{"status": "deleted", "task_id": "abc", "filename": "report.pdf"}
```

### POST /api/documents/reindex/{filename}

重新索引磁盘上的文件（用于修复解析失败或 orphan 文档）。会先清理旧向量数据。

**查询参数:**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| kb_id | string | 否 | 默认 "default" |
| tenant_id | string | 否 | 默认 "default" |

**响应 200:** DocumentUploadResponse

**错误:** 404 — 文件不存在

---

## 5. 智能对话

### POST /api/chat/rag

**主要的 RAG 对话接口，前端使用此端点。** 意图分类 → 按需检索 → LLM 回答。

**请求体:**
```json
{
  "message": "有哪些文件？",
  "session_id": "default",
  "kb_id": "default",
  "tenant_id": "default",
  "user_id": "default",
  "top_k": 5,
  "rerank_strategy": "none"
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| message | string | 是 | - | 用户问题 |
| session_id | string | 否 | "default" | 会话 ID |
| kb_id | string | 否 | "default" | 知识库 ID |
| tenant_id | string | 否 | "default" | 租户 ID |
| user_id | string | 否 | "default" | 用户 ID（被 auth middleware 覆盖） |
| top_k | int | 否 | 5 | 检索返回数 |
| rerank_strategy | string | 否 | "none" | "mmr" / "cross_encoder" / "none" |

**响应 200:**
```json
{
  "session_id": "default",
  "answer": "根据知识库统计，当前有3个文档...",
  "sources": [
    {
      "doc_id": "abc123...",
      "filename": "readme.md",
      "page_number": 1,
      "chunk_index": 0,
      "content": "文档片段内容...",
      "score": 0.92,
      "is_table": false,
      "table_html": null
    }
  ],
  "token_usage": null
}
```

**错误:** 400 — 消息为空

### POST /api/chat/send

与 `/rag` 功能完全相同，备用端点。

### POST /api/chat/stream

流式对话（SSE）。不支持 Agent Tool Calling。

**请求格式:** multipart/form-data，参数同 `/rag`

**响应:** `text/event-stream`，每条消息以 `data: ` 前缀，结束标记 `data: [DONE]`

### GET /api/chat/history/{session_id}

获取会话历史。

**响应 200:**
```json
{
  "session_id": "default",
  "history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！我是..."}
  ]
}
```

### POST /api/chat/clear/{session_id}

清除会话历史。

**响应 200:**
```json
{"status": "ok", "session_id": "default"}
```

---

## 6. 评估

### POST /api/eval/run

对单条 RAG 查询进行 RAGAS 评估（faithfulness / answer_relevancy / context_precision）。

**请求体:**
```json
{
  "question": "什么是 RAG？",
  "kb_id": "default"
}
```

**响应 200:**
```json
{
  "question": "什么是 RAG？",
  "answer": "RAG 是检索增强生成...",
  "sources": [...],
  "metrics": {
    "faithfulness": 0.85,
    "answer_relevancy": 0.90,
    "context_precision": 0.78
  }
}
```

---

## 对话流程说明

```
用户发送消息
  → _classify_intent(query)         ← 意图分类（规则 + LLM）
  → CHAT: 跳过检索，直接 LLM 回答
  → KNOWLEDGE:
       → retrieve()                  ← 混合检索（向量 + BM25）
       → _compress_context()         ← Token 预算截断
       → _agent_loop()              ← Tool Calling 循环 (≤5轮)
       → _finalize_turn()           ← 保存历史 + 后台提取记忆
  → 返回 ChatResponse
```

**可用 Tools:**
| 工具名 | 描述 |
|--------|------|
| memory_search | 搜索历史对话记忆 |
| doc_stats | 查看知识库文档统计 |
| calculator | 安全数学计算 |

---

## 错误响应格式

所有错误统一格式：
```json
{"detail": "错误描述信息"}
```

---

## 静态页面

| 路径 | 说明 |
|------|------|
| `/` | 主页面（需登录） |
| `/login` | 登录页 |
| `/docs` | FastAPI 自动生成的 Swagger UI |
