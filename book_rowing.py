#!/usr/bin/env python3
"""
Pure Fitness 划船机进阶课程自动预约脚本

功能:
- 每周六 8:50 自动登录 Pure360 预约系统
- 9:00 整准时抢订两天后的划船机进阶训练课程
- 支持重试、截图、通知

使用方式:
  python book_rowing.py              # 立即执行一次预约流程
  python book_rowing.py --schedule   # 启动定时任务, 每周六自动执行
  python book_rowing.py --dry-run    # 测试模式, 只登录不预约
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

try:
    import requests as req_lib
except ImportError:
    req_lib = None

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"booking_{datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 截图目录
# ---------------------------------------------------------------------------
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


def load_config(path: str = "config.yaml") -> dict:
    """加载配置文件"""
    config_path = Path(__file__).parent / path
    # 优先读取 config.local.yaml (不纳入版本控制)
    local_path = Path(__file__).parent / "config.local.yaml"
    if local_path.exists():
        config_path = local_path
        logger.info("使用本地配置: config.local.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_driver(config: dict) -> webdriver.Chrome:
    """创建并配置 Chrome WebDriver"""
    browser_cfg = config.get("browser", {})

    options = ChromeOptions()
    if browser_cfg.get("headless", False):
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # 降低自动化检测
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver_path = browser_cfg.get("driver_path", "")
    if driver_path:
        service = ChromeService(executable_path=driver_path)
    elif ChromeDriverManager:
        service = ChromeService(ChromeDriverManager().install())
    else:
        service = ChromeService()

    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    timeout = browser_cfg.get("page_timeout", 30)
    driver.set_page_load_timeout(timeout)
    driver.implicitly_wait(5)

    return driver


def take_screenshot(driver: webdriver.Chrome, name: str) -> str:
    """截图保存"""
    filename = f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
    filepath = SCREENSHOT_DIR / filename
    driver.save_screenshot(str(filepath))
    logger.info(f"截图已保存: {filepath}")
    return str(filepath)


def send_notification(config: dict, title: str, message: str):
    """发送通知 (Bark / Webhook)"""
    notif = config.get("notification", {})
    if not notif.get("enabled", False) or req_lib is None:
        return

    method = notif.get("method", "")
    try:
        if method == "bark":
            bark_key = notif.get("bark_key", "")
            if bark_key:
                url = f"https://api.day.app/{bark_key}/{title}/{message}"
                req_lib.get(url, timeout=10)
                logger.info("Bark 通知已发送")
        elif method == "webhook":
            webhook_url = notif.get("webhook_url", "")
            if webhook_url:
                req_lib.post(
                    webhook_url,
                    json={"msgtype": "text", "text": {"content": f"{title}\n{message}"}},
                    timeout=10,
                )
                logger.info("Webhook 通知已发送")
    except Exception as e:
        logger.warning(f"通知发送失败: {e}")


# ===================================================================
#  核心预约流程
# ===================================================================


def login(driver: webdriver.Chrome, config: dict) -> bool:
    """
    登录 Pure360 账号

    注意: 以下 CSS 选择器基于 Pure360 网站的常见结构。
    如果登录失败, 请用浏览器开发者工具 (F12) 检查实际的
    输入框和按钮的选择器, 然后更新下方的常量。
    """
    creds = config["credentials"]
    urls = config.get("urls", {})
    login_url = urls.get("login", "https://pure360.pure-fitness.cn/zh-cn/CN/signin")

    logger.info(f"正在打开登录页面: {login_url}")
    driver.get(login_url)
    time.sleep(3)
    take_screenshot(driver, "01_login_page")

    wait = WebDriverWait(driver, 15)

    # ---------------------------------------------------------------
    # ⚠️  重要: 以下选择器可能需要根据实际页面调整
    #     请先手动打开网站, 用 F12 检查元素, 确认选择器正确
    # ---------------------------------------------------------------

    # 常见的登录表单选择器 (按优先级尝试多种)
    USERNAME_SELECTORS = [
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[type='tel']"),
        (By.CSS_SELECTOR, "input[name='username']"),
        (By.CSS_SELECTOR, "input[name='email']"),
        (By.CSS_SELECTOR, "input[name='phone']"),
        (By.CSS_SELECTOR, "input[placeholder*='手机']"),
        (By.CSS_SELECTOR, "input[placeholder*='邮箱']"),
        (By.CSS_SELECTOR, "input[placeholder*='email']"),
        (By.CSS_SELECTOR, "#username"),
        (By.CSS_SELECTOR, "#email"),
    ]

    PASSWORD_SELECTORS = [
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.CSS_SELECTOR, "input[name='password']"),
        (By.CSS_SELECTOR, "#password"),
    ]

    LOGIN_BTN_SELECTORS = [
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//button[contains(text(), '登录')]"),
        (By.XPATH, "//button[contains(text(), 'Sign')]"),
        (By.XPATH, "//button[contains(text(), 'Log')]"),
        (By.CSS_SELECTOR, ".login-btn"),
        (By.CSS_SELECTOR, ".btn-primary"),
    ]

    def find_first(selectors):
        for by, selector in selectors:
            try:
                el = driver.find_element(by, selector)
                if el.is_displayed():
                    return el
            except Exception:
                continue
        return None

    try:
        # 输入用户名
        username_input = find_first(USERNAME_SELECTORS)
        if not username_input:
            logger.error("找不到用户名输入框, 请检查选择器配置")
            take_screenshot(driver, "error_no_username_field")
            return False

        username_input.clear()
        username_input.send_keys(creds["username"])
        logger.info("已输入用户名")
        time.sleep(0.5)

        # 输入密码
        password_input = find_first(PASSWORD_SELECTORS)
        if not password_input:
            logger.error("找不到密码输入框, 请检查选择器配置")
            take_screenshot(driver, "error_no_password_field")
            return False

        password_input.clear()
        password_input.send_keys(creds["password"])
        logger.info("已输入密码")
        time.sleep(0.5)

        take_screenshot(driver, "02_credentials_entered")

        # 点击登录按钮
        login_btn = find_first(LOGIN_BTN_SELECTORS)
        if not login_btn:
            logger.error("找不到登录按钮, 请检查选择器配置")
            take_screenshot(driver, "error_no_login_btn")
            return False

        login_btn.click()
        logger.info("已点击登录按钮")
        time.sleep(5)
        take_screenshot(driver, "03_after_login")

        # 验证登录是否成功 — 检查是否跳转到了课程页面或出现用户头像等
        current_url = driver.current_url
        page_source = driver.page_source.lower()

        if "signin" in current_url and "error" in page_source:
            logger.error("登录失败, 请检查用户名和密码")
            return False

        logger.info(f"登录成功! 当前页面: {current_url}")
        return True

    except Exception as e:
        logger.error(f"登录过程出错: {e}")
        take_screenshot(driver, "error_login_exception")
        return False


def navigate_to_schedule(driver: webdriver.Chrome, config: dict):
    """导航到课程表页面, 定位到两天后的日期"""
    urls = config.get("urls", {})
    location_id = config["booking"].get("location_id", 83)
    schedule_url = urls.get(
        "schedule",
        f"https://pure360.pure-fitness.cn/zh-cn/CN?location_id={location_id}",
    )

    # 计算两天后的日期
    target_date = datetime.now() + timedelta(days=2)
    target_date_str = target_date.strftime("%Y-%m-%d")
    logger.info(f"目标预约日期: {target_date_str} ({target_date.strftime('%A')})")

    logger.info(f"正在打开课程表: {schedule_url}")
    driver.get(schedule_url)
    time.sleep(3)
    take_screenshot(driver, "04_schedule_page")

    # 尝试选择目标日期
    # Pure360 通常使用日期选择器或日历组件
    DATE_SELECTORS = [
        # 按 data 属性匹配日期
        (By.CSS_SELECTOR, f"[data-date='{target_date_str}']"),
        (By.CSS_SELECTOR, f"[data-value='{target_date_str}']"),
        # 按文本匹配日期 (日)
        (By.XPATH, f"//div[contains(@class,'date')]//*[text()='{target_date.day}']"),
        (By.XPATH, f"//span[contains(@class,'date')][text()='{target_date.day}']"),
        (By.XPATH, f"//a[contains(@class,'date')][text()='{target_date.day}']"),
        # 如果是 tab / 标签页形式
        (By.XPATH, f"//li[contains(text(), '{target_date.day}')]"),
        (By.XPATH, f"//div[contains(@class,'day')][contains(text(), '{target_date.day}')]"),
    ]

    for by, selector in DATE_SELECTORS:
        try:
            el = driver.find_element(by, selector)
            if el.is_displayed():
                el.click()
                logger.info(f"已选择日期: {target_date_str}")
                time.sleep(2)
                take_screenshot(driver, "05_date_selected")
                return target_date_str
        except Exception:
            continue

    logger.warning("未能自动选择日期, 可能需要手动调整日期选择器")
    take_screenshot(driver, "05_date_selection_failed")
    return target_date_str


def find_and_book_class(driver: webdriver.Chrome, config: dict) -> bool:
    """
    查找并预约划船机进阶课程

    返回 True 表示预约成功
    """
    booking_cfg = config["booking"]
    class_name = booking_cfg.get("class_name", "划船机进阶")
    class_name_alt = booking_cfg.get("class_name_alt", "Rowing")
    preferred_time = booking_cfg.get("preferred_time", "")

    logger.info(f"正在查找课程: {class_name} / {class_name_alt}")

    # 查找课程卡片
    # Pure360 通常用卡片或列表展示课程
    CLASS_SELECTORS = [
        # 按课程名称文本匹配
        (By.XPATH, f"//*[contains(text(), '{class_name}')]"),
        (By.XPATH, f"//*[contains(text(), '{class_name_alt}')]"),
        # 常见的课程卡片 class 名
        (By.XPATH, f"//div[contains(@class,'class-card')]//*[contains(text(), '{class_name}')]"),
        (By.XPATH, f"//div[contains(@class,'schedule-item')]//*[contains(text(), '{class_name}')]"),
        (By.XPATH, f"//div[contains(@class,'event')]//*[contains(text(), '{class_name}')]"),
    ]

    target_element = None
    for by, selector in CLASS_SELECTORS:
        try:
            elements = driver.find_elements(by, selector)
            for el in elements:
                if el.is_displayed():
                    text = el.text
                    # 如果有首选时间, 检查时间是否匹配
                    if preferred_time:
                        parent = el.find_element(By.XPATH, "./ancestor::div[contains(@class,'card') or contains(@class,'item') or contains(@class,'event')]")
                        parent_text = parent.text
                        if preferred_time not in parent_text:
                            continue
                    target_element = el
                    logger.info(f"找到课程: {text}")
                    break
            if target_element:
                break
        except Exception:
            continue

    if not target_element:
        logger.error(f"未找到课程: {class_name}")
        take_screenshot(driver, "error_class_not_found")
        return False

    take_screenshot(driver, "06_class_found")

    # 点击课程卡片或预约按钮
    try:
        # 先尝试点击课程卡片本身
        target_element.click()
        time.sleep(1)
        take_screenshot(driver, "07_class_clicked")
    except Exception as e:
        logger.warning(f"点击课程卡片失败: {e}, 尝试 JavaScript 点击")
        driver.execute_script("arguments[0].click();", target_element)
        time.sleep(1)

    # 查找并点击 "预约" / "Book" 按钮
    BOOK_BTN_SELECTORS = [
        (By.XPATH, "//button[contains(text(), '预约')]"),
        (By.XPATH, "//button[contains(text(), 'Book')]"),
        (By.XPATH, "//a[contains(text(), '预约')]"),
        (By.XPATH, "//a[contains(text(), 'Book')]"),
        (By.CSS_SELECTOR, ".book-btn"),
        (By.CSS_SELECTOR, ".btn-book"),
        (By.CSS_SELECTOR, "[class*='book']"),
        (By.XPATH, "//button[contains(@class, 'book')]"),
        (By.XPATH, "//div[contains(@class, 'book')]"),
    ]

    for by, selector in BOOK_BTN_SELECTORS:
        try:
            btn = driver.find_element(by, selector)
            if btn.is_displayed() and btn.is_enabled():
                btn.click()
                logger.info("已点击预约按钮!")
                time.sleep(2)
                take_screenshot(driver, "08_book_clicked")

                # 处理可能的确认弹窗
                confirm_booking(driver)
                return True
        except Exception:
            continue

    # 如果没有单独的预约按钮, 可能点击课程卡片就是预约
    # (Pure360 的 "双击预约" 模式)
    logger.info("尝试再次点击课程卡片 (双击预约模式)")
    try:
        target_element.click()
        time.sleep(2)
        take_screenshot(driver, "08_double_click_book")
        confirm_booking(driver)
        return True
    except Exception as e:
        logger.error(f"预约操作失败: {e}")
        take_screenshot(driver, "error_book_failed")
        return False


def confirm_booking(driver: webdriver.Chrome):
    """处理预约确认弹窗"""
    CONFIRM_SELECTORS = [
        (By.XPATH, "//button[contains(text(), '确认')]"),
        (By.XPATH, "//button[contains(text(), 'Confirm')]"),
        (By.XPATH, "//button[contains(text(), 'OK')]"),
        (By.XPATH, "//button[contains(text(), '确定')]"),
        (By.CSS_SELECTOR, ".confirm-btn"),
        (By.CSS_SELECTOR, ".modal .btn-primary"),
    ]

    time.sleep(1)
    for by, selector in CONFIRM_SELECTORS:
        try:
            btn = driver.find_element(by, selector)
            if btn.is_displayed():
                btn.click()
                logger.info("已确认预约")
                time.sleep(2)
                take_screenshot(driver, "09_booking_confirmed")
                return
        except Exception:
            continue

    logger.info("未检测到确认弹窗 (可能不需要确认)")


def wait_until_book_time(config: dict):
    """
    精确等待到 9:00:00 再执行预约

    在 9 点前会持续刷新检查, 确保在开放的第一秒执行
    """
    schedule_cfg = config.get("schedule", {})
    book_time_str = schedule_cfg.get("book_time", "09:00:00")

    h, m, s = map(int, book_time_str.split(":"))
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=s, microsecond=0)

    if now >= target:
        logger.info("当前时间已过预约开放时间, 直接执行预约")
        return

    wait_seconds = (target - now).total_seconds()
    logger.info(f"距离预约开放时间 {book_time_str} 还有 {wait_seconds:.0f} 秒")

    # 粗等待: 距离目标 2 秒前用 sleep
    while True:
        now = datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0.5:
            break
        if remaining > 10:
            logger.info(f"等待中... 剩余 {remaining:.0f} 秒")
            time.sleep(min(remaining - 2, 5))
        elif remaining > 2:
            time.sleep(0.5)
        else:
            # 最后 2 秒, 忙等待获取最高精度
            time.sleep(0.05)

    logger.info(f"🕘 到达预约时间! 当前: {datetime.now().strftime('%H:%M:%S.%f')}")


# ===================================================================
#  主流程
# ===================================================================


def run_booking(config: dict, dry_run: bool = False) -> bool:
    """执行一次完整的预约流程"""
    logger.info("=" * 60)
    logger.info("Pure Fitness 自动预约 - 开始执行")
    logger.info(f"目标课程: {config['booking']['class_name']}")
    logger.info(f"目标门店: {config['booking']['location_name']}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 60)

    driver = None
    success = False

    try:
        # 1. 创建浏览器
        logger.info("[步骤 1/5] 启动浏览器...")
        driver = create_driver(config)

        # 2. 登录
        logger.info("[步骤 2/5] 登录 Pure360...")
        if not login(driver, config):
            logger.error("登录失败, 终止预约")
            send_notification(config, "预约失败", "Pure Fitness 登录失败, 请检查账号密码")
            return False

        # 3. 导航到课程表
        logger.info("[步骤 3/5] 导航到课程表...")
        target_date = navigate_to_schedule(driver, config)

        if dry_run:
            logger.info("[Dry Run] 测试模式, 跳过预约步骤")
            take_screenshot(driver, "dryrun_final")
            return True

        # 4. 等待 9:00
        logger.info("[步骤 4/5] 等待预约开放...")
        preload_sec = config.get("schedule", {}).get("preload_seconds", 10)

        # 在 9 点前几秒刷新页面, 确保课程数据是最新的
        schedule_cfg = config.get("schedule", {})
        book_time_str = schedule_cfg.get("book_time", "09:00:00")
        h, m, s = map(int, book_time_str.split(":"))
        now = datetime.now()
        target_time = now.replace(hour=h, minute=m, second=s, microsecond=0)
        remaining = (target_time - now).total_seconds()

        if remaining > preload_sec:
            # 提前等到 preload 时间点
            pre_wait = remaining - preload_sec
            logger.info(f"先等待 {pre_wait:.0f} 秒后刷新页面...")
            time.sleep(pre_wait)

        if remaining > 0:
            # 刷新页面获取最新课程
            logger.info("刷新课程表页面...")
            driver.refresh()
            time.sleep(3)
            navigate_to_schedule(driver, config)

        # 精确等待到 9:00:00
        wait_until_book_time(config)

        # 5. 抢课!
        logger.info("[步骤 5/5] 执行预约!")
        retry_count = config.get("schedule", {}).get("retry_count", 5)
        retry_interval = config.get("schedule", {}).get("retry_interval", 1)

        for attempt in range(1, retry_count + 1):
            logger.info(f"--- 预约尝试 {attempt}/{retry_count} ---")

            # 每次尝试前刷新页面 (第一次除外)
            if attempt > 1:
                logger.info("刷新页面重试...")
                driver.refresh()
                time.sleep(2)
                navigate_to_schedule(driver, config)

            if find_and_book_class(driver, config):
                success = True
                logger.info("✅ 预约成功!")
                send_notification(
                    config,
                    "预约成功",
                    f"已成功预约 {target_date} 的划船机进阶课程!",
                )
                break
            else:
                logger.warning(f"第 {attempt} 次尝试失败")
                if attempt < retry_count:
                    time.sleep(retry_interval)

        if not success:
            logger.error("所有尝试均失败")
            send_notification(config, "预约失败", "未能成功预约划船机进阶课程, 请手动预约")

        take_screenshot(driver, "10_final_result")
        return success

    except Exception as e:
        logger.error(f"预约流程异常: {e}", exc_info=True)
        if driver:
            take_screenshot(driver, "error_unexpected")
        send_notification(config, "预约异常", f"程序异常: {e}")
        return False

    finally:
        if driver:
            logger.info("关闭浏览器")
            driver.quit()


def run_scheduled(config: dict):
    """定时任务模式: 每周六 8:50 自动启动预约流程"""
    try:
        import schedule as sched_lib
    except ImportError:
        logger.error("请安装 schedule 库: pip install schedule")
        sys.exit(1)

    login_time = config.get("schedule", {}).get("login_time", "08:50")
    day = config.get("schedule", {}).get("day_of_week", "saturday")

    logger.info(f"定时任务已启动: 每周{day} {login_time} 执行预约")
    logger.info("按 Ctrl+C 退出")

    job_fn = lambda: run_booking(config)

    # 根据配置的星期几设置定时任务
    day_map = {
        "monday": sched_lib.every().monday,
        "tuesday": sched_lib.every().tuesday,
        "wednesday": sched_lib.every().wednesday,
        "thursday": sched_lib.every().thursday,
        "friday": sched_lib.every().friday,
        "saturday": sched_lib.every().saturday,
        "sunday": sched_lib.every().sunday,
    }

    scheduler = day_map.get(day.lower(), sched_lib.every().saturday)
    scheduler.at(login_time).do(job_fn)

    while True:
        sched_lib.run_pending()
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(
        description="Pure Fitness 划船机进阶课程自动预约"
    )
    parser.add_argument(
        "--schedule", action="store_true",
        help="启动定时任务模式 (每周六自动执行)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="测试模式: 只登录不预约"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )

    args = parser.parse_args()
    config = load_config(args.config)

    if args.schedule:
        run_scheduled(config)
    else:
        success = run_booking(config, dry_run=args.dry_run)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
