# captures/ — HAR 抓包存放目录

## ⚠️ 此目录内的文件不会被 git 提交

`.gitignore` 已配置忽略所有 `*.har` 和 `*.json` 文件, 因为 HAR 含有:
- 明文密码
- 认证 token (Bearer JWT)
- 会员 ID 和个人信息
- 所有请求的 cookies

**不要**:
- 把 HAR 文件发到群 / 云盘 / 邮件
- 截图展示 HAR 内容
- 提交到任何 git 仓库

---

## 使用流程

1. 按 [项目根 README](../README.md) 里的指南做一次浏览器抓包
2. 把导出的 `.har` 文件放到这个目录, 命名为 `pure360.har`
3. 运行分析器:
   ```bash
   python -m fast.extract_har captures/pure360.har
   ```
4. 生成 `captures/extracted.env`, 里面包含所有 API 端点和字段映射
5. 把 `extracted.env` 的内容合并到 `deploy/.env` 或 FC 控制台环境变量

---

## 文件清单

| 文件 | 用途 | 含敏感信息 |
|------|------|-----------|
| `pure360.har` | 原始抓包 | ✅ 密码 + token |
| `extracted.env` | 自动生成的环境变量 | ❌ 已 redact |
| `README.md` | 本文件 | ❌ |
