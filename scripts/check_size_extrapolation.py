"""규모 외삽 진단 — 회귀 적정배수가 대형주에서 무엇을 하고 있나 (ADR-0014 후속).

    python scripts/check_size_extrapolation.py          # KR·US 둘 다
    python scripts/check_size_extrapolation.py KR

네트워크가 필요하다(전 종목 스냅숏). CI가 아니라 `check_warranted.py`와 같은 수동 계열이다.
per-종목 조회는 file_cache를 타므로 두 번째 실행부터는 빠르다.

## 무엇을 의심하는가

`warranted_multiple`의 외삽 가드는 **아래쪽만** 있다 — `too_small = mcap < mcap_min / 5`
뿐이고 `too_large`가 없다. `fit_leg`이 `mcap_max`를 계산해 캐시에 넣지만 저장소 어디서도
읽지 않는다(정의 2곳 + 테스트 1곳이 전부).

그런데 학습 표본이 **그 시장의 전 종목**이라(한국 KRX 전체 · 미국 S&P 1500) '범위 밖'은
애초에 드물 수밖에 없다. 삼성전자도 애플도 학습 표본 **안**에 있다. 그러면 위험은
'범위 밖 외삽'이 아니라 **범위 안 레버리지**다 — 로그선형을 가정했는데 대형 구간에서
기울기가 꺾이면, 꼬리에 있는 소수 종목의 적정배수를 β가 체계적으로 밀어 올린다.
그리고 `size_adj`는 학습 범위가 아니라 **섹터 중앙값 시총 대비**로 계산되므로
(`exp(β·(log mcap − log sector_median_mcap)) − 1`) 범위 안에서도 얼마든지 커진다.

**그래서 [1]과 [3]이 서로 다른 가설이다.** [1]이 0곳인데 [3]이 크면 "가드를 다는 것"은
헛짚은 처방이고, 고쳐야 할 것은 함수형(로그선형)이다.

## 잰 것 네 가지 — [3]이 핵심이다

    [1] 학습 시총 범위 대비 유니버스의 위치 — 상한 가드가 실제로 몇 종목에 걸리나
    [2] size_adj 분포 — "빅테크에 +400%가 붙는다"가 참인가
    [3] 시총 십분위별 잔차 — 로그선형 가정이 대형 구간에서 깨지나  ← 핵심
    [4] 대표 종목 실측 — 삼성전자·SK하이닉스·NAVER / AAPL·MSFT·NVDA

[3]의 잔차는 **표본 내(in-sample)**다. `check_warranted.py`의 leave-one-out과 목적이
다르다 — 저기는 '얼마나 맞히나'(정확도)를 재고, 여기는 '어디서 체계적으로 틀리나'
(함수형 오설정)를 잰다. 오설정은 표본 내 잔차의 **구간별 부호**로 드러나므로 LOO가
필요 없다. 오히려 LOO는 꼬리에서 분산이 커져 부호를 흐린다.

종료 코드 1은 '문제 있음'이다(`check_dcf_viability.py`와 같은 관례).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.warranted import (EXTRAPOLATION_LIMIT, fit_leg,  # noqa: E402
                                    size_slope, size_term,
                                    warranted_multiple)
from src.data.universe_multiples import (LEG_BOUNDS, collect_kr,   # noqa: E402
                                         collect_us)

# 십분위 평균 잔차가 이만큼 벌어지면 '구간별로 체계적으로 틀린다'고 본다.
# 로그 스케일 0.15는 원 스케일 ±16%다. 추정값이 아니라 판단값이다
# (`PBR_GATE`·`BAND_CORR_LIMIT`와 같은 성격) — 그래서 값을 함께 찍어 읽는 사람이
# 직접 판단할 수 있게 한다.
RESID_LIMIT = 0.15

# size_adj가 이보다 크면 '규모 하나로 배수를 몇 배로 만든다'고 본다. 같은 성격의 판단값.
SIZE_ADJ_LIMIT = 3.0    # +300%

# 윈저화 후보(처방 B)에서 log(시총)을 자를 분위.
WINSOR_Q = 0.99

WATCH = {
    "KR": {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
           "005380": "현대차"},
    "US": {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
           "GOOGL": "Alphabet"},
}

UNIT = {"KR": ("조원", 1e12), "US": ("$B", 1e9)}


def _prepare(snap: pd.DataFrame, leg: str):
    """(표본, 계수) — build_coefficients와 같은 방식으로 거른다.

    거르는 규칙을 여기서 다시 쓰지 않고 `LEG_BOUNDS`를 그대로 쓴다. 두 곳이 각자
    기준을 가지면 언젠가 어긋나고, 그러면 이 진단이 실제 계수와 다른 표본을 재게 된다.
    """
    if leg not in snap.columns:
        return None, None
    lo, hi = LEG_BOUNDS[leg]
    v = pd.to_numeric(snap[leg], errors="coerce")
    d = snap.assign(multiple=v)[(v > lo) & (v < hi) & (snap["mcap"] > 0)]
    d = d.dropna(subset=["multiple", "mcap", "sector"]).reset_index(drop=True)
    if len(d) < 50:
        return None, None
    return d, fit_leg(d, leg=leg)


def _predict(d: pd.DataFrame, coef: dict) -> pd.DataFrame:
    """행마다 적정배수·size_adj·잔차. 계산 불가(가드에 걸림)는 NaN으로 남긴다."""
    mult, sadj, too_small = [], [], []
    roe = d["roe"] if "roe" in d.columns else pd.Series([None] * len(d))
    for mc, sec, rv in zip(d["mcap"], d["sector"].astype(str), roe):
        w = warranted_multiple(coef, mc, sec, rv)
        mult.append(w["multiple"] if w["multiple"] is not None else np.nan)
        sadj.append(w["size_adj"] if w["size_adj"] is not None else np.nan)
        too_small.append(bool(w["too_small"]))
    out = d.assign(fitted=mult, size_adj=sadj, too_small=too_small)
    # 잔차 = log(실제) − log(적합). 음수면 회귀가 그 종목의 배수를 **과대추정**한 것이고,
    # 적정배수가 과대라는 것은 그 종목이 '저평가' 쪽으로 밀린다는 뜻이다.
    out["resid"] = np.log(out["multiple"]) - np.log(out["fitted"])
    return out


def _fmt_cap(v: float, market: str) -> str:
    label, div = UNIT[market]
    return f"{v / div:,.1f}{label}"


def _section_range(res: pd.DataFrame, coef: dict, market: str) -> int:
    """[1] 학습 범위 대비 위치 — 상한 가드가 걸릴 자리가 실제로 있나."""
    mc = res["mcap"].to_numpy(float)
    lo_cut, hi_cut = coef["mcap_min"] / EXTRAPOLATION_LIMIT, coef["mcap_max"] * EXTRAPOLATION_LIMIT
    n_small = int((mc < lo_cut).sum())
    n_over_max = int((mc > coef["mcap_max"]).sum())
    n_large = int((mc > hi_cut).sum())
    print(f"    학습 범위      {_fmt_cap(coef['mcap_min'], market)} ~ "
          f"{_fmt_cap(coef['mcap_max'], market)}   (표본 {coef['n']:,})")
    print(f"    하한 가드 발동  {n_small:>5}곳  (mcap < 하한/{EXTRAPOLATION_LIMIT:g})")
    print(f"    상한 초과      {n_over_max:>5}곳  (mcap > 상한)")
    print(f"    상한 가드 후보  {n_large:>5}곳  (mcap > 상한×{EXTRAPOLATION_LIMIT:g}) ← 가드를 달면 걸릴 수")
    return n_large


def _section_sizeadj(res: pd.DataFrame) -> float:
    """[2] size_adj 분포 — 규모 조정이 실제로 얼마나 붙나."""
    s = res["size_adj"].dropna()
    if s.empty:
        print("    size_adj 없음")
        return 0.0
    qs = s.quantile([0.50, 0.90, 0.99])
    print(f"    size_adj  중앙 {qs[0.50]:+7.1%}   p90 {qs[0.90]:+8.1%}   "
          f"p99 {qs[0.99]:+9.1%}   최대 {s.max():+10.1%}")
    over = int((s > SIZE_ADJ_LIMIT).sum())
    print(f"    +{SIZE_ADJ_LIMIT:.0%} 초과 {over}곳 ({over / len(s):.1%})")
    return float(s.max())


def _section_resid(res: pd.DataFrame, market: str) -> tuple[float, float]:
    """[3] 시총 십분위별 잔차 — 로그선형이 대형 구간에서 깨지나. **핵심 검사.**"""
    d = res.dropna(subset=["resid", "mcap"])
    if len(d) < 100:
        print("    표본 부족")
        return 0.0, 0.0
    # 동점 시총이 많으면 qcut이 실패한다 — rank로 자르면 항상 10칸이 나온다.
    dec = pd.qcut(d["mcap"].rank(method="first"), 10, labels=False)
    print(f"    {'십분위':<8}{'n':>6}{'시총 중앙':>12}{'평균 잔차':>11}{'표준오차':>10}")
    print("    " + "─" * 47)
    means = []
    for i in range(10):
        g = d[dec == i]
        m = float(g["resid"].mean())
        se = float(g["resid"].std() / np.sqrt(len(g))) if len(g) > 1 else float("nan")
        means.append(m)
        mark = "  ←" if i == 9 else ""
        print(f"    {i + 1:<8}{len(g):>6}{_fmt_cap(g['mcap'].median(), market):>12}"
              f"{m:>+11.3f}{se:>10.3f}{mark}")
    top = means[-1]
    # 최상위 1%는 십분위보다 더 꼬리다 — 십분위가 잠잠해도 여기서 갈릴 수 있다.
    p99 = d["mcap"].quantile(0.99)
    tail = d[d["mcap"] >= p99]
    tail_m = float(tail["resid"].mean()) if len(tail) else float("nan")
    print(f"    최상위 1%({len(tail)}곳) 평균 잔차 {tail_m:+.3f}")
    if top < -RESID_LIMIT:
        print(f"    → 최상위 십분위 잔차 {top:+.3f} — 회귀가 대형주 배수를 **과대추정**한다")
    elif top > RESID_LIMIT:
        print(f"    → 최상위 십분위 잔차 {top:+.3f} — 회귀가 대형주 배수를 **과소추정**한다")
    return top, tail_m


def _section_watch(res: pd.DataFrame, market: str) -> None:
    """[4] 대표 종목 — 숫자를 사람이 아는 회사에 붙여 본다."""
    watch = WATCH.get(market, {})
    if "code" not in res.columns:
        return
    idx = res.set_index(res["code"].astype(str))
    for code, name in watch.items():
        if code not in idx.index:
            print(f"    {name:<12} 표본에 없음")
            continue
        r = idx.loc[code]
        r = r.iloc[0] if isinstance(r, pd.DataFrame) else r
        if not np.isfinite(r["fitted"]):
            print(f"    {name:<12} 계산 불가(가드)")
            continue
        print(f"    {name:<12} 시총 {_fmt_cap(r['mcap'], market):>10}   "
              f"실제 {r['multiple']:>7.2f}   적정 {r['fitted']:>7.2f}   "
              f"size_adj {r['size_adj']:>+9.1%}   잔차 {r['resid']:>+7.3f}")


def _section_remedy(res: pd.DataFrame, coef: dict, market: str) -> None:
    """[5] 처방 후보 둘의 발동률 — 고르기 전에 무엇이 얼마나 바뀌는지 본다."""
    mc = res["mcap"].to_numpy(float)
    n = len(res)
    hi_cut = coef["mcap_max"] * EXTRAPOLATION_LIMIT
    a = int((mc > hi_cut).sum())
    print(f"    (A) 상한 컷(계산 불가)   {a:>5}곳 ({a / n:.2%}) — 걸리면 ①에서 그 다리가 빠진다")

    # (B) 윈저화 — log(시총)을 학습 분포 p99에서 자른다. 계수는 그대로 두고 입력만 자른다.
    # 규모 항은 스플라인일 수 있으므로(ADR-0020) β를 곱하지 않고 `size_term`을 부른다 —
    # 여기서 다시 계산하면 계수 스키마가 바뀔 때 이 스크립트만 조용히 어긋난다.
    cap = float(np.quantile(mc, WINSOR_Q))
    hit = res[(res["mcap"] > cap) & res["size_adj"].notna()]
    if hit.empty:
        print(f"    (B) 윈저화(p{WINSOR_Q:.0%})        0곳")
        return
    s_cap = size_term(coef, float(np.log(cap)))
    shrunk = np.exp([s_cap - size_term(coef, float(np.log(m)))
                     for m in hit["mcap"].to_numpy(float)])
    ratio = float(np.median(shrunk))
    print(f"    (B) 윈저화(p{WINSOR_Q:.0%})    {len(hit):>5}곳 ({len(hit) / n:.2%}) — "
          f"적정배수 중앙 ×{ratio:.2f} (최소 ×{shrunk.min():.2f})")


def run(market: str) -> int:
    print(f"\n{'=' * 72}\n{market} — 규모 외삽 진단\n{'=' * 72}")
    snap = collect_kr() if market == "KR" else collect_us()
    print(f"스냅숏 {len(snap):,}종목\n")

    bad = 0
    for leg in LEG_BOUNDS:
        d, coef = _prepare(snap, leg)
        if coef is None:
            print(f"■ {leg} — 표본 부족, 계수 없음\n")
            continue
        res = _predict(d, coef)
        # β는 이제 하나가 아니다(ADR-0020) — 몸통과 꼬리의 국소 기울기를 함께 찍는다.
        lm = np.log(d["mcap"].to_numpy(float))
        b_body = size_slope(coef, float(np.quantile(lm, 0.50)))
        b_tail = size_slope(coef, float(lm.max()))
        knots = len(coef.get("size_knots") or ())
        print(f"■ {leg}   β(중앙) {b_body:+.3f}   β(꼬리) {b_tail:+.3f}   마디 {knots}개")
        print("  [1] 학습 범위")
        n_large = _section_range(res, coef, market)
        print("  [2] size_adj 분포")
        max_adj = _section_sizeadj(res)
        print("  [3] 시총 십분위별 잔차")
        top, tail = _section_resid(res, market)
        print("  [4] 대표 종목")
        _section_watch(res, market)
        print("  [5] 처방 후보")
        _section_remedy(res, coef, market)

        flags = []
        if abs(top) > RESID_LIMIT:
            flags.append(f"최상위 십분위 잔차 {top:+.3f}")
        if max_adj > SIZE_ADJ_LIMIT:
            flags.append(f"size_adj 최대 {max_adj:+.0%}")
        if n_large:
            flags.append(f"상한 가드 후보 {n_large}곳")
        if flags:
            bad += 1
            print(f"  [문제] {' · '.join(flags)}\n")
        else:
            print("  [확인] 대형 구간에 체계적 편향이 안 보인다\n")
    return bad


def main() -> int:
    args = [a.upper() for a in sys.argv[1:]] or ["KR", "US"]
    bad = sum(run(m) for m in args if m in ("KR", "US"))
    print(f"{'=' * 72}\n문제 {bad}건")
    print("잔차가 음수면 회귀가 그 구간의 배수를 과대추정한 것이고, 적정배수가 과대라는 것은")
    print("그 종목이 '저평가' 쪽으로 밀린다는 뜻이다. 처방을 고르기 전에 [1]과 [3]을 함께 읽어라 —")
    print("[1]이 0곳인데 [3]이 크면 가드가 아니라 **함수형**을 고쳐야 한다.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
