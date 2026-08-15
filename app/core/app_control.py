"""运行期控制通道：设置接口通过这里请求桌面启动器重启服务。"""
from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
_target_port: Optional[int] = None


def request_restart(port: int) -> None:
    """请求启动器在指定端口重启后端。"""
    global _target_port
    with _lock:
        _target_port = int(port)


def take_restart_request() -> Optional[int]:
    """取出待处理的重启请求端口；没有请求时返回 None。"""
    global _target_port
    with _lock:
        port = _target_port
        _target_port = None
    return port
