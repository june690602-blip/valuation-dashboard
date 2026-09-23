"""첫 방문자가 콜드를 겪지 않게 미리 받아 둔다 (ADR-0048).

**이 파일이 있는 이유는 Render의 파일시스템이 휘발성이기 때문이다.** 디스크를 붙이지
않은 서비스는 *"배포·재시작할 때마다 로컬 파일 변경이 사라진다"*(Render 공식 문서).
즉 `data/cache/`는 **배포할 때마다 통째로 빈다.** 실측한 완전 콜드는 **12.33초**이고
(`scripts/check_load_timing.py`) 그 첫 12초는 언제나 방문자가 낸다.

`server._warm_coefficients`가 이미 같은 판단을 해 뒀다 — *"하루 한 번 누군가는 반드시
112초를 낸다. 그 한 명이 방문자가 아니라 서버가 되게 한다."* 여기서는 그 대상을 계수
하나에서 **쇼케이스 종목과 금리**까지 넓힌다.

**목록이 한 벌이어야 한다.** 예전에는 이 목록이 `scripts/prewarm_cache.py`에만 있어서
서버가 예열하려면 같은 목록을 한 벌 더 적어야 했고, 두 벌은 반드시 갈라진다. 스크립트는
이제 이 모듈의 얇은 CLI다.

⚠ **예열은 편의 기능이라 절대 실패를 올리지 않는다.** 무료 데이터라 결측이 흔하고,
예열이 서버 기동을 막으면 고치려던 것보다 나쁘다.
"""
from __future__ import annotations

import time
from typing import Callable

# 홈·검색 예시로 노출되는 대표 종목. 여기 있는 종목이 곧 "첫 방문자가 누를 것"이다.
SHOWCASE = [("KR", "005930"), ("KR", "035420"), ("KR", "105560"), ("US", "AAPL")]
TENORS = [1, 2, 3, 5, 10, 20, 30]

Log = Callable[[str], None]

# ⚠ 로그 문자는 **cp949로 인코딩 가능해야 한다.** 이 코드는 이제 서버 예열 스레드에서
# 도는데(`server._warm_cache`), `대시보드실행.bat`으로 여는 윈도우 콘솔이 cp949다.
# 예전에 여기 `✓`/`✗`가 있었고 CLI에서 `UnicodeEncodeError`로 터졌다 — 스레드에서
# 터지면 **첫 종목 뒤로 예열이 통째로 조용히 죽는다.** `tests/test_prewarm.py`가 지킨다.
OK, BAD = "[완료]", "[실패]"


def mem_note() -> str:
    """현재 메모리와 지금까지의 최고치를 로그 조각으로. 리눅스가 아니면 빈 문자열.

    **왜 남기나.** 이 예열이 기동 중 **한 번에** 고점을 찍는다 — 운영 실측으로 가동
    3분에 이미 최고 515MB였고 인스턴스 한도가 512MiB다. 그런데 어느 단계에서 치솟는지는
    밖에서 볼 방법이 없었다(대시보드는 분 단위 그래프이고, 예열은 100초에 끝난다).
    느린 원천을 한 줄 남긴 것과 같은 이유다(ADR-0052) — **없는 표시는 몇 시간을 먹는다.**

    `/proc`이 없는 윈도우에서는 조용히 빈 문자열이라 로컬 로그 모양이 바뀌지 않는다.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            cur = hwm = None
            for line in f:
                if line.startswith("VmRSS:"):
                    cur = int(line.split()[1]) // 1024
                elif line.startswith("VmHWM:"):
                    hwm = int(line.split()[1]) // 1024
    except OSError:
        return ""
    if cur is None:
        return ""
    return f" · 메모리 {cur}MB(최고 {hwm}MB)" if hwm is not None else f" · 메모리 {cur}MB"


def warm_stock(market: str, query: str, log: Log = print) -> bool:
    """종목 하나의 분석 캐시를 채운다. 성공 여부를 돌려주되 예외는 올리지 않는다.

    `include_news=False`인 이유: 뉴스는 6시간 캐시에 헤드라인이라 미리 받아 둘 값어치가
    적고, 예열이 길어지면 그만큼 원천을 오래 두드린다.
    """
    t0 = time.time()
    try:
        from src.web.serialize import analyze
        analyze(market, query, include_news=False)
    except Exception as e:  # noqa: BLE001 — 예열 실패가 무엇도 막으면 안 된다
        log(f"  {BAD} {market} {query}: {type(e).__name__}: {e}")
        return False
    log(f"  {OK} {market} {query}  ({time.time() - t0:.1f}s){mem_note()}")
    return True


def warm_bonds(log: Log = print) -> None:
    """수익률곡선·기준금리와 만기별 이력을 채운다."""
    from src.web.serialize import bond_data, bond_history
    try:
        bond_data()
        log(f"  {OK} bond_data (수익률곡선·기준금리·뉴스){mem_note()}")
    except Exception as e:  # noqa: BLE001
        log(f"  {BAD} bond_data: {e}")
    for market in ("KR", "US"):
        t0 = time.time()
        for tenor in TENORS:
            try:
                bond_history(market, tenor)
            except Exception:  # noqa: BLE001
                pass
        log(f"  {OK} {market} 금리 이력 {len(TENORS)}개 테너  ({time.time() - t0:.1f}s){mem_note()}")


def warm_all(log: Log = print) -> None:
    """쇼케이스 전부 — 한 종목이 실패해도 나머지는 계속한다."""
    log("종목 프리워밍…")
    for market, query in SHOWCASE:
        warm_stock(market, query, log)
    log("금리 프리워밍…")
    warm_bonds(log)
