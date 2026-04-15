"""HAR 文件解析器 — 一次性工具, 从浏览器抓包里提取 Pure360 API 细节.

用法:
    python -m fast.extract_har captures/pure360.har

输出:
    - 打印检测到的 login/schedule/book/cancel 端点
    - 打印登录 body 字段名、token 在响应里的 JSON path
    - 打印课表查询响应里 class_id / name / time 的字段名
    - 生成 captures/extracted.env (环境变量文件, 可直接粘到 FC 控制台)
    - generate 时自动 redact 掉密码和 token (不写进文件)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def load_har(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_entries(har: dict) -> list[dict]:
    return har.get("log", {}).get("entries", [])


def parse_body(text: str | None, mime: str | None = None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        # Try to parse form-encoded
        if text and "=" in text and not text.startswith("{"):
            try:
                return {k: v[0] for k, v in parse_qs(text).items()}
            except Exception:
                pass
        return None


def find_json_path(obj: Any, target_value: Any, current_path: str = "") -> str | None:
    """在嵌套 dict 里找某个值, 返回其 JSON path (如 'data.token')."""
    if obj == target_value:
        return current_path
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{current_path}.{k}" if current_path else k
            result = find_json_path(v, target_value, path)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{current_path}.{i}" if current_path else str(i)
            result = find_json_path(v, target_value, path)
            if result is not None:
                return result
    return None


def find_by_key_substring(obj: Any, substr: str, _path: str = "") -> list[tuple[str, Any]]:
    """在嵌套 dict 里找 key 包含 substr 的所有字段, 返回 [(path, value), ...]."""
    results: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{_path}.{k}" if _path else k
            if substr.lower() in k.lower() and not isinstance(v, (dict, list)):
                results.append((path, v))
            results.extend(find_by_key_substring(v, substr, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{_path}.{i}" if _path else str(i)
            results.extend(find_by_key_substring(v, substr, path))
    return results


def redact(value: str, keep: int = 4) -> str:
    if not value or len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}***{value[-keep:]}"


# ---------------------------------------------------------------------------
# 分析器
# ---------------------------------------------------------------------------
class HARAnalyzer:
    # 端点名关键字 (正则)
    LOGIN_PATTERNS = [r"log[\-_]?in", r"sign[\-_]?in", r"auth", r"/member/login"]
    SCHEDULE_PATTERNS = [r"schedule", r"view[\-_]schedule", r"class[\-_]list", r"timetable"]
    BOOK_PATTERNS = [r"book[\-_]?class", r"create[\-_]?book", r"/booking/create", r"book[\-_]?session", r"/book(?![\-_]?history)"]
    CANCEL_PATTERNS = [r"cancel[\-_]?book", r"cancel[\-_]?session", r"unbook"]
    ME_PATTERNS = [r"/me\b", r"profile", r"current[\-_]?user", r"my[\-_]?info"]

    def __init__(self, har: dict) -> None:
        self.har = har
        self.entries = get_entries(har)
        self.findings: dict[str, dict] = {}

    def find_matching(self, patterns: list[str]) -> list[dict]:
        results = []
        for entry in self.entries:
            req = entry.get("request", {})
            url = req.get("url", "")
            method = req.get("method", "")
            # Prefer POST for mutations, GET for reads — but don't filter here
            for pat in patterns:
                if re.search(pat, url, re.IGNORECASE):
                    results.append(entry)
                    break
        return results

    def classify(self) -> None:
        print("\n=== 端点发现 ===")
        for label, patterns in [
            ("login", self.LOGIN_PATTERNS),
            ("schedule", self.SCHEDULE_PATTERNS),
            ("book", self.BOOK_PATTERNS),
            ("cancel", self.CANCEL_PATTERNS),
            ("me", self.ME_PATTERNS),
        ]:
            matches = self.find_matching(patterns)
            if matches:
                # Prefer 2xx responses
                ok_matches = [
                    m for m in matches
                    if 200 <= m.get("response", {}).get("status", 0) < 300
                ]
                chosen = ok_matches[0] if ok_matches else matches[0]
                req = chosen["request"]
                resp = chosen["response"]
                url = req["url"]
                parsed = urlparse(url)
                path = parsed.path
                query = parsed.query
                host = parsed.netloc
                print(f"\n  [{label}] {req['method']} {host}{path}")
                if query:
                    print(f"       query: {query}")
                print(f"       status: {resp.get('status')}")
                self.findings[label] = {
                    "method": req["method"],
                    "scheme": parsed.scheme,
                    "host": host,
                    "path": path,
                    "query": query,
                    "headers": {h["name"]: h["value"] for h in req.get("headers", [])},
                    "body": req.get("postData", {}).get("text"),
                    "response_status": resp.get("status"),
                    "response_body": resp.get("content", {}).get("text"),
                    "response_headers": {h["name"]: h["value"] for h in resp.get("headers", [])},
                }
            else:
                print(f"\n  [{label}] ❌ 未找到")

    def analyze_login(self) -> dict[str, str]:
        env: dict[str, str] = {}
        login = self.findings.get("login")
        if not login:
            print("\n❌ 未检测到登录请求, 无法推断 token 字段")
            return env

        print("\n=== 登录请求分析 ===")
        env["PURE_LOGIN_ENDPOINT"] = login["path"]
        env["PURE_API_BASE"] = f"{login['scheme']}://{login['host']}"

        body = parse_body(login["body"])
        if isinstance(body, dict):
            # 识别用户名/密码字段
            user_keys = [k for k in body.keys() if re.search(r"(user|phone|mobile|email|account|member)", k, re.I)]
            pwd_keys = [k for k in body.keys() if "pass" in k.lower() or "pwd" in k.lower()]
            if user_keys:
                env["PURE_LOGIN_USERNAME_FIELD"] = user_keys[0]
                print(f"  username field: {user_keys[0]} (redacted: {redact(str(body[user_keys[0]]))})")
            if pwd_keys:
                env["PURE_LOGIN_PASSWORD_FIELD"] = pwd_keys[0]
                print(f"  password field: {pwd_keys[0]} (redacted)")
            print(f"  all body fields: {list(body.keys())}")

        # 分析响应找 token
        resp_body = parse_body(login["response_body"])
        if isinstance(resp_body, dict):
            # Find any string value that looks like a JWT/token
            token_candidates = find_by_key_substring(resp_body, "token")
            for path, value in token_candidates:
                if isinstance(value, str) and len(value) > 20:
                    print(f"  token path: {path} (len={len(value)}, preview={redact(value)})")
                    if "refresh" in path.lower():
                        env["PURE_REFRESH_TOKEN_PATH"] = path
                    else:
                        env["PURE_TOKEN_PATH"] = path

            # member_id
            id_candidates = find_by_key_substring(resp_body, "member_id") + find_by_key_substring(resp_body, "user_id")
            for path, value in id_candidates[:3]:
                print(f"  possible member_id path: {path} = {value}")
                if "member" in path.lower() or "user" in path.lower():
                    env["PURE_MEMBER_ID_PATH"] = path
                    break

        return env

    def analyze_schedule(self) -> dict[str, str]:
        env: dict[str, str] = {}
        sch = self.findings.get("schedule")
        if not sch:
            return env

        print("\n=== 课表查询分析 ===")
        env["PURE_SCHEDULE_ENDPOINT"] = sch["path"]

        # Check query params for location_id, date format
        if sch["query"]:
            print(f"  query params: {sch['query']}")

        resp_body = parse_body(sch["response_body"])
        if isinstance(resp_body, (dict, list)):
            # Find list of classes
            if isinstance(resp_body, list):
                sample = resp_body[0] if resp_body else {}
                env["PURE_CLASS_LIST_PATH"] = ""
            elif isinstance(resp_body, dict):
                # Find the key whose value is a list of dicts (likely the class list)
                for k, v in resp_body.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        env["PURE_CLASS_LIST_PATH"] = k
                        sample = v[0]
                        print(f"  class list at path: {k} ({len(v)} classes)")
                        break
                    if isinstance(v, dict):
                        for k2, v2 in v.items():
                            if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                                env["PURE_CLASS_LIST_PATH"] = f"{k}.{k2}"
                                sample = v2[0]
                                print(f"  class list at path: {k}.{k2} ({len(v2)} classes)")
                                break
                else:
                    sample = {}

            if sample:
                # Identify id/name/time field names
                id_fields = [k for k in sample.keys() if re.search(r"(^id$|class_?id|session_?id|event_?id)", k, re.I)]
                name_fields = [k for k in sample.keys() if re.search(r"(name|title|class_?name)", k, re.I)]
                time_fields = [k for k in sample.keys() if re.search(r"(start|time|begin)", k, re.I)]
                print(f"  sample class fields: {list(sample.keys())}")
                if id_fields:
                    env["PURE_CLASS_ID_FIELD"] = id_fields[0]
                    print(f"  class_id field: {id_fields[0]}")
                if name_fields:
                    env["PURE_CLASS_NAME_FIELD"] = name_fields[0]
                    print(f"  class_name field: {name_fields[0]}")
                if time_fields:
                    env["PURE_CLASS_TIME_FIELD"] = time_fields[0]
                    print(f"  class_time field: {time_fields[0]}")

        return env

    def analyze_book(self) -> dict[str, str]:
        env: dict[str, str] = {}
        book = self.findings.get("book")
        if not book:
            print("\n❌ 未检测到预约请求 — 你抓包时可能没有点击 Book 按钮")
            return env

        print("\n=== 预约请求分析 ===")
        env["PURE_BOOK_ENDPOINT"] = book["path"]
        env["PURE_BOOK_METHOD"] = book["method"]

        body = parse_body(book["body"])
        if isinstance(body, dict):
            print(f"  body fields: {list(body.keys())}")
            print(f"  body sample: {json.dumps(body, ensure_ascii=False)[:200]}")
            # Store body template with placeholder
            template = dict(body)
            for k in list(template.keys()):
                if re.search(r"class|session|event", k, re.I):
                    template[k] = "{{class_id}}"
            env["PURE_BOOK_BODY_TEMPLATE"] = json.dumps(template, ensure_ascii=False)

        # Check required headers (CSRF, custom X-*)
        important_headers = {
            k: v for k, v in book["headers"].items()
            if k.lower() in ("x-csrf-token", "x-requested-with", "origin", "referer")
            or k.lower().startswith("x-")
        }
        if important_headers:
            print(f"  important headers: {list(important_headers.keys())}")
            for k, v in important_headers.items():
                if k.lower() == "origin":
                    env["PURE_ORIGIN"] = v
                elif k.lower() == "referer":
                    env["PURE_REFERER"] = v
                elif k.lower() == "user-agent":
                    env["PURE_USER_AGENT"] = v

        ua = book["headers"].get("User-Agent") or book["headers"].get("user-agent")
        if ua:
            env["PURE_USER_AGENT"] = ua

        return env

    def write_env_file(self, env: dict[str, str], path: Path) -> None:
        lines = [
            "# Pure Fitness FC 环境变量 — 由 extract_har.py 自动生成",
            "# ⚠️  密码和 token 已被 redact, 不会出现在此文件里",
            "# 部署到阿里云 FC 时, 这些值需要填到 FC 控制台的环境变量里",
            "",
            "# === 账号 (在 FC 控制台手动填写, 此文件不含) ===",
            "# PURE_USERNAME=<你的手机号或邮箱>",
            "# PURE_PASSWORD=<你的密码>",
            "",
            "# === 预约目标 ===",
            "PURE_LOCATION_ID=83",
            "PURE_CLASS_NAME=划船机进阶",
            "PURE_CLASS_NAME_ALT=Rowing",
            "",
            "# === API (由 HAR 自动提取) ===",
        ]
        for k, v in env.items():
            # Escape quotes/newlines
            v_str = str(v).replace('"', '\\"').replace("\n", "\\n")
            lines.append(f'{k}="{v_str}"')
        lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n✅ 环境变量写入: {path}")
        print(f"   (共 {len(env)} 个字段)")

    def run(self) -> dict[str, str]:
        print("=" * 60)
        print("Pure Fitness HAR 分析器")
        print("=" * 60)
        print(f"  总请求数: {len(self.entries)}")

        self.classify()

        env: dict[str, str] = {}
        env.update(self.analyze_login())
        env.update(self.analyze_schedule())
        env.update(self.analyze_book())

        return env


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m fast.extract_har <path-to-har>")
        return 1

    har_path = Path(sys.argv[1])
    if not har_path.exists():
        print(f"❌ 文件不存在: {har_path}")
        return 1

    har = load_har(har_path)
    analyzer = HARAnalyzer(har)
    env = analyzer.run()

    out_path = har_path.parent / "extracted.env"
    analyzer.write_env_file(env, out_path)

    print("\n" + "=" * 60)
    print("下一步:")
    print(f"  1. 检查 {out_path} 内容是否合理")
    print("  2. 手动在 FC 控制台设置 PURE_USERNAME / PURE_PASSWORD")
    print("  3. 把 extracted.env 里的其他变量粘到 FC 环境变量里")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
