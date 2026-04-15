"""精准等待到指定的墙钟时间 (毫秒级).

策略:
    1. 粗等待: asyncio.sleep 到 T - busy_wait_ms
    2. 细等待: time.time_ns() 轮询自旋到 T
    3. 可选的 server_offset_ns 用于校正本地时钟与服务器时钟偏差

避坑:
    - 不用 perf_counter 做绝对时间 (它是单调时钟, 起点不固定)
    - time.time_ns() 在 Linux 下直接读 CLOCK_REALTIME, 精度 ns 级
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def target_epoch_ns(target_date: datetime, time_str: str = "09:00:00.000") -> int:
    """把 '09:00:00.000' 这样的字符串 + 日期转成 epoch ns (Asia/Shanghai).

    Args:
        target_date: 目标日期 (datetime, 任意时区; 只取 date 部分)
        time_str:    'HH:MM:SS' 或 'HH:MM:SS.mmm'

    Returns:
        epoch ns (int)
    """
    parts = time_str.split(":")
    h = int(parts[0])
    m = int(parts[1])
    if "." in parts[2]:
        s_str, ms_str = parts[2].split(".")
        s = int(s_str)
        us = int(ms_str.ljust(6, "0")[:6])
    else:
        s = int(parts[2])
        us = 0

    d = target_date.astimezone(SHANGHAI).date() if target_date.tzinfo else target_date.date()
    target = datetime(d.year, d.month, d.day, h, m, s, us, tzinfo=SHANGHAI)
    return int(target.timestamp() * 1e9)


class PreciseTimer:
    """混合 sleep + busy-wait 的精准时间控制器."""

    def __init__(
        self,
        target_epoch_ns: int,
        busy_wait_ms: int = 50,
        fire_lead_ms: int = 10,
        server_offset_ns: int = 0,
    ) -> None:
        """
        Args:
            target_epoch_ns: 目标墙钟时间 (ns since epoch)
            busy_wait_ms:    最后多少毫秒进入 busy-wait
            fire_lead_ms:    提前多少毫秒发射 (抵消 RTT/2)
            server_offset_ns: 本地时钟比服务器 **快** 多少 ns (正数表示本地快)
        """
        self.target_ns = target_epoch_ns - int(fire_lead_ms * 1e6) + server_offset_ns
        self.busy_wait_ns = int(busy_wait_ms * 1e6)

    def remaining_ns(self) -> int:
        return self.target_ns - time.time_ns()

    async def wait_until_fire(self) -> int:
        """等待到发射时刻. 返回实际发射时的 wall_us (以便日志)."""
        remaining = self.remaining_ns()

        # 粗等待: 用 asyncio.sleep 到 T - busy_wait_ms
        if remaining > self.busy_wait_ns:
            sleep_s = (remaining - self.busy_wait_ns) / 1e9
            await asyncio.sleep(sleep_s)

        # 细等待: 忙等到精确时刻
        while time.time_ns() < self.target_ns:
            pass

        return time.time_ns() // 1000

    def update_offset(self, server_offset_ns: int) -> None:
        """动态校正 (比如从 HTTP Date 响应头算出的偏差)."""
        delta = server_offset_ns
        self.target_ns += delta


def parse_http_date(date_header: str) -> int | None:
    """解析 HTTP Date 响应头, 返回 epoch ns.

    例: 'Sat, 15 Apr 2026 00:59:50 GMT' → ns
    """
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(date_header)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1e9)
    except Exception:
        return None


def compute_server_offset_ns(
    server_date_ns: int,
    local_sent_ns: int,
    local_recv_ns: int,
) -> int:
    """估算本地时钟相对于服务器的偏差.

    假设 RTT 对称, 服务器打时间戳的时刻 ≈ (sent + recv) / 2.
    返回 "本地比服务器快多少 ns" (正数 = 本地快).
    """
    mid_local = (local_sent_ns + local_recv_ns) // 2
    return mid_local - server_date_ns
