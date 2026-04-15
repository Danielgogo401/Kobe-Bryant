"""根据日期 + 课程名关键字定位 class_id.

注意: 9:00 前课表已经可见 (用户确认), 所以可以提前拿 class_id,
只需最后的 book POST 等到 9:00:00.000 发射.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .observability import get_logger

if TYPE_CHECKING:
    from .api_client import ClassInfo, PureAPI

log = get_logger()
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ClassNotFoundError(RuntimeError):
    pass


class ClassResolver:
    def __init__(
        self,
        api: "PureAPI",
        location_id: int,
        class_name: str,
        class_name_alt: str = "",
        preferred_time: str = "",
    ) -> None:
        self.api = api
        self.location_id = location_id
        self.class_name = class_name.lower()
        self.class_name_alt = class_name_alt.lower()
        self.preferred_time = preferred_time

    def target_date(self, days_ahead: int = 2) -> date:
        now_sha = datetime.now(tz=SHANGHAI)
        return (now_sha + timedelta(days=days_ahead)).date()

    async def resolve(self, days_ahead: int = 2) -> "ClassInfo":
        td = self.target_date(days_ahead)
        log.info(f"resolver: querying schedule for {td} (location={self.location_id})")
        classes = await self.api.list_schedule(self.location_id, td)
        log.info(f"resolver: got {len(classes)} classes for {td}")

        matched = [
            c for c in classes
            if self.class_name in c.name.lower()
            or (self.class_name_alt and self.class_name_alt in c.name.lower())
        ]
        log.info(f"resolver: {len(matched)} matched '{self.class_name}'")

        if not matched:
            # 打印所有课名, 方便调试
            names = [c.name for c in classes[:20]]
            log.error(f"resolver: no match. Sample names: {names}")
            raise ClassNotFoundError(
                f"No class matching '{self.class_name}' on {td} at location {self.location_id}"
            )

        # 若指定了 preferred_time, 按时间过滤
        if self.preferred_time:
            filtered = [c for c in matched if self.preferred_time in c.start_time]
            if filtered:
                matched = filtered

        chosen = matched[0]
        log.info(f"resolver: chose class_id={chosen.class_id} name={chosen.name} time={chosen.start_time}")
        return chosen

    @classmethod
    def from_env(cls, api: "PureAPI") -> "ClassResolver":
        return cls(
            api=api,
            location_id=int(os.environ.get("PURE_LOCATION_ID", "83")),
            class_name=os.environ.get("PURE_CLASS_NAME", "划船机进阶"),
            class_name_alt=os.environ.get("PURE_CLASS_NAME_ALT", "Rowing"),
            preferred_time=os.environ.get("PURE_PREFERRED_TIME", ""),
        )
