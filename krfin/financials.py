"""재무 데이터 수집: 자산총계 · 매출액 · 영업이익.

Open DART `fnlttMultiAcnt` (다중회사 주요계정)은 한 번에 최대 100개 기업을 조회할 수 있어
전체 상장사를 훑는 데 호출 수가 가장 적게 든다.
응답에는 연결(CFS)·별도(OFS) 행이 함께 오므로 연결 우선, 없으면 별도로 대체한다.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .dart import DartClient, DartError, STATUS_OK
from .periods import Period

log = logging.getLogger(__name__)

# 계정명은 회사마다 표기가 조금씩 다르다(금융업은 매출액 대신 영업수익 등).
TOTAL_ASSETS_NAMES = {"자산총계", "자산총액"}
REVENUE_NAMES = {
    "매출액", "매출", "수익(매출액)", "매출액(수익)", "영업수익",
    "영업수익(매출액)", "매출액및지분법이익", "수익",
}
OPERATING_INCOME_NAMES = {
    "영업이익", "영업손실", "영업이익(손실)", "영업손실(이익)",
    "영업이익(△손실)", "영업이익(손실)계", "영업손익",
}

FS_PREFERENCE = ("CFS", "OFS")   # 연결 우선
FS_NAMES = {"CFS": "연결", "OFS": "별도"}


def normalize_account(name: str) -> str:
    """공백·중점·유니코드 마이너스를 제거해 계정명을 비교 가능한 형태로 만든다."""
    text = re.sub(r"[\s 　·]", "", name or "")
    return text.replace("△", "").replace("▲", "").replace("(-)", "")


def _matches(name: str, candidates: set[str]) -> bool:
    return normalize_account(name) in {normalize_account(c) for c in candidates}


def parse_amount(raw) -> int | None:
    """'1,234,567' / '-1,234' / '(1,234)' / '-' / '' → int 또는 None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if text in ("", "-", "--", "N/A", "해당사항없음"):
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.startswith(("△", "▲")):
        negative, text = True, text[1:]
    if text.startswith("-"):
        negative, text = True, text[1:]
    text = re.sub(r"[^\d.]", "", text)
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return -value if negative else value


@dataclass
class FinancialFacts:
    """한 기업 · 한 분기 · 한 재무제표 구분(연결/별도)의 주요 계정."""

    corp_code: str
    fs_div: str
    rcept_no: str = ""
    currency: str = ""
    total_assets: int | None = None            # 재무상태표: 기말 잔액
    revenue_reported: int | None = None        # 손익계산서 thstrm_amount (당분기 3개월인 경우가 많음)
    revenue_cumulative: int | None = None      # thstrm_add_amount (당기 누적)
    operating_income_reported: int | None = None
    operating_income_cumulative: int | None = None
    accounts_seen: set[str] = field(default_factory=set)

    @property
    def fs_name(self) -> str:
        return FS_NAMES.get(self.fs_div, self.fs_div)


def _apply_row(facts: FinancialFacts, row: dict) -> None:
    account = row.get("account_nm", "")
    facts.accounts_seen.add(normalize_account(account))
    facts.rcept_no = facts.rcept_no or (row.get("rcept_no") or "").strip()
    facts.currency = facts.currency or (row.get("currency") or "").strip()

    amount = parse_amount(row.get("thstrm_amount"))
    add_amount = parse_amount(row.get("thstrm_add_amount"))
    sj_div = (row.get("sj_div") or "").strip().upper()

    if sj_div == "BS" and _matches(account, TOTAL_ASSETS_NAMES):
        if amount is not None:
            facts.total_assets = amount
        return

    if sj_div in ("IS", "CIS"):
        if _matches(account, REVENUE_NAMES):
            # 포괄손익계산서(CIS)만 제출한 회사도 있으므로 이미 값이 있으면 덮어쓰지 않는다.
            if facts.revenue_reported is None and amount is not None:
                facts.revenue_reported = amount
            if facts.revenue_cumulative is None and add_amount is not None:
                facts.revenue_cumulative = add_amount
        elif _matches(account, OPERATING_INCOME_NAMES):
            if facts.operating_income_reported is None and amount is not None:
                facts.operating_income_reported = amount
            if facts.operating_income_cumulative is None and add_amount is not None:
                facts.operating_income_cumulative = add_amount


def parse_multi_account(payload: dict) -> dict[tuple[str, str], FinancialFacts]:
    """fnlttMultiAcnt 응답 → {(corp_code, fs_div): FinancialFacts}."""
    out: dict[tuple[str, str], FinancialFacts] = {}
    for row in payload.get("list", []) or []:
        corp_code = (row.get("corp_code") or "").strip()
        fs_div = (row.get("fs_div") or "").strip().upper()
        if not corp_code or not fs_div:
            continue
        key = (corp_code, fs_div)
        facts = out.get(key)
        if facts is None:
            facts = out[key] = FinancialFacts(corp_code=corp_code, fs_div=fs_div)
        _apply_row(facts, row)
    return out


def pick_preferred(by_fs: dict[str, FinancialFacts]) -> FinancialFacts | None:
    """연결(CFS) 우선, 없으면 별도(OFS). 연결에 값이 하나도 없으면 별도로 넘어간다."""
    for fs_div in FS_PREFERENCE:
        facts = by_fs.get(fs_div)
        if facts is None:
            continue
        if any(v is not None for v in (facts.total_assets, facts.revenue_reported,
                                       facts.revenue_cumulative, facts.operating_income_reported,
                                       facts.operating_income_cumulative)):
            return facts
    return next(iter(by_fs.values()), None)


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_period(client: DartClient, corp_codes: list[str], period: Period,
                 chunk_size: int = 100, max_workers: int = 8) -> dict[str, FinancialFacts]:
    """한 분기의 전체 기업 주요계정을 수집해 {corp_code: FinancialFacts} 로 돌려준다."""
    chunks = list(chunked(corp_codes, chunk_size))
    merged: dict[tuple[str, str], FinancialFacts] = {}

    def worker(chunk: list[str]) -> dict[tuple[str, str], FinancialFacts]:
        payload = client.get_json(
            "fnlttMultiAcnt.json",
            corp_code=",".join(chunk),
            bsns_year=str(period.year),
            reprt_code=period.reprt_code,
        )
        if str(payload.get("status")) != STATUS_OK:
            return {}
        return parse_multi_account(payload)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, chunk): chunk for chunk in chunks}
        for done, fut in enumerate(as_completed(futures), start=1):
            try:
                merged.update(fut.result())
            except DartError as exc:
                log.warning("%s 재무 조회 실패(기업 %d개 구간): %s",
                            period.label, len(futures[fut]), exc)
            if done % 10 == 0:
                log.info("%s 재무 수집 %d/%d 구간", period.label, done, len(chunks))

    by_corp: dict[str, dict[str, FinancialFacts]] = {}
    for (corp_code, fs_div), facts in merged.items():
        by_corp.setdefault(corp_code, {})[fs_div] = facts

    result = {}
    for corp_code, by_fs in by_corp.items():
        picked = pick_preferred(by_fs)
        if picked is not None:
            result[corp_code] = picked
    log.info("%s 재무 수집 완료: %d개사", period.label, len(result))
    return result
