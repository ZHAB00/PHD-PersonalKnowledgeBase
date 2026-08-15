from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException, Form, Request
from fastapi.responses import StreamingResponse
from app.models.chat import ChatRequest, ChatResponse
from app.rag.graph import chat, chat_stream, get_chat_history, get_chat_history_paginated, clear_chat_history

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _get_user_id(request: Request, fallback: str = "default") -> str:
    """从鉴权中间件获取 user_id，缺失时回退到参数。"""
    return getattr(request.state, "user_id", fallback)


@router.post("/rag", response_model=ChatResponse)
async def chat_rag(chat_req: ChatRequest, request: Request):
    """RAG 聊天接口 —— 与 /send 相同，保留给前端兼容。"""
    if not chat_req.message.strip():
        raise HTTPException(400, "消息不能为空")
    return await chat(
        session_id=chat_req.session_id, message=chat_req.message,
        kb_id=chat_req.kb_id, tenant_id=chat_req.tenant_id,
        user_id=_get_user_id(request, chat_req.user_id),
        top_k=chat_req.top_k, rerank_strategy=chat_req.rerank_strategy,
    )


@router.post("/send", response_model=ChatResponse)
async def send_message(chat_req: ChatRequest, request: Request):
    if not chat_req.message.strip():
        raise HTTPException(400, "消息不能为空")
    return await chat(
        session_id=chat_req.session_id, message=chat_req.message,
        kb_id=chat_req.kb_id, tenant_id=chat_req.tenant_id,
        user_id=_get_user_id(request, chat_req.user_id),
        top_k=chat_req.top_k, rerank_strategy=chat_req.rerank_strategy,
    )


@router.post("/stream")
async def send_message_stream(
    request: Request,
    session_id: str = Form("default"),
    message: str = Form(...),
    kb_id: str = Form("default"),
    tenant_id: str = Form("default"),
    user_id: str = Form("default"),
    top_k: int = Form(5),
    rerank_strategy: str = Form("none"),
    enable_graphrag: str = Form("true"),
):
    graphrag_enabled = enable_graphrag.lower() == "true"
    if not message.strip():
        raise HTTPException(400, "消息不能为空")

    effective_user = _get_user_id(request, user_id)

    async def generate():
        async for chunk in chat_stream(session_id, message, kb_id, tenant_id, top_k, rerank_strategy, graphrag_enabled, effective_user):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps('[DONE]', ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")



@router.post("/title")
async def generate_title(data: dict):
    """使用 AI 根据首条用户消息生成简短标题。"""
    session_id = data.get("session_id", "")
    message = data.get("message", "")
    from openai import AsyncOpenAI
    from app.config import settings

    client = AsyncOpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key)
    try:
        resp = await client.chat.completions.create(
            model=settings.deepseek_model,  # 与 .env/设置中的对话模型保持一致
            messages=[
                {"role": "system", "content": "用15个字以内总结以下用户消息作为对话标题，只返回标题文本，不要引号和其他内容"},
                {"role": "user", "content": message[:200]},
            ],
            temperature=0.3,
            max_tokens=50,
        )
        title = resp.choices[0].message.content.strip()
        # 清理引号并截断
        import re; title = re.sub(r'[#*_`~>|]', '', title).replace('"', '').replace("'", '').strip()
        if len(title) > 20:
            title = title[:20]
        # 保存标题到会话表
        try:
            from app.core.chat_store import save_session
            save_session(session_id, title, "default")
        except Exception:
            pass
        return {"title": title or "???"}
    except Exception as e:
        # 回退方案：使用消息前 15 个字符
        fallback = message[:15].replace("\n", " ")
        try:
            from app.core.chat_store import save_session
            save_session(session_id, fallback, "default")
        except Exception:
            pass
        return {"title": fallback or "???"}

@router.get("/history/{session_id}")
async def get_history(session_id: str, offset: int = 0, limit: int = 20):
    if offset > 0:
        messages, has_more = await get_chat_history_paginated(session_id, offset, limit)
        return {"session_id": session_id, "history": messages, "has_more": has_more, "offset": offset}
    history = await get_chat_history(session_id)
    return {"session_id": session_id, "history": history, "has_more": len(history) > 20}


@router.post("/clear/{session_id}")
async def clear_history(session_id: str):
    await clear_chat_history(session_id)
    return {"status": "ok", "session_id": session_id}


@router.get('/sessions')
async def get_sessions(user_id: str = 'default'):
    from app.core.chat_store import list_sessions as ls
    return {'sessions': ls(user_id)}


@router.post('/sessions/save')
async def save_session_endpoint(data: dict):
    from app.core.chat_store import save_session
    save_session(data.get('id', ''), data.get('title', ''), data.get('user_id', 'default'))
    return {'status': 'ok'}


@router.delete('/sessions/{session_id}')
async def delete_session_endpoint(session_id: str):
    from app.core.chat_store import delete_session
    delete_session(session_id)
    return {'status': 'ok'}
