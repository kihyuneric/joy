"""실행 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILES = (".env", ".env.local")


def load_dotenv(root: Path) -> None:
    """의존성 없이 .env 파일의 KEY=VALUE 를 os.environ 에 채운다(기존 값 우선)."""
    for name in ENV_FILES:
        path = root / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.split(" #", 1)[0].strip().strip("'\"")
            os.environ.setdefault(key, value)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    api_key: str = ""
    cache_dir: Path = Path("data/cache")
    out_dir: Path = Path("data/out")
    rate_limit: float = 8.0        # 초당 요청 수 상한
    max_workers: int = 8           # 동시 요청 수
    daily_quota: int = 20000       # Open DART 일일 호출 한도
    timeout: float = 30.0
    max_retries: int = 4
    multi_chunk_size: int = 100    # fnlttMultiAcnt 한 번에 조회할 회사 수
    markets: tuple[str, ...] = ("Y", "K")   # Y=유가증권(코스피), K=코스닥
    use_cache: bool = True
    limit: int = 0                 # 0 이면 전체, 그 외에는 테스트용 상위 N개 기업만
    emp_report_codes: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        root = root or Path.cwd()
        load_dotenv(root)
        return cls(
            api_key=os.environ.get("DART_API_KEY", "").strip(),
            cache_dir=Path(os.environ.get("KRFIN_CACHE_DIR", "data/cache")),
            out_dir=Path(os.environ.get("KRFIN_OUT_DIR", "data/out")),
            rate_limit=_env_float("KRFIN_RATE_LIMIT", 8.0),
            max_workers=_env_int("KRFIN_MAX_WORKERS", 8),
            daily_quota=_env_int("KRFIN_DAILY_QUOTA", 20000),
        )

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
