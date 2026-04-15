"""Pure Fitness 毫秒级抢课 — 核心模块.

部署环境:
    阿里云函数计算 (cn-shanghai), 与 Pure 服务器同城 RTT < 20ms.

触发:
    Timer cron `0 58 8 ? * SAT` (Asia/Shanghai)

模块:
    - api_client:      httpx.AsyncClient 封装的 Pure360 API
    - auth:            登录 / token 生命周期管理
    - class_resolver:  按 (date, name) 查 class_id
    - session_warmer:  TLS 连接保活
    - precise_timer:   墙钟 + perf_counter 混合精准等待
    - burst_fire:      asyncio.gather N 路并发 POST
    - booking_engine:  编排器
    - observability:   结构化日志 + 微秒级时间戳
    - extract_har:     一次性 HAR 解析工具 (本地运行)
"""

__version__ = "1.0.0"
