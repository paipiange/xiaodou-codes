#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小豆邀请码巡检脚本（不调用 X API）。

本地 ORCA bot 用已登录的 EGO Lite 浏览器在 x.com 搜索、把看到的帖子整理成
JSON，再交给本脚本：筛选、抽码、合并进 codes.json，并用 GitHub Contents API 提交。
提交后 GitHub Actions 会自动部署，网站每 60 秒拉取 codes.json。

凭据只从环境变量读取，不要把 token 写进文件或仓库：
  GITHUB_TOKEN   仅开通 xiaodou-codes 仓库 Contents 读写的 fine-grained PAT
                 --dry-run / --self-test 可不设（dry-run 会改读线上公开的 codes.json）

用法：
  python patrol.py --posts posts.json
  python patrol.py --posts posts.json --dry-run
  python patrol.py --posts -              # 从标准输入读帖子 JSON
  python patrol.py --self-test            # 用线上/本地 codes.json + 模拟推文做离线测试
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BEIJING = timezone(timedelta(hours=8))  # 中国无夏令时，用固定 UTC+8，避免 Windows 缺 tzdata
DEADLINE_BEIJING = datetime(2026, 11, 26, 16, 30, tzinfo=BEIJING)

GITHUB_OWNER = "paipiange"
GITHUB_REPO = "xiaodou-codes"
GITHUB_BRANCH = "main"
GITHUB_PATH = "codes.json"
PUBLIC_CODES_URL = "https://paipiange.github.io/xiaodou-codes/codes.json"
SITE_URL = "https://paipiange.github.io/xiaodou-codes/"

SHA_RETRIES = 3
USER_AGENT = "xiaodou-codes-patrol/1.0 (community handoff script)"

OFFICIAL_HOST_PATH = "activity.doubao.com/ug/invite-share/"
OFFICIAL_CODE_RE = re.compile(
    r"activity\.doubao\.com/ug/invite-share/([A-Za-z0-9-]{4,16})",
    re.IGNORECASE,
)
# 5–8 位大写字母数字，或官方那种「字母-其余」形式（如 M-342WXG）
BARE_CODE_RE = re.compile(r"(?<![A-Z0-9-])([A-Z0-9]{5,8}|[A-Z]-[A-Z0-9]{5,8})(?![A-Z0-9-])")

# 容易从英文单词里误抽到的码，一律丢掉
STOPWORDS = {
    "INVITE", "INVITES", "INVITED", "CODE", "CODES", "XIAODOU", "DOUBAO",
    "HTTPS", "HTTP", "STATUS", "TWITTER", "BEIJING", "PLEASE", "THANKS",
    "THANKYOU", "FOLLOW", "RETWEET", "REPLY", "REPLIES", "GITHUB", "GOOGLE",
    "SEARCH", "SHARE", "SHARED", "SHARING", "AVAILABLE", "EXPIRED", "UPDATE",
    "UPDATED", "COMMENT", "COMMENTS", "WEBSITE", "PAGES", "CLICK", "CHECK",
    "TODAY", "TOMORROW", "OFFICIAL", "ACCOUNT", "MESSAGE", "NUMBER",
    "WINDOWS", "ANDROID", "IPHONE", "APPSTORE", "TESTCODE",
}

# 码前后出现这些产品名就不收（BEGWUM 就是 Muse 码被误收的教训）
OTHER_PRODUCTS = [
    "Muse", "Midjourney", "Claude", "ChatGPT", "Gemini", "Grok", "Sora",
    "Runway", "Pika", "Kling", "Copilot", "Notion", "Devin", "Cursor",
    "即梦", "可灵", "通义", "Kimi", "文心", "混元", "智谱", "百川", "讯飞",
    "Stability", "Suno", "Udio", "Luma", "Vidu",
]

SEEK_PATTERNS = [
    r"求码", r"求个码", r"求一个", r"求邀请", r"跪求", r"求一个邀请",
    r"有没有码", r"有没有邀请码", r"谁有码", r"谁有邀请码", r"有码吗",
    r"求一个小豆", r"有多余的码", r"求个邀请码",
]
SELL_PATTERNS = [
    r"卖码", r"出码", r"收码", r"出售", r"代抢", r"代刷", r"收费",
    r"闲鱼", r"一个\s*\d+\s*元", r"\d+\s*元/?个", r"¥\s*\d+", r"￥\s*\d+",
]
# 汇总/整理帖，以及明确说码被改过、不保证能用的（@Lewis6666688888 那种）
COMPILATION_PATTERNS = [
    r"整理了", r"整理了?一些", r"汇总", r"合集", r"收集了",
    r"随便改", r"随机改", r"码被?改过", r"改过的", r"不保证能用",
    r"不保证可用", r"不保证[能可]用",
]
RELAY_PATTERNS = [r"占楼", r"^接力$", r"^路过$", r"^来了$"]

