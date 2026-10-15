import unittest
from datetime import datetime

from newsbot.message import format_message, header
from newsbot.naver import KST, parse_item
from newsbot.select import pick

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=KST)


def item(title, n, day="16", hour="07", host="n.news.naver.com"):
    return {"title": title, "description": "",
            "link": f"https://{host}/mnews/article/003/{n:010d}",
            "pubDate": f"Fri, {day} Oct 2026 {hour}:00:00 +0900"}


def arts(*items):
    return [a for it in items if (a := parse_item(it, "q"))]


class NewsbotTest(unittest.TestCase):
    def test_header(self):
        self.assertEqual(header(NOW), "26년 10월 16일 금요일 부동산 주요뉴스")

    def test_parse_filters_non_naver_and_strips_html(self):
        self.assertIsNone(parse_item(item("부동산", 1, host="www.example.com"), "q"))
        a = parse_item(item("<b>부동산</b> &quot;경매&quot;", 2), "q")
        self.assertEqual(a.title, '부동산 "경매"')

    def test_pick_prefers_today_dedupes_and_reserves_auction(self):
        topics = ["강남", "송파", "분당", "노원", "마포", "성동", "수원", "인천", "부산", "대구", "광주"]
        items = [item(f"{topics[i - 1]} 아파트 집값 동향 {chr(0xAC00 + i * 50)}{chr(0xB000 + i * 70)}", i)
                 for i in range(1, 12)]
        items += [item("법원경매 낙찰가율 급등 서울 아파트", 50),
                  item("법원경매 낙찰가율 급등 서울 아파트 (종합)", 51),   # 중복
                  item("강제경매 증가 재건축 단지 주의", 52),
                  item("어제 부동산 경매 기사", 53, day="15")]
        chosen = pick(arts(*items), NOW, count=10, auction_slots=4)
        titles = [a.title for a in chosen]
        self.assertEqual(len(chosen), 10)
        self.assertEqual(sum("법원경매 낙찰가율" in t for t in titles), 1)
        self.assertIn("강제경매 증가 재건축 단지 주의", titles)
        self.assertNotIn("어제 부동산 경매 기사", titles)  # 오늘 기사로 충분하면 제외

    def test_fallback_to_previous_day_when_few(self):
        chosen = pick(arts(item("부동산 경매 동향", 1), item("어제 부동산 경매 기사", 2, day="15", hour="20")),
                      NOW, count=10)
        self.assertEqual(len(chosen), 2)

    def test_format(self):
        text = format_message(arts(item("부동산 경매 동향", 1)), NOW)
        self.assertEqual(text.splitlines()[0], "26년 10월 16일 금요일 부동산 주요뉴스")
        self.assertIn("https://n.news.naver.com/mnews/article/003/0000000001", text)


if __name__ == "__main__":
    unittest.main()
