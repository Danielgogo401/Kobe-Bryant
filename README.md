# Pure Fitness 划船机进阶课程自动预约

每周六 8:50 自动登录 Pure Fitness (Pure360) 南京西路门店预约系统，9:00 整准时抢订两天后的划船机进阶训练课程。

## 预约规则

根据 Pure Fitness 官方规则：
- 课程预约在 **开课前 2 天的早上 9:00** 开放
- 热门课程需要准时抢位
- 取消需在开课前 4 小时, 否则计为 Late-cancel

## 环境要求

- Python 3.9+
- Google Chrome 浏览器
- ChromeDriver（脚本会通过 webdriver-manager 自动安装）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置账号

复制配置文件并填写你的信息：

```bash
cp config.yaml config.local.yaml
```

编辑 `config.local.yaml`，填写：
- `credentials.username` — 你的手机号或邮箱
- `credentials.password` — 你的密码
- `booking.class_name` — 课程名称关键字（默认"划船机进阶"）
- `booking.preferred_time` — 偏好的课程时间（如 "10:00"，留空则匹配任意时间）

> `config.local.yaml` 已在 `.gitignore` 中，不会被提交到 Git。

### 3. 测试登录

先用 dry-run 模式测试登录是否正常：

```bash
python book_rowing.py --dry-run
```

检查 `screenshots/` 目录下的截图，确认：
- 登录页面是否正确打开
- 账号密码是否成功输入
- 登录后是否跳转到课程表

### 4. 手动执行一次

```bash
python book_rowing.py
```

### 5. 设置定时任务

**方式 A：Cron（Linux/Mac）**

```bash
bash setup_cron.sh
```

**方式 B：Python 内置调度器**

```bash
python book_rowing.py --schedule
```

此模式会常驻运行，每周六 8:50 自动触发。适合配合 `systemd` 或 `screen`/`tmux` 使用。

## 重要提示：选择器适配

由于 Pure360 网站可能更新页面结构，脚本中的 CSS/XPath 选择器可能需要手动调整。

**适配步骤：**

1. 用 Chrome 打开 https://pure360.pure-fitness.cn/zh-cn/CN/signin
2. 按 F12 打开开发者工具
3. 检查用户名输入框、密码输入框、登录按钮的 HTML 结构
4. 如果脚本中的选择器不匹配，修改 `book_rowing.py` 中 `login()` 函数的 `USERNAME_SELECTORS`、`PASSWORD_SELECTORS`、`LOGIN_BTN_SELECTORS`
5. 同理检查课程表页面的课程卡片和预约按钮结构

## 通知配置（可选）

支持预约结果推送通知：

- **Bark**（iOS）：填写 `notification.bark_key`
- **Webhook**（企业微信/钉钉/飞书）：填写 `notification.webhook_url`

在 `config.local.yaml` 中启用：

```yaml
notification:
  enabled: true
  method: "bark"
  bark_key: "your-bark-key"
```

## 文件结构

```
├── book_rowing.py       # 主预约脚本
├── config.yaml          # 配置模板
├── config.local.yaml    # 你的本地配置（git 忽略）
├── setup_cron.sh        # Cron 安装脚本
├── requirements.txt     # Python 依赖
├── screenshots/         # 运行截图（自动生成）
└── logs/                # 运行日志（自动生成）
```

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| 登录失败 | 检查账号密码；用 `--dry-run` 看截图 |
| 找不到输入框 | F12 检查页面，更新选择器 |
| 课程找不到 | 确认 `class_name` 与网站显示一致 |
| 日期选择失败 | 检查日历组件结构，更新 `DATE_SELECTORS` |
| ChromeDriver 版本不匹配 | `pip install --upgrade webdriver-manager` |
