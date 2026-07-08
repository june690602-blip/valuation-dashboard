"""간단한 파일 캐시.

- DataFrame은 parquet, 그 외(dict/list)는 json으로 저장
- ttl이 지나면 다시 받아오되, 원천 API가 실패하면 만료된 캐시라도 반환(stale-ok)
- streamlit 없이도 동작해야 하므로 st.cache_data에 의존하지 않는다
"""
from __future__ import annotations

import hashlib
import json
import time
from functools import wraps
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


def _key(name: str, args, kwargs) -> str:
    raw = json.dumps([args, kwargs], default=str, ensure_ascii=False, sort_keys=True)
    return f"{name}_{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def _is_fresh(path: Path, ttl_hours: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600


def file_cache(name: str, ttl_hours: float = 24.0):
    """함수 결과를 파일로 캐시하는 데코레이터. 반환형은 DataFrame 또는 json 직렬화 가능 객체."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            key = _key(name, args, kwargs)
            pq, js = CACHE_DIR / f"{key}.parquet", CACHE_DIR / f"{key}.json"

            for path in (pq, js):
                if _is_fresh(path, ttl_hours):
                    return _load(path)
            try:
                result = fn(*args, **kwargs)
            except Exception:
                # 원천 실패 시 만료된 캐시라도 사용
                for path in (pq, js):
                    if path.exists():
                        return _load(path)
                raise
            _save(result, pq, js)
            return result

        return wrapper

    return deco


def _load(path: Path):
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _save(result, pq: Path, js: Path):
    try:
        if isinstance(result, pd.DataFrame):
            result.to_parquet(pq)
        else:
            js.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass  # 캐시 저장 실패는 치명적이지 않음
