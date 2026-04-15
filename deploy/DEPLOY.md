# 阿里云函数计算部署指南

本文档详细说明如何将 Pure Fitness 抢课程序部署到阿里云函数计算（FC 3.0）。

## 前置条件

- ✅ 阿里云账号（已实名认证）
- ✅ HAR 抓包已完成，`captures/extracted.env` 已生成
- ✅ 已获取预约用户名 / 密码
- 本地环境：Node.js 16+（用于 Serverless Devs CLI）

---

## 步骤 1: 创建阿里云 AccessKey

函数计算需要 AccessKey 才能部署。

1. 登录 [阿里云控制台](https://home.console.aliyun.com/)
2. 右上角头像 → **AccessKey 管理**
3. 创建 **子用户 AccessKey**（**不要用主账号 AK**，不安全）
   - 访问控制 RAM → 用户 → 创建用户
   - 用户名：`pure-booking-deployer`
   - 访问方式：✅ OpenAPI 调用访问
   - 创建后，**立即复制 AccessKey ID 和 AccessKey Secret**（只显示一次）
4. 给这个子用户授权：
   - 点用户名 → 权限管理 → 新增授权
   - 系统策略：`AliyunFCFullAccess`（函数计算全部权限）
   - 系统策略：`AliyunLogFullAccess`（日志服务，FC 需要写日志）

---

## 步骤 2: 开通函数计算服务

1. 访问 [函数计算 FC 3.0 控制台](https://fcnext.console.aliyun.com/)
2. 如果首次使用，会提示开通服务 — 点"立即开通"
3. 选择地域：**华东 2（上海）cn-shanghai**
4. 开通成功后进入 FC 控制台

---

## 步骤 3: 安装 Serverless Devs CLI

在本地 Mac 终端：

```bash
# 安装 Node.js (若未安装)
brew install node

# 安装 Serverless Devs
npm install -g @serverless-devs/s

# 验证安装
s --version
```

---

## 步骤 4: 配置阿里云凭据

```bash
s config add
```

按提示选择：
- Provider: **Alibaba Cloud (alibaba)**
- AccessKeyID: `<步骤 1 的 AK>`
- AccessKeySecret: `<步骤 1 的 SK>`
- Alias: **default**

---

## 步骤 5: 填写环境变量

```bash
cd deploy
cp .env.example .env
```

编辑 `.env`：

```bash
# 账号
PURE_USERNAME=你的手机号或邮箱
PURE_PASSWORD=你的密码

# 从 captures/extracted.env 里复制这些值 (HAR 分析后自动生成):
PURE_API_BASE=...
PURE_LOGIN_ENDPOINT=...
...
```

**⚠️ `.env` 文件包含密码，已被 `.gitignore`，不会提交到 git。**

加载到当前 shell：

```bash
set -a && source .env && set +a
```

---

## 步骤 6: 部署函数

```bash
cd deploy   # 必须在 deploy/ 目录下
s deploy -y
```

Serverless Devs 会：
1. 打包 `../` 目录的代码（index.py + fast/ + deploy/requirements.txt）
2. 上传到 OSS
3. 创建/更新函数
4. 创建定时触发器
5. 输出部署结果

预期输出：
```
  pure-booking:
    region:       cn-shanghai
    functionName: pure-rowing-booking
    runtime:      python3.10
    handler:      index.handler
    triggers:
      - name: weekly-saturday
        type: timer
```

---

## 步骤 7: 验证部署

### 7.1 控制台查看

1. [FC 控制台](https://fcnext.console.aliyun.com/cn-shanghai/) → 函数列表
2. 点 `pure-rowing-booking` 进入详情
3. 确认：
   - 运行时：Python 3.10 ✅
   - 环境变量：有 PURE_USERNAME / PURE_PASSWORD / PURE_* ✅
   - 触发器：weekly-saturday，cron `0 58 8 ? * SAT` ✅

### 7.2 手动 dry-run 测试

在"配置 → 环境变量"临时加：
```
DRY_RUN=true
```

然后在"测试函数"里点**执行**，查看：
- 日志里能否看到 `login ok`、`class_resolved` 等关键事件
- 是否走到发射步骤（dry-run 模式会跳过实际 POST）

测试完记得把 `DRY_RUN` 环境变量删掉。

### 7.3 查看运行日志

函数控制台 → **日志** 标签 → 最近调用
或直接去 [SLS 日志服务](https://sls.console.aliyun.com/) 搜索 `pure-rowing-booking`

关键日志行：
- `engine_start`
- `login_ok`
- `class_resolved class_id=... name=...`
- `burst_fire_start`
- `burst_winner idx=... status=200 latency_us=...`
- `SUMMARY fire_delta_us=... first_byte_us=...`

---

## 步骤 8: 首次实战

下周六 **08:58:00** FC 自动触发。你可以在 08:57 左右打开 FC 控制台看实时日志。

如果设置了 Bark 推送，9:00 后几秒会收到结果通知。

---

## 故障排查

### 冷启动过慢

如果看到"cold start 1500ms"之类的日志：
- 在控制台给函数加"**预留模式实例**"（免费额度有限，可能产生费用）
- 或加一个 `0 55 8 ? * SAT` 的 warmup 触发器，payload `{"mode":"warmup"}`，提前 3 分钟启动容器

### 日志里没有 winner

全部 burst 失败：
- 查看 `burst_attempt_N` 和 `burst_recv` 事件的 status 码
- 409 "已预约" → 其实成功了，检查是否幂等处理
- 401 → token 过期或无效，检查是否需要重登
- 429 → 被限流，减小 BURST_COUNT
- 超时 → 检查 endpoint 是否正确

### HAR 过期

Pure360 可能更新前端，导致 API 变化。重新抓一次包 → 跑 extract_har.py → 更新环境变量 → `s deploy` 重新部署。

---

## 成本估算

| 项 | 每次运行消耗 | 免费额度 | 每月实际消耗 |
|----|------------|----------|------------|
| 函数调用次数 | 1 次 | 1M 次 | 4 次 |
| GB·秒 | 128MB × 125s ≈ 16 GB·s | 400,000 GB·s | 64 GB·s |
| 公网出流量 | < 100 KB | — | < 1 MB |

**每月实际费用: ¥0**（远低于免费额度）

---

## 删除部署

```bash
cd deploy
s remove -y
```

这会删除函数、触发器和角色，不会删除日志（需要手动去 SLS 删）。