PRODUCT_WINDOW = 20  # 检查码前后各多少个字符是否写了其他产品名

# ---------------------------------------------------------------------------
# 时间
# ---------------------------------------------------------------------------

def now_beijing() -> datetime:
    return datetime.now(tz=BEIJING)


def format_last_updated(dt: datetime | None = None) -> str:
    """codes.json 的 lastUpdated，例如 2026-11-24 21:04 北京时间。"""
    dt = dt or now_beijing()
    return dt.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M") + " 北京时间"


def format_posted_at(dt: datetime) -> str:
    """发帖时间，带秒，例如 2026-11-24 20:39:00 北京时间。"""
    return dt.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S") + " 北京时间"


def format_posted_at_utc(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_created_at(value: str) -> datetime:
    """解析发帖时间：X 页面上的 UTC ISO（...Z）、带偏移的 ISO，或「YYYY-MM-DD HH:MM:SS 北京时间」。"""
    value = value.strip()
    if "北京时间" in value:
        value = value.replace("北京时间", "").strip()
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # 无时区时按 UTC 理解，避免把北京时间误当成 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# HTTP（只用标准库，Windows / ORCA 不必先装 requests）
# ---------------------------------------------------------------------------

def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, str], bytes]:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, body


def http_json(url: str, **kwargs: Any) -> tuple[int, Any, dict[str, str]]:
    status, headers, raw = http_request(url, **kwargs)
    payload: Any = None
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            payload = raw.decode("utf-8", errors="replace")
    return status, payload, headers


# ---------------------------------------------------------------------------
# 筛选：抽码、排除规则、有效期
# ---------------------------------------------------------------------------

def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def is_seeking_post(text: str) -> bool:
    """求码帖：在要码，而不是在分享码。"""
    return _contains_any(text, SEEK_PATTERNS)


def is_selling_post(text: str) -> bool:
    """卖码 / 代抢 / 闲鱼广告。"""
    return _contains_any(text, SELL_PATTERNS)


def is_compilation_post(text: str) -> bool:
    """汇总整理帖，或声明码被改过、不保证能用。"""
    return _contains_any(text, COMPILATION_PATTERNS)


def is_relay_empty(text: str) -> bool:
    """接力楼空帖：几乎没有正文，或不含码的占楼。"""
    stripped = re.sub(r"\s+", "", text)
    if _contains_any(text, RELAY_PATTERNS) and not BARE_CODE_RE.search(text.upper()) and not OFFICIAL_CODE_RE.search(text):
        return True
    if len(stripped) <= 4 and not OFFICIAL_CODE_RE.search(text):
        return True
    return False


def reject_post_reason(text: str) -> str | None:
    """整帖应直接丢掉时返回原因，否则返回 None。"""
    if is_seeking_post(text):
        return "求码帖"
    if is_selling_post(text):
        return "卖码广告"
    if is_compilation_post(text):
        return "汇总/整理帖或声明码被改过"
    if is_relay_empty(text):
        return "接力楼空帖"
    return None


def _code_near_other_product(text: str, code: str) -> bool:
    """码前后写着其他产品名则不收。官方 invite-share 链接抽出的码不走这条。"""
    upper = text.upper()
    code_u = code.upper()
    start = 0
    while True:
        idx = upper.find(code_u, start)
        if idx < 0:
            return False
        lo = max(0, idx - PRODUCT_WINDOW)
        hi = min(len(text), idx + len(code) + PRODUCT_WINDOW)
        window = text[lo:hi]
        for name in OTHER_PRODUCTS:
            if name.lower() in window.lower():
                return True
        start = idx + 1


def extract_codes(text: str, urls: list[str] | None = None) -> list[dict[str, str]]:
    """从正文和展开后的链接里抽小豆邀请码。

    返回 [{"code": "...", "officialUrl": "..."}]，officialUrl 可缺省。
    同一 code 只保留一次；官方链接里的码优先。
    """
    urls = urls or []
    blob = text + "\n" + "\n".join(urls)
    found: dict[str, dict[str, str]] = {}

    # 1) 官方邀请页里的码，一定是小豆的
    for match in OFFICIAL_CODE_RE.finditer(blob):
        code = match.group(1).upper()
        official = "https://activity.doubao.com/ug/invite-share/" + code
        found[code] = {"code": code, "officialUrl": official}

    # 2) 正文里的裸码
    upper_text = text.upper()
    for match in BARE_CODE_RE.finditer(upper_text):
        code = match.group(1).upper()
        if code in STOPWORDS:
            continue
        if re.fullmatch(r"\d+", code.replace("-", "")):
            continue  # 纯数字不要
        if code in found:
            continue
        if _code_near_other_product(text, code):
            continue
        found[code] = {"code": code}

    return list(found.values())


