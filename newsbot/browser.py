"""Playwright로 네이버 뉴스 검색 결과 페이지를 읽는 키 없는 수집기.

API 키가 없을 때의 대안. 검색 화면 HTML 구조에 의존하므로 네이버가 화면을 바꾸면
깨질 수 있다(그럴 땐 JS_EXTRACT만 고치면 된다). 최신순(sort=1) + 1일(pd=4) 필터를 사용.

설치: pip install playwright && playwright install chromium
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from .naver import KST, Article, clean

SEARCH_URL = "https://search.naver.com/search.naver?where=news&sort=1&pd=4&query={q}"

# n.news.naver.com 링크마다, 조상 요소를 올라가며 처음 나오는 '긴 텍스트 링크'를 제목으로 사용
JS_EXTRACT = """
() => {
  const out = new Map();
  for (const a of document.querySelectorAll('a[href*="n.news.naver.com/mnews/article"]')) {
    const url = a.href.split('?')[0];
    let title = (a.innerText || '').trim();
    let node = a;
    while (title.length < 10 && node.parentElement) {
      node = node.parentElement;
      for (const b of node.querySelectorAll('a')) {
        const t = (b.innerText || '').trim();
        if (t.length >= 10) { title = t; break; }
      }
    }
    if (title.length >= 10 && !out.has(url)) out.set(url, title);
  }
  return [...out].map(([url, title]) => ({url, title}));
}
"""


def search_all(queries: list[str], headless: bool = True) -> list[Article]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("playwright 미설치: pip install playwright && playwright install chromium") from e

    now = datetime.now(KST)
    articles: list[Article] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context(locale="ko-KR").new_page()
        for q in queries:
            page.goto(SEARCH_URL.format(q=quote(q)), wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            for _ in range(3):  # 무한스크롤 형태 대비
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(500)
            for it in page.evaluate(JS_EXTRACT):
                articles.append(Article(clean(it["title"]), it["url"], now, "", q))
        browser.close()
    return articles
