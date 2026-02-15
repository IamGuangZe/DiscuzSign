import os
import re
import sys
import time
import random  # 新增：用于生成随机延迟
import cloudscraper
import pyperclip
import yaml
from datetime import datetime
from winotify import Notification, audio

LOG_FILE = "logs.txt"
LOG_LEVELS = ["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]


def log(msg, level="INFO"):
    if level not in LOG_LEVELS:
        level = "INFO"
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} [{level}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_notification(title, msg, copy_to_clipboard=False):
    if copy_to_clipboard:
        try:
            pyperclip.copy(msg)
        except Exception:
            msg += "\n\n⚠️ 无法复制到剪贴板"
    try:
        toast = Notification(
            app_id="Discuz AutoSign",
            title=title,
            msg=msg,
            duration="short"
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception as e:
        log(f"通知失败: {e}", level="ERROR")
    log(f"{title}: {msg}", level="INFO")


def load_config(config_path):
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                "site:\n"
                "  url: \"https://example.com\"\n\n"
                "auth:\n"
                "  cookies:\n"
                "    - \"xxx=yyy;mmm=nnn\"\n\n"
                "options:\n"
                "  rotate_accounts: true\n"
                "  timeout: 15\n"
                "  delay_range: [1, 5]  # 签到前的随机延迟范围(秒)\n"
                "  retry_times: 3       # 失败重试次数\n"
                "  retry_interval: 10   # 重试间隔(秒)\n"
            )
        log("未找到 config.yaml，已创建模板，请填写后重试。", level="FATAL")
        raise FileNotFoundError("未找到 config.yaml，已创建模板，请填写后重试。")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    base_url = config.get("site", {}).get("url", "").rstrip("/")
    cookie_list = config.get("auth", {}).get("cookies", [])
    options = config.get("options", {})
    timeout = options.get("timeout", 15)
    delay_range = options.get("delay_range", [0, 0])
    retry_times = options.get("retry_times", 1)
    retry_interval = options.get("retry_interval", 5)

    if not base_url:
        log("site.url 为空，请检查配置", level="FATAL")
        raise ValueError("config.yaml 中 site.url 为空，请填写后重试。")
    if not cookie_list:
        log("auth.cookies 为空，请检查配置", level="FATAL")
        raise ValueError("config.yaml 中 auth.cookies 为空，请填写至少一个 cookie。")

    log(f"配置加载成功: {len(cookie_list)} 个账号，论坛地址 {base_url}", level="INFO")
    return base_url, cookie_list, options, timeout, delay_range, retry_times, retry_interval


def parse_cookie(cookie_str):
    cookies = {}
    for item in cookie_str.split(";"):
        if "=" in item:
            parts = item.strip().split("=", 1)
            if len(parts) == 2:
                cookies[parts[0].strip()] = parts[1].strip()
    return cookies


def fetch_formhash(base_url, cookies, headers, timeout):
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(base_url, headers=headers, cookies=cookies, timeout=timeout)
    html = resp.text
    patterns = [r"formhash=([a-zA-Z0-9]+)", r'name="formhash"\s+value="([a-zA-Z0-9]+)"']
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def fetch_continuous_days(base_url, cookies, headers, timeout):
    scraper = cloudscraper.create_scraper()
    sign_page = f"{base_url}/k_misign-sign.html"
    try:
        resp = scraper.get(sign_page, headers=headers, cookies=cookies, timeout=timeout)
        m = re.search(r'<input type="hidden" class="hidnum" id="lxdays" value="(\d+)">', resp.text)
        return m.group(1) if m else None
    except:
        return None


def sign_account(base_url, cookie_str, timeout):
    cookies = parse_cookie(cookie_str)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Referer": base_url + "/",
        "Origin": base_url,
    }

    formhash = fetch_formhash(base_url, cookies, headers, timeout)
    if not formhash:
        return "失败：未找到 formhash"

    url = f"{base_url}/k_misign-sign.html?operation=qiandao&format=button&formhash={formhash}"
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, headers=headers, cookies=cookies, timeout=timeout)
    text = resp.text.strip()

    if "今日已签" in text:
        msg = "今日已签，明日再来~"
    elif "签到成功" in text or "已签到" in text:
        m = re.search(r"获得随机奖励\s*(.*?)。", text)
        reward = m.group(1) if m else "成功"
        msg = f"签到成功，奖励：{reward}"
    else:
        return f"失败：未知响应 {text[:20]}"

    days = fetch_continuous_days(base_url, cookies, headers, timeout)
    if days:
        msg += f" | 连续: {days}天"
    return msg


def main():
    try:
        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        config_path = os.path.join(current_dir, "config.yaml")
        base_url, cookie_list, options, timeout, delay_range, retry_times, retry_interval = load_config(config_path)
    except Exception as e:
        send_notification("❌ 配置错误", str(e))
        sys.exit()

    results = []
    has_error = False

    for idx, cookie_str in enumerate(cookie_list):
        if delay_range[1] > 0:
            wait_time = random.randint(delay_range[0], delay_range[1])
            log(f"账号 {idx + 1} 随机延迟 {wait_time} 秒...", level="INFO")
            time.sleep(wait_time)

        success = False
        res = ""
        for i in range(retry_times):
            res = sign_account(base_url, cookie_str, timeout)
            if "失败" not in res and "错误" not in res:
                success = True
                break
            else:
                log(f"账号 {idx + 1} 第 {i + 1} 次尝试失败: {res}，{retry_interval}秒后重试...", level="WARN")
                time.sleep(retry_interval)

        results.append(res)
        if not success:
            has_error = True

        if options.get("rotate_accounts", True) and idx < len(cookie_list) - 1:
            time.sleep(2)

    final_msg = "\n".join(f"{idx + 1}. {res}" for idx, res in enumerate(results))
    title = "✅ 签到完成" if not has_error else "⚠️ 签到异常"
    send_notification(title, final_msg)


if __name__ == "__main__":
    main()