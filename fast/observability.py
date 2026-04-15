"""结构化日志 + 微秒级时间戳追踪.

设计目标:
    - 每个关键事件都打一行 JSON, 方便 FC 日志服务 (SLS) 过滤和分析
    - 记录墙钟时间 (`wall_us`) 和单调时钟 (`mono_ns`) 双重时间戳
    - 最后输出一行 SUMMARY 包含发射偏差等关键指标
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_LOGGER_NAME = "pure_booking"
_logger: logging.Logger | None = None


def setup_logging(context: Any = None) -> logging.Logger:
    """初始化 logger. 在 FC 环境下 context 是 FC 的 context 对象."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

    # 清掉可能已有的 handler (FC 复用容器时重入)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)

    request_id = getattr(context, "request_id", None) if context else None
    if request_id:
        logger.info(f"logging initialized request_id={request_id}")

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    if _logger is None:
        return setup_logging()
    return _logger


# ---------------------------------------------------------------------------
# 事件追踪
# ---------------------------------------------------------------------------


@dataclass
class TraceEvent:
    name: str
    wall_us: int  # 墙钟 μs since epoch
    mono_ns: int  # 单调时钟 ns
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "event": self.name,
                "wall_us": self.wall_us,
                "mono_ns": self.mono_ns,
                **self.extra,
            },
            ensure_ascii=False,
        )


class Tracer:
    """收集关键事件, 结束时输出汇总."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.target_wall_us: int | None = None
        self.log = get_logger()

    def event(self, name: str, **extra: Any) -> TraceEvent:
        ev = TraceEvent(
            name=name,
            wall_us=time.time_ns() // 1000,
            mono_ns=time.monotonic_ns(),
            extra=extra,
        )
        self.events.append(ev)
        self.log.info(f"TRACE {ev.to_json()}")
        return ev

    def set_target(self, target_wall_us: int) -> None:
        self.target_wall_us = target_wall_us

    def summary(self) -> dict[str, Any]:
        """输出一行总结, 包含发射偏差等."""
        fire = next((e for e in self.events if e.name == "burst_fire_start"), None)
        winner = next((e for e in self.events if e.name == "burst_winner"), None)

        summary: dict[str, Any] = {
            "total_events": len(self.events),
        }
        if fire and self.target_wall_us is not None:
            summary["fire_delta_us"] = fire.wall_us - self.target_wall_us
        if winner and fire:
            summary["first_byte_us"] = winner.wall_us - fire.wall_us
        if winner:
            summary["winner_idx"] = winner.extra.get("idx")
            summary["winner_status"] = winner.extra.get("status")

        self.log.info(f"SUMMARY {json.dumps(summary, ensure_ascii=False)}")
        return summary
