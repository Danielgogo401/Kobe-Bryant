"""TLS 连接保活 — 每隔 N 秒发一个 ping, 防止 HTTP/2 连接被对端关闭.

额外作用: 从响应头 Date 校正服务器时钟偏差 (供 PreciseTimer 使用).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from .observability import get_logger
from .precise_timer import parse_http_date, compute_server_offset_ns

if TYPE_CHECKING:
    from .api_client import PureAPI

log = get_logger()


class SessionWarmer:
    def __init__(self, api: "PureAPI", interval_s: float = 15.0) -> None:
        self.api = api
        self.interval = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_server_offset_ns: int = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def ping_once(self) -> None:
        sent_ns = time.time_ns()
        try:
            resp = await self.api.ping()
            recv_ns = time.time_ns()
            if resp.status_code == 200:
                # 尝试从 Date header 校正时钟
                date_hdr = resp.headers.get("Date")
                if date_hdr:
                    server_ns = parse_http_date(date_hdr)
                    if server_ns:
                        self.last_server_offset_ns = compute_server_offset_ns(
                            server_ns, sent_ns, recv_ns
                        )
                        log.info(
                            f"warm: ping ok, server_offset={self.last_server_offset_ns / 1e6:.1f}ms"
                        )
                    else:
                        log.info("warm: ping ok (no Date header)")
            else:
                log.warning(f"warm: ping status {resp.status_code}")
        except Exception as e:
            log.warning(f"warm: ping failed {e}")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self.ping_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue
