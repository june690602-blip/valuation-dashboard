"""간단한 파일 캐시.

- DataFrame은 parquet, 그 외(dict/list)는 json으로 저장
- ttl이 지나면 다시 받아오되, 원천 API가 실패하면 만료된 캐시라도 반환(stale-ok)
- streamlit 없이도 동작해야 하므로 st.cache_data에 의존하지 않는다
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from functools import wraps
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"

# 키마다 락 하나 — **같은 것을 동시에 두 번 만들지 않는다**(single-flight).
#
# 왜 필요했나: 서버는 `ThreadingHTTPServer`라 요청마다 스레드가 뜨고, 기동 직후엔
# 예열 스레드(`server._warm_coefficients`)가 같은 함수를 부른다. 락이 없으면 캐시가
# 빈 상태에서 **예열 + 방문자 N명이 각자 전 종목 수집을 시작한다** — 한국 2,871종목을
# 12 워커로 긁는 일이 동시에 여러 벌 돈다. yfinance는 평상시에도 성공률 62%(레이트리밋)라
# 부하가 겹치면 실패·재시도가 쌓여 **혼자 돌 때 58초짜리가 몇십 분이 된다.**
#
# 락은 **키 단위**다. 전역 락 하나로 묶으면 KR 계수를 만드는 동안 무관한 AI 피어 캐시까지
# 멈춘다 — 고치려던 것보다 나쁜 병목을 만든다.
#
# ⚠ 한계: 프로세스 안에서만 유효하다. 인스턴스를 여러 개로 늘리면 인스턴스마다 한 벌씩
# 만든다(파일 락이 아니다). 지금 배포는 단일 인스턴스라 이걸로 충분하고, 늘릴 때는
# 계수를 저장소에 구워 넣는 쪽(이슈 #131의 1번)이 답이지 파일 락이 아니다.
_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = _KEY_LOCKS[key] = threading.Lock()
        return lock


def _key(name: str, args, kwargs) -> str:
    raw = json.dumps([args, kwargs], default=str, ensure_ascii=False, sort_keys=True)
    return f"{name}_{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def _is_fresh(path: Path, ttl_hours: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600


def file_cache(name: str, ttl_hours: float = 24.0, validate=None):
    """함수 결과를 파일로 캐시하는 데코레이터. 반환형은 DataFrame 또는 json 직렬화 가능 객체.

    validate: 결과가 '실질이 있는지' 검사하는 콜러블. 무료 API가 레이트리밋으로
    빈 값을 성공처럼 반환할 때가 있는데, 검사에 실패한 결과는 캐시에 저장하지 않고
    (오염 방지) 기존 캐시(만료 포함)가 검사를 통과하면 그것을 대신 반환한다.
    """

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            key = _key(name, args, kwargs)
            pq, js = CACHE_DIR / f"{key}.parquet", CACHE_DIR / f"{key}.json"

            def fresh_hit():
                """(맞았나, 값) — 살아 있고 검사를 통과하는 캐시."""
                for path in (pq, js):
                    if _is_fresh(path, ttl_hours):
                        cached = _load(path)
                        if validate is None or validate(cached):
                            return True, cached
                        break  # 과거에 저장된 빈 캐시 → 무시하고 다시 받아온다
                return False, None

            hit, cached = fresh_hit()
            if hit:
                return cached
            # 여기부터가 '만들어야 하는' 경로다. 락을 잡고 **다시 확인한다** —
            # 기다리는 동안 앞선 호출자가 이미 채웠으면 그걸 쓰면 되고, 그것이
            # 이 락이 노리는 전부다(두 번 만들지 않는 것).
            with _lock_for(key):
                hit, cached = fresh_hit()
                if hit:
                    return cached
                return _build(fn, args, kwargs, pq, js, validate)

        return wrapper

    return deco


def _build(fn, args, kwargs, pq: Path, js: Path, validate):
    """원천을 실제로 부르고 저장한다. **키 락을 잡은 채** 불린다."""
    try:
        result = fn(*args, **kwargs)
    except Exception:
        # 원천 실패 시 만료된 캐시라도 사용
        for path in (pq, js):
            if path.exists():
                return _load(path)
        raise
    if validate is not None and not validate(result):
        # 빈 결과 — 저장하지 않고, 검사를 통과하는 이전 캐시가 있으면 그걸 반환
        for path in (pq, js):
            if path.exists():
                old = _load(path)
                if validate(old):
                    return old
        return result
    _save(result, pq, js)
    return result


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
