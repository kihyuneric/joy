"""수집한 원자료를 기업 × 분기 레코드로 결합한다.

손익 항목은 DART 가 '당기 3개월'과 '당기 누적'을 함께 주기도 하고 누적만 주기도 한다.
따라서 누적치를 확정한 뒤, 분기 단독 실적을 다음 우선순위로 산출한다.
  1) 1분기        → 누적 = 분기
  2) 회사가 3개월 금액을 별도로 제출 → 그 값(reported)
  3) 직전 분기 누적과의 차이(derived)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .corps import Corp
from .employees import EmployeeFacts
from .financials import FinancialFacts
from .periods import Period

RECORD_COLUMNS = [
    "corp_code", "corp_name", "stock_code", "market", "corp_cls", "induty_code",
    "acc_mt", "non_december_fy",
    "fiscal_year", "quarter", "period", "report_code", "report_name",
    "fs_div", "fs_name", "currency",
    "total_assets",
    "revenue_cumulative", "revenue_quarter", "revenue_quarter_source",
    "operating_income_cumulative", "operating_income_quarter", "operating_income_quarter_source",
    "employees", "employees_regular", "employees_contract", "employees_source",
    "rcept_no", "employees_rcept_no",
]

KOREAN_HEADERS = {
    "corp_code": "고유번호", "corp_name": "회사명", "stock_code": "종목코드",
    "market": "시장", "corp_cls": "시장구분코드", "induty_code": "업종코드",
    "acc_mt": "결산월", "non_december_fy": "12월결산아님",
    "fiscal_year": "사업연도", "quarter": "분기", "period": "기간",
    "report_code": "보고서코드", "report_name": "보고서",
    "fs_div": "재무제표구분", "fs_name": "연결/별도", "currency": "통화",
    "total_assets": "자산총액",
    "revenue_cumulative": "매출액(누적)", "revenue_quarter": "매출액(분기)",
    "revenue_quarter_source": "매출액(분기)산출근거",
    "operating_income_cumulative": "영업이익(누적)", "operating_income_quarter": "영업이익(분기)",
    "operating_income_quarter_source": "영업이익(분기)산출근거",
    "employees": "인력수", "employees_regular": "정규직", "employees_contract": "계약직",
    "employees_source": "인력수출처",
    "rcept_no": "재무접수번호", "employees_rcept_no": "직원현황접수번호",
}


@dataclass
class Record:
    values: dict = field(default_factory=dict)

    def __getitem__(self, key):
        return self.values.get(key)


def _resolve_cumulative(reported: int | None, cumulative: int | None) -> int | None:
    return cumulative if cumulative is not None else reported


def _resolve_quarter(quarter: int, reported: int | None, cumulative: int | None,
                     prev_cumulative: int | None) -> tuple[int | None, str]:
    if cumulative is None and reported is None:
        return None, "na"
    if quarter == 1:
        return _resolve_cumulative(reported, cumulative), "1분기=누적"
    if reported is not None and cumulative is not None and reported != cumulative:
        return reported, "공시(3개월)"
    if cumulative is not None and prev_cumulative is not None:
        return cumulative - prev_cumulative, "직전분기차감"
    if cumulative is None:
        return reported, "공시(3개월)"
    return None, "na"


def build_records(
    corps: list[Corp],
    periods: list[Period],
    financials: dict[str, dict[str, FinancialFacts]],
    employees: dict[str, dict[str, EmployeeFacts]],
) -> list[dict]:
    """financials/employees 는 {period.label: {corp_code: facts}} 구조."""
    ordered = sorted(periods, key=lambda p: (p.year, p.quarter))
    rows: list[dict] = []

    for corp in corps:
        # 회계연도별 누적치 캐시 → 분기 실적 차감에 사용
        cum_cache: dict[tuple[int, int], dict[str, int | None]] = {}
        last_employee: tuple[str, EmployeeFacts] | None = None

        for period in ordered:
            fin = financials.get(period.label, {}).get(corp.corp_code)
            emp = employees.get(period.label, {}).get(corp.corp_code)

            rev_cum = _resolve_cumulative(
                fin.revenue_reported if fin else None,
                fin.revenue_cumulative if fin else None,
            )
            op_cum = _resolve_cumulative(
                fin.operating_income_reported if fin else None,
                fin.operating_income_cumulative if fin else None,
            )
            prev = cum_cache.get((period.year, period.quarter - 1), {})
            rev_q, rev_src = _resolve_quarter(
                period.quarter, fin.revenue_reported if fin else None, rev_cum, prev.get("revenue")
            )
            op_q, op_src = _resolve_quarter(
                period.quarter, fin.operating_income_reported if fin else None, op_cum,
                prev.get("operating_income"),
            )
            cum_cache[(period.year, period.quarter)] = {"revenue": rev_cum, "operating_income": op_cum}

            if emp and emp.total is not None:
                emp_source = f"{period.label} {period.report_name}"
                last_employee = (period.label, emp)
            elif last_employee is not None:
                # 분기보고서는 직원 현황을 싣지 않으므로 직전 공시치를 참고값으로 채운다.
                emp = last_employee[1]
                emp_source = f"{last_employee[0]} 공시치 이월"
            else:
                emp, emp_source = None, ""

            rows.append({
                "corp_code": corp.corp_code,
                "corp_name": corp.corp_name,
                "stock_code": corp.stock_code,
                "market": corp.market,
                "corp_cls": corp.corp_cls,
                "induty_code": corp.induty_code,
                "acc_mt": corp.acc_mt,
                "non_december_fy": "Y" if not corp.is_december_fy else "",
                "fiscal_year": period.year,
                "quarter": period.quarter,
                "period": period.label,
                "report_code": period.reprt_code,
                "report_name": period.report_name,
                "fs_div": fin.fs_div if fin else "",
                "fs_name": fin.fs_name if fin else "",
                "currency": fin.currency if fin else "",
                "total_assets": fin.total_assets if fin else None,
                "revenue_cumulative": rev_cum,
                "revenue_quarter": rev_q,
                "revenue_quarter_source": rev_src,
                "operating_income_cumulative": op_cum,
                "operating_income_quarter": op_q,
                "operating_income_quarter_source": op_src,
                "employees": emp.total if emp else None,
                "employees_regular": emp.regular if emp else None,
                "employees_contract": emp.contract if emp else None,
                "employees_source": emp_source,
                "rcept_no": fin.rcept_no if fin else "",
                "employees_rcept_no": emp.rcept_no if emp else "",
            })
    return rows
