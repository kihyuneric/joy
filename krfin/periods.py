"""조회 대상 분기 정의.

Open DART 의 보고서 코드(reprt_code)
  11013 = 1분기보고서, 11012 = 반기보고서, 11014 = 3분기보고서, 11011 = 사업보고서
"""

from __future__ import annotations

from dataclasses import dataclass

REPORT_NAMES = {
    "11013": "1분기보고서",
    "11012": "반기보고서",
    "11014": "3분기보고서",
    "11011": "사업보고서",
}

# 회계연도 내 누적 순서 (1분기 → 반기 → 3분기 → 사업보고서)
QUARTER_ORDER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}

# 직원 현황(직원 등의 현황)은 사업보고서·반기보고서에만 실리는 것이 일반적이다.
# 분기보고서는 2019년 서식 간소화 이후 직원 현황을 생략하는 회사가 대부분이다.
EMPLOYEE_REPORT_CODES = ("11011", "11012")


@dataclass(frozen=True)
class Period:
    year: int
    reprt_code: str

    @property
    def quarter(self) -> int:
        return QUARTER_ORDER[self.reprt_code]

    @property
    def label(self) -> str:
        """2025Q1 형태의 라벨 (12월 결산 기준)."""
        return f"{self.year}Q{self.quarter}"

    @property
    def report_name(self) -> str:
        return REPORT_NAMES[self.reprt_code]

    @property
    def has_employee_data(self) -> bool:
        return self.reprt_code in EMPLOYEE_REPORT_CODES

    def previous_in_year(self) -> "Period | None":
        """같은 회계연도의 직전 누적 구간. 1분기면 None."""
        for code, order in QUARTER_ORDER.items():
            if order == self.quarter - 1:
                return Period(self.year, code)
        return None


# 2025년 1분기 ~ 2026년 2분기(반기)
DEFAULT_PERIODS: tuple[Period, ...] = (
    Period(2025, "11013"),   # 2025Q1
    Period(2025, "11012"),   # 2025Q2 (반기)
    Period(2025, "11014"),   # 2025Q3
    Period(2025, "11011"),   # 2025Q4 (사업보고서 = 연간)
    Period(2026, "11013"),   # 2026Q1
    Period(2026, "11012"),   # 2026Q2 (반기)
)


def parse_period(token: str) -> Period:
    """'2025Q1' 또는 '2025:11013' 형식을 Period 로 변환."""
    token = token.strip().upper()
    if ":" in token:
        year, _, code = token.partition(":")
        if code not in REPORT_NAMES:
            raise ValueError(f"알 수 없는 보고서 코드: {code}")
        return Period(int(year), code)
    if "Q" in token:
        year, _, q = token.partition("Q")
        for code, order in QUARTER_ORDER.items():
            if order == int(q):
                return Period(int(year), code)
    raise ValueError(f"분기 표기를 해석할 수 없습니다: {token}")


def parse_periods(spec: str) -> tuple[Period, ...]:
    """'2025Q1,2025Q2' 또는 '2025Q1..2026Q2' 를 Period 목록으로 변환."""
    spec = spec.strip()
    if ".." in spec:
        start, _, end = spec.partition("..")
        first, last = parse_period(start), parse_period(end)
        out = []
        year, quarter = first.year, first.quarter
        while (year, quarter) <= (last.year, last.quarter):
            out.append(parse_period(f"{year}Q{quarter}"))
            quarter += 1
            if quarter > 4:
                year, quarter = year + 1, 1
        return tuple(out)
    return tuple(parse_period(tok) for tok in spec.split(",") if tok.strip())
