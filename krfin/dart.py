"""Open DART API 클라이언트.

- 초당 요청 수 제한 + 재시도(지수 백오프)
- 응답 디스크 캐시 → 중단 후 재실행 시 이미 받은 구간은 호출하지 않음
- 일일 호출 한도(기본 20,000건) 추적
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://opendart.fss.or.kr/api"

# Open DART 공통 응답 상태 코드
STATUS_OK = "000"
STATUS_NO_DATA = "013"          # 조회된 데이터가 없음 → 정상적인 '빈 결과'
STATUS_RATE_LIMITED = "020"     # 요청 제한 초과
STATUS_MAINTENANCE = "800"      # 서비스 점검
STATUS_MESSAGES = {
    "000": "정상",
    "010": "등록되지 않은 인증키입니다",
    "011": "사용할 수 없는 인증키입니다",
    "012": "접근할 수 없는 IP 입니다",
    "013": "조회된 데이터가 없습니다",
    "014": "파일이 존재하지 않습니다",
    "020": "요청 제한을 초과하였습니다 (일일 20,000건)",
    "021": "조회 가능한 회사 개수가 초과하였습니다 (최대 100건)",
    "100": "필드의 부적절한 값입니다",
    "101": "부적절한 접근입니다",
    "800": "시스템 점검으로 인한 서비스가 중지 중입니다",
    "900": "정의되지 않은 오류가 발생하였습니다",
    "901": "사용자 계정의 개인정보 보유기간이 만료되었습니다",
}

# 재시도해도 의미가 없는(설정을 고쳐야 하는) 상태 코드
FATAL_STATUSES = {"010", "011", "012", "100", "101", "901"}


class DartError(RuntimeError):
    def __init__(self, status: str, message: str = "", endpoint: str = ""):
        self.status = status
        self.message = message or STATUS_MESSAGES.get(status, "")
        self.endpoint = endpoint
        super().__init__(f"[{endpoint or 'dart'}] status={status} {self.message}")


class QuotaExceeded(RuntimeError):
    pass


class RateLimiter:
    """초당 N건으로 요청을 고르게 흘려보내는 단순 토큰 게이트."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if wait:
            time.sleep(wait)


class DartClient:
    def __init__(self, settings):
        if not settings.api_key:
            raise DartError("010", "DART_API_KEY 가 비어 있습니다. .env 를 확인하세요.")
        self.s = settings
        self.cache_dir = Path(settings.cache_dir) / "api"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = RateLimiter(settings.rate_limit)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "krfin/0.1 (+https://opendart.fss.or.kr)"
        self._lock = threading.Lock()
        self.calls_made = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ cache
    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        payload = json.dumps(
            {"e": endpoint, "p": {k: v for k, v in sorted(params.items()) if k != "crtfc_key"}},
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        bucket = self.cache_dir / endpoint.replace(".", "_") / digest[:2]
        bucket.mkdir(parents=True, exist_ok=True)
        return bucket / f"{digest}.json"

    def _read_cache(self, path: Path) -> dict | None:
        if not (self.s.use_cache and path.is_file()):
            return None
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        tmp.replace(path)

    # ------------------------------------------------------------------ calls
    def _count_call(self) -> None:
        with self._lock:
            self.calls_made += 1
            if self.s.daily_quota and self.calls_made > self.s.daily_quota:
                raise QuotaExceeded(
                    f"일일 호출 한도({self.s.daily_quota}건)에 도달했습니다. "
                    "내일 다시 실행하면 캐시된 구간은 건너뛰고 이어서 수집합니다."
                )

    def get_json(self, endpoint: str, **params: Any) -> dict:
        """JSON 엔드포인트 호출. 상태 013(자료 없음)은 예외 없이 그대로 돌려준다."""
        params = {k: v for k, v in params.items() if v not in (None, "")}
        cache_path = self._cache_path(endpoint, params)
        cached = self._read_cache(cache_path)
        if cached is not None:
            with self._lock:
                self.cache_hits += 1
            return cached

        url = f"{BASE_URL}/{endpoint}"
        query = dict(params, crtfc_key=self.s.api_key)
        last_exc: Exception | None = None

        for attempt in range(self.s.max_retries + 1):
            self.limiter.acquire()
            self._count_call()
            try:
                resp = self.session.get(url, params=query, timeout=self.s.timeout)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, json.JSONDecodeError) as exc:
                last_exc = exc
                self._sleep_backoff(attempt, f"{endpoint}: {exc}")
                continue

            status = str(data.get("status", ""))
            if status in (STATUS_OK, STATUS_NO_DATA):
                self._write_cache(cache_path, data)
                return data
            if status in FATAL_STATUSES:
                raise DartError(status, data.get("message", ""), endpoint)
            # 020(호출 초과) / 800(점검) / 900(미정의) → 재시도
            last_exc = DartError(status, data.get("message", ""), endpoint)
            self._sleep_backoff(attempt, str(last_exc))

        raise DartError("900", f"재시도 후에도 실패: {last_exc}", endpoint)

    def get_bytes(self, endpoint: str, **params: Any) -> bytes:
        """corpCode.xml 처럼 zip 바이너리를 돌려주는 엔드포인트."""
        params = {k: v for k, v in params.items() if v not in (None, "")}
        cache_path = self._cache_path(endpoint, params).with_suffix(".bin")
        if self.s.use_cache and cache_path.is_file():
            with self._lock:
                self.cache_hits += 1
            return cache_path.read_bytes()

        url = f"{BASE_URL}/{endpoint}"
        query = dict(params, crtfc_key=self.s.api_key)
        last_exc: Exception | None = None
        for attempt in range(self.s.max_retries + 1):
            self.limiter.acquire()
            self._count_call()
            try:
                resp = self.session.get(url, params=query, timeout=self.s.timeout * 3)
                resp.raise_for_status()
            except requests.RequestException as exc:
                last_exc = exc
                self._sleep_backoff(attempt, f"{endpoint}: {exc}")
                continue
            body = resp.content
            # 오류일 때는 zip 대신 XML 로 status 를 돌려준다.
            if body[:2] != b"PK":
                status = _status_from_xml(body)
                if status:
                    if status in FATAL_STATUSES:
                        raise DartError(status, "", endpoint)
                    last_exc = DartError(status, "", endpoint)
                    self._sleep_backoff(attempt, str(last_exc))
                    continue
            cache_path.write_bytes(body)
            return body
        raise DartError("900", f"재시도 후에도 실패: {last_exc}", endpoint)

    def _sleep_backoff(self, attempt: int, reason: str) -> None:
        delay = min(2 ** attempt, 32)
        log.warning("재시도 %d회차 (%.0fs 대기): %s", attempt + 1, delay, reason)
        time.sleep(delay)

    def stats(self) -> str:
        return f"API 호출 {self.calls_made}건 / 캐시 적중 {self.cache_hits}건"


def _status_from_xml(body: bytes) -> str:
    import re

    m = re.search(rb"<status>(\d+)</status>", body)
    return m.group(1).decode() if m else ""
