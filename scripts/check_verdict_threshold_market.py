"""시장마다 문턱을 따로 두어야 하나 — 후보 넷을 3년·5년 자로 잰다 (판정 문턱 2차).

    python scripts/check_verdict_threshold_market.py --market KR
    python scripts/check_verdict_threshold_market.py --market US

사전등록: `docs/review/2026-08-13-문턱-2차-사전등록.md` — **이 스크립트보다 먼저 커밋됐다**
(`5213625`). 자·성공선·후보·다중성 규칙은 거기서 정해졌고 **여기서 바꾸지 않는다.**

**이 스크립트는 승자를 고르지 않는다.** 사전등록의 성공선을 그대로 적용해 통과/실패만
찍고, 둘 이상 통과하면 성적이 아니라 **가장 넓은 문턱**을 가리킨다.

후보 넷 (사전등록 §후보 — 숫자를 손으로 적지 않고 규칙을 코드가 계산한다)
--------------------------------------------------------------------
    ㉣ 현행유지       VERDICT_LOG_THRESHOLD       양 시장 공통  ← 기준선
    ㉠ 시장별·최대     max(LEG_MAE[market])
    ㉡a 시장별·중앙값  median(LEG_MAE[market])
    ㉡b 시장별·평균    mean(LEG_MAE[market])

한국에서 ㉣와 ㉠은 **같은 값이다**(0.897이 애초에 KR의 최대 다리다). 두 칸이 똑같이
찍히는 것이 스크립트가 옳다는 확인이기도 하다.

성공선 (셋 전부 · 3년과 5년 **둘 다** · 시장마다 따로)
-----------------------------------------------------
    1 단조성      등급별 이후 수익률 **중앙값이 비감소**(고평가 → 적정 → 저평가 순)
    2 간격        ㉣의 같은 시장·같은 자·같은 표본 간격 **+3%p 이상**, **그리고 양수**
    3 시점 일관성  시점별 간격의 부호가 **ceil(0.70 × N)** 이상 양수

**조건 2의 '양수'가 1차와 다른 점이다** — 기준선 간격이 음수면 후보가 여전히 음수인 채로
"+3%p"를 통과해 버린다(−5% 기준선에 −1% 후보). 그건 판정이 덜 거꾸로일 뿐 여전히 거꾸로다.

**`N`은 후보와 무관하게 고정한다** — 그 시장·그 자에서 선행수익률이 있는 시점 수다.
분모를 "그 후보에서 간격이 계산된 시점 수"로 잡으면 극단 버킷이 자주 비는 **좁은 문턱일수록
분모가 줄어 유리해진다.** 1차는 절대 개수(5·4)를 고정해 그 문제를 피했고, 여기서는
분모를 후보 이전에 고정해 같은 성질을 지킨다. `ceil(0.7×7)=5`·`ceil(0.7×5)=4`로 1차를 재현한다.

**t값은 내지 않는다** — 3년·5년 창은 연 1회 시점과 겹치고 비중복 시점이 너무 적다.
조건 3이 그 대체이고 t보다 약한 기준이다(1차와 같다).

⚠ `check_verdict_thresholds.py`(1차)는 **건드리지 않는다.** 그것의 일은 ADR-0042를 낳은
비교(옛 5등급 대 오차유도 3등급)를 언제든 다시 만드는 것이고, 기준선을 현행 3등급으로
바꾸면 그 재현이 무너진다. 공용 함수만 임포트해 쓴다.
"""
from __future__ import annotations

import argparse
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from check_backtest_horizon import HORIZONS, build  # noqa: E402
# 1차 스크립트에서 **공용 함수만** 가져온다(사전등록 §재현). 판정 규칙은 가져오지 않는다.
from check_verdict_thresholds import (BAD, OK, TRI, by_date_signs,  # noqa: E402
                                      grade_table, monotone, spread)

from src.analysis.valuation import VERDICT_LOG_THRESHOLD  # noqa: E402
from src.analysis.warranted import LEG_MAE  # noqa: E402

