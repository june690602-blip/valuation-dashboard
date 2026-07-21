"""배포 후 캐시 프리워밍 — 쇼케이스 종목·금리 데이터를 미리 받아 file_cache를 채운다.

첫 방문자(채용담당자 등)가 느린 첫 조회를 겪지 않게 한다:
- 종목 분석: 피어 지표 병렬 수집(수 초)
- 금리 이력: 국고채 3년치 네이버 페이지 병렬 수집(수 초)

사용:
    python scripts/prewarm_cache.py           # 전체
    python scripts/prewarm_cache.py KR 005930  # 특정 종목만

배포 직후 1회, 또는 주기적(캐시 TTL: 분석 30분·금리 6시간)으로 돌리면 좋다.
네트워크 실패는 건너뛰고 계속한다(프리워밍은 편의 기능).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 홈·검색 예시로 노출되는 대표 종목
SHOWCASE = [("KR", "005930"), ("KR", "035420"), ("KR", "105560"), ("US", "AAPL")]
TENORS = [1, 2, 3, 5, 10, 20, 30]


def warm_stock(market: str, query: str) -> None:
    from src.web.serialize import analyze
    t0 = time.time()
    try:
        analyze(market, query, include_news=False)
        print(f"  ✓ {market} {query}  ({time.time() - t0:.1f}s)")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {market} {query}: {type(e).__name__}: {e}")


def warm_bonds() -> None:
    from src.web.serialize import bond_data, bond_history
    try:
        bond_data()
        print("  ✓ bond_data (수익률곡선·기준금리·뉴스)")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ bond_data: {e}")
    for market in ("KR", "US"):
        t0 = time.time()
        for tenor in TENORS:
            try:
                bond_history(market, tenor)
            except Exception:  # noqa: BLE001
                pass
        print(f"  ✓ {market} 금리 이력 {len(TENORS)}개 테너  ({time.time() - t0:.1f}s)")


def main() -> None:
    args = sys.argv[1:]
    started = time.time()
    if len(args) >= 2:
        warm_stock(args[0].upper(), args[1])
    else:
        print("종목 프리워밍…")
        for market, query in SHOWCASE:
            warm_stock(market, query)
        print("금리 프리워밍…")
        warm_bonds()
    print(f"완료 — 총 {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
