"""阿里云函数计算 (FC) 入口.

触发器: 定时触发器 cron `0 58 8 ? * SAT` (Asia/Shanghai)
运行时: Python 3.10
内存:   128 MB
超时:   300 秒 (05:00, 足够从 08:58 等到 09:00:05)

函数流程:
    1. 初始化日志 + tracer
    2. 构造 BookingEngine (读取环境变量)
    3. 异步执行 engine.run()
    4. 返回结果字典 (FC 会记到调用日志里)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fast.booking_engine import BookingEngine
from fast.observability import setup_logging


def _send_notification(title: str, message: str) -> None:
    """可选: 通过 Bark / Webhook 推送结果."""
    bark_key = os.environ.get("BARK_KEY", "")
    webhook_url = os.environ.get("WEBHOOK_URL", "")

    if not bark_key and not webhook_url:
        return

    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            if bark_key:
                # URL-encode
                from urllib.parse import quote
                url = f"https://api.day.app/{bark_key}/{quote(title)}/{quote(message)}"
                client.get(url)
            if webhook_url:
                client.post(
                    webhook_url,
                    json={"msgtype": "text", "text": {"content": f"{title}\n{message}"}},
                )
    except Exception:
        pass  # 通知失败不影响主流程


async def _run() -> dict[str, Any]:
    engine = BookingEngine.from_env()
    result = await engine.run()

    return {
        "success": result.ok,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "burst_status": result.burst.status if result.burst else None,
        "winner_idx": result.burst.winner_idx if result.burst else None,
        "burst_latency_us": result.burst.latency_us if result.burst else None,
        "summary": result.summary,
    }


def handler(event: Any, context: Any) -> str:
    """阿里云 FC Python handler.

    Args:
        event:   触发事件 (timer trigger 时是 payload 字节串)
        context: FC 上下文对象 (包含 request_id, credentials 等)

    Returns:
        JSON 字符串
    """
    logger = setup_logging(context)
    logger.info(f"FC invoke start, request_id={getattr(context, 'request_id', 'n/a')}")

    try:
        result = asyncio.run(_run())
    except Exception as e:
        logger.exception("handler: top-level error")
        result = {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
        }

    # 推通知
    if result.get("success"):
        _send_notification(
            "🎉 划船机进阶预约成功",
            f"延迟 {result.get('burst_latency_us', '?')}μs",
        )
    else:
        _send_notification(
            "❌ 划船机预约失败",
            str(result.get("error", "unknown"))[:200],
        )

    logger.info(f"FC invoke done: {json.dumps(result, ensure_ascii=False, default=str)}")
    return json.dumps(result, ensure_ascii=False, default=str)


# 本地测试入口
if __name__ == "__main__":
    class _FakeContext:
        request_id = "local-test"

    print(handler({}, _FakeContext()))
