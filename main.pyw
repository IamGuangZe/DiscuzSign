import os
import re
import sys
import time
import cloudscraper
import pyperclip
import yaml
from datetime import datetime
from winotify import Notification, audio
import pyperclip

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
            duration="short"  # "short" 或 "long"
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
            )
        log("未找到 config.yaml，已创建模板，请填写后重试。", level="FATAL")
        raise FileNotFoundError("未找到 config.yaml，已创建模板，请填写后重试。")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    base_url = config.get("site", {}).get("url", "").rstrip("/")
    cookie_list = config.get("auth", {}).get("cookies", [])
    options = config.get("options", {})
    timeout = options.get("timeout", 15)

    if not base_url:
        log("site.url 为空，请检查配置", level="FATAL")
        raise ValueError("config.yaml 中 site.url 为空，请填写后重试。")
    if not cookie_list:
        log("auth.cookies 为空，请检查配置", level="FATAL")
        raise ValueError("config.yaml 中 auth.cookies 为空，请填写至少一个 cookie。")

    log(f"配置加载成功: {len(cookie_list)} 个账号，论坛地址 {base_url}", level="INFO")
    return base_url, cookie_list, options, timeout

def parse_cookie(cookie_str):
    cookies = {}
    for item in cookie_str.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            cookies[k.strip()] = v.strip()
    masked = "; ".join(f"{k}=xxx" for k in cookies.keys())
    log(f"解析 cookie: {masked}", level="TRACE")
    return cookies

def fetch_formhash(base_url, cookies, headers, timeout):
    scraper = cloudscraper.create_scraper()
    log(f"访问论坛首页获取 formhash: {base_url}", level="INFO")
    try:
        resp = scraper.get(base_url, headers=headers, cookies=cookies, timeout=timeout)
        log(f"访问论坛首页成功，响应长度: {len(resp.text)}", level="DEBUG")
    except Exception as e:
        log(f"访问论坛首页失败: {e}", level="ERROR")
        raise RuntimeError(f"无法访问论坛首页：{e}")

    html = resp.text
    patterns = [
        r"formhash=([a-zA-Z0-9]+)",
        r'name="formhash"\s+value="([a-zA-Z0-9]+)"'
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            formhash = m.group(1)
            log(f"formhash 获取成功: {formhash}", level="INFO")
            return formhash
    log("未找到 formhash", level="WARN")
    raise ValueError("未找到 formhash，请检查登录状态或网页结构。")

def fetch_continuous_days(base_url, cookies, headers, timeout):
    scraper = cloudscraper.create_scraper()
    sign_page = f"{base_url}/k_misign-sign.html"
    try:
        resp = scraper.get(sign_page, headers=headers, cookies=cookies, timeout=timeout)
        html = resp.text
        m = re.search(r'<input type="hidden" class="hidnum" id="lxdays" value="(\d+)">', html)
        if m:
            days = m.group(1)
            log(f"连续签到天数获取成功: {days}", level="INFO")
            return days
        else:
            log("未找到连续签到天数", level="WARN")
            return None
    except Exception as e:
        log(f"访问签到页失败: {e}", level="ERROR")
        return None

def sign_account(base_url, cookie_str, timeout):
    cookies = parse_cookie(cookie_str)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/114.0.0.0 Safari/537.36",
        "Referer": base_url + "/",
        "Origin": base_url,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
    }

    try:
        formhash = fetch_formhash(base_url, cookies, headers, timeout)
    except Exception as e:
        msg = f"formhash 获取失败: {e}"
        log(msg, level="ERROR")
        return msg

    url = f"{base_url}/k_misign-sign.html?operation=qiandao&format=button&formhash={formhash}"
    log(f"发送签到请求: {url}", level="INFO")
    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get(url, headers=headers, cookies=cookies, timeout=timeout)
        log(f"签到请求成功，响应长度: {len(resp.text)}", level="DEBUG")
    except Exception as e:
        msg = f"请求失败: {e}"
        log(msg, level="ERROR")
        return msg

    text = resp.text.strip()
    if resp.status_code == 200:
        if text.startswith("<?xml") and "今日已签" in text:
            msg = "今日已签，明日再来~"
        elif "签到成功" in text and "已签到" in text:
            m = re.search(r"获得随机奖励\s*(.*?)。", text)
            reward = m.group(1) if m else "未知奖励"
            msg = f"签到成功，奖励：{reward}"
        else:
            msg = f"未知响应: {text[:50]}..."
            log(f"未知签到响应内容: {text[:200]}", level="WARN")
    else:
        msg = f"签到失败，状态码：{resp.status_code}"
        log(msg, level="ERROR")

    # 获取连续签到天数
    days = fetch_continuous_days(base_url, cookies, headers, timeout)
    if days:
        msg += f" | 连续签到: {days} 天"
    else:
        log("未能获取连续签到天数", level="WARN")

    log(msg, level="INFO")
    return msg

def main():
    try:
        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        config_path = os.path.join(current_dir, "config.yaml")
        base_url, cookie_list, options, timeout = load_config(config_path)
    except Exception as e:
        send_notification("❌ 配置错误", str(e))
        sys.exit()

    results = []
    for cookie_str in cookie_list:
        result = sign_account(base_url, cookie_str, timeout)
        results.append(result)
        if options.get("rotate_accounts", True):
            time.sleep(2)

    final_msg = "\n".join(f"{idx+1}. {res}" for idx, res in enumerate(results))
    send_notification("✅ 签到完成", final_msg)

if __name__ == "__main__":
    main()
