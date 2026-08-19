"""상장 기업 유니버스(코스피/코스닥) 구성.

1) corpCode.xml  : DART 고유번호 전체 목록 (zip → xml)
2) company.json  : 기업별 시장구분(corp_cls)·업종·결산월 → 코스피(Y)/코스닥(K) 필터
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

from .dart import DartClient, DartError, STATUS_OK

log = logging.getLogger(__name__)

MARKET_NAMES = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}

UNIVERSE_COLUMNS = [
    "corp_code", "corp_name", "stock_code", "corp_cls", "market",
    "induty_code", "acc_mt", "ceo_nm", "est_dt", "modify_date",
]


@dataclass
class Corp:
    corp_code: str
    corp_name: str
    stock_code: str
    corp_cls: str = ""
    market: str = ""
    induty_code: str = ""
    acc_mt: str = ""          # 결산월 (12 = 12월 결산)
    ceo_nm: str = ""
    est_dt: str = ""
    modify_date: str = ""

    @property
    def is_december_fy(self) -> bool:
        return self.acc_mt.strip().lstrip("0") == "12"


def fetch_corp_codes(client: DartClient) -> list[Corp]:
    """corpCode.xml 전체를 받아 상장 종목코드가 있는 기업만 남긴다."""
    blob = client.get_bytes("corpCode.xml")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        xml_bytes = zf.read(name)

    root = ElementTree.fromstring(xml_bytes)
    corps: list[Corp] = []
    for node in root.iter("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        if not stock_code:      # 비상장(공시대상회사)은 종목코드가 비어 있다
            continue
        corps.append(
            Corp(
                corp_code=(node.findtext("corp_code") or "").strip(),
                corp_name=(node.findtext("corp_name") or "").strip(),
                stock_code=stock_code,
                modify_date=(node.findtext("modify_date") or "").strip(),
            )
        )
    log.info("corpCode.xml: 상장 종목코드 보유 기업 %d개", len(corps))
    return corps


def _fill_company_detail(client: DartClient, corp: Corp) -> Corp:
    data = client.get_json("company.json", corp_code=corp.corp_code)
    if str(data.get("status")) != STATUS_OK:
        return corp
    corp.corp_cls = (data.get("corp_cls") or "").strip()
    corp.market = MARKET_NAMES.get(corp.corp_cls, corp.corp_cls)
    corp.induty_code = (data.get("induty_code") or "").strip()
    corp.acc_mt = (data.get("acc_mt") or "").strip()
    corp.ceo_nm = (data.get("ceo_nm") or "").strip()
    corp.est_dt = (data.get("est_dt") or "").strip()
    if not corp.corp_name:
        corp.corp_name = (data.get("corp_name") or "").strip()
    return corp


def enrich_markets(client: DartClient, corps: list[Corp], max_workers: int = 8) -> list[Corp]:
    """company.json 을 병렬로 조회해 시장구분을 채운다(캐시되므로 재실행 시 즉시)."""
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fill_company_detail, client, c): c for c in corps}
        for fut in as_completed(futures):
            corp = futures[fut]
            try:
                fut.result()
            except DartError as exc:
                log.warning("company.json 실패 %s(%s): %s", corp.corp_name, corp.corp_code, exc)
            done += 1
            if done % 250 == 0:
                log.info("시장구분 조회 %d/%d", done, len(corps))
    return corps


def build_universe(client: DartClient, markets: tuple[str, ...], max_workers: int = 8,
                   limit: int = 0) -> list[Corp]:
    corps = fetch_corp_codes(client)
    corps.sort(key=lambda c: c.stock_code)
    if limit:
        corps = corps[:limit]
    enrich_markets(client, corps, max_workers=max_workers)
    universe = [c for c in corps if c.corp_cls in markets]
    universe.sort(key=lambda c: (c.corp_cls, c.stock_code))
    counts = {m: sum(1 for c in universe if c.corp_cls == m) for m in markets}
    log.info("유니버스 확정: %s (합계 %d개사)",
             ", ".join(f"{MARKET_NAMES.get(k, k)} {v}개" for k, v in counts.items()), len(universe))
    return universe


def save_universe(corps: list[Corp], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNIVERSE_COLUMNS)
        writer.writeheader()
        for corp in corps:
            writer.writerow({k: v for k, v in asdict(corp).items() if k in UNIVERSE_COLUMNS})


def load_universe(path: Path) -> list[Corp]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [Corp(**{k: (row.get(k) or "") for k in UNIVERSE_COLUMNS}) for row in csv.DictReader(fh)]