def parse_expires(text: str, near_code: str | None = None) -> str | None:
    """从帖子里解析有效期，写成 YYYY-MM-DD HH:MM（北京时间，不加后缀）。"""
    sources = [text]
    if near_code:
        idx = text.upper().find(near_code.upper())
        if idx >= 0:
            sources.insert(0, text[max(0, idx - 40): idx + len(near_code) + 80])

    patterns = [
        r"(20\d{2})[-年/\.](\d{1,2})[-月/\.](\d{1,2})日?\s*(\d{1,2})[:：](\d{2})",
        r"(?:有效期?\s*[至到截止为是]?\s*)?(\d{1,2})[月\./](\d{1,2})日?\s*(\d{1,2})[:：](\d{2})",
    ]
    year_now = now_beijing().year
    for src in sources:
        m = re.search(patterns[0], src)
        if m:
            year, month, day, hour, minute = (int(x) for x in m.groups())
            return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        m = re.search(patterns[1], src)
        if m:
            month, day, hour, minute = (int(x) for x in m.groups())
            return f"{year_now:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
    return None


# ---------------------------------------------------------------------------
# 把 bot 交来的帖子 JSON 收成内部结构
# ---------------------------------------------------------------------------

def collect_urls_from_payload(post: dict[str, Any]) -> list[str]:
    """接受 url（单个）、urls（列表），以及 X 页面里已展开的 t.co。"""
    urls: list[str] = []
    raw = post.get("urls")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        urls.extend(str(item) for item in raw if item)
    for key in ("url", "expanded_url", "unwound_url", "officialUrl"):
        value = post.get(key)
        if value and str(value) not in urls:
            # url 若是帖子自身链接（x.com/.../status/...）不当作邀请链接，但留着也无害
            urls.append(str(value))
    for item in (post.get("entities") or {}).get("urls") or []:
        if not isinstance(item, dict):
            continue
        for key in ("unwound_url", "expanded_url", "url"):
            value = item.get(key)
            if value and str(value) not in urls:
                urls.append(str(value))
    return urls


def is_retweet(post: dict[str, Any]) -> bool:
    if post.get("is_retweet") or post.get("isRetweet"):
        return True
    for ref in post.get("referenced_tweets") or []:
        if isinstance(ref, dict) and ref.get("type") == "retweeted":
            return True
    text = post.get("text") or ""
    return text.startswith("RT @")


def normalize_post(post: dict[str, Any]) -> dict[str, Any]:
    """把 bot 抓到的帖子字段统一成内部结构。

    最低限度需要：id、author、text、created_at、reply_count。
    url / urls 可选，用于放下展开后的 t.co / 官方邀请链接。
    """
    post_id = str(post.get("id") or post.get("postId") or "").strip()
    if not post_id:
        raise ValueError("帖子缺少 id")
    author = str(post.get("author") or post.get("username") or "").lstrip("@").strip()
    if not author:
        author = "unknown"
    text = str(post.get("text") or post.get("content") or "")
    created_raw = post.get("created_at") or post.get("createdAt") or post.get("postedAtUtc")
    if not created_raw:
        raise ValueError(f"帖子 {post_id} 缺少 created_at")
    created = parse_created_at(str(created_raw))
    reply_count = post.get("reply_count")
    if reply_count is None:
        reply_count = post.get("replyCount", 0)
    return {
        "id": post_id,
        "text": text,
        "author": author,
        "created_at": created,
        "reply_count": int(reply_count or 0),
        "urls": collect_urls_from_payload(post),
        "is_retweet": is_retweet(post),
    }


# 兼容自测里对旧函数名的调用
def normalize_tweet(
    tweet: dict[str, Any],
    users_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = dict(tweet)
    if "author" not in payload and users_by_id:
        user = users_by_id.get(str(payload.get("author_id") or "")) or {}
        if user.get("username"):
            payload["author"] = user["username"]
    if "reply_count" not in payload and "replyCount" not in payload:
        metrics = payload.get("public_metrics") or {}
        if "reply_count" in metrics:
            payload["reply_count"] = metrics["reply_count"]
    return normalize_post(payload)


def load_posts(source: str) -> list[dict[str, Any]]:
    """从文件或 stdin 读取 bot 整理好的帖子 JSON，返回已规范化的列表。

    接受：
    - 数组：[ {...}, {...} ]
    - 对象：{ "posts": [ ... ] }
    """
    if source == "-":
        raw_text = sys.stdin.read()
    else:
        with open(source, "r", encoding="utf-8") as fh:
            raw_text = fh.read()
    payload = json.loads(raw_text)
    if isinstance(payload, dict) and "posts" in payload:
        items = payload["posts"]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("帖子 JSON 必须是数组，或带 posts 数组的对象")
    if not isinstance(items, list):
        raise ValueError("posts 不是数组")
    return [normalize_post(item) for item in items]


def build_entry(tweet: dict[str, Any], extracted: dict[str, str]) -> dict[str, Any]:
    created = tweet["created_at"]
    entry: dict[str, Any] = {
        "code": extracted["code"],
        "postId": str(tweet["id"]),
        "author": tweet["author"],
        "postUrl": f"https://x.com/{tweet['author']}/status/{tweet['id']}",
        "postedAt": format_posted_at(created),
        "postedAtUtc": format_posted_at_utc(created),
        "replyCount": int(tweet["reply_count"]),
        "status": "maybe_available" if int(tweet["reply_count"]) == 0 else "maybe_used",
    }
    if extracted.get("officialUrl"):
        entry["officialUrl"] = extracted["officialUrl"]
        entry["note"] = "官方邀请页 " + extracted["officialUrl"]
    expires = parse_expires(tweet["text"], extracted["code"])
    if expires:
        entry["expires"] = expires
    return entry


def screen_tweet(tweet: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """返回 (新条目列表, 拒绝原因)。转推、整帖排除、抽不出码时条目为空。"""
    if tweet.get("is_retweet"):
        return [], "转推"
    reason = reject_post_reason(tweet["text"])
    if reason:
        return [], reason
    extracted = extract_codes(tweet["text"], tweet.get("urls") or [])
    if not extracted:
        return [], "未抽到邀请码"
    return [build_entry(tweet, item) for item in extracted], None


# ---------------------------------------------------------------------------
# 合并进 codes.json
# ---------------------------------------------------------------------------

def max_since_id(*values: str | None) -> str | None:
    best: int | None = None
    for value in values:
        if not value:
            continue
        try:
            number = int(str(value))
        except ValueError:
            continue
        if best is None or number > best:
            best = number
    return str(best) if best is not None else None


def merge_codes(
    data: dict[str, Any],
    new_entries: list[dict[str, Any]],
    reply_updates: dict[str, int] | None = None,
    seen_since_ids: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """把新码插到数组最前面，刷新已有帖子的回复数，更新时间戳。

    返回 (新数据, 本次新增且原帖 0 回复的码, 是否有任何变化)。
    同一 code 不重复插入；confirmed_used 不会因为回复数被降级或改回 maybe_available。
    """
    now = now or now_beijing()
    codes = [dict(item) for item in data.get("codes") or []]
    by_code = {str(item.get("code", "")).upper(): item for item in codes}
    changed = False
    fresh_zero: list[dict[str, Any]] = []

    reply_updates = reply_updates or {}
    post_to_replies = {str(pid): int(n) for pid, n in reply_updates.items()}
    for item in codes:
        post_id = str(item.get("postId") or "")
        if post_id not in post_to_replies:
            continue
        new_count = post_to_replies[post_id]
        if int(item.get("replyCount") or 0) != new_count:
            item["replyCount"] = new_count
            changed = True
        if new_count >= 1 and item.get("status") == "maybe_available":
            item["status"] = "maybe_used"
            changed = True

    def entry_time(entry: dict[str, Any]) -> str:
        return str(entry.get("postedAtUtc") or "")

    ordered = sorted(new_entries, key=entry_time, reverse=True)
    prepend: list[dict[str, Any]] = []
    for entry in ordered:
        key = str(entry["code"]).upper()
        if key in by_code:
            continue
        prepend.append(entry)
        by_code[key] = entry
        changed = True
        if int(entry.get("replyCount") or 0) == 0 and entry.get("status") == "maybe_available":
            fresh_zero.append(entry)

    if prepend:
        codes = prepend + codes

    new_data = {
        "lastUpdated": data.get("lastUpdated"),
        "lastSinceId": data.get("lastSinceId"),
        "codes": codes,
    }
    if changed:
        new_data["lastUpdated"] = format_last_updated(now)
        new_since = max_since_id(
            data.get("lastSinceId"),
            *(seen_since_ids or []),
            *(entry.get("postId") for entry in prepend),
        )
        if new_since:
            new_data["lastSinceId"] = new_since
    return new_data, fresh_zero, changed


def process_posts(
    data: dict[str, Any],
    posts: list[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """纯逻辑入口：已规范化的帖子列表 + 现有 codes.json → 合并结果。

    - 只把当时 reply_count 为 0 的分享帖收为新码；
    - 输入里若带上已有码对应的原帖，则用其 reply_count 刷新状态；
    - lastSinceId 视为「上次处理到的帖子 ID」，有变化提交时再向前推。
    """
    since_id = data.get("lastSinceId")
    try:
        since_num = int(str(since_id)) if since_id else 0
    except ValueError:
        since_num = 0

    new_entries: list[dict[str, Any]] = []
    reply_updates: dict[str, int] = {}
    seen_ids: list[str] = []

    existing_post_ids = {
        str(item.get("postId")) for item in (data.get("codes") or []) if item.get("postId")
    }

    for post in posts:
        post_id = str(post["id"])
        seen_ids.append(post_id)
        if post_id in existing_post_ids:
            reply_updates[post_id] = int(post["reply_count"])
        # 新搜索结果：只看 id 大于上次处理到的帖子（雪花 ID 随时间增大）
        try:
            newer = int(post_id) > since_num
        except ValueError:
            newer = True
        if not newer:
            continue
        entries, _reason = screen_tweet(post)
        for entry in entries:
            if int(entry.get("replyCount") or 0) == 0:
                new_entries.append(entry)

    return merge_codes(
        data,
        new_entries,
        reply_updates=reply_updates,
        seen_since_ids=seen_ids,
        now=now,
    )


# ---------------------------------------------------------------------------
# GitHub Contents API
# ---------------------------------------------------------------------------

def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def contents_url() -> str:
    return (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/"
        f"{urllib.parse.quote(GITHUB_PATH)}"
    )


def load_codes_public() -> dict[str, Any]:
    status, payload, _ = http_json(PUBLIC_CODES_URL)
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"读取线上 codes.json 失败 HTTP {status}")
    return payload


def github_get_codes(token: str) -> tuple[dict[str, Any], str]:
    """返回 (codes.json 对象, 当前 blob sha)。sha 提交时必须带上。"""
    url = contents_url() + "?" + urllib.parse.urlencode({"ref": GITHUB_BRANCH})
    status, payload, _ = http_json(url, headers=github_headers(token))
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"读取 GitHub codes.json 失败 HTTP {status}: {payload}")
    sha = payload.get("sha")
    encoded = payload.get("content")
    if not sha or not encoded:
        raise RuntimeError("GitHub 返回的 codes.json 缺少 sha 或 content")
    raw = base64.b64decode(encoded.replace("\n", ""))
    data = json.loads(raw.decode("utf-8"))
    return data, sha


def github_put_codes(token: str, data: dict[str, Any], sha: str, message: str) -> dict[str, Any]:
    body = {
        "message": message,
        "content": base64.b64encode(
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii"),
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    raw = json.dumps(body).encode("utf-8")
    status, payload, _ = http_json(
        contents_url(),
        method="PUT",
        headers={**github_headers(token), "Content-Type": "application/json"},
        data=raw,
    )
    return {"status": status, "payload": payload}


def update_codes_with_retry(token: str, data: dict[str, Any], message: str) -> None:
    """提交 codes.json；遇到 sha 冲突（HTTP 409）就重新读取再合并重试。"""
    last_error = None
    current = data
    for attempt in range(1, SHA_RETRIES + 1):
        remote, sha = github_get_codes(token)
        if attempt > 1:
            ours = {item["code"]: item for item in current.get("codes") or []}
            theirs = {item["code"]: item for item in remote.get("codes") or []}
            prepend = [item for code, item in ours.items() if code not in theirs]
            reply_updates = {}
            for item in current.get("codes") or []:
                reply_updates[str(item.get("postId"))] = int(item.get("replyCount") or 0)
            current, _, changed = merge_codes(
                remote,
                prepend,
                reply_updates=reply_updates,
                seen_since_ids=[current.get("lastSinceId"), remote.get("lastSinceId")],
            )
            if not changed:
                print("远端已包含本次变更，无需再提交")
                return
        result = github_put_codes(token, current, sha, message)
        status = result["status"]
        if status in (200, 201):
            print(f"已提交 codes.json（attempt {attempt}），站点稍后会自动部署")
            return
        if status == 409:
            last_error = result["payload"]
            print(f"sha 冲突（HTTP 409），第 {attempt} 次重试……", file=sys.stderr)
            time.sleep(1)
            continue
        raise RuntimeError(f"提交 codes.json 失败 HTTP {status}: {result['payload']}")
    raise RuntimeError(f"提交 codes.json 多次 sha 冲突仍失败: {last_error}")


# ---------------------------------------------------------------------------
# 通知（打印到控制台，交给 ORCA / 任务计划程序的日志）
# ---------------------------------------------------------------------------

def print_new_codes(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    print(f"发现 {len(entries)} 个新的 0 回复邀请码：")
    for item in entries:
        print(f"- {item['code']}")
        print(f"  帖子：{item['postUrl']}")
        print(f"  发帖：{item['postedAt']}")
        if item.get("expires"):
            print(f"  有效期至：{item['expires']} 北京时间")


def configure_stdio() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_patrol(posts_source: str, dry_run: bool = False) -> int:
    configure_stdio()
    now = now_beijing()
    print(f"巡检开始：{format_last_updated(now)}")
    if now >= DEADLINE_BEIJING:
        print(
            "提示：原定巡检截止时间是 2026-11-26 16:30 北京时间，现已过截止。"
            "脚本仍会照常跑，是否继续由你决定。",
            file=sys.stderr,
        )

    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not dry_run and not gh_token:
        print("缺少环境变量 GITHUB_TOKEN（--dry-run 可不设）", file=sys.stderr)
        return 1

    posts = load_posts(posts_source)
    print(f"读到 {len(posts)} 条浏览器抓来的帖子")

    if gh_token:
        data, _sha = github_get_codes(gh_token)
    else:
        data = load_codes_public()

    print(
        f"当前 lastSinceId={data.get('lastSinceId')}  "
        f"lastUpdated={data.get('lastUpdated')}  "
        f"已有 {len(data.get('codes') or [])} 个码"
    )

    new_data, fresh_zero, changed = process_posts(data, posts, now=now)
    print_new_codes(fresh_zero)
    if not changed:
        print("没有需要写入的变化，不提交。")
        return 0

    added = len(new_data["codes"]) - len(data.get("codes") or [])
    print(
        f"将写入：新增 {added} 个码，"
        f"lastUpdated={new_data['lastUpdated']}，"
        f"lastSinceId={new_data['lastSinceId']}"
    )
    if dry_run:
        print("（--dry-run：已跳过提交）")
        return 0

    message = f"chore: 巡检更新 codes.json（{new_data['lastUpdated']}）"
    update_codes_with_retry(gh_token, new_data, message)
    return 0


# ---------------------------------------------------------------------------
# 离线单元测试（不访问 X，也不提交 GitHub）
# ---------------------------------------------------------------------------

def _mock(id: str, text: str, author: str, created: str, replies: int = 0, urls: list[str] | None = None) -> dict[str, Any]:
    return normalize_post(
        {
            "id": id,
            "text": text,
            "author": author,
            "created_at": created,
            "reply_count": replies,
            "urls": urls or [],
        }
    )


def run_self_test(codes_path: str | None = None) -> int:
    """用当前线上（或本地）codes.json 加几条模拟推文验证筛选和合并逻辑。"""
    configure_stdio()
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  [ok] {name}")
        else:
            msg = f"  [FAIL] {name}" + (f"：{detail}" if detail else "")
            print(msg)
            failures.append(name)

    print("加载 codes.json 做离线测试……")
    if codes_path:
        with open(codes_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        print(f"  来源：本地文件 {codes_path}")
    else:
        data = load_codes_public()
        print(f"  来源：{PUBLIC_CODES_URL}")
    check("codes.json 含 lastSinceId 和 codes 数组", isinstance(data.get("lastSinceId"), str) and isinstance(data.get("codes"), list))
    check("lastSinceId 是数字字符串（避免 JS 精度问题）", str(data.get("lastSinceId", "")).isdigit())
    existing_codes = {item["code"] for item in data["codes"]}
    check("线上已有样本码 L4TT9C / KFCV50 / M-342WXG", {"L4TT9C", "KFCV50", "M-342WXG"} <= existing_codes, str(sorted(existing_codes)[:8]))

    share, reason = screen_tweet(_mock(
        "9000000000000000001",
        "分享一个小豆邀请码 ABC12D，有效期至11月26日 19:30，先到先得",
        "good_share",
        "2026-11-24T13:00:00.000Z",
        0,
    ))
    check("正例：普通分享帖被收录且 reply 为 0", len(share) == 1 and share[0]["code"] == "ABC12D" and share[0]["status"] == "maybe_available", reason or str(share))
    check("正例：解析有效期为 2026-11-26 19:30", bool(share) and share[0].get("expires") == "2026-11-26 19:30", str(share[0].get("expires") if share else None))

    official, reason = screen_tweet(_mock(
        "9000000000000000002",
        "豆包小豆邀请走这里",
        "official_share",
        "2026-11-24T13:01:00.000Z",
        0,
        urls=["https://t.co/abc", "https://activity.doubao.com/ug/invite-share/Z-9TEST1"],
    ))
    check(
        "正例：官方 invite-share 链接抽出 Z-9TEST1",
        len(official) == 1 and official[0]["code"] == "Z-9TEST1" and official[0].get("officialUrl", "").endswith("Z-9TEST1"),
        reason or str(official),
    )

    muse, reason = screen_tweet(_mock(
        "9000000000000000003",
        "Muse 邀请码 BEGWUM 有人要的话自取",
        "muse_fan",
        "2026-11-24T10:00:00.000Z",
        0,
    ))
    check("反例：BEGWUM 是 Muse 码，应整条丢弃或抽不出小豆码", len(muse) == 0, f"entries={muse} reason={reason}")

    compilation, reason = screen_tweet(_mock(
        "9000000000000000004",
        "整理了一些小豆邀请码：AAAAA1 BBBBB2，这些码是随便改过的，不保证能用",
        "Lewis6666688888",
        "2026-11-24T12:40:00.000Z",
        0,
    ))
    check("反例：@Lewis6666688888 类整理帖（码被改过、不保证能用）应拒绝", len(compilation) == 0 and reason is not None, f"entries={compilation} reason={reason}")

    seeking, reason = screen_tweet(_mock(
        "9000000000000000005",
        "求一个小豆邀请码，有没有多余的码啊",
        "asker",
        "2026-11-24T12:41:00.000Z",
        0,
    ))
    check("反例：求码帖应拒绝", len(seeking) == 0, f"entries={seeking} reason={reason}")

    selling, reason = screen_tweet(_mock(
        "9000000000000000006",
        "出小豆邀请码，闲鱼私我，一个30元",
        "seller",
        "2026-11-24T12:42:00.000Z",
        0,
    ))
    check("反例：卖码/闲鱼广告应拒绝", len(selling) == 0, f"entries={selling} reason={reason}")

    retweet, reason = screen_tweet(_mock(
        "9000000000000000007",
        "RT @good_share: 分享一个小豆邀请码 ABC12D",
        "retweeter",
        "2026-11-24T12:43:00.000Z",
        0,
    ))
    rt2 = normalize_post({
        "id": "9000000000000000008",
        "text": "分享一个小豆邀请码 ABC12D",
        "author": "retweeter",
        "created_at": "2026-11-24T12:43:01.000Z",
        "reply_count": 0,
        "is_retweet": True,
    })
    rt2_entries, rt2_reason = screen_tweet(rt2)
    check("反例：转推应拒绝", len(retweet) == 0 and len(rt2_entries) == 0, f"{reason}/{rt2_reason}")

    baseline = json.loads(json.dumps(data))
    kfc = next((c for c in baseline["codes"] if c["code"] == "KFCV50"), None)
    check("夹具：线上仍有 KFCV50，用于回复数刷新测试", kfc is not None)
    reply_updates = {}
    if kfc:
        reply_updates[str(kfc["postId"])] = 2

    merged, fresh, changed = merge_codes(
        baseline,
        share + official,
        reply_updates=reply_updates,
        seen_since_ids=["9000000000000000002"],
        now=datetime(2026, 11, 24, 21, 20, tzinfo=BEIJING),
    )
    check("有变化才标记 changed", changed is True)
    check("新码插到数组最前面（更新的在更前面）", merged["codes"][0]["code"] == "Z-9TEST1" and merged["codes"][1]["code"] == "ABC12D", merged["codes"][0]["code"])
    check("同一 code 不会重复", sum(1 for c in merged["codes"] if c["code"] == "ABC12D") == 1)
    kfc2 = next((c for c in merged["codes"] if c["code"] == "KFCV50"), None)
    check("已有帖子回复数≥1 则 status=maybe_used", bool(kfc2) and kfc2["replyCount"] == 2 and kfc2["status"] == "maybe_used")
    confirmed = next((c for c in merged["codes"] if c["code"] == "L4TT9C"), None)
    check("confirmed_used 不会被回复数改回", bool(confirmed) and confirmed["status"] == "confirmed_used")
    check("lastUpdated 写成北京时间", merged["lastUpdated"] == "2026-11-24 21:20 北京时间")
    check("lastSinceId 更新为更大的雪花 ID 且仍是字符串", merged["lastSinceId"] == "9000000000000000002" and isinstance(merged["lastSinceId"], str))
    check("通知列表只含本次新增的 0 回复码", {c["code"] for c in fresh} == {"ABC12D", "Z-9TEST1"})

    merged2, fresh2, changed2 = merge_codes(merged, share + official, now=datetime(2026, 11, 24, 21, 21, tzinfo=BEIJING))
    check("重复提交同一批新码时不再插入", not changed2 and fresh2 == [] and len(merged2["codes"]) == len(merged["codes"]))

    unchanged, _, flag = merge_codes(data, [], reply_updates={}, seen_since_ids=["1"])
    check("完全没有变化时不改 lastUpdated / 不标记 changed", flag is False and unchanged["lastUpdated"] == data["lastUpdated"])

    # ---- 浏览器帖子 JSON → process_posts 整条链路 ----
    bot_posts = [
        {
            "id": "9000000000000000101",
            "author": "good_share",
            "text": "分享一个小豆邀请码 ZZ11AA，有效期至11月26日 20:00",
            "created_at": "2026-11-24T13:10:00.000Z",
            "reply_count": 0,
            "url": "https://x.com/good_share/status/9000000000000000101",
        },
        {
            "id": "9000000000000000003",
            "author": "muse_fan",
            "text": "Muse 邀请码 BEGWUM 有人要的话自取",
            "created_at": "2026-11-24T10:00:00.000Z",
            "reply_count": 0,
        },
        {
            "id": str(kfc["postId"]) if kfc else "0",
            "author": kfc["author"] if kfc else "huzhouagi",
            "text": "小豆邀请码 KFCV50",
            "created_at": kfc["postedAtUtc"] if kfc else "2026-11-24T08:17:30.000Z",
            "reply_count": 3,
        },
    ]
    # 把 bot JSON 先写成临时结构再经 normalize，模拟 --posts 文件
    normalized = [normalize_post(item) for item in bot_posts]
    piped, piped_fresh, piped_changed = process_posts(
        json.loads(json.dumps(data)),
        normalized,
        now=datetime(2026, 11, 24, 21, 30, tzinfo=BEIJING),
    )
    check("process_posts 收录浏览器来的新 0 回复码 ZZ11AA", any(c["code"] == "ZZ11AA" and c["expires"] == "2026-11-26 20:00" for c in piped["codes"]))
    check("process_posts 不收录 Muse 码 BEGWUM", all(c["code"] != "BEGWUM" for c in piped["codes"]))
    kfc3 = next((c for c in piped["codes"] if c["code"] == "KFCV50"), None)
    check("process_posts 用回访的原帖刷新回复数", bool(kfc3) and kfc3["replyCount"] == 3 and kfc3["status"] == "maybe_used")
    check("process_posts 通知里只有新的 0 回复码", {c["code"] for c in piped_fresh} == {"ZZ11AA"} and piped_changed)

    # 旧于 lastSinceId 的分享帖不应再当新码插入（但回访刷新仍有效，已在上一条覆盖）
    old_share = normalize_post({
        "id": "1",  # 远小于当前 lastSinceId
        "author": "old_user",
        "text": "分享一个小豆邀请码 OLDCOD1",
        "created_at": "2026-11-24T01:00:00.000Z",
        "reply_count": 0,
    })
    skipped, skipped_fresh, skipped_changed = process_posts(
        json.loads(json.dumps(data)),
        [old_share],
        now=datetime(2026, 11, 24, 21, 31, tzinfo=BEIJING),
    )
    check("id 小于 lastSinceId 的帖不当新码插入", all(c["code"] != "OLDCOD1" for c in skipped["codes"]) and not skipped_changed and skipped_fresh == [])

    if failures:
        print(f"\n自测失败 {len(failures)} 项：{', '.join(failures)}")
        return 2
    print("\n自测全部通过。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="小豆邀请码巡检：读入浏览器抓到的帖子 JSON，筛选后更新 codes.json"
    )
    parser.add_argument(
        "--posts",
        help="bot 整理好的帖子 JSON 文件路径，或 - 表示从标准输入读取",
    )
    parser.add_argument("--dry-run", action="store_true", help="筛选并打印结果，不向 GitHub 提交")
    parser.add_argument("--self-test", action="store_true", help="离线单元测试，不提交")
    parser.add_argument("--codes-json", help="自测时使用的本地 codes.json 路径；缺省则拉取线上文件")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test(args.codes_json)
    if not args.posts:
        parser.error("请提供 --posts 文件（或 --self-test）")
    return run_patrol(args.posts, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
