"""기사 점수화·중복 제거·상위 N개 선정 (경매 기사 우선)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .naver import KST, Article

# (키워드, 가중치) — 제목 기준. 경매 관련을 가장 높게.
AUCTION_TERMS = {
    "경매": 6, "강제경매": 2, "낙찰": 5, "낙찰가율": 2, "공매": 4, "NPL": 4,
    "부실채권": 3, "임의경매": 3, "법원": 1,
}
NEWS_TERMS = {
    "부동산": 2, "집값": 3, "아파트": 2, "재건축": 2, "재개발": 2, "청약": 2,
    "전세": 2, "월세": 1, "매매": 2, "대출": 2, "규제": 2, "대책": 2, "분양": 1,
    "거래량": 2, "국토교통부": 1, "금리": 1, "공급": 1, "세금": 1, "종부세": 2,
}
# 정보가치가 낮은 말머리는 감점(제외하지는 않음)
NOISE = ("[기고]", "[사설]", "[포토]", "[부고]", "[인사]", "[오늘의 주요일정]", "[게시판]")

DEFAULT_QUERIES = ["부동산 경매", "법원경매", "경매 낙찰가율", "부동산", "아파트 집값", "부동산 대책"]


def is_auction(a: Article) -> bool:
    return any(t in a.title for t in ("경매", "낙찰", "공매", "NPL", "부실채권"))


def score(a: Article) -> int:
    s = 0
    for table in (AUCTION_TERMS, NEWS_TERMS):
        s += sum(w for t, w in table.items() if t in a.title)
    # 본문 요약에만 나오는 경매는 약한 가산
    s += sum(1 for t in ("경매", "낙찰") if t in a.description and t not in a.title)
    if any(n in a.title for n in NOISE):
        s -= 6
    return s


def _tokens(title: str) -> set[str]:
    return set(re.findall(r"[0-9A-Za-z가-힣]{2,}", title))


def _similar(a: str, b: str, threshold: float = 0.55) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def dedupe(articles: list[Article]) -> list[Article]:
    seen_urls: set[str] = set()
    kept: list[Article] = []
    for a in articles:
        if a.url in seen_urls or any(_similar(a.title, k.title) for k in kept):
            continue
        seen_urls.add(a.url)
        kept.append(a)
    return kept


def pick(articles: list[Article], now: datetime, count: int = 10, auction_slots: int = 4,
         fallback_hours: int = 36) -> list[Article]:
    """오늘(KST) 기사를 우선, 부족하면 최근 fallback_hours 시간까지 확장."""
    now = now.astimezone(KST)
    today = now.date()
    pool = [a for a in articles if score(a) > 0]
    fresh = [a for a in pool if a.published.date() == today]
    older = [a for a in pool if a.published.date() != today
             and now - a.published <= timedelta(hours=fallback_hours)]

    def rank(items: list[Article]) -> list[Article]:
        return dedupe(sorted(items, key=lambda a: (score(a), a.published), reverse=True))

    ranked = rank(fresh)
    if len(ranked) < count:
        ranked = rank(fresh + older)

    chosen = [a for a in ranked if is_auction(a)][:auction_slots]
    for a in ranked:
        if len(chosen) >= count:
            break
        if a not in chosen:
            chosen.append(a)
    return sorted(chosen, key=lambda a: (score(a), a.published), reverse=True)[:count]
