"""Pure360 API 异步客户端封装.

所有 HAR-specific 的细节 (端点路径、字段名、JSON path) 都从 config/env 注入,
这样 extract_har.py 解析完后只需更新环境变量, 不用改代码.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from .observability import get_logger

log = get_logger()


# ---------------------------------------------------------------------------
# 辅助: 从嵌套 dict 按 "data.token" 这样的 path 取值
# ---------------------------------------------------------------------------
def json_path_get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit():
            idx = int(part)
            obj = obj[idx] if 0 <= idx < len(obj) else None
        else:
            return None
    return obj


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class AuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_at_ns: int | None = None
    member_id: str | int | None = None

    def as_header(self, scheme: str = "bearer") -> dict[str, str]:
        if scheme == "bearer":
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}


@dataclass
class ClassInfo:
    class_id: str
    name: str
    start_time: str
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# API 配置
# ---------------------------------------------------------------------------
@dataclass
class APIConfig:
    base_url: str
    endpoints: dict[str, str]
    json_paths: dict[str, str]
    login_fields: dict[str, str]
    static_headers: dict[str, str]
    auth_scheme: str = "bearer"
    pool_size: int = 8
    http2: bool = True
    timeout: float = 5.0
    connect_timeout: float = 2.0

    @classmethod
    def from_env(cls) -> "APIConfig":
        return cls(
            base_url=os.environ.get("PURE_API_BASE", "https://pure360.pure-fitness.cn"),
            auth_scheme=os.environ.get("PURE_AUTH_SCHEME", "bearer"),
            endpoints={
                "login": os.environ.get("PURE_LOGIN_ENDPOINT", "/api/v1/member/login"),
                "refresh": os.environ.get("PURE_REFRESH_ENDPOINT", "/api/v1/member/refresh"),
                "schedule": os.environ.get("PURE_SCHEDULE_ENDPOINT", "/api/v1/schedule"),
                "book": os.environ.get("PURE_BOOK_ENDPOINT", "/api/v1/booking/create"),
                "me": os.environ.get("PURE_ME_ENDPOINT", "/api/v1/member/me"),
            },
            json_paths={
                "token": os.environ.get("PURE_TOKEN_PATH", "data.token"),
                "refresh_token": os.environ.get("PURE_REFRESH_TOKEN_PATH", "data.refresh_token"),
                "token_expires": os.environ.get("PURE_TOKEN_EXPIRES_PATH", "data.expires_in"),
                "member_id": os.environ.get("PURE_MEMBER_ID_PATH", "data.member.id"),
                "class_list": os.environ.get("PURE_CLASS_LIST_PATH", "data.classes"),
                "class_id_field": os.environ.get("PURE_CLASS_ID_FIELD", "id"),
                "class_name_field": os.environ.get("PURE_CLASS_NAME_FIELD", "name"),
                "class_time_field": os.environ.get("PURE_CLASS_TIME_FIELD", "start_time"),
            },
            login_fields={
                "username": os.environ.get("PURE_LOGIN_USERNAME_FIELD", "username"),
                "password": os.environ.get("PURE_LOGIN_PASSWORD_FIELD", "password"),
            },
            static_headers={
                "User-Agent": os.environ.get(
                    "PURE_USER_AGENT",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": os.environ.get("PURE_ORIGIN", "https://pure360.pure-fitness.cn"),
                "Referer": os.environ.get("PURE_REFERER", "https://pure360.pure-fitness.cn/zh-cn/CN"),
            },
        )


# ---------------------------------------------------------------------------
# 主客户端
# ---------------------------------------------------------------------------
class PureAPI:
    def __init__(self, config: APIConfig) -> None:
        self.cfg = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            http2=config.http2,
            headers=config.static_headers,
            limits=httpx.Limits(
                max_keepalive_connections=config.pool_size,
                max_connections=config.pool_size * 2,
            ),
            timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
        )
        self.token: AuthToken | None = None

    async def close(self) -> None:
        await self.client.aclose()

    # ---------- Auth ----------
    async def login(self, username: str, password: str) -> AuthToken:
        body = {
            self.cfg.login_fields["username"]: username,
            self.cfg.login_fields["password"]: password,
        }
        log.info(f"login → {self.cfg.endpoints['login']}")
        resp = await self.client.post(self.cfg.endpoints["login"], json=body)
        resp.raise_for_status()
        data = resp.json()

        token_str = json_path_get(data, self.cfg.json_paths["token"])
        if not token_str:
            raise RuntimeError(
                f"login: token not found at path '{self.cfg.json_paths['token']}'. "
                f"Response keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
            )

        self.token = AuthToken(
            access_token=token_str,
            refresh_token=json_path_get(data, self.cfg.json_paths["refresh_token"]),
            member_id=json_path_get(data, self.cfg.json_paths["member_id"]),
        )
        # Set auth header on client so future requests include it
        if self.cfg.auth_scheme == "bearer":
            self.client.headers["Authorization"] = f"Bearer {token_str}"
        return self.token

    async def refresh(self) -> AuthToken:
        if not self.token or not self.token.refresh_token:
            raise RuntimeError("refresh: no refresh token available")
        resp = await self.client.post(
            self.cfg.endpoints["refresh"],
            json={"refresh_token": self.token.refresh_token},
        )
        resp.raise_for_status()
        data = resp.json()
        new_token = json_path_get(data, self.cfg.json_paths["token"])
        if not new_token:
            raise RuntimeError("refresh: no token in response")
        self.token.access_token = new_token
        self.client.headers["Authorization"] = f"Bearer {new_token}"
        return self.token

    # ---------- Schedule ----------
    async def list_schedule(
        self, location_id: int, target_date: date
    ) -> list[ClassInfo]:
        params = {
            "location_ids": location_id,
            "date": target_date.isoformat(),
        }
        resp = await self.client.get(self.cfg.endpoints["schedule"], params=params)
        resp.raise_for_status()
        data = resp.json()

        raw_list = json_path_get(data, self.cfg.json_paths["class_list"])
        if raw_list is None:
            # Fallback: maybe root is already a list
            raw_list = data if isinstance(data, list) else []

        id_field = self.cfg.json_paths["class_id_field"]
        name_field = self.cfg.json_paths["class_name_field"]
        time_field = self.cfg.json_paths["class_time_field"]

        classes: list[ClassInfo] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            classes.append(
                ClassInfo(
                    class_id=str(item.get(id_field, "")),
                    name=str(item.get(name_field, "")),
                    start_time=str(item.get(time_field, "")),
                    raw=item,
                )
            )
        return classes

    # ---------- Warm ping ----------
    async def ping(self) -> httpx.Response:
        return await self.client.get(self.cfg.endpoints["me"])

    # ---------- Book (build + send) ----------
    def build_book_request(self, class_id: str) -> httpx.Request:
        """预构造预约请求, 9:00:00.000 时原封不动发送."""
        body = {"class_id": class_id}
        # Allow override of body shape via env
        body_override = os.environ.get("PURE_BOOK_BODY_TEMPLATE")
        if body_override:
            import json as _json
            body = _json.loads(body_override.replace("{{class_id}}", class_id))

        return self.client.build_request(
            "POST",
            self.cfg.endpoints["book"],
            json=body,
        )

    async def send_prebuilt(self, request: httpx.Request) -> httpx.Response:
        return await self.client.send(request)