BASE = "㉣ 현행유지"
EFFECT_FLOOR = 0.03        # 사전등록 §성공선 2 — 기준선 대비 +3%p
CONSISTENCY_FRAC = 0.70    # 사전등록 §성공선 3 — ceil(0.70 × N)
ADJUDICATE = ("3년", "5년")  # 둘 다 통과해야 채택


def candidates(market: str) -> dict[str, float]:
    """후보 넷의 m — **손으로 적지 않는다.** 사전등록이 정한 것은 이 식이다."""
    v = list(LEG_MAE[market].values())
    return {BASE: VERDICT_LOG_THRESHOLD,
            "㉠ 시장별·최대": max(v),
            "㉡a 시장별·중앙값": st.median(v),
            "㉡b 시장별·평균": st.fmean(v)}


def label(gap: pd.Series, m: float) -> pd.Series:
    """로그 공간 대칭 — `gap`은 비(比)라 원 스케일에서 이미 비대칭이다(ADR-0017)."""
    lg = np.log1p(gap)
    return pd.Series(np.where(lg >= m, "저평가",
                              np.where(lg <= -m, "고평가", "적정 수준")),
                     index=gap.index, dtype=object)


def data_end_of(raw: Path) -> pd.Timestamp:
    """수집된 주가의 마지막 날. `full_dates`와 같은 값이지만 **한 번만** 읽는다
    (4,170파일을 자마다 다시 읽으면 이 PC에서 값이 아니라 시간만 든다)."""
    ends = []
    for p in raw.glob("px_*.parquet"):
        try:
            s = pd.read_parquet(p)["close"]
        except Exception:  # noqa: BLE001 — 한 파일이 깨져도 나머지로 판단한다
            continue
        if len(s):
            ends.append(pd.to_datetime(s.index).max())
    if not ends:
        raise SystemExit(f"{BAD} {raw}에 px_*.parquet이 없다.")
    return max(ends)


def matched_rank_cut(s: pd.DataFrame, col: str) -> pd.Series:
    """그 후보와 **똑같은 극단 비중**을 gap 순위로만 자른다 (사전등록 §함께 보고 1).

    같은 간격이 나오면 이긴 것은 '오차 문턱'이 아니라 **'더 독한 선별'**이다.
    1차에서 이 진단이 ADR-0042의 가장 값어치 있는 한 줄을 만들었다.
    **채택 후보가 아니다** — 순위 절단은 도구의 주장을 바꾼다.
    """
    lo = float((s[col] == "고평가").mean())
    hi = float((s[col] == "저평가").mean())
    out = pd.Series("적정 수준", index=s.index, dtype=object)
    for _t, idx in s.groupby("date").groups.items():
        g = s.loc[idx, "gap"]
        if hi > 0:
            out.loc[idx[g >= g.quantile(1 - hi)]] = "저평가"
        if lo > 0:
            out.loc[idx[g <= g.quantile(lo)]] = "고평가"
    return out


