"""전 종목 배수 스냅숏 — ①의 회귀 계수(ADR-0014)를 만들 학습 표본.

한국은 두 원천이 **정확히 상보적**이다:
    네이버   → per·pbr·roe   (yfinance는 한국 종목의 per·pbr을 주지 않는다: 실측 0%)
    yfinance → psr·ev_ebitda (네이버에 없다)
미국은 yfinance 하나로 넷 다 나온다.

yfinance 전 종목 수집은 불안정하다 — 한국 2,688종목 시도에서 1,655종목(62%)만 성공했고
나머지는 401 Invalid Crumb(레이트리밋)이었다. 그래서 **부분 수집을 정상으로 취급**하고,
표본이 모자라는 다리만 계수를 만들지 않는다(호출부가 피어 중앙값으로 폴백한다).
한 원천이 죽어도 다른 원천 값은 살린다 — try를 원천별로 따로 두는 이유다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .cache import file_cache

UNIVERSE_WORKERS = 12


def _collect(rows, one):
    """유니버스 전 종목을 병렬로 훑으며 **진행을 보고한다**.

    이 단계가 분석에서 가장 오래 걸린다 — 실측 131초짜리 첫 분석 중 **112.6초**가 여기였다
    (피어 수집은 0.1초). 그런데 진행 표시가 없어서 화면은 그동안 "피어 수집 14/14"를
    띄우고 있었다. **범인을 잘못 가리키는 표시는 없는 것보다 나쁘다** — 기다리는 사람이
    무엇을 기다리는지 모른다. 계수 캐시(24시간)가 살아 있으면 이 함수는 아예 불리지 않는다.
    """
    from concurrent.futures import as_completed
    from .progress import report

    total = len(rows)
    out, done = [], 0
    report("업종 회귀 계수", 0, total)
    with ThreadPoolExecutor(UNIVERSE_WORKERS) as ex:
        futures = [ex.submit(one, r) for r in rows]
        for fut in as_completed(futures):
            out.append(fut.result())
            done += 1
            if done % 20 == 0 or done == total:
                report("업종 회귀 계수", done, total)
    return pd.DataFrame(out)


def _num(v):
    """숫자로 못 읽히거나 유한하지 않으면 None. 무료 데이터에는 둘 다 섞인다."""
    x = pd.to_numeric(v, errors="coerce")
    try:
        return None if not np.isfinite(x) else x
    except TypeError:
        return None


# 원천 호출을 모듈 수준 얇은 래퍼로 둔다 — 테스트가 patch할 수 있고, 지연 임포트라
# 서버 기동에 임포트 비용을 얹지 않는다.
def _kr_listing():
    from .universe import get_kr_listing
    return get_kr_listing()


def _naver_fundamental(code: str) -> dict:
    from .naver import fetch_naver_fundamental
    return fetch_naver_fundamental(code)


def _info_metrics(ticker: str) -> dict:
    from .base import fetch_info_metrics
    return fetch_info_metrics(ticker)


def _us_universe():
    from .universe import get_sp1500
    return get_sp1500()


def _kr_ticker(code: str, market: str) -> str:
    from .universe import yahoo_ticker_kr
    return yahoo_ticker_kr(code, market)


def collect_kr() -> pd.DataFrame:
    """한국 보통주 전 종목의 per·pbr·psr·ev_ebitda·roe·mcap·sector."""
    listing = _kr_listing()
    pool = listing[listing["is_common"] & listing["Sector"].notna()
                   & (listing["Marcap"] > 0)]

    def one(row):
        base = {"code": row.Code, "sector": row.Sector, "mcap": float(row.Marcap),
                "per": None, "pbr": None, "psr": None, "ev_ebitda": None, "roe": None}
        # 원천마다 try를 따로 둔다 — yfinance가 레이트리밋으로 죽어도 네이버 값은 남는다
        try:
            nv = _naver_fundamental(row.Code)
            base.update(per=_num(nv.get("per")), pbr=_num(nv.get("pbr")),
                        roe=_num(nv.get("roe_approx")))
        except Exception:
            pass
        try:
            yv = _info_metrics(_kr_ticker(row.Code, row.Market))
            base.update(psr=_num(yv.get("psr")), ev_ebitda=_num(yv.get("ev_ebitda")))
        except Exception:
            pass
        return base

    return _collect(list(pool.itertuples()), one)


def collect_us() -> pd.DataFrame:
    """S&P 1500의 per·pbr·psr·ev_ebitda·roe·mcap·sector."""
    def one(row):
        base = {"code": row.Symbol, "sector": row.Sector, "mcap": None,
                "per": None, "pbr": None, "psr": None, "ev_ebitda": None, "roe": None}
        try:
            v = _info_metrics(row.Symbol)
            base.update(mcap=_num(v.get("market_cap")), per=_num(v.get("per")),
                        pbr=_num(v.get("pbr")), psr=_num(v.get("psr")),
                        ev_ebitda=_num(v.get("ev_ebitda")), roe=_num(v.get("roe")))
        except Exception:
            pass
        return base

    return _collect(list(_us_universe().itertuples()), one)


LEGS = ("pbr", "per", "psr", "ev_ebitda")

# 다리별 유효 범위. 무료 데이터에는 배수가 터무니없이 큰 값이 섞이는데(적자 직전 EPS 등)
# 로그를 취하면 그 하나가 계수를 끌고 간다. 상·하한은 실측 분포를 보고 정한 판단값이다.
LEG_BOUNDS = {"pbr": (0.05, 20.0), "per": (1.0, 200.0),
              "psr": (0.02, 50.0), "ev_ebitda": (0.5, 100.0)}


def build_coefficients(snapshot: pd.DataFrame) -> dict:
    """스냅숏 → {다리: 계수}. 표본이 모자라는 다리는 넣지 않는다."""
    from ..analysis.warranted import fit_leg

    out = {}
    for leg in LEGS:
        if leg not in snapshot.columns:
            continue
        lo, hi = LEG_BOUNDS[leg]
        v = pd.to_numeric(snapshot[leg], errors="coerce")
        d = snapshot.assign(multiple=v)[(v > lo) & (v < hi)]
        coef = fit_leg(d, leg=leg)
        if coef:
            out[leg] = coef
    return out


def _coefficients_usable(d) -> bool:
    """캐시된 계수가 지금 코드가 기대하는 모양인가.

    껍데기만 보면(`"pbr" in d`) **스키마가 바뀐 뒤 남아 있는 24시간짜리 옛 캐시가
    그대로 통과한다.** `warranted_multiple`이 그걸 받아 예외를 내면 판정 전체가
    무너지므로, 여기서 다리마다 필수 키를 확인해 아예 쓰지 않는다.
    캐시 키에 버전(`_v1`)이 있지만 그것만으로는 부족하다 — 버전을 올리는 걸 잊어도
    이 검사가 막는다.
    """
    from ..analysis.warranted import REQUIRED_COEF_KEYS

    if not isinstance(d, dict) or "pbr" not in d:
        return False
    return all(isinstance(v, dict) and REQUIRED_COEF_KEYS <= v.keys()
               for v in d.values())


# 밤에 GitHub Actions가 구워 두는 계수 (이슈 #131). **main이 아니라 별도 브랜치**라
# 이 파일이 갱신돼도 배포가 트리거되지 않고 main 히스토리도 안 더러워진다.
# 브랜치는 orphan + force-push라 커밋이 늘 하나다.
COEF_BRANCH = "data-coefficients"
COEF_BASE_URL = (f"https://raw.githubusercontent.com/june690602-blip/"
                 f"valuation-dashboard/{COEF_BRANCH}")
COEF_FETCH_TIMEOUT = 8.0     # 여기서 오래 기다리면 고치려던 것(첫 방문자 대기)을 재현한다


def _from_repo(market: str) -> dict | None:
    """저장소에 구워진 계수를 받아온다. 실패하면 None — 그러면 직접 수집한다.

    **이 경로가 이슈 #131의 핵심이다.** 전 종목 수집은 혼자 돌 때도 실측 58초인데,
    이건 35KB짜리 JSON 한 번이라 0.1초다. 게다가 yfinance 레이트리밋(성공률 62%)을
    아예 안 탄다 — 밤에 CI가 대신 맞아 준다.

    실패를 **조용히 삼키고 None을 준다**: 깃허브가 죽어도 판정이 멈추면 안 되고,
    물러날 곳(직접 수집)이 이미 있다. 즉 이 함수는 **빨라지는 길이지 의존성이 아니다.**

    `COEF_BASE_URL`을 빈 값으로 두면 통째로 끈다(오프라인 개발·테스트).
    """
    if not COEF_BASE_URL:
        return None
    import json as _json
    import urllib.request

    url = f"{COEF_BASE_URL}/{market.upper()}.json"
    try:
        with urllib.request.urlopen(url, timeout=COEF_FETCH_TIMEOUT) as r:
            if r.status != 200:
                return None
            got = _json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    # 받아온 것도 **같은 검사를 통과해야 한다.** 브랜치에 깨진 파일이 올라가면
    # 그것이 24시간 캐시로 저장돼 그날 전 종목이 피어 중앙값 폴백을 타게 된다.
    return got if _coefficients_usable(got) else None


@file_cache("warranted_coef_v2", ttl_hours=24, validate=_coefficients_usable)
def get_coefficients(market: str) -> dict:
    """시장의 다리별 계수 (24시간 캐시).

    읽는 순서는 셋이고 **아래로 갈수록 느리다**:

        1. 로컬 파일 캐시 (24시간)          — 즉시
        2. 저장소에 구워진 파일 (`_from_repo`) — 0.1초 · 밤에 CI가 만들어 둔 것
        3. 전 종목 직접 수집                 — 실측 58초 · 레이트리밋에 걸리면 더

    3이 이슈 #131이 없애려던 것이다. 2가 들어오면서 **사용자가 3을 겪는 일은
    깃허브까지 죽었을 때뿐**이 됐다. 2는 빨라지는 길이지 의존성이 아니다 — 실패하면
    조용히 3으로 내려간다.

    ⚠ **밤에 도는 빌더는 이 함수를 부르면 안 된다** — 2번에서 어제 파일을 그대로
    돌려받아 계수가 영원히 갱신되지 않는다. `scripts/build_coefficients.py`가
    수집·적합을 직접 부르는 이유다.

    캐시하는 것은 **전 종목 테이블이 아니라 계수**다(숫자 약 70개). 적정 배수는
    `계수 × 자사 값`인데 계수는 시장 구조라 느리게 변하고 자사 시총·ROE는 매일 변한다.
    계수만 저장하면 어제 계수를 써도 오늘 주가 변동이 그대로 반영된다.

    file_cache가 원천 실패 시 만료된 캐시를 반환하므로(stale-ok) 장애에도 판정이 멈추지
    않는다. 다만 그 만료 캐시도 `_coefficients_usable`을 통과해야 한다 — 스키마가 어긋난
    옛 캐시를 되살려 쓰면 `warranted_multiple`이 계산 불가로 물러나고, 결국 그날 전
    종목이 피어 중앙값 폴백을 타게 된다. 조용히 그러느니 처음부터 안 쓰는 게 낫다.
    """
    seeded = _from_repo(market)
    if seeded is not None:
        return seeded
    snap = collect_kr() if market.upper() == "KR" else collect_us()
    return build_coefficients(snap)


def coefficients_or_none(market: str) -> dict | None:
    """계수를 읽되 어떤 실패에도 판정을 멈추지 않는다 — 실패하면 피어 중앙값 폴백이다."""
    try:
        return get_coefficients(market) or None
    except Exception:
        return None
