"""预约编排器 — 串起 登录 → 预取 class_id → 精准等待 → burst 发射.

这是阿里云 FC handler 直接调用的主入口.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from .api_client import APIConfig, PureAPI
from .auth import AuthManager
from .burst_fire import BurstResult, fire_burst
from .class_resolver import ClassResolver
from .observability import Tracer, get_logger
from .precise_timer import SHANGHAI, PreciseTimer, target_epoch_ns
from .session_warmer import SessionWarmer

log = get_logger()


@dataclass
class EngineResult:
    ok: bool
    burst: BurstResult | None = None
    error: str | None = None
    summary: dict | None = None
    latency_ms: float | None = None


class BookingEngine:
    def __init__(
        self,
        api: PureAPI,
        auth: AuthManager,
        resolver: ClassResolver,
        warmer: SessionWarmer,
        tracer: Tracer,
        target_time: str = "09:00:00.000",
        days_ahead: int = 2,
        burst_n: int = 5,
        stagger_ms: int = 10,
        max_bursts: int = 3,
        burst_interval_ms: int = 200,
        busy_wait_ms: int = 50,
        fire_lead_ms: int = 10,
        idempotent_status: list[int] | None = None,
    ) -> None:
        self.api = api
        self.auth = auth
        self.resolver = resolver
        self.warmer = warmer
        self.tracer = tracer
        self.target_time = target_time
        self.days_ahead = days_ahead
        self.burst_n = burst_n
        self.stagger_ms = stagger_ms
        self.max_bursts = max_bursts
        self.burst_interval_ms = burst_interval_ms
        self.busy_wait_ms = busy_wait_ms
        self.fire_lead_ms = fire_lead_ms
        self.idempotent_status = idempotent_status or [200, 201]

    @classmethod
    def from_env(cls) -> "BookingEngine":
        api_cfg = APIConfig.from_env()
        api = PureAPI(api_cfg)
        auth = AuthManager(api)
        resolver = ClassResolver.from_env(api)
        warmer = SessionWarmer(
            api, interval_s=float(os.environ.get("WARM_INTERVAL_S", "15"))
        )
        tracer = Tracer()

        idem = [int(x) for x in os.environ.get("BURST_IDEMPOTENT_STATUS", "200,201").split(",")]

        return cls(
            api=api,
            auth=auth,
            resolver=resolver,
            warmer=warmer,
            tracer=tracer,
            target_time=os.environ.get("TARGET_TIME", "09:00:00.000"),
            days_ahead=int(os.environ.get("DAYS_AHEAD", "2")),
            burst_n=int(os.environ.get("BURST_COUNT", "5")),
            stagger_ms=int(os.environ.get("BURST_STAGGER_MS", "10")),
            max_bursts=int(os.environ.get("MAX_BURSTS", "3")),
            burst_interval_ms=int(os.environ.get("BURST_INTERVAL_MS", "200")),
            busy_wait_ms=int(os.environ.get("BUSY_WAIT_MS", "50")),
            fire_lead_ms=int(os.environ.get("FIRE_LEAD_MS", "10")),
            idempotent_status=idem,
        )

    async def run(self) -> EngineResult:
        t_start = time.monotonic_ns()
        try:
            self.tracer.event("engine_start")

            # 1. 登录
            await self.auth.ensure_logged_in()
            self.tracer.event("login_ok")

            # 2. 预取目标课程
            target_class = await self.resolver.resolve(self.days_ahead)
            self.tracer.event(
                "class_resolved",
                class_id=target_class.class_id,
                class_name=target_class.name,
                start_time=target_class.start_time,
            )

            # 3. 预构造预约请求
            book_req = self.api.build_book_request(target_class.class_id)
            self.tracer.event("request_built")

            # 4. 启动连接保活 (后台任务)
            self.warmer.start()
            self.tracer.event("warmer_started")

            # 5. 计算目标发射时间 (NS)
            target_date = self.resolver.target_date(self.days_ahead)
            target_dt = datetime(
                target_date.year, target_date.month, target_date.day, tzinfo=SHANGHAI
            )
            target_ns = target_epoch_ns(target_dt, self.target_time)
            self.tracer.set_target(target_ns // 1000)

            # 当前时间到目标时间的秒数
            now_ns = time.time_ns()
            wait_s = (target_ns - now_ns) / 1e9
            log.info(
                f"engine: waiting {wait_s:.1f}s until "
                f"{self.target_time} on {target_date}"
            )
            self.tracer.event("wait_start", wait_s=wait_s)

            # 6. 最后 30 秒前做一次 token 刷新检查
            if wait_s > 35:
                await self.auth.refresh_if_needed(min_remaining_seconds=600)

            # 7. 精准等待发射
            timer = PreciseTimer(
                target_epoch_ns=target_ns,
                busy_wait_ms=self.busy_wait_ms,
                fire_lead_ms=self.fire_lead_ms,
                server_offset_ns=self.warmer.last_server_offset_ns,
            )
            fire_wall_us = await timer.wait_until_fire()
            self.tracer.event("fire_armed", fire_wall_us=fire_wall_us)

            # 停止保活 (避免占用连接)
            await self.warmer.stop()

            # 8. Burst 发射
            for burst_i in range(self.max_bursts):
                self.tracer.event(f"burst_attempt_{burst_i}")
                result = await fire_burst(
                    client=self.api.client,
                    request_template=book_req,
                    n=self.burst_n,
                    stagger_ms=self.stagger_ms,
                    idempotent_status=self.idempotent_status,
                    tracer=self.tracer,
                )

                if result.ok:
                    elapsed_ms = (time.monotonic_ns() - t_start) / 1e6
                    summary = self.tracer.summary()
                    return EngineResult(
                        ok=True,
                        burst=result,
                        summary=summary,
                        latency_ms=elapsed_ms,
                    )

                # 检查是否可重试
                log.warning(
                    f"burst {burst_i} failed: statuses={result.all_statuses}, errors={result.errors[:3]}"
                )
                if burst_i < self.max_bursts - 1:
                    import asyncio
                    await asyncio.sleep(self.burst_interval_ms / 1000)
                    # 重建 request (httpx 内部状态)
                    book_req = self.api.build_book_request(target_class.class_id)

            # 所有 burst 都失败
            elapsed_ms = (time.monotonic_ns() - t_start) / 1e6
            summary = self.tracer.summary()
            return EngineResult(
                ok=False,
                burst=result,
                error="all bursts failed",
                summary=summary,
                latency_ms=elapsed_ms,
            )

        except Exception as e:
            log.exception("engine: unhandled error")
            elapsed_ms = (time.monotonic_ns() - t_start) / 1e6
            return EngineResult(ok=False, error=f"{type(e).__name__}: {e}", latency_ms=elapsed_ms)
        finally:
            try:
                await self.warmer.stop()
            except Exception:
                pass
            await self.api.close()
