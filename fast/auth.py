"""登录 / token 生命周期管理.

由于 Pure360 通常用短期 JWT, 需要在 9:00 之前刷新一次以避免过期.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from .observability import get_logger

if TYPE_CHECKING:
    from .api_client import AuthToken, PureAPI

log = get_logger()


class AuthManager:
    def __init__(self, api: "PureAPI") -> None:
        self.api = api
        self.username = os.environ.get("PURE_USERNAME", "")
        self.password = os.environ.get("PURE_PASSWORD", "")
        if not self.username or not self.password:
            raise RuntimeError(
                "PURE_USERNAME and PURE_PASSWORD env vars required. "
                "Set them in Alibaba Cloud FC function configuration."
            )

    async def ensure_logged_in(self) -> "AuthToken":
        if self.api.token is None:
            log.info("auth: performing login")
            return await self.api.login(self.username, self.password)
        return self.api.token

    async def refresh_if_needed(self, min_remaining_seconds: int = 600) -> "AuthToken":
        """如果 token 距离过期 < min_remaining_seconds, 就刷新.

        如果没有 expires_at 元数据, 直接重登.
        """
        token = self.api.token
        if token is None:
            return await self.ensure_logged_in()

        now_ns = time.time_ns()
        if token.expires_at_ns is None:
            # 无过期信息, 保守起见重登
            log.info("auth: no expires_at — re-login for safety")
            return await self.api.login(self.username, self.password)

        remaining_s = (token.expires_at_ns - now_ns) / 1e9
        if remaining_s < min_remaining_seconds:
            log.info(f"auth: token remaining {remaining_s:.0f}s — refreshing")
            try:
                return await self.api.refresh()
            except Exception as e:
                log.warning(f"auth: refresh failed ({e}), re-login")
                return await self.api.login(self.username, self.password)

        log.info(f"auth: token ok, remaining {remaining_s:.0f}s")
        return token
