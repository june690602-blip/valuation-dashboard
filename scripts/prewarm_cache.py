"""배포 후 캐시 프리워밍 — 쇼케이스 종목·금리 데이터를 미리 받아 file_cache를 채운다.

    python scripts/prewarm_cache.py            # 전체
    python scripts/prewarm_cache.py KR 005930  # 특정 종목만

**서버가 기동 직후 같은 일을 자동으로 한다**(ADR-0048 · `server._warm_cache`). 이 스크립트는
손으로 돌리고 싶을 때(특정 종목만 · 배포 밖에서 캐시를 채울 때) 쓰는 CLI다. 목록과 실제
동작은 `src/web/prewarm.py` 한 곳에 있다 — 두 벌로 적으면 반드시 갈라진다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")   # 다른 진단 스크립트와 같은 규약

from src.web.prewarm import warm_all, warm_stock  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    started = time.time()
    if len(args) >= 2:
        warm_stock(args[0].upper(), args[1])
    else:
        warm_all()
    print(f"완료 — 총 {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
