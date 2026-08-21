from __future__ import annotations
from collections import defaultdict
import logging
import os
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
from app.api.settings import router as settings_router

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

_qdrant_process = None


def _start_qdrant():
    """以子进程方式启动 Qdrant（若尚未运行）。"""
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
        qdrant_data_dir = Path(settings.data_dir).resolve() / "qdrant"
        qdrant_data_dir.mkdir(parents=True, exist_ok=True)
        _qdrant_process = subprocess.Popen(
            [str(qdrant_path.resolve())],
            cwd=str(qdrant_data_dir),
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
    logger.info(f"PDH-PKG 服务启动 - 端口 {settings.service_port}")
    if JWT_SECRET.startswith("insecure-fallback"):
        logger.warning("Security: JWT secret is a fallback. Set JWT_SECRET_KEY in .env.")
    elif settings.jwt_secret_key in ("change-me-in-production-use-a-strong-random-key", ""):
        logger.warning("Security: JWT secret auto-generated and persisted to data/secret.key.")
    if "admin123" in settings.preset_users or "user123" in settings.preset_users:
        logger.warning("Security: preset passwords are weak defaults. Change them in .env.")
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
                if not vector_store.check_embedding_consistency(kb.id):
                    logger.error(
                        "KB %s: embedding dimension mismatch; document retrieval disabled until reindex or provider switch",
                        kb.id,
                    )
            except Exception as e:
                logger.warning(f"Qdrant init failed for kb:{kb.id}: {e}")
        logger.info("Qdrant collections 就绪")
    except Exception as e:
        logger.warning(f"Qdrant 连接失败（服务仍可启动）: {e}")


    # Neo4j 知识图谱
    import os
    from app.core.user_settings import get_settings
    neo4j_bin = settings.neo4j_bin
    if neo4j_bin and os.path.exists(neo4j_bin) and get_settings().neo4j_enabled:
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
            if settings.neo4j_java_home:
                env.setdefault('JAVA_HOME', settings.neo4j_java_home)
            subprocess.Popen([neo4j_bin, 'start'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            import time
            for _ in range(10):
                try:
                    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s2.settimeout(1)
                    s2.settimeout(2)
                    s2.connect(('127.0.0.1', 7687))
                    s2.close()
                    break
                except Exception:
                    time.sleep(2)
    # 预热知识图谱模块
    if get_settings().neo4j_enabled:
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
    title="PDH-PKG",
    description="个人知识库搭建与智能对话系统",
    version="0.2.0",
    lifespan=lifespan,
)


# ============================================================
# 鉴权中间件：从 JWT 令牌中解析用户信息
# ============================================================
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.core.auth import decode_token, JWT_SECRET

_rate_hits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 120
RATE_LIMIT_WINDOW = 60


def _check_rate_limit(key: str) -> bool:
    if os.environ.get("PDH_PKG_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    now = time.monotonic()
    hits = [t for t in _rate_hits[key] if now - t < RATE_LIMIT_WINDOW]
    _rate_hits[key] = hits
    if len(hits) >= RATE_LIMIT_MAX:
        return False
    _rate_hits[key].append(now)
    return True

class AuthMiddleware(BaseHTTPMiddleware):
    """要求 /api/* 使用 Bearer 鉴权（登录接口除外），并注入用户状态。"""
    async def dispatch(self, request: Request, call_next):
        from fastapi.responses import JSONResponse
        path = request.url.path
        if path.startswith("/api/"):
            client_host = request.client.host if request.client else "local"
            if not _check_rate_limit(client_host + ":" + path):
                return JSONResponse({"detail": "请求过于频繁"}, status_code=429)
        if path.startswith("/api/") and path not in (
            "/api/auth/login",
            "/api/auth/local-token",
            "/api/auth/login-local",
            "/api/settings/public",
        ):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse({"detail": "未认证"}, status_code=401)
            decoded = decode_token(auth_header[7:])
            if not decoded:
                return JSONResponse({"detail": "无效的认证令牌"}, status_code=401)
            request.state.user_id = decoded["user_id"]
            request.state.username = decoded["username"]
            return await call_next(request)
        user_id = "default"
        username = "anonymous"
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
app.include_router(settings_router)



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
    return {"message": "PDH-PKG API 已运行，请访问 /docs 查看 API 文档"}
