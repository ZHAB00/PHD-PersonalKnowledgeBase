from __future__ import annotations
import logging
import subprocess
import time
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.core import cache
from app.rag.tools import BUILTIN_TOOLS_LC
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.api.chat import router as chat_router
from app.api.evaluation import router as eval_router
from app.api.kb import router as kb_router
from app.api.auth import router as auth_router
from app.api.graph_api import router as graph_router

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

_qdrant_process = None


def _start_qdrant():
    """Start Qdrant as a subprocess if not already running."""
    global _qdrant_process
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", settings.qdrant_port))
        s.close()
        logger.info(f"Qdrant 已在端口 {settings.qdrant_port} 运行")
        return
    except (ConnectionRefusedError, OSError):
        pass
    finally:
        s.close()

    qdrant_path = Path("qdrant/qdrant.exe")
    if not qdrant_path.exists():
        qdrant_path = Path("qdrant.exe")
    if not qdrant_path.exists():
        logger.warning("找不到 qdrant.exe，请手动启动 Qdrant")
        return

    try:
        _qdrant_process = subprocess.Popen(
            [str(qdrant_path.resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for i in range(10):
            time.sleep(0.5)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("127.0.0.1", settings.qdrant_port))
                s.close()
                logger.info(f"Qdrant 已启动（端口 {settings.qdrant_port}）")
                return
            except (ConnectionRefusedError, OSError):
                pass
        logger.warning("Qdrant 启动超时（10秒），请手动检查")
    except Exception as e:
        logger.warning(f"Qdrant 启动失败: {e}")


def _stop_qdrant():
    global _qdrant_process
    if _qdrant_process:
        try:
            _qdrant_process.terminate()
            _qdrant_process.wait(timeout=5)
            logger.info("Qdrant 已关闭")
        except Exception as e:
            logger.warning(f"Qdrant 关闭异常: {e}")
            try:
                _qdrant_process.kill()
            except Exception:
                pass
        _qdrant_process = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"LangChain 1.0+ tools loaded: {len(BUILTIN_TOOLS_LC)} tools")
    logger.info(f"企业知识库服务启动 - 端口 {settings.service_port}")
    _start_qdrant()
    await cache._init()

    from app.core.kb_service import _create_default_kb, get_kb_list
    try:
        kbs = await get_kb_list()
        if not kbs:
            await _create_default_kb()
        from app.core import vector_store
        for kb in kbs:
            try:
                vector_store.ensure_collection(kb.id)
            except Exception as e:
                logger.warning(f"Qdrant init failed for kb:{kb.id}: {e}")
        logger.info("Qdrant collections 就绪")
    except Exception as e:
        logger.warning(f"Qdrant 连接失败（服务仍可启动）: {e}")


    # Neo4j
    import os
    neo4j_bin = r'E:/neo4j-chs-community-2026.05.0-windows/bin/neo4j.bat'
    if os.path.exists(neo4j_bin):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.settimeout(2)
            s.connect(('127.0.0.1', 7687))
            s.close()
        except Exception:
            s.close()
            import subprocess
            env = os.environ.copy()
            env.setdefault('JAVA_HOME', r'D:\Program Files\Java\jdk-21')
            subprocess.Popen([neo4j_bin, 'start'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            import time
            for _ in range(20):
                try:
                    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s2.settimeout(1)
                    s2.settimeout(2)
                    s2.connect(('127.0.0.1', 7687))
                    s2.close()
                    break
                except Exception:
                    time.sleep(2)
    # Warm up graph_rag
    try:
        from app.rag.graph_rag import _get_driver
        _get_driver()
    except Exception:
        pass

    yield

    await cache.close()
    _stop_qdrant()
    logger.info("服务关闭")


app = FastAPI(
    title="企业知识库",
    description="企业级知识库搭建与智能对话系统",
    version="2.0.0",
    lifespan=lifespan,
)


# ============================================================
# Auth middleware: extract user_id from JWT token for all requests
# ============================================================
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.core.auth import decode_token

class AuthMiddleware(BaseHTTPMiddleware):
    """Extract user_id from Authorization header and inject into request state."""
    async def dispatch(self, request: Request, call_next):
        user_id = "default"
        username = "anonymous"
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            decoded = decode_token(token)
            if decoded:
                user_id = decoded["user_id"]
                username = decoded["username"]
        request.state.user_id = user_id
        request.state.username = username
        return await call_next(request)

app.add_middleware(AuthMiddleware)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(str(static_dir / "favicon.ico")) if (static_dir / "favicon.ico").exists() else Response(status_code=204)

app.include_router(health_router)
app.include_router(kb_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(eval_router)
app.include_router(auth_router)
app.include_router(graph_router)



@app.get("/login")
async def login_page():
    template_path = Path(__file__).parent / "templates" / "login.html"
    if template_path.exists():
        return FileResponse(str(template_path))
    return {"message": "Login page"}


@app.get("/minimal")
async def minimal_page():
    from fastapi.responses import FileResponse
    from pathlib import Path
    template_path = Path(__file__).parent / "templates" / "minimal.html"
    if template_path.exists():
        return FileResponse(str(template_path))
    return {"message": "Minimal test page"}

@app.get("/")
async def index():
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        return FileResponse(str(template_path))
    return {"message": "企业知识库 API 已运行，请访问 /docs 查看 API 文档"}