def print_grades(tbl: pd.DataFrame) -> None:
    print(f"    {'등급':<10}{'n':>7}{'비중':>8}{'중앙':>9}{'평균':>9}")
    for _i, r in tbl.iterrows():
        if r["n"]:
            print(f"    {r['등급']:<10}{r['n']:>7,}{r['비중']:>8.1%}"
                  f"{r['중앙']:>9.1%}{r['평균']:>9.1%}")
        else:
            print(f"    {r['등급']:<10}{0:>7}{'—':>8}{'—':>9}{'—':>9}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    ap.add_argument("--horizon", default=None,
                    help="이 자만 본다(참고용). 판정은 항상 3년+5년이다.")
    a = ap.parse_args()
    mk = a.market

    base_dir = ROOT / "data" / ("backtest" if mk == "KR" else "backtest_us")
    raw = base_dir / "raw"
    panel_path = base_dir / "panel.parquet"
    if not panel_path.exists() or not raw.exists():
        raise SystemExit(f"{BAD} {base_dir}에 panel.parquet 또는 raw/가 없다.")

    panel = pd.read_parquet(panel_path)
    panel["date"] = pd.to_datetime(panel["date"])

    cands = candidates(mk)
    print(f"■ 시장 {mk} · 패널 {len(panel):,}행 · 시점 {panel['date'].nunique()}개")
    print("  사전등록: docs/review/2026-08-13-문턱-2차-사전등록.md")
    print("\n  후보 넷 (규칙이 계산한 값 — 손으로 적지 않았다)")
    print(f"    {'후보':<18}{'m':>8}{'저평가 gap':>13}{'고평가 gap':>13}   적정가 배수")
    for name, m in cands.items():
        print(f"    {name:<18}{m:>8.3f}{np.expm1(m):>13.1%}{np.expm1(-m):>13.1%}"
              f"   ×{math.exp(m):.2f}")
    if mk == "KR":
        same = abs(cands[BASE] - cands["㉠ 시장별·최대"]) < 1e-12
        print(f"    → ㉣와 ㉠이 같은 값인가: {'예 (예상대로)' if same else '아니오 — 이상하다'}")

    # 사전등록 §함께 보고 4 — 두 패널 다 ev_ebitda 다리를 쓰지 않았다. **후보가 아니다.**
    no_ev = {k: v for k, v in LEG_MAE[mk].items() if k != "ev_ebitda"}
    print(f"\n  (참고 · 후보 아님) ev_ebitda를 빼면 — 패널이 실제로 쓴 다리는 pbr·per·psr다"
          f"\n    최대 {max(no_ev.values()):.3f} · 중앙값 {st.median(list(no_ev.values())):.3f}"
          f" · 평균 {st.fmean(list(no_ev.values())):.3f}")

    print(f"\n선행수익률을 만든다 (raw {len(list(raw.glob('px_*.parquet'))):,}종목)…", flush=True)
    d = build(panel, raw)
    d = d[d["price"].notna() & d["fair_mid"].notna()].copy()
    d["gap"] = d["fair_mid"] / d["price"] - 1
    for name, m in cands.items():
        d[name] = label(d["gap"], m)

    end = data_end_of(raw)
    print(f"주가 데이터 끝: {end.date()}")

    horizons = [a.horizon] if a.horizon else ["1년", "3년", "5년"]
    passed: dict[str, dict[str, bool]] = {}

    for hz in horizons:
        ret = f"fwd_{hz}"
        if ret not in d.columns:
            print(f"\n{BAD} {ret} 열이 없다.")
            continue
        s = d[d[ret].notna()].copy()
        adj = hz in ADJUDICATE

        # 사전등록 §성공선 3 — N은 **후보와 무관하게** 여기서 정해진다.
        n_dates = int(s["date"].nunique())
        need = math.ceil(CONSISTENCY_FRAC * n_dates)

        # 완전관측 시점 — 최근 시점은 폐지 종목만 남은 '재난 표본'이다(1차 full_dates 주석).
        keep = {t for t in s["date"].unique()
                if pd.Timestamp(t) + pd.Timedelta(days=HORIZONS[hz]) <= end}
        dropped = sorted(set(pd.Timestamp(t) for t in s["date"].unique()) - keep)

        head = f"■ {mk} · {hz} 자" + ("" if adj else "  (참고 — 판정에 쓰지 않는다)")
        print(f"\n{head}   표본 {len(s):,}행 · 시점 N={n_dates}개"
              + (f" · 필요 부호 {need}개" if adj else ""))
        if dropped:
            n_bad = int(s["date"].isin(dropped).sum())
            print(f"   ⚠ 그중 {len(dropped)}개 시점은 **폐지 종목만 남은 단면**이다"
                  f"({n_bad:,}행 · {', '.join(str(t.date()) for t in dropped)}). "
                  f"완전관측 {len(keep)}개.")
        print("─" * 78)

        base_sp = None
        for name, m in cands.items():
            tbl = grade_table(s, name, TRI, ret)
            sp, mono = spread(tbl), monotone(tbl)
            pos, tot = by_date_signs(s, name, TRI, ret)
            if name == BASE:
                base_sp = sp
            print(f"\n  {name}   (m={m:.3f})" + ("   ← 기준선" if name == BASE else ""))
            print_grades(tbl)
            print(f"    간격 {sp:+.1%} · 단조 {'예' if mono else '아니오'}"
                  f" · 시점 부호 {pos}/{n_dates}"
                  + (f" (간격이 계산된 시점 {tot}개)" if tot != n_dates else ""))

            f = s[s["date"].isin(keep)]
            ftbl = grade_table(f, name, TRI, ret)
            fpos, ftot = by_date_signs(f, name, TRI, ret)
            print(f"    └ 완전관측만({len(keep)}시점 · {len(f):,}행): 간격 {spread(ftbl):+.1%}"
                  f" · 단조 {'O' if monotone(ftbl) else 'X'} · 부호 {fpos}/{len(keep)}")

            if adj and name != BASE:
                c1 = mono
                c2 = (np.isfinite(sp) and np.isfinite(base_sp)
                      and sp >= base_sp + EFFECT_FLOOR and sp > 0)
                c3 = pos >= need
                ok = bool(c1 and c2 and c3)
                passed.setdefault(name, {})[hz] = ok
                print(f"    {OK if ok else BAD} 단조 {'O' if c1 else 'X'} · "
                      f"간격 {'O' if c2 else 'X'}"
                      f"(필요 {max(base_sp + EFFECT_FLOOR, 0.0):+.1%} 이상이고 양수) · "
                      f"일관성 {'O' if c3 else 'X'}(필요 {need})")

        # 진단 — 사전등록 §함께 보고 1. 합격/불합격에 쓰지 않는다.
        if adj:
            print("\n  ■ 진단 — 우위가 문턱 때문인가, 그냥 더 좁게 골라서인가 (채택 후보 아님)")
            for name in cands:
                if name == BASE:
                    continue
                mt = grade_table(s.assign(_m=matched_rank_cut(s, name)), "_m", TRI, ret)
                et = grade_table(s, name, TRI, ret)
                esp, msp = spread(et), spread(mt)
                lo = float((s[name] == "고평가").mean())
                hi = float((s[name] == "저평가").mean())
                print(f"    {name:<18} {esp:+7.1%}  vs 같은 비중 순위절단 {msp:+7.1%}"
                      f"   차이 {(esp - msp) * 100:+5.1f}%p   (고 {lo:.1%} · 저 {hi:.1%})")

    if a.horizon:
        return 0

    # ── 사전등록 §결과에 따른 행동 ────────────────────────────────────────────
    print("\n" + "═" * 78)
    print(f"■ {mk} — 사전등록 성공선에 따른 결론 (3년과 5년 **둘 다** 통과해야 채택)")
    winners = []
    for name in cands:
        if name == BASE:
            continue
        got = passed.get(name, {})
        both = all(got.get(h, False) for h in ADJUDICATE)
        if both:
            winners.append(name)
        marks = " · ".join(f"{h} {'O' if got.get(h) else 'X'}" for h in ADJUDICATE)
        print(f"  {name:<18} {OK + ' 통과' if both else BAD + ' 기각'}   ({marks})")

    if not winners:
        print(f"\n  → **{mk}는 현행 문턱을 유지한다.** ADR-0053의 "
              f"*'한국에서 검증된 자를 그대로 쓰는 쪽이 덜 자의적'*이 재확인됐다.")
    elif len(winners) == 1:
        w = winners[0]
        print(f"\n  → **{mk}에 {w} 채택** (m={cands[w]:.3f}).")
    else:
        # 사전등록 §다중성 — 성적순이 아니라 **가장 넓은 문턱**(가장 말을 덜 하는 쪽).
        w = max(winners, key=lambda n: cands[n])
        print(f"\n  → {len(winners)}개가 통과했다: {', '.join(winners)}")
        print(f"    사전등록 §다중성에 따라 **가장 넓은 문턱**을 채택한다 — "
              f"**{w}** (m={cands[w]:.3f}). 성적순으로 고르지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
