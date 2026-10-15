"""카카오톡 오픈채팅용 메시지 포맷."""

from __future__ import annotations

from datetime import datetime

from .naver import KST, Article

WEEKDAYS = "월화수목금토일"


def header(now: datetime) -> str:
    now = now.astimezone(KST)
    return f"{now:%y}년 {now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]}요일 부동산 주요뉴스"


def format_message(articles: list[Article], now: datetime) -> str:
    blocks = [header(now)]
    blocks += [f"{a.title}\n{a.url}" for a in articles]
    return "\n\n".join(blocks) + "\n"
