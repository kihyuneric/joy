"""네트워크 없이 파이프라인을 검증하기 위한 가짜 Open DART 서버."""

from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import urlparse

CORPS = {
    "00100001": {"name": "가나전자", "stock": "005930", "cls": "Y", "induty": "264", "acc_mt": "12"},
    "00100002": {"name": "다라은행", "stock": "105560", "cls": "Y", "induty": "641", "acc_mt": "12"},
    "00100003": {"name": "마바바이오", "stock": "247540", "cls": "K", "induty": "212", "acc_mt": "12"},
    "00100004": {"name": "사아코넥스", "stock": "900001", "cls": "N", "induty": "582", "acc_mt": "12"},
}

# 회계연도별 누적 매출/영업이익 (단위: 원) — 분기 차감 로직 검증용
CUMULATIVE = {
    ("00100001", 2025): {1: (100, 10), 2: (250, 26), 3: (400, 44), 4: (600, 70)},
    ("00100001", 2026): {1: (150, 15), 2: (330, 36)},
    ("00100002", 2025): {1: (80, 8), 2: (170, 18), 3: (260, 27), 4: (350, 35)},
    ("00100002", 2026): {1: (90, 9), 2: (190, 20)},
    ("00100003", 2025): {1: (5, -2), 2: (11, -3), 3: (18, -5), 4: (25, -6)},
    ("00100003", 2026): {1: (7, -1), 2: (16, -2)},
}
ASSETS = {"00100001": 5_000, "00100002": 300_000, "00100003": 120}
QUARTER_BY_CODE = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
UNIT = 1_000_000


class FakeResponse:
    def __init__(self, payload=None, content: bytes = b""):
        self._payload = payload
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


class FakeSession:
    """DartClient.session 자리에 꽂아 쓰는 대역. headers/get 만 흉내낸다."""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        endpoint = urlparse(url).path.rsplit("/", 1)[-1]
        self.calls.append((endpoint, params))
        if endpoint == "corpCode.xml":
            return FakeResponse(content=_corp_code_zip())
        if endpoint == "company.json":
            return FakeResponse(_company(params["corp_code"]))
        if endpoint == "fnlttMultiAcnt.json":
            return FakeResponse(_multi_account(params))
        if endpoint == "empSttus.json":
            return FakeResponse(_emp_sttus(params))
        return FakeResponse({"status": "100", "message": "unknown endpoint"})


def _corp_code_zip() -> bytes:
    rows = "".join(
        f"<list><corp_code>{code}</corp_code><corp_name>{info['name']}</corp_name>"
        f"<stock_code>{info['stock']}</stock_code><modify_date>20260701</modify_date></list>"
        for code, info in CORPS.items()
    )
    # 비상장(종목코드 없음) 한 건을 섞어 필터 동작을 확인한다.
    rows += ("<list><corp_code>00109999</corp_code><corp_name>비상장기업</corp_name>"
             "<stock_code> </stock_code><modify_date>20260701</modify_date></list>")
    xml = f"<?xml version='1.0' encoding='utf-8'?><result>{rows}</result>".encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def _company(corp_code: str) -> dict:
    info = CORPS.get(corp_code)
    if not info:
        return {"status": "013", "message": "조회된 데이타가 없습니다."}
    return {
        "status": "000", "message": "정상", "corp_name": info["name"],
        "corp_cls": info["cls"], "stock_code": info["stock"],
        "induty_code": info["induty"], "acc_mt": info["acc_mt"],
        "ceo_nm": "홍길동", "est_dt": "19690113",
    }


def _multi_account(params: dict) -> dict:
    year = int(params["bsns_year"])
    code = params["reprt_code"]
    quarter = QUARTER_BY_CODE[code]
    rows = []
    for corp_code in params["corp_code"].split(","):
        table = CUMULATIVE.get((corp_code, year))
        if not table or quarter not in table:
            continue
        cum_rev, cum_op = table[quarter]
        prev_rev, prev_op = table.get(quarter - 1, (0, 0))
        info = CORPS[corp_code]
        base = {
            "rcept_no": f"2026{corp_code}", "bsns_year": str(year), "corp_code": corp_code,
            "stock_code": info["stock"], "reprt_code": code, "fs_div": "CFS",
            "fs_nm": "연결재무제표", "currency": "KRW",
        }
        rows.append({**base, "sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "자산총계",
                     "thstrm_amount": f"{ASSETS[corp_code] * UNIT:,}", "thstrm_add_amount": ""})
        # 금융업(다라은행)은 '매출액' 대신 '영업수익'으로 공시한다.
        revenue_name = "영업수익" if corp_code == "00100002" else "수익(매출액)"
        if code == "11011":
            # 사업보고서는 누적(=연간)만 제출한다.
            three_month_rev, three_month_op = "", ""
        else:
            three_month_rev = f"{(cum_rev - prev_rev) * UNIT:,}"
            three_month_op = f"{(cum_op - prev_op) * UNIT:,}"
        rows.append({**base, "sj_div": "IS", "sj_nm": "손익계산서", "account_nm": revenue_name,
                     "thstrm_amount": three_month_rev or f"{cum_rev * UNIT:,}",
                     "thstrm_add_amount": f"{cum_rev * UNIT:,}" if code != "11011" else ""})
        rows.append({**base, "sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "영업이익(손실)",
                     "thstrm_amount": three_month_op or f"{cum_op * UNIT:,}",
                     "thstrm_add_amount": f"{cum_op * UNIT:,}" if code != "11011" else ""})
        # 별도재무제표 행도 함께 내려온다(연결이 우선되어야 함).
        rows.append({**base, "fs_div": "OFS", "fs_nm": "재무제표", "sj_div": "BS",
                     "sj_nm": "재무상태표", "account_nm": "자산총계",
                     "thstrm_amount": f"{ASSETS[corp_code] * UNIT // 2:,}", "thstrm_add_amount": ""})
    if not rows:
        return {"status": "013", "message": "조회된 데이타가 없습니다."}
    return {"status": "000", "message": "정상", "list": rows}


def _emp_sttus(params: dict) -> dict:
    corp_code = params["corp_code"]
    code = params["reprt_code"]
    if code not in ("11011", "11012") or corp_code not in CUMULATIVE_CORPS:
        return {"status": "013", "message": "조회된 데이타가 없습니다."}
    head = {"00100001": 1200, "00100002": 900, "00100003": 60}[corp_code]
    rows = [
        {"rcept_no": f"emp{corp_code}", "fo_bbm": "전사", "sexdstn": "남",
         "rgllbr_co": f"{int(head * 0.6):,}", "cnttk_co": "10", "sm": f"{int(head * 0.6) + 10:,}"},
        {"rcept_no": f"emp{corp_code}", "fo_bbm": "전사", "sexdstn": "여",
         "rgllbr_co": f"{int(head * 0.4):,}", "cnttk_co": "5", "sm": f"{int(head * 0.4) + 5:,}"},
    ]
    return {"status": "000", "message": "정상", "list": rows}


CUMULATIVE_CORPS = {"00100001", "00100002", "00100003"}
