#!/usr/bin/env python3
"""코스피·코스닥 상장사 분기 재무/인력 데이터 수집 CLI.

사용 예)
    python run.py all                          # 2025Q1~2026Q2 전체 수집 후 파일 생성
    python run.py all --limit 50               # 소규모 시험 실행
    python run.py all --markets kospi          # 코스피만
    python run.py all --periods 2026Q1..2026Q2 # 특정 구간만
    python run.py universe                     # 유니버스(상장사 목록)만 갱신
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from krfin import employees as emp_mod
from krfin import financials as fin_mod
from krfin.assemble import build_records
from krfin.config import Settings
from krfin.corps import build_universe, load_universe, save_universe
from krfin.dart import DartClient, DartError, QuotaExceeded
from krfin.export import coverage_report, write_excel, write_pivot_csvs, write_tidy_csv
from krfin.periods import DEFAULT_PERIODS, parse_periods

MARKET_ALIASES = {"kospi": "Y", "코스피": "Y", "y": "Y", "kosdaq": "K", "코스닥": "K", "k": "K"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KOSPI/KOSDAQ 분기 재무·인력 데이터 수집")
    parser.add_argument("command", choices=["all", "universe", "financials", "employees", "export"],
                        help="all=전체 실행, universe=상장사 목록만, export=캐시로 파일만 재생성")
    parser.add_argument("--periods", default="2025Q1..2026Q2",
                        help="예: 2025Q1..2026Q2 또는 2025Q4,2026Q1 (기본: 2025Q1..2026Q2)")
    parser.add_argument("--markets", default="kospi,kosdaq", help="kospi / kosdaq / kospi,kosdaq")
    parser.add_argument("--limit", type=int, default=0, help="시험 실행용 상위 N개사 제한")
    parser.add_argument("--no-employees", action="store_true", help="인력수 수집 생략(호출 수 절약)")
    parser.add_argument("--emp-all-quarters", action="store_true",
                        help="1·3분기에도 직원현황 조회 시도(호출 수 증가, 대부분 무자료)")
    parser.add_argument("--no-cache", action="store_true", help="디스크 캐시 무시하고 재수집")
    parser.add_argument("--workers", type=int, default=0, help="동시 요청 수 (기본 8)")
    parser.add_argument("--rate", type=float, default=0.0, help="초당 요청 수 (기본 8)")
    parser.add_argument("--out", default="", help="출력 디렉터리 (기본 data/out)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def resolve_markets(spec: str) -> tuple[str, ...]:
    out = []
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            continue
        code = MARKET_ALIASES.get(token)
        if code is None:
            raise SystemExit(f"알 수 없는 시장 구분: {token} (kospi/kosdaq 중 선택)")
        if code not in out:
            out.append(code)
    return tuple(out)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = Settings.from_env(Path.cwd())
    settings.markets = resolve_markets(args.markets)
    settings.limit = args.limit
    settings.use_cache = not args.no_cache
    if args.workers:
        settings.max_workers = args.workers
    if args.rate:
        settings.rate_limit = args.rate
    if args.out:
        settings.out_dir = Path(args.out)
    settings.ensure_dirs()

    if not settings.api_key:
        print(
            "DART_API_KEY 가 설정되지 않았습니다.\n"
            "  1) https://opendart.fss.or.kr 에서 인증키를 발급받고\n"
            "  2) cp .env.example .env 후 DART_API_KEY 값을 채워주세요.",
            file=sys.stderr,
        )
        return 2

    periods = list(parse_periods(args.periods))
    universe_path = Path(settings.cache_dir) / "universe.csv"

    try:
        client = DartClient(settings)

        # 1) 유니버스
        if args.command in ("all", "universe") or not universe_path.is_file():
            corps = build_universe(client, settings.markets,
                                   max_workers=settings.max_workers, limit=settings.limit)
            save_universe(corps, universe_path)
        else:
            corps = [c for c in load_universe(universe_path) if c.corp_cls in settings.markets]
            if settings.limit:
                corps = corps[: settings.limit]
        if args.command == "universe":
            print(f"유니버스 {len(corps)}개사 저장 → {universe_path}  ({client.stats()})")
            return 0

        corp_codes = [c.corp_code for c in corps]

        # 2) 재무
        financials = {}
        if args.command in ("all", "financials", "export"):
            for period in periods:
                financials[period.label] = fin_mod.fetch_period(
                    client, corp_codes, period,
                    chunk_size=settings.multi_chunk_size, max_workers=settings.max_workers,
                )

        # 3) 인력
        employees = {}
        if args.command in ("all", "employees", "export") and not args.no_employees:
            for period in periods:
                if not (period.has_employee_data or args.emp_all_quarters):
                    employees[period.label] = {}
                    continue
                employees[period.label] = emp_mod.fetch_period(
                    client, corp_codes, period, max_workers=settings.max_workers
                )

        if args.command in ("financials", "employees"):
            print(f"수집 완료. ({client.stats()})")
            return 0

        # 4) 결합 및 저장
        rows = build_records(corps, periods, financials, employees)
        out_dir = Path(settings.out_dir)
        write_tidy_csv(rows, out_dir / "kospi_kosdaq_quarterly.csv")
        write_pivot_csvs(rows, out_dir)
        write_excel(rows, out_dir / "kospi_kosdaq_quarterly.xlsx")
        report = coverage_report(rows, [p.label for p in periods])
        (out_dir / "coverage.md").write_text(report, encoding="utf-8")

        print()
        print(report)
        print(f"완료: {len(rows):,}행 / {len(corps):,}개사 × {len(periods)}개 분기  ({client.stats()})")
        print(f"출력 위치: {out_dir.resolve()}")
        return 0

    except QuotaExceeded as exc:
        print(f"\n[중단] {exc}", file=sys.stderr)
        return 3
    except DartError as exc:
        print(f"\n[DART 오류] {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
