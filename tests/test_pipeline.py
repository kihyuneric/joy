"""가짜 DART 응답으로 수집 → 결합 → 저장 전 과정을 검증한다(네트워크 불필요)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from krfin import employees as emp_mod
from krfin import financials as fin_mod
from krfin.assemble import build_records
from krfin.config import Settings
from krfin.corps import build_universe
from krfin.dart import DartClient
from krfin.export import coverage_report, pivot, write_excel, write_pivot_csvs, write_tidy_csv
from krfin.financials import parse_amount, parse_multi_account, pick_preferred
from krfin.periods import DEFAULT_PERIODS, parse_periods
from tests.fake_dart import UNIT, FakeSession


def make_client(tmp: Path) -> DartClient:
    settings = Settings(api_key="x" * 40, cache_dir=tmp / "cache", out_dir=tmp / "out",
                        rate_limit=0, max_workers=4)
    settings.ensure_dirs()
    client = DartClient(settings)
    client.session = FakeSession()
    return client


class ParsingTests(unittest.TestCase):
    def test_parse_amount_variants(self):
        self.assertEqual(parse_amount("1,234,567"), 1234567)
        self.assertEqual(parse_amount("(1,234)"), -1234)
        self.assertEqual(parse_amount("△500"), -500)
        self.assertEqual(parse_amount("-7"), -7)
        for empty in ("", "-", None, "해당사항없음"):
            self.assertIsNone(parse_amount(empty))

    def test_consolidated_preferred_over_separate(self):
        payload = {"list": [
            {"corp_code": "A", "fs_div": "CFS", "sj_div": "BS", "account_nm": "자산총계",
             "thstrm_amount": "1,000"},
            {"corp_code": "A", "fs_div": "OFS", "sj_div": "BS", "account_nm": "자산총계",
             "thstrm_amount": "500"},
        ]}
        picked = pick_preferred({k[1]: v for k, v in parse_multi_account(payload).items()})
        self.assertEqual(picked.fs_div, "CFS")
        self.assertEqual(picked.total_assets, 1000)

    def test_falls_back_to_separate_when_consolidated_empty(self):
        payload = {"list": [
            {"corp_code": "A", "fs_div": "CFS", "sj_div": "BS", "account_nm": "부채총계",
             "thstrm_amount": "1,000"},
            {"corp_code": "A", "fs_div": "OFS", "sj_div": "BS", "account_nm": "자산총계",
             "thstrm_amount": "500"},
        ]}
        picked = pick_preferred({k[1]: v for k, v in parse_multi_account(payload).items()})
        self.assertEqual(picked.fs_div, "OFS")
        self.assertEqual(picked.total_assets, 500)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.client = make_client(self.tmp)
        self.periods = list(DEFAULT_PERIODS)
        self.corps = build_universe(self.client, ("Y", "K"), max_workers=4)
        codes = [c.corp_code for c in self.corps]
        self.fin = {p.label: fin_mod.fetch_period(self.client, codes, p, chunk_size=100, max_workers=4)
                    for p in self.periods}
        self.emp = {
            p.label: (emp_mod.fetch_period(self.client, codes, p, max_workers=4)
                      if p.has_employee_data else {})
            for p in self.periods
        }
        self.rows = build_records(self.corps, self.periods, self.fin, self.emp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_universe_excludes_konex_and_unlisted(self):
        names = {c.corp_name for c in self.corps}
        self.assertEqual(names, {"가나전자", "다라은행", "마바바이오"})
        self.assertEqual({c.market for c in self.corps}, {"코스피", "코스닥"})

    def test_row_count(self):
        self.assertEqual(len(self.rows), len(self.corps) * len(self.periods))

    def _row(self, name: str, period: str) -> dict:
        return next(r for r in self.rows if r["corp_name"] == name and r["period"] == period)

    def test_total_assets_uses_consolidated(self):
        self.assertEqual(self._row("가나전자", "2025Q1")["total_assets"], 5_000 * UNIT)

    def test_cumulative_and_quarterly_revenue(self):
        q2 = self._row("가나전자", "2025Q2")
        self.assertEqual(q2["revenue_cumulative"], 250 * UNIT)
        self.assertEqual(q2["revenue_quarter"], 150 * UNIT)      # 250 - 100
        self.assertEqual(q2["revenue_quarter_source"], "공시(3개월)")

    def test_annual_report_quarter_is_derived(self):
        q4 = self._row("가나전자", "2025Q4")
        self.assertEqual(q4["revenue_cumulative"], 600 * UNIT)
        self.assertEqual(q4["revenue_quarter"], 200 * UNIT)      # 600 - 400
        self.assertEqual(q4["revenue_quarter_source"], "직전분기차감")

    def test_first_quarter_cumulative_equals_quarter(self):
        q1 = self._row("마바바이오", "2026Q1")
        self.assertEqual(q1["revenue_quarter"], q1["revenue_cumulative"])
        self.assertEqual(q1["revenue_quarter_source"], "1분기=누적")

    def test_financial_sector_revenue_label(self):
        # '영업수익'으로 공시하는 금융회사도 매출액 항목으로 잡혀야 한다.
        self.assertEqual(self._row("다라은행", "2026Q2")["revenue_cumulative"], 190 * UNIT)

    def test_operating_loss_negative(self):
        self.assertEqual(self._row("마바바이오", "2025Q4")["operating_income_cumulative"], -6 * UNIT)

    def test_employees_from_half_year_report(self):
        row = self._row("가나전자", "2025Q2")
        self.assertEqual(row["employees"], 720 + 10 + 480 + 5)
        self.assertIn("반기보고서", row["employees_source"])

    def test_employees_carried_into_quarters(self):
        row = self._row("가나전자", "2025Q3")
        self.assertEqual(row["employees"], self._row("가나전자", "2025Q2")["employees"])
        self.assertIn("이월", row["employees_source"])

    def test_employees_absent_before_first_disclosure(self):
        self.assertIsNone(self._row("가나전자", "2025Q1")["employees"])

    def test_pivot_shape(self):
        header, body = pivot(self.rows, "total_assets")
        self.assertEqual(header[-6:], [p.label for p in self.periods])
        self.assertEqual(len(body), len(self.corps))

    def test_exports_written(self):
        out = self.tmp / "out"
        tidy = write_tidy_csv(self.rows, out / "t.csv")
        pivots = write_pivot_csvs(self.rows, out)
        xlsx = write_excel(self.rows, out / "t.xlsx")
        self.assertTrue(tidy.is_file() and all(p.is_file() for p in pivots))
        if xlsx:
            self.assertTrue(xlsx.is_file())
        report = coverage_report(self.rows, [p.label for p in self.periods])
        self.assertIn("코스피", report)
        self.assertIn("자산총액", report)

    def test_cache_avoids_second_round_of_calls(self):
        before = len(self.client.session.calls)
        codes = [c.corp_code for c in self.corps]
        fin_mod.fetch_period(self.client, codes, self.periods[0], chunk_size=100, max_workers=4)
        self.assertEqual(len(self.client.session.calls), before)


class PeriodTests(unittest.TestCase):
    def test_range_expansion(self):
        self.assertEqual([p.label for p in parse_periods("2025Q1..2026Q2")],
                         ["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"])

    def test_employee_reports(self):
        flags = {p.label: p.has_employee_data for p in DEFAULT_PERIODS}
        self.assertEqual(flags, {"2025Q1": False, "2025Q2": True, "2025Q3": False,
                                 "2025Q4": True, "2026Q1": False, "2026Q2": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
