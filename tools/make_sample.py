#!/usr/bin/env python3
"""가짜 DART 응답으로 samples/ 에 출력 형식 예시를 만든다(네트워크 불필요).

실제 수집 결과와 컬럼 구성이 동일하므로, 인증키를 넣기 전에 산출물 형태를 확인할 수 있다.
    python tools/make_sample.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from krfin import employees as emp_mod
from krfin import financials as fin_mod
from krfin.assemble import build_records
from krfin.config import Settings
from krfin.corps import build_universe
from krfin.dart import DartClient
from krfin.export import coverage_report, write_excel, write_pivot_csvs, write_tidy_csv
from krfin.periods import DEFAULT_PERIODS
from tests.fake_dart import FakeSession


def main() -> int:
    out_dir = ROOT / "samples"
    out_dir.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        settings = Settings(api_key="x" * 40, cache_dir=tmp, out_dir=out_dir, rate_limit=0)
        settings.ensure_dirs()
        client = DartClient(settings)
        client.session = FakeSession()

        periods = list(DEFAULT_PERIODS)
        corps = build_universe(client, ("Y", "K"))
        codes = [c.corp_code for c in corps]
        financials = {p.label: fin_mod.fetch_period(client, codes, p) for p in periods}
        employees = {
            p.label: (emp_mod.fetch_period(client, codes, p) if p.has_employee_data else {})
            for p in periods
        }
        rows = build_records(corps, periods, financials, employees)

        write_tidy_csv(rows, out_dir / "sample_quarterly.csv")
        write_pivot_csvs(rows, out_dir)
        write_excel(rows, out_dir / "sample_quarterly.xlsx")
        (out_dir / "coverage.md").write_text(
            coverage_report(rows, [p.label for p in periods]), encoding="utf-8")
        print(f"샘플 {len(rows)}행 생성 → {out_dir}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
