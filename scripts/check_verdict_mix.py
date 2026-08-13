"""판정이 저평가·적정·고평가로 얼마나 갈리나 — 지금 코드·지금 데이터로 (ADR-0042).

    python scripts/check_verdict_mix.py                 # KR 60 · US 40
    python scripts/check_verdict_mix.py --kr 100 --us 0

**네트워크가 필요하다.** CI 관문이 아니라 수동 진단이다.

## 왜 이 스크립트가 있나

ADR-0042가 판정을 5등급 → 3등급으로 넓히며 *"'적정 수준'이 70%가 된다 — 도구가 훨씬 말을
덜 한다"*고 적었다. 그 예측이 **지금도 맞는지** 확인할 수단이 없었다. 화면을 열어 본 사람이
"대부분 중립으로 나온다"고 느낄 때, 그것이 **설계대로인지 고장인지** 가릴 수 있어야 한다.

과거 패널(`data/backtest/panel.parquet`)로도 같은 비율을 낼 수 있지만 그쪽은 한국·과거다.
여기서는 **오늘의 계수와 오늘의 시세**로, 두 시장에서 잰다.

## 표본

시총 십분위로 **층화 추출**한다. 대형주만 보면 비율이 달라진다 — 소형주는 적자·데이터
부족으로 '판정 불가'가 많고, 그것도 사용자가 실제로 겪는 화면이다.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")

ORDER = ["저평가", "적정 수준", "고평가"]


def sample_kr(n: int, seed: int) -> list[str]:
    from src.data.universe import get_kr_listing
    d = get_kr_listing()
    d = d[d["is_common"] & (d["Marcap"] > 0)] if "is_common" in d.columns else d[d["Marcap"] > 0]
    # ⚠ 인덱스가 아니라 `Code` 열이다 — `.name`을 쓰면 위치 번호를 뽑는다(실제로 그랬다).
    return _stratify(d.sort_values("Marcap"), n, seed, lambda r: str(r["Code"]))


def sample_us(n: int, seed: int) -> list[str]:
    from src.data.universe import get_sp500
    d = get_sp500()
    col = "Symbol" if "Symbol" in d.columns else d.columns[0]
    return list(d[col].sample(min(n, len(d)), random_state=seed))


def _stratify(d, n: int, seed: int, key) -> list[str]:
    """시총 십분위에서 고르게 뽑는다 — 대형주만 보면 비율이 달라진다."""
    import numpy as np
    if len(d) <= n:
        return [key(r) for _, r in d.iterrows()]
    out, rng = [], np.random.default_rng(seed)
    chunks = np.array_split(np.arange(len(d)), 10)
    per = max(n // 10, 1)
    for ch in chunks:
        take = rng.choice(ch, size=min(per, len(ch)), replace=False)
        out += [key(d.iloc[i]) for i in sorted(take)]
    return out[:n]


def alt_threshold_note(market: str, gaps: list[float]) -> None:
    """**문턱은 한국 오차로 만들어졌고 미국에도 그대로 쓰인다**(ADR-0042 · `LEG_MAE`).

    `VERDICT_LOG_THRESHOLD = 0.897`은 KR의 가장 나쁜 다리(psr)에서 왔는데, US의 가장 나쁜
    다리는 0.656이다. 같은 문턱을 쓰면 미국에는 **자기 오차보다 37% 넓은 자**를 대는 셈이라
    구조적으로 '적정 수준'이 많아진다. 여기서는 **바꾸지 않고 얼마나 달라지는지만** 보인다.

    ⚠ **그 대안은 사전등록하고 재봤고, 기각됐다**([ADR-0054](../docs/adr/0054-the-threshold-is-not-the-thing-to-move.md)).
    문턱을 좁히면 두 시장·두 자에서 **예외 없이 양 끝 간격이 줄었고**, 미국은 어느 문턱에서도
    단조가 아니었다(기준선 포함 — '적정 수준'이 양 끝보다 나쁘다). **아래 표를 "이렇게 하면
    낫다"로 읽지 말 것.** 분포가 어떻게 움직이는지만 보이는 표다.
    """
    import math

    from src.analysis.valuation import VERDICT_LOG_THRESHOLD as M
    from src.analysis.warranted import LEG_MAE

    own = max(LEG_MAE.get(market.upper(), {}).values(), default=None)
    if not gaps or own is None:
        return
    lg = [math.log(1 + g) for g in gaps if g is not None and g > -1]
    if not lg:
        return

    def mix(m):
        return (sum(x >= m for x in lg), sum(-m < x < m for x in lg), sum(x <= -m for x in lg))

    a, b = mix(M), mix(own)
    n = len(lg)
    print("  ── 문턱을 이 시장의 실측 오차로 바꾸면 (참고 · 바꾸지 않았다)")
    print(f"     지금  로그 ±{M:.3f} (KR psr에서 옴)   저평가 {a[0]} · 적정 {a[1]} · 고평가 {a[2]}"
          f"   → 적정 {a[1] / n:.0%}")
    print(f"     대안  로그 ±{own:.3f} ({market} 최대 다리)  저평가 {b[0]} · 적정 {b[1]} · 고평가 {b[2]}"
          f"   → 적정 {b[1] / n:.0%}")


def run(market: str, codes: list[str]) -> tuple[Counter, list]:
    from src.analysis.capital_cost import compute_capital_cost
    from src.analysis.indicators import compute_indicators
    from src.analysis.valuation import compute_valuation
    from src.data.universe_multiples import coefficients_or_none
    from src.web.serialize import _defaults, _load

    rf, mrp = _defaults(market)
    coef = coefficients_or_none(market)
    tally: Counter = Counter()
    gaps: list = []
    for i, code in enumerate(codes, 1):
        try:
            d = _load(market, code, 9, (), ())
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
            val = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
        except Exception as e:  # noqa: BLE001 — 한 종목 실패가 표를 막지 않는다
            tally["조회 실패"] += 1
            print(f"  [{i}/{len(codes)}] {code} 실패: {type(e).__name__}", flush=True)
            continue
        tally[val.verdict if val.verdict else "판정 불가"] += 1
        if val.gap is not None:
            gaps.append(val.gap)
        print(f"  [{i}/{len(codes)}] {code} {d.name[:12]:<13} "
              f"{val.verdict or '판정 불가'}", flush=True)
    return tally, gaps


def report(market: str, tally: Counter) -> None:
    total = sum(tally.values())
    judged = sum(tally[k] for k in ORDER)
    print(f"\n=== {market} · 표본 {total}종목 ===")
    for k in ORDER:
        c = tally[k]
        share = f"{c / judged:6.1%}" if judged else "     —"
        print(f"  {k:<10} {c:4}건  판정 중 {share}   전체 중 {c / total:6.1%}")
    for k in ("판정 불가", "조회 실패"):
        if tally[k]:
            print(f"  {k:<10} {tally[k]:4}건                    전체 중 {tally[k] / total:6.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kr", type=int, default=60)
    ap.add_argument("--us", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260813)
    a = ap.parse_args()

    for market, n, sampler in (("KR", a.kr, sample_kr), ("US", a.us, sample_us)):
        if n <= 0:
            continue
        codes = sampler(n, a.seed)
        print(f"\n{market} {len(codes)}종목 (시총 층화 · seed {a.seed})")
        tally, gaps = run(market, codes)
        report(market, tally)
        alt_threshold_note(market, gaps)
    print("\n※ '판정 불가'는 적자·데이터 부족으로 적정가가 안 나온 종목이다 — "
          "그것도 사용자가 겪는 화면이라 함께 센다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
