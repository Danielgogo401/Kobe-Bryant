# Pure Fitness 划船机进阶课程毫秒级抢课

每周六 9:00 整**从阿里云函数计算 (上海)** 自动抢订两天后的 Pure Fitness 南京西路门店"划船机进阶训练"课程。

## 为什么不是本地脚本?

1. **同城延迟最低**: 阿里云 FC 上海区与 Pure 服务器同城, RTT < 20 ms (比本地 Mac 还快)
2. **免维护**: 不需要 Mac 保持唤醒、不需要管家里的网络
3. **毫秒级精度**: 函数内部 `asyncio.sleep + time.time_ns()` 精准自旋到 9:00:00.000
4. **永久免费**: 每月 40 万 GB·s 额度, 每周运行 1 次只用 0.004%
5. **并发 burst**: 同时发 5 路 POST, 抢 sub-10ms 的预约时间窗口

## 总体流程

```
周六 08:58:00  阿里云 FC timer 触发器 (cron: 0 58 8 ? * SAT)
          ↓
08:58:00.200  冷启动 + import
08:58:01      登录 Pure360 → 拿 token
08:58:02      查 2 天后的课表 → 找到"划船机进阶" class_id
08:58:03      预构造 POST /api/book 请求 (httpx.Request 对象)
08:58:05-59:59 每 15 秒发 /api/me 保活 TLS 连接 + 校准服务器时钟
08:59:59.950  asyncio.sleep 结束, 进入 time.time_ns() 忙等待
09:00:00.000  asyncio.gather(5 × POST) → burst 发射!
09:00:00.020  请求抵达 Pure 服务器 (同城 RTT)
09:00:00.050  首个 2xx 响应 → cancel 其余 → 发 Bark 通知
09:00:01      函数 return, 容器空闲等待下次触发
```

## 文件结构

```
Kobe-Bryant/
├── index.py              # 阿里云 FC 入口 (handler 函数)
├── fast/                 # 核心业务模块
│   ├── api_client.py     # Pure360 API 封装 (httpx.AsyncClient)
│   ├── auth.py           # 登录 / token 刷新
│   ├── session_warmer.py # TLS 连接保活
│   ├── class_resolver.py # 按名字/日期找 class_id
│   ├── precise_timer.py  # 毫秒级精准等待
│   ├── burst_fire.py     # 并发 burst 发射
│   ├── booking_engine.py # 编排器 (主流程)
│   ├── extract_har.py    # HAR 解析工具 (一次性本地跑)
│   └── observability.py  # 结构化日志 + 微秒级追踪
├── captures/             # HAR 抓包 (gitignored)
│   ├── README.md         # 抓包注意事项
│   └── pure360.har       # ← 你手动放这里
├── deploy/               # 部署相关
│   ├── s.yaml            # Serverless Devs 配置
│   ├── requirements.txt  # FC 运行时依赖
│   ├── .env.example      # 环境变量模板
│   └── DEPLOY.md         # 详细部署教程
├── config.yaml           # 运行配置 (env-var driven)
├── requirements.txt      # 本地开发依赖
└── README.md             # 本文件
```

## 部署总览

实施分 3 步:

### 1. HAR 抓包 (一次性, 5 分钟)

用浏览器 DevTools 抓一次"登录 → 查课 → 预约 → 取消"的完整网络流量,
导出 HAR 文件, 放到 `captures/pure360.har`.

详细步骤见 [主 README 的抓包章节](#har-抓包详细步骤) 或项目对话中的指引.

### 2. 解析 HAR → 生成环境变量

```bash
pip install -r requirements.txt
python -m fast.extract_har captures/pure360.har
```

会生成 `captures/extracted.env`, 自动识别所有 API 端点和字段名.

### 3. 部署到阿里云 FC

见 [`deploy/DEPLOY.md`](deploy/DEPLOY.md) 的超详细教程 (预计 30 分钟完成).

简要版:
```bash
npm install -g @serverless-devs/s
s config add                # 配置阿里云 AK
cd deploy
cp .env.example .env        # 填写账号和 API 信息
set -a && source .env && set +a
s deploy -y
```

## HAR 抓包详细步骤

1. Chrome **无痕窗口** (`Cmd+Shift+N`)
2. `F12` 打开 DevTools → **Network** 标签
3. 勾 **Preserve log** + **Disable cache**, 点 **Fetch/XHR** 过滤
4. 访问 https://pure360.pure-fitness.cn → 登录
5. 进入南京西路门店课程表
6. 找一节**非热门**课 → 点 **Book** → 确认预约
7. 进入"我的预约" → **取消**这节课 (释放名额)
8. Network 面板顶部工具栏找 **⬇️ Export HAR** 按钮 (或右键任意请求 → Save as HAR)
9. 保存为 `pure360.har` → 放到 `captures/` 目录

**安全**: HAR 含明文密码和 token, 不要外传.

## 本地测试

```bash
# 1. 设置环境变量
set -a && source deploy/.env && set +a

# 2. dry-run 本地测试 (跳过真实预约)
python -c "
import asyncio
from fast.booking_engine import BookingEngine
engine = BookingEngine.from_env()
print(asyncio.run(engine.run()))
"
```

## 观测性

- **SLS 日志服务**: 所有事件结构化 JSON 日志, 30 天保留
- **关键指标**: `fire_delta_us` (发射偏差), `first_byte_us` (首字节响应), `winner_idx`
- **通知**: 支持 Bark (iOS) / 企业微信 / 钉钉 webhook

## 成本

每月实际花费: **¥0** (每月 4 次调用, 远低于免费额度 1M 次 + 400,000 GB·s)

## 声明

- 本工具仅用于**个人使用者**为自己预约已付费会员课程
- 5 路 burst 的并发数很小, 不构成 DDoS 或恶意访问
- 不绕过认证、不爬数据、不对抗 Pure 的反爬措施
- 若 Pure 明确禁止 API 自动化, 请停止使用

## 开发日志

| 版本 | 变更 |
|------|------|
| v0.1 | Selenium 原型 (已废弃, 10+ 秒延迟) |
| v1.0 | 阿里云 FC 异步 httpx 实现, 目标 < 50 ms 延迟 |
