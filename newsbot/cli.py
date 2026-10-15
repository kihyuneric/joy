"""사용: python -m newsbot [--count 10] [--out news.txt] [--date 2026-10-16]"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .message import format_message
from .naver import KST, NaverNews
from .select import DEFAULT_QUERIES, pick


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="부동산·경매 주요뉴스 → 카카오톡 오픈채팅 메시지")
    p.add_argument("--count", type=int, default=10, help="뉴스 개수 (기본 10)")
    p.add_argument("--auction-slots", type=int, default=4, help="경매 기사 최소 배정 수 (기본 4)")
    p.add_argument("--query", action="append", help="검색어 (여러 번 지정 가능, 기본값 내장)")
    p.add_argument("--out", help="메시지를 저장할 파일 경로 (생략 시 화면 출력만)")
    p.add_argument("--source", choices=["auto", "api", "playwright"], default="auto",
                   help="auto=API 키가 있으면 API, 없으면 Playwright")
    p.add_argument("--headed", action="store_true", help="Playwright 브라우저 창 표시(디버깅)")
    p.add_argument("--date", help="헤더 날짜 덮어쓰기 YYYY-MM-DD (예: 내일 아침용 미리 생성)")
    args = p.parse_args(argv)

    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    source = args.source if args.source != "auto" else ("api" if cid and secret else "playwright")
    if source == "api" and not (cid and secret):
        print("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 필요합니다. (.env.example 참고)",
              file=sys.stderr)
        return 2

    now = datetime.now(KST)
    header_day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=KST) if args.date else now

    queries = args.query or DEFAULT_QUERIES
    if source == "api":
        client = NaverNews(cid, secret)
        articles = [a for q in queries for a in client.search(q)]
    else:
        from .browser import search_all
        articles = search_all(queries, headless=not args.headed)

    chosen = pick(articles, now, args.count, args.auction_slots)
    if not chosen:
        print("조건에 맞는 기사를 찾지 못했습니다.", file=sys.stderr)
        return 1

    text = format_message(chosen, header_day)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
