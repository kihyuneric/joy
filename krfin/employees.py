"""인력수 수집: '직원 등의 현황'(empSttus).

- 사업부문 × 성별로 행이 쪼개져 오므로 합계(sm)를 합산한다.
- 직원 현황은 사업보고서·반기보고서에 실린다. 분기보고서는 대부분 생략하므로
  1·3분기는 직전 공시 값을 앞으로 채워(forward fill) 참고치로 제공한다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .dart import DartClient, DartError, STATUS_OK
from .financials import parse_amount
from .periods import Period

log = logging.getLogger(__name__)

# 부문/성별 칸에 들어오면 소계·합계 행으로 간주해 중복 합산을 피한다.
TOTAL_ROW_TOKENS = {"합계", "계", "총계", "소계", "전체", "합 계"}


@dataclass
class EmployeeFacts:
    corp_code: str
    total: int | None = None            # 인력수 합계
    regular: int | None = None          # 정규직(기간의 정함이 없는 근로자)
    contract: int | None = None         # 계약직(기간제 근로자)
    rows: int = 0
    rcept_no: str = ""


def _is_total_row(row: dict) -> bool:
    for key in ("fo_bbm", "sexdstn"):
        value = (row.get(key) or "").strip()
        if value.replace(" ", "") in {t.replace(" ", "") for t in TOTAL_ROW_TOKENS}:
            return True
    return False


def parse_emp_sttus(payload: dict, corp_code: str = "") -> EmployeeFacts:
    rows = payload.get("list", []) or []
    facts = EmployeeFacts(corp_code=corp_code)
    if not rows:
        return facts

    detail = [r for r in rows if not _is_total_row(r)]
    # 회사가 합계 행만 제출한 경우에는 그 행을 그대로 쓴다.
    target = detail or rows

    total = regular = contract = None
    for row in target:
        facts.rcept_no = facts.rcept_no or (row.get("rcept_no") or "").strip()
        sm = parse_amount(row.get("sm"))
        rg = parse_amount(row.get("rgllbr_co"))
        ct = parse_amount(row.get("cnttk_co"))
        if sm is None and (rg is not None or ct is not None):
            sm = (rg or 0) + (ct or 0)
        if sm is not None:
            total = (total or 0) + sm
        if rg is not None:
            regular = (regular or 0) + rg
        if ct is not None:
            contract = (contract or 0) + ct

    facts.total, facts.regular, facts.contract = total, regular, contract
    facts.rows = len(target)
    return facts


def fetch_period(client: DartClient, corp_codes: list[str], period: Period,
                 max_workers: int = 8) -> dict[str, EmployeeFacts]:
    """한 분기의 전체 기업 직원 현황. 기업당 1콜이므로 호출 수가 가장 많은 단계."""

    def worker(corp_code: str) -> tuple[str, EmployeeFacts]:
        payload = client.get_json(
            "empSttus.json",
            corp_code=corp_code,
            bsns_year=str(period.year),
            reprt_code=period.reprt_code,
        )
        if str(payload.get("status")) != STATUS_OK:
            return corp_code, EmployeeFacts(corp_code=corp_code)
        return corp_code, parse_emp_sttus(payload, corp_code)

    out: dict[str, EmployeeFacts] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, code): code for code in corp_codes}
        for done, fut in enumerate(as_completed(futures), start=1):
            try:
                corp_code, facts = fut.result()
                out[corp_code] = facts
            except DartError as exc:
                log.warning("%s 직원현황 실패(%s): %s", period.label, futures[fut], exc)
            if done % 500 == 0:
                log.info("%s 직원현황 %d/%d", period.label, done, len(corp_codes))

    filled = sum(1 for f in out.values() if f.total is not None)
    log.info("%s 직원현황 완료: %d개사 중 %d개사 값 확보", period.label, len(corp_codes), filled)
    return out
