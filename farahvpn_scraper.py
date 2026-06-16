#!/usr/bin/env python3
"""
FarahVPN Telegram channel scraper with auto GitHub push.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from git import Repo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHANNEL_SLUG = "FarahVPN"
BASE_URL = f"https://t.me/s/{CHANNEL_SLUG}"
DEFAULT_OUTPUT = "farahvpn_subscription.txt"
DEFAULT_PLAIN_OUTPUT = "farahvpn_sub.txt"
DEFAULT_POST_LIMIT = 60
DEFAULT_INTERVAL_HOURS = 2

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

PROTOCOL_PREFIXES = ("vmess://", "vless://", "trojan://", "ss://")
GREEN_PING_MARKERS = ("🟢",)

CONFIG_RE = re.compile(r"(vmess://|vless://|trojan://|ss://)[^\s<\"'`\]]+", re.IGNORECASE)

logger = logging.getLogger("farahvpn_scraper")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ScrapedConfig:
    uri: str
    post_id: int
    posted_at: datetime | None = None
    has_green_ping: bool = False
    ping_ms: int | None = None
    protocol: str = ""
    host: str = ""
    port: int | None = None

    def __post_init__(self):
        self.protocol = self.uri.split("://", 1)[0].lower()
        host, port = extract_host_port(self.uri)
        self.host = host
        self.port = port


@dataclass
class ScrapeResult:
    configs: list[ScrapedConfig] = field(default_factory=list)
    posts_scraped: int = 0
    pages_fetched: int = 0


# ---------------------------------------------------------------------------
# Helper functions (all previous functions)
# ---------------------------------------------------------------------------

def fetch_page(before_id: int | None = None, timeout: int = 60) -> str:
    """Download with retry"""
    url = BASE_URL if before_id is None else f"{BASE_URL}?before={before_id}"
    
    for attempt in range(5):  # 5 بار تلاش
        try:
            print(f"در حال دریافت صفحه... (تلاش {attempt+1})")
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                proxies=None
            )
            response.raise_for_status()
            print("✅ صفحه دریافت شد")
            return response.text
        except Exception as e:
            print(f"❌ تلاش {attempt+1} ناموفق: {e}")
            time.sleep(5 * (attempt + 1))  # backoff
    
    raise Exception("همه تلاش‌ها برای دریافت صفحه ناموفق بود")


def parse_post_id(data_post: str | None) -> int | None:
    if not data_post or "/" not in data_post:
        return None
    try:
        return int(data_post.rsplit("/", 1)[-1])
    except ValueError:
        return None


def message_has_green_ping(message_text_html: str) -> bool:
    return any(marker in message_text_html for marker in GREEN_PING_MARKERS)


def parse_ping_ms(message_text: str) -> int | None:
    match = re.search(r"P.{0,4}:\s*(\d+)\s*ms", message_text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def clean_config_uri(raw: str) -> str | None:
    if not raw:
        return None
    uri = unescape(raw).strip().splitlines()[0].strip()
    uri = re.split(r"[\s<]", uri, maxsplit=1)[0]
    if not uri.lower().startswith(PROTOCOL_PREFIXES):
        return None
    return uri


def extract_config_from_message(message_el) -> str | None:
    code_el = message_el.select_one("code")
    if code_el:
        text = code_el.get_text(separator="", strip=True)
        cleaned = clean_config_uri(text)
        if cleaned:
            return cleaned

    text_block = message_el.select_one(".tgme_widget_message_text")
    if text_block:
        html = unescape(str(text_block))
        match = CONFIG_RE.search(html)
        if match:
            return clean_config_uri(match.group(0))
    return None


def extract_host_port(uri: str) -> tuple[str, int | None]:
    # Simple version
    try:
        if "://" in uri:
            part = uri.split("://", 1)[1].split("/")[0].split("?")[0]
            if "@" in part:
                part = part.split("@")[-1]
            if ":" in part:
                host, port = part.rsplit(":", 1)
                return host.strip("[]"), int(port)
    except:
        pass
    return "", None


def deduplicate_configs(configs: Iterable[ScrapedConfig]) -> list[ScrapedConfig]:
    seen = set()
    unique = []
    for item in configs:
        key = item.uri.strip()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def filter_configs(configs: list[ScrapedConfig], require_green_ping: bool = True) -> list[ScrapedConfig]:
    return [cfg for cfg in configs if not require_green_ping or cfg.has_green_ping]


def scrape_channel(post_limit: int = DEFAULT_POST_LIMIT) -> ScrapeResult:
    result = ScrapeResult()
    before_id = None
    collected = 0

    while collected < post_limit:
        html = fetch_page(before_id)
        result.pages_fetched += 1
        soup = BeautifulSoup(html, "html.parser")
        messages = soup.select("div.tgme_widget_message[data-post]")

        for msg in messages:
            if collected >= post_limit:
                break
            post_id = parse_post_id(msg.get("data-post"))
            if not post_id:
                continue
            collected += 1
            uri = extract_config_from_message(msg)
            if uri:
                cfg = ScrapedConfig(
                    uri=uri,
                    post_id=post_id,
                    has_green_ping=message_has_green_ping(str(msg)),
                    ping_ms=parse_ping_ms(msg.get_text())
                )
                result.configs.append(cfg)
        if not messages:
            break
        # Simple pagination
        before_id = parse_post_id(messages[-1].get("data-post")) if messages else None
        time.sleep(1)

    result.posts_scraped = collected
    return result


def build_plain_subscription(configs):
    return "\n".join(cfg.uri for cfg in configs)


def build_base64_subscription(configs):
    plain = build_plain_subscription(configs)
    return base64.b64encode(plain.encode("utf-8")).decode("ascii")


def write_subscription_files(configs, output_path):
    base64_body = build_base64_subscription(configs)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(base64_body)
    return output_path, None


def git_auto_push():
    try:
        repo = Repo(".")
        repo.index.add(["farahvpn_subscription.txt", "farahvpn_sub.txt"])
        commit_msg = f"Auto update - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        repo.index.commit(commit_msg)
        origin = repo.remote("origin")
        origin.push()
        print("✅ Push موفق به GitHub")
        return True
    except Exception as e:
        print(f"❌ Push ناموفق: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--posts", type=int, default=DEFAULT_POST_LIMIT)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()

    while True:
        try:
            result = scrape_channel(post_limit=args.posts)
            configs = deduplicate_configs(result.configs)
            configs = filter_configs(configs)

            if configs:
                write_subscription_files(configs, DEFAULT_OUTPUT)
                print(f"✅ {len(configs)} کانفیگ ذخیره شد")
                git_auto_push()
            else:
                print("⚠️ کانفیگ پیدا نشد")
        except Exception as e:
            print(f"خطا: {e}")

        if not args.watch:
            break
        print(f"⏳ خواب ۲ ساعته تا آپدیت بعدی...")
        time.sleep(7200)


if __name__ == "__main__":
    main()