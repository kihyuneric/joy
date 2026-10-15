"""네이버 검색 API(뉴스) 클라이언트.

발급: https://developers.naver.com/apps → 애플리케이션 등록 → '검색' API 선택
환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 로 전달한다.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

API_URL = "https://openapi.naver.com/v1/search/news.json"
KST = timezone(timedelta(hours=9))
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Article:
    title: str
    url: str          # n.news.naver.com 링크
    published: datetime
    description: str
    query: str


def clean(text: str) -> str:
    return html.unescape(_TAG.sub("", text)).strip()


def parse_item(item: dict, query: str) -> Article | None:
    """네이버에 호스팅된 기사(n.news.naver.com)만 채택한다."""
    url = item.get("link", "")
    if "n.news.naver.com" not in url:
        return None
    try:
        published = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
    except (KeyError, TypeError, ValueError):
        return None
    return Article(clean(item.get("title", "")), url, published,
                   clean(item.get("description", "")), query)


class NaverNews:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        })

    def search(self, query: str, display: int = 100, pages: int = 1) -> list[Article]:
        out: list[Article] = []
        for page in range(pages):
            resp = self.session.get(API_URL, timeout=10, params={
                "query": query, "display": display,
                "start": 1 + page * display, "sort": "date",
            })
            if resp.status_code in (401, 403):
                raise RuntimeError("네이버 API 인증 실패: NAVER_CLIENT_ID/SECRET 을 확인하세요.")
            resp.raise_for_status()
            items = resp.json().get("items", [])
            out += [a for it in items if (a := parse_item(it, query))]
            if len(items) < display:
                break
            time.sleep(0.15)
        return out
