import json
import os
import re
import sys
import time
from urllib.parse import urlparse, parse_qs
from pathlib import Path

import locale
sys.stdout.reconfigure(encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'reconfigure') else None

from playwright.sync_api import sync_playwright

OUTPUT_FILE = Path(__file__).parent / "apis.json"
TARGET_URL = "https://data.wanmei.com/csgo"

API_PATTERNS = [
    re.compile(r"\.json"),
    re.compile(r"/api/"),
    re.compile(r"/gateway/"),
    re.compile(r"/v\d/"),
    re.compile(r"graphql"),
    re.compile(r"match"),
    re.compile(r"team"),
    re.compile(r"player"),
    re.compile(r"event"),
    re.compile(r"series"),
    re.compile(r"league"),
    re.compile(r"tournament"),
    re.compile(r"standing"),
    re.compile(r"stat"),
    re.compile(r"schedule"),
    re.compile(r"result"),
]

IGNORED_RESOURCES = ["png", "jpg", "jpeg", "gif", "svg", "ico", "woff", "woff2", "ttf", "eot"]


def should_track(url: str) -> bool:
    parsed = urlparse(url)
    ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
    if ext in IGNORED_RESOURCES:
        return False
    if not parsed.netloc:
        return False
    return any(p.search(url.lower()) for p in API_PATTERNS)


def extract_sample_structure(data, max_depth=3, current_depth=0):
    if current_depth >= max_depth:
        return "..."
    if isinstance(data, dict):
        return {k: extract_sample_structure(v, max_depth, current_depth + 1) for k, v in data.items()}
    elif isinstance(data, list):
        if data:
            return [extract_sample_structure(data[0], max_depth, current_depth + 1)]
        return []
    return type(data).__name__


def discover():
    print(f"[*] 启动浏览器访问 {TARGET_URL}")
    print("[*] 请稍候，正在探测API端点...")

    discovered = {}
    request_data = {}
    request_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def on_request(request):
            if should_track(request.url):
                request_data[request.url] = {
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data if request.method == "POST" else None,
                }

        def on_response(response):
            nonlocal request_count
            url = response.url
            if not should_track(url):
                return
            if response.status >= 400:
                return

            try:
                body = response.json()
            except Exception:
                return

            request_count += 1
            parsed = urlparse(url)
            path = parsed.path
            query = parse_qs(parsed.query)
            query_clean = {k: v[0] if len(v) == 1 else v for k, v in query.items()}

            print(f"  [{request_count}] {response.status} {response.request.method} {path}")

            req_info = request_data.get(url, {})
            headers = req_info.get("headers", dict(response.request.headers))

            entry = {
                "method": response.request.method,
                "url": url,
                "path": path,
                "query": query_clean,
                "headers": dict(headers),
                "sample_response": extract_sample_structure(body),
                "response_keys": list(body.keys()) if isinstance(body, dict) else [],
            }
            if req_info.get("post_data"):
                entry["post_data"] = req_info["post_data"]

            key = f"{response.request.method} {path.split('?')[0]}"
            if key not in discovered:
                discovered[key] = entry
            else:
                existing = discovered[key]
                if isinstance(body, dict):
                    existing_keys = set(existing.get("response_keys", []))
                    new_keys = set(body.keys())
                    if new_keys - existing_keys:
                        existing["response_keys"] = list(existing_keys | new_keys)

        page.on("request", on_request)
        page.on("response", on_response)

        print("[*] 加载首页...")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        time.sleep(3)

        print("[*] 提取JS中的签名密钥...")
        sign_secret = None
        js_bundle_content = ""
        api_domain = f"https://{urlparse(TARGET_URL).netloc}"

        for script in page.query_selector_all("script"):
            try:
                src = script.get_attribute("src")
                if src and ".js" in src:
                    url = src if src.startswith("http") else api_domain + src
                    resp = page.request.get(url)
                    js_bundle_content += resp.text() + "\n"
            except Exception:
                continue

        js_keywords = [
            "signSecret", "secretKey", "appSecret", "hmacSecret",
            "secret", "sign", "hmac", "signature"
        ]
        sign_secret = None
        for kw in js_keywords:
            idx = js_bundle_content.find(kw)
            if idx >= 0:
                snippet = js_bundle_content[max(0, idx - 40):idx + 80]
                m = re.search(r"[\"']([a-zA-Z0-9+/=]{8,})[\"']", snippet)
                if m:
                    candidate = m.group(1)
                    if len(candidate) >= 16:
                        sign_secret = candidate
                        print(f"[*] 发现签名密钥 (from '{kw}'): {candidate[:30]}...")
                        break

        if not sign_secret:
            m = re.search(r"[\"']([a-zA-Z0-9+/=]{24,64})[\"']", js_bundle_content)
            if m:
                candidate = m.group(1)
                if len(candidate) >= 24:
                    sign_secret = candidate
                    print(f"[*] 猜测签名密钥: {candidate[:30]}...")

        if not sign_secret:
            print("[*] 未找到签名密钥，将直接复用浏览器session")

        print("[*] 滚动页面/点击导航发现更多API...")
        nav_items = page.query_selector_all(
            ".nav-list .el-menu-item, .el-tabs__item, .nav-item, [class*='tab']"
        )
        clicked_texts = set()
        for item in nav_items:
            try:
                text = item.inner_text().strip()
                if not text or text in clicked_texts or len(text) > 10:
                    continue
                clicked_texts.add(text)
                print(f"[*] 点击: '{text}'")
                item.click()
                time.sleep(2)
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                continue

        browser.close()

    if not discovered:
        print("[!] 未发现任何API端点。可能页面需要登录或采用nonce/token方式。")
        return

    result = {
        "base": TARGET_URL,
        "api_base": f"https://{urlparse(next(iter(discovered.values()))['url']).netloc}" if discovered else "",
        "sign_secret": sign_secret or "",
        "total_endpoints": len(discovered),
        "endpoints": discovered,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n[✓] 发现 {len(discovered)} 个API端点")
    for key in sorted(discovered.keys()):
        info = discovered[key]
        params = "&".join(f"{k}={v}" for k, v in info["query"].items())
        print(f"    {info['method']:6s} {info['path']}  {params}")
    print(f"\n[✓] API信息已保存至: {OUTPUT_FILE}")
    print("[!] 注意: 仅保存了自动发现的端点。fetcher.py 还使用硬编码的端点路径")
    print("    (getMatchDetail, team/detail, match/fuzzySearch 等), 无需额外操作。")


if __name__ == "__main__":
    discover()
