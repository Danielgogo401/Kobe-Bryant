"""并发 burst 发射 N 个相同的预约 POST, 谁先 2xx 谁赢.

设计:
    - 同时发 N 个相同请求 (N=5 默认)
    - 使用 asyncio.as_completed 取第一个成功响应
    - 收到成功后立即 cancel 其余 task
    - 记录每个 task 的发送/接收时间戳 (微秒级)
    - Pure 通常幂等: 即使多个 2xx 也不会重复预约
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from .observability import get_logger, Tracer

if TYPE_CHECKING:
    pass

log = get_logger()


@dataclass
class BurstResult:
    ok: bool
    winner_idx: int | None = None
    status: int | None = None
    response_text: str = ""
    latency_us: int | None = None
    errors: list[str] = field(default_factory=list)
    all_statuses: list[int | None] = field(default_factory=list)

    @classmethod
    def success(cls, idx: int, resp: httpx.Response, latency_us: int) -> "BurstResult":
        return cls(
            ok=True,
            winner_idx=idx,
            status=resp.status_code,
            response_text=resp.text[:500],
            latency_us=latency_us,
        )

    @classmethod
    def failure(cls, errors: list[str], statuses: list[int | None]) -> "BurstResult":
        return cls(ok=False, errors=errors, all_statuses=statuses)


async def _send_one(
    client: httpx.AsyncClient,
    request: httpx.Request,
    idx: int,
    stagger_us: int,
    tracer: Tracer,
) -> tuple[int, int, int, httpx.Response | None, str | None]:
    """发送一个请求, 返回 (idx, sent_ns, recv_ns, response, error_str)."""
    # burst 内部错峰, 避免同时抵达被 WAF 识别为攻击
    if stagger_us > 0:
        await asyncio.sleep(stagger_us / 1e6)

    sent_ns = time.time_ns()
    tracer.event("burst_send", idx=idx, stagger_us=stagger_us)
    try:
        # 注意: 每次都要用 build_request 生成独立对象, 因为 httpx.Request
        # 对象在被 send 后内部状态会变, 不能重用.
        # 这里我们接受外部传入的是"模板" request, 每次调用 send 会自动处理.
        resp = await client.send(request)
        recv_ns = time.time_ns()
        tracer.event("burst_recv", idx=idx, status=resp.status_code)
        return idx, sent_ns, recv_ns, resp, None
    except Exception as e:
        recv_ns = time.time_ns()
        err = f"{type(e).__name__}: {e}"
        tracer.event("burst_error", idx=idx, error=err)
        return idx, sent_ns, recv_ns, None, err


async def fire_burst(
    client: httpx.AsyncClient,
    request_template: httpx.Request,
    n: int,
    stagger_ms: int,
    idempotent_status: list[int],
    tracer: Tracer,
) -> BurstResult:
    """并发发射 N 个请求, 返回首个成功结果."""
    fire_ns = time.time_ns()
    tracer.event("burst_fire_start", n=n, stagger_ms=stagger_ms)

    # 为每个任务构造独立的 request (避免 httpx 内部状态复用问题)
    tasks: list[asyncio.Task] = []
    for i in range(n):
        req = client.build_request(
            request_template.method,
            request_template.url,
            content=request_template.content,
            headers=request_template.headers,
        )
        task = asyncio.create_task(_send_one(client, req, i, i * stagger_ms * 1000, tracer))
        tasks.append(task)

    errors: list[str] = []
    statuses: list[int | None] = []

    try:
        for fut in asyncio.as_completed(tasks):
            idx, sent_ns, recv_ns, resp, err = await fut
            if err:
                errors.append(f"[{idx}] {err}")
                statuses.append(None)
                continue
            assert resp is not None
            statuses.append(resp.status_code)
            if resp.status_code in idempotent_status or (200 <= resp.status_code < 300):
                latency_us = (recv_ns - sent_ns) // 1000
                tracer.event(
                    "burst_winner",
                    idx=idx,
                    status=resp.status_code,
                    latency_us=latency_us,
                )
                # Cancel siblings
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return BurstResult.success(idx, resp, latency_us)

        return BurstResult.failure(errors, statuses)
    finally:
        # Ensure all tasks resolved
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
