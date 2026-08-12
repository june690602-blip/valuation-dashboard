"""표본이 얇아진 계수는 판정을 얼마나 움직이나 — 문턱을 정하기 전에 재는 것 (ADR-0050).

    python scripts/check_coefficient_thinning.py 두꺼운.json 얇은.json [종목코드...]

**네트워크가 필요하다.** CI 관문이 아니라 수동 진단이다.

## 왜 이 스크립트가 있나

`_coefficients_usable`은 **모양만 본다.** 다리마다 필수 키가 있으면 통과다. 그래서
레이트리밋에 걸린 빌드가 만든 **얇은 계수**가 멀쩡한 두꺼운 계수를 조용히 덮어쓴다.
2026-08-12 실측 — 같은 2,687행 스냅숏인데 CI 쪽 표본이 이랬다:

    pbr        2,529 vs 2,529      (같다)
    per        1,527 vs 1,527      (같다)
    ev_ebitda  1,376 vs   565      (41%)
    psr        1,999 vs   728      (36%)

**"몇 % 이하면 거부한다"를 지어내지 않기 위해** 먼저 잰다. 이 저장소는 근거 없는 문턱으로
여러 번 데였다(ADR-0041의 가중치 폭 · ADR-0042의 판정 문턱). 재고 나서 정한다.

## 무엇을 재나

같은 종목·같은 재무·같은 시세에서 **계수만 바꿔** ①의 적정 배수와 종합 판정을 다시 낸다.
바뀌는 것은 계수뿐이므로 차이는 전부 표본 얇아짐 탓이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8")

# 규모·업종을 흩어 놓은 표본. 한 구간만 보면 "안 바뀐다"는 잘못된 결론이 나온다.
DEFAULT = ["005930", "000660", "035420", "105560", "005380", "051910",
           "068270", "207940", "012330", "066570", "015760", "009150",
           "032830", "086790", "010130", "011070", "161890", "007070"]


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def leg_n(coef: dict) -> dict:
    return {leg: (coef.get(leg) or {}).get("n") for leg in sorted(coef)}


def sweep() -> int:
    """전 종목을 한 번 모아 **표본을 일부러 깎아 가며** 적정가가 언제부터 흔들리는지 잰다.

        python scripts/check_coefficient_thinning.py --sweep

    문턱을 지어내지 않기 위한 자다. 같은 스냅숏을 비율만 바꿔 적합하므로 차이는
    **표본 크기 하나** 탓이다(무작위 추출이라 종자를 고정한다).
    """
    import numpy as np

    from src.analysis.capital_cost import compute_capital_cost
    from src.analysis.indicators import compute_indicators
    from src.analysis.valuation import compute_valuation
    from src.data.universe_multiples import build_coefficients, collect_kr
    from src.web.serialize import _defaults, _load

    print("전 종목 스냅숏을 모으는 중… (캐시가 차 있으면 빠르다)")
    snap = collect_kr()
    print(f"스냅숏 {len(snap):,}행\n")

    rng = np.random.default_rng(20260813)
    full = build_coefficients(snap)
    fracs = [1.0, 0.8, 0.6, 0.4, 0.2, 0.1]
    coefs = {}
    for f in fracs:
        if f == 1.0:
            coefs[f] = full
            continue
        take = rng.choice(len(snap), size=max(int(len(snap) * f), 30), replace=False)
        coefs[f] = build_coefficients(snap.iloc[sorted(take)])

    print(f"{'비율':>6}" + "".join(f"{leg:>12}" for leg in sorted(full)))
    for f in fracs:
        ns = [(coefs[f].get(leg) or {}).get("n") for leg in sorted(full)]
        print(f"{f:>6.0%}" + "".join(f"{(n if n else '—'):>12}" for n in ns))

    rf, mrp = _defaults("KR")
    base, rows = {}, []
    for code in DEFAULT:
        try:
            d = _load("KR", code, 9, (), ())
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
        except Exception:  # noqa: BLE001
            continue
        vals = {}
        for f in fracs:
            try:
                v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coefs[f])
                vals[f] = v.fair_mid
            except Exception:  # noqa: BLE001
                vals[f] = None
        if vals.get(1.0):
            base[code] = vals[1.0]
            rows.append((d.name, vals))

    print(f"\n적정가가 100% 표본 대비 얼마나 어긋나나 — 종목 {len(rows)}개")
    print(f"{'비율':>6}{'|차이| 중앙값':>14}{'|차이| 최대':>12}{'5% 넘는 종목':>14}")
    print("─" * 48)
    for f in fracs:
        ds = [abs(v[f] / v[1.0] - 1) for _, v in rows if v.get(f) and v.get(1.0)]
        if not ds:
            continue
        ds.sort()
        over = sum(1 for x in ds if x > 0.05)
        print(f"{f:>6.0%}{ds[len(ds) // 2]:>13.1%}{ds[-1]:>12.1%}{over:>10}/{len(ds)}")
    print("\n※ 바뀐 것은 표본 크기뿐이다 — 종목·재무·시세는 모든 행에서 같다.")
    return 0


def isolate() -> int:
    """**어느 다리가 기둥인가** — ADR-0050이 문턱을 기둥에만 건 근거를 다시 낸다.

        python scripts/check_coefficient_thinning.py --isolate

    균일 축소(`--sweep`)는 "80%만 돼도 9.5% 움직인다"고 말하는데, 실제 사고(per·pbr는
    그대로 · ev_ebitda·psr만 36~41%)는 0.1%였다. 두 묶음을 **따로** 깎아 원인을 가른다.
    """
    import numpy as np

    from src.analysis.capital_cost import compute_capital_cost
    from src.analysis.indicators import compute_indicators
    from src.analysis.valuation import compute_valuation
    from src.data.universe_multiples import (GUARDED_LEGS, build_coefficients,
                                             collect_kr)
    from src.web.serialize import _defaults, _load

    snap = collect_kr()
    print(f"스냅숏 {len(snap):,}행")
    rng = np.random.default_rng(20260813)
    full = build_coefficients(snap)
    take = rng.choice(len(snap), size=int(len(snap) * 0.4), replace=False)
    thin = build_coefficients(snap.iloc[sorted(take)])
    flaky = tuple(leg for leg in full if leg not in GUARDED_LEGS)

    cases = {
        f"① {'·'.join(GUARDED_LEGS)}만 40%": {**full, **{k: thin[k] for k in GUARDED_LEGS if k in thin}},
        f"② {'·'.join(flaky)}만 40%": {**full, **{k: thin[k] for k in flaky if k in thin}},
        "③ 전부 40%": thin,
    }

    rf, mrp = _defaults("KR")
    res: dict[str, list] = {k: [] for k in cases}
    n_ok = 0
    for code in DEFAULT:
        try:
            d = _load("KR", code, 9, (), ())
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
            base = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=full).fair_mid
        except Exception:  # noqa: BLE001
            continue
        if not base:
            continue
        n_ok += 1
        for name, c in cases.items():
            try:
                v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=c).fair_mid
                if v:
                    res[name].append(abs(v / base - 1))
            except Exception:  # noqa: BLE001
                pass

    print(f"\n적정가가 100% 표본 대비 얼마나 어긋나나 — 종목 {n_ok}개")
    print(f"{'경우':<26}{'중앙값':>10}{'최대':>10}{'5% 초과':>10}")
    print("─" * 56)
    for name, ds in res.items():
        if not ds:
            continue
        ds.sort()
        print(f"{name:<26}{ds[len(ds) // 2]:>9.1%}{ds[-1]:>10.1%}"
              f"{sum(1 for x in ds if x > .05):>7}/{len(ds)}")
    print(f"\n※ 지키는 다리: {GUARDED_LEGS} (ADR-0050). 나머지는 yfinance 원천이라 요동친다.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--sweep":
        return sweep()
    if args and args[0] == "--isolate":
        return isolate()
    if len(args) < 2:
        print(__doc__)
        return 2
    thick, thin = load(args[0]), load(args[1])
    codes = args[2:] or DEFAULT

    print(f"{'다리':<12}{'두꺼움':>10}{'얇음':>10}{'비율':>8}")
    tn, hn = leg_n(thick), leg_n(thin)
    for leg in sorted(set(tn) | set(hn)):
        a, b = tn.get(leg), hn.get(leg)
        ratio = f"{b / a:.0%}" if a and b else "—"
        print(f"{leg:<12}{a or '—':>10}{b or '—':>10}{ratio:>8}")

    from src.analysis.capital_cost import compute_capital_cost
    from src.analysis.indicators import compute_indicators
    from src.analysis.valuation import compute_valuation
    from src.web.serialize import _load, _defaults

    rf, mrp = _defaults("KR")
    print(f"\n{'종목':<10}{'적정가(두꺼움)':>16}{'적정가(얇음)':>16}{'차이':>9}"
          f"{'판정(두꺼움)':>14}{'판정(얇음)':>12}")
    print("─" * 80)

    diffs, flips, done = [], 0, 0
    for code in codes:
        try:
            d = _load("KR", code, 9, (), ())
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
            a = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=thick)
            b = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=thin)
        except Exception as e:  # noqa: BLE001 — 한 종목 실패가 표를 막지 않는다
            print(f"{code:<10}  건너뜀: {type(e).__name__}: {e}")
            continue
        done += 1
        if a.fair_mid and b.fair_mid:
            diff = b.fair_mid / a.fair_mid - 1
            diffs.append(abs(diff))
            mark = "  ←" if a.verdict != b.verdict else ""
            if a.verdict != b.verdict:
                flips += 1
            print(f"{d.name[:9]:<10}{a.fair_mid:>16,.0f}{b.fair_mid:>16,.0f}"
                  f"{diff:>+8.1%}{str(a.verdict):>14}{str(b.verdict):>12}{mark}")
        else:
            print(f"{d.name[:9]:<10}{'적정가 없음':>16}")

    if diffs:
        diffs.sort()
        print("─" * 80)
        print(f"종목 {done}개 · 적정가 |차이| 중앙값 {diffs[len(diffs) // 2]:.1%} · "
              f"최대 {diffs[-1]:.1%} · **판정이 바뀐 종목 {flips}개**")
        print("\n※ 바뀐 것은 계수뿐이다 — 재무·시세·피어는 두 계산이 같은 것을 썼다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
