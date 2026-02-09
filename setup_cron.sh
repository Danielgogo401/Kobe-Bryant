#!/bin/bash
# ============================================================
# Pure Fitness 自动预约 - Cron 定时任务安装脚本
#
# 安装后每周六 8:50 自动运行预约脚本
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_PATH="$(which python3)"
BOOK_SCRIPT="${SCRIPT_DIR}/book_rowing.py"
LOG_FILE="${SCRIPT_DIR}/logs/cron_booking.log"

# 确保日志目录存在
mkdir -p "${SCRIPT_DIR}/logs"

# Cron 表达式: 每周六 8:50 执行
CRON_EXPR="50 8 * * 6"
CRON_CMD="${PYTHON_PATH} ${BOOK_SCRIPT} >> ${LOG_FILE} 2>&1"
CRON_LINE="${CRON_EXPR} ${CRON_CMD}"
CRON_COMMENT="# Pure Fitness auto-booking"

echo "=========================================="
echo " Pure Fitness 自动预约 - Cron 安装"
echo "=========================================="
echo ""
echo "Python:  ${PYTHON_PATH}"
echo "脚本:    ${BOOK_SCRIPT}"
echo "日志:    ${LOG_FILE}"
echo "Cron:    ${CRON_LINE}"
echo ""

# 检查是否已安装
if crontab -l 2>/dev/null | grep -q "book_rowing.py"; then
    echo "⚠️  检测到已有 Pure Fitness 预约任务"
    read -p "是否替换? (y/n): " replace
    if [ "$replace" != "y" ]; then
        echo "取消安装"
        exit 0
    fi
    # 移除旧任务
    crontab -l 2>/dev/null | grep -v "book_rowing.py" | grep -v "Pure Fitness auto-booking" | crontab -
fi

# 安装新任务
(crontab -l 2>/dev/null; echo "${CRON_COMMENT}"; echo "${CRON_LINE}") | crontab -

echo ""
echo "✅ Cron 任务已安装!"
echo ""
echo "验证:"
crontab -l | grep -A1 "Pure Fitness"
echo ""
echo "其他命令:"
echo "  查看任务:  crontab -l"
echo "  删除任务:  crontab -l | grep -v 'book_rowing' | crontab -"
echo "  查看日志:  tail -f ${LOG_FILE}"
