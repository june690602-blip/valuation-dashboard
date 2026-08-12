"""판정 문턱·등급 수를 바꿔야 하나 — 후보 셋을 3년·5년 자로 잰다 (HANDOFF-AXES 5단계).

    python scripts/check_verdict_thresholds.py

사전등록: `docs/review/2026-08-12-문턱-사전등록.md` — **이 스크립트보다 먼저 커밋됐다**
(`e78043a`). 자·성공선·효과크기 하한은 거기서 정해졌고 여기서 바꾸지 않는다.

**이 스크립트는 승자를 고르지 않는다.** 사전등록의 성공선을 그대로 적용해 통과/실패만
찍는다. 결과가 마음에 안 든다고 기준을 손보는 것이 ADR-0024·0039가 이해충돌 절을 달아야
했던 바로 그 일이다.

후보 셋 (사전등록 §후보)
------------------------
    ㉠ 현행 5등급      _verdict(gap) 그대로 · 문턱 ±10% / ±30%
    ㉡ 3등급 · 오차유도  |log(적정가/주가)| ≥ m,  m = 0.671 (KR LEG_MAE 최대 = ev_ebitda)
    ㉢ 3등급 · 분위     각 시점 단면에서 gap 상위 20% / 하위 20%   ← **채택 후보가 아니다**

㉢을 채택하지 않는 이유는 통계가 아니라 주장이다 — *"내재가치보다 싸다"*가
*"시장의 다른 종목보다 싸다"*로 바뀌면 **시장 전체가 거품일 때 아무도 비싸지 않게 된다.**

성공선 (셋 전부 · 3년과 5년 **둘 다**)
--------------------------------------
    1 단조성       등급별 이후 수익률 **중앙값이 비감소**(고평가 → 저평가 순)
    2 간격         양 끝 중앙값 차 ≥ ㉠의 같은 자·같은 표본 간격 **+ 3%p**
    3 시점 일관성   시점별 간격의 부호가 3년 7시점 중 5개↑ · 5년 5시점 중 4개↑ 양수

**t값은 내지 않는다.** 3년·5년 창은 연 1회 시점과 겹치고, 겹침을 없애면 남는 시점이
3개·1개로 `MIN_T_DATES`(4) 미만이다. 겹친 창의 t는 부풀려진다. 조건 3이 그 대체이고
**t보다 약한 기준**이다(사전등록에 적었다).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.valuation import VERDICTS, _verdict  # noqa: E402
from src.analysis.warranted import LEG_MAE  # noqa: E402

OK, BAD = "[통과]", "[실패]"

# 사전등록 §㉡ — 종목별 최악 다리를 쓸 수 없어(패널에 다리 구성이 없다) 상수 하나를 쓴다.
ERROR_M = max(LEG_MAE["KR"].values())          # 0.671 · ev_ebitda
TRI = ["고평가", "적정 수준", "저평가"]         # 나쁨 → 좋음 순
EFFECT_FLOOR = 0.03                             # 사전등록 §성공선 2 — +3%p
# 사전등록 §성공선 3 — (필요 시점 수, 그중 양수여야 하는 최소 개수)
CONSISTENCY = {"3년": 5, "5년": 4}
ADJUDICATE = ("3년", "5년")                     # 둘 다 통과해야 채택


def label_current(gap: float) -> str:
    return _verdict(gap)


def label_error(gap: float) -> str:
    """로그 공간 대칭 — gap은 비(比)라 원 스케일에서 비대칭이다(ADR-0017)."""
    lg = np.log1p(gap)
    return "저평가" if lg >= ERROR_M else "고평가" if lg <= -ERROR_M else "적정 수준"


def label_quantile(s: pd.DataFrame) -> pd.Series:
    """각 **시점 단면**에서 상위/하위 20%. 시점을 가로질러 자르면 룩어헤드다."""
    out = pd.Series("적정 수준", index=s.index, dtype=object)
    for _t, idx in s.groupby("date").groups.items():
        g = s.loc[idx, "gap"]
        hi, lo = g.quantile(0.80), g.quantile(0.20)
        out.loc[idx[g >= hi]] = "저평가"
        out.loc[idx[g <= lo]] = "고평가"
    return out


def grade_table(s: pd.DataFrame, col: str, order: list, ret: str) -> pd.DataFrame:
    rows = []
    for g in order:
        v = s.loc[s[col] == g, ret].dropna()
        rows.append({"등급": g, "n": len(v),
                     "비중": len(v) / len(s) if len(s) else np.nan,
                     "중앙": v.median() if len(v) else np.nan,
                     "평균": v.mean() if len(v) else np.nan})
    return pd.DataFrame(rows)


def spread(tbl: pd.DataFrame) -> float:
    """양 끝 중앙값 차 (가장 좋은 등급 − 가장 나쁜 등급)."""
    if tbl["n"].iloc[0] == 0 or tbl["n"].iloc[-1] == 0:
        return np.nan
    return float(tbl["중앙"].iloc[-1] - tbl["중앙"].iloc[0])


def monotone(tbl: pd.DataFrame) -> bool:
    v = tbl["중앙"].to_numpy(dtype=float)
    return bool(np.all(np.diff(v) >= 0)) and not np.isnan(v).any()


def by_date_signs(s: pd.DataFrame, col: str, order: list, ret: str):
    """시점별 간격의 부호 — 겹침에 강한 t값 대체(사전등록 §성공선 3)."""
    pos = tot = 0
    for _t, g in s.groupby("date"):
        tb = grade_table(g, col, order, ret)
        sp = spread(tb)
        if np.isfinite(sp):
            tot += 1
            pos += int(sp > 0)
    return pos, tot


def full_dates(d: pd.DataFrame, raw: Path, days: int) -> set:
    """구간이 데이터 끝 안에 **완전히** 들어오는 시점만.

    ⚠ 사전등록이 이 지점을 틀리게 적었다 — *"구간이 데이터 끝을 넘으면 제외"*라고 썼는데,
    `check_backtest_horizon.build()`는 **폐지 종목은 예외로 남긴다**(정리매매 실현이라
    미래가 아니기 때문이다. 그 자체는 옳다). 그 결과 **최근 시점은 폐지 종목만 남은
    단면**이 된다 — 3년은 2024·2025, 5년은 2022~2025가 그렇다.

    그런 시점은 '판정이 수익률을 가르는가'를 재는 표본이 아니라 **재난 표본**이다.
    사전등록의 성공선은 그대로 적용하되(결과를 보고 기준을 바꾸지 않는다), 이 함수로
    **완전관측 시점만의 결과를 함께 내서** 둘이 갈리는지 밝힌다.
    """
    ends = []
    for p in raw.glob("px_*.parquet"):
        try:
            s = pd.read_parquet(p)["close"]
        except Exception:
            continue
        if len(s):
            ends.append(pd.to_datetime(s.index).max())
    if not ends:
        return set()
    data_end = max(ends)
    return {t for t in d["date"].unique()
            if pd.Timestamp(t) + pd.Timedelta(days=days) <= data_end}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", default=None,
                    help="이 자만 본다(참고용). 판정은 항상 3년+5년이다.")
    a = ap.parse_args()

    panel = pd.read_parquet(ROOT / "data" / "backtest" / "panel.parquet")
    raw = ROOT / "data" / "backtest" / "raw"
    if not raw.exists():
        raise SystemExit(f"{BAD} raw/가 없다 — backtest_pack.py unpack을 먼저 돌려라.")

    from check_backtest_horizon import build
    print(f"선행수익률을 만든다 (패널 {len(panel):,}행 · raw {len(list(raw.glob('px_*.parquet'))):,}종목)…")
    d = build(panel, raw)
    d = d[d["price"].notna() & d["fair_mid"].notna()].copy()
    d["gap"] = d["fair_mid"] / d["price"] - 1

    d["㉠ 현행5등급"] = d["gap"].map(label_current)
    d["㉡ 오차유도3등급"] = d["gap"].map(label_error)
    d["㉢ 분위3등급"] = label_quantile(d)

    cands = [("㉠ 현행5등급", VERDICTS[::-1], True),      # 나쁨 → 좋음 순으로 뒤집는다
             ("㉡ 오차유도3등급", TRI, True),
             ("㉢ 분위3등급", TRI, False)]                # False = 채택 후보 아님

    print(f"\n오차유도 문턱 m = {ERROR_M:.3f} → "
          f"저평가 gap ≥ {np.expm1(ERROR_M):+.1%} · 고평가 gap ≤ {np.expm1(-ERROR_M):+.1%}")

    horizons = [a.horizon] if a.horizon else ["1년", "3년", "5년"]
    verdict_pass: dict[str, dict[str, bool]] = {}
    robust: dict[str, dict[str, tuple]] = {}

    from check_backtest_horizon import HORIZONS
    for hz in horizons:
        ret = f"fwd_{hz}"
        if ret not in d.columns:
            print(f"\n{BAD} {ret} 열이 없다."); continue
        s = d[d[ret].notna()].copy()
        adj = hz in ADJUDICATE
        keep = full_dates(s, raw, HORIZONS[hz])
        dropped = sorted(set(pd.Timestamp(t) for t in s["date"].unique()) - keep)
        head = f"■ {hz} 자" + ("" if adj else "  (참고 — 판정에 쓰지 않는다)")
        print(f"\n{head}   표본 {len(s):,}행 · 시점 {s['date'].nunique()}개")
        if dropped:
            n_bad = int(s["date"].isin(dropped).sum())
            print(f"   ⚠ 그중 {len(dropped)}개 시점({', '.join(str(t.date()) for t in dropped)})은"
                  f" **폐지 종목만 남은 단면**이다 — {n_bad:,}행. 완전관측 시점은 {len(keep)}개.")
        print("─" * 78)

        base_sp = None
        for name, order, adoptable in cands:
            tbl = grade_table(s, name, order, ret)
            sp, mono = spread(tbl), monotone(tbl)
            if name.startswith("㉠"):
                base_sp = sp
            print(f"\n  {name}" + ("" if adoptable else "   ← 채택 후보 아님(주장이 바뀐다)"))
            print(f"    {'등급':<10}{'n':>6}{'비중':>8}{'중앙':>9}{'평균':>9}")
            for _i, r in tbl.iterrows():
                print(f"    {r['등급']:<10}{r['n']:>6,}{r['비중']:>8.1%}"
                      f"{r['중앙']:>9.1%}{r['평균']:>9.1%}"
                      if r["n"] else f"    {r['등급']:<10}{0:>6}{'—':>8}{'—':>9}{'—':>9}")
            pos, tot = by_date_signs(s, name, order, ret)
            need = CONSISTENCY.get(hz)
            print(f"    간격 {sp:+.1%} · 단조 {'예' if mono else '아니오'}"
                  f" · 시점 부호 {pos}/{tot}" + (f" (필요 {need}↑)" if need else ""))

            # 완전관측 시점만 — 사전등록의 합격선을 바꾸지 않고 **함께** 낸다.
            f = s[s["date"].isin(keep)]
            ftbl = grade_table(f, name, order, ret)
            fsp, fmono = spread(ftbl), monotone(ftbl)
            fpos, ftot = by_date_signs(f, name, order, ret)
            robust.setdefault(hz, {})[name] = (fsp, fmono, fpos, ftot)
            print(f"    └ 완전관측 시점만({len(keep)}개 · {len(f):,}행): "
                  f"간격 {fsp:+.1%} · 단조 {'예' if fmono else '아니오'} · 부호 {fpos}/{ftot}")

            if adj and not name.startswith("㉠"):
                c1 = mono
                c2 = np.isfinite(sp) and np.isfinite(base_sp) and sp >= base_sp + EFFECT_FLOOR
                c3 = need is not None and pos >= need
                ok = c1 and c2 and c3
                verdict_pass.setdefault(name, {})[hz] = ok
                print(f"    {OK if ok else BAD} 단조 {'O' if c1 else 'X'} · "
                      f"간격 {'O' if c2 else 'X'}(필요 {base_sp + EFFECT_FLOOR:+.1%}) · "
                      f"일관성 {'O' if c3 else 'X'}")

    if a.horizon:
        return 0
    print("\n" + "═" * 78)
    print("■ 사전등록 성공선에 따른 결론 — 3년과 5년 **둘 다** 통과해야 채택")
    for name, _order, adoptable in cands[1:]:
        got = verdict_pass.get(name, {})
        both = all(got.get(h, False) for h in ADJUDICATE)
        if not adoptable:
            print(f"  {name}: {'둘 다 통과' if both else '통과 못함'} — "
                  f"**그러나 채택 후보가 아니다**(사전등록 §㉢).")
        else:
            print(f"  {name}: {OK + ' 채택' if both else BAD + ' 기각'} "
                  f"({' · '.join(f'{h} {"O" if got.get(h) else "X"}' for h in ADJUDICATE)})")
    if not any(all(verdict_pass.get(n, {}).get(h, False) for h in ADJUDICATE)
               for n, _o, ad in cands[1:] if ad):
        print("\n  → **현행 5등급을 유지한다.** ADR-0017의 결정이 백테스트로 재확인됐다.")

    # ── 진단: ㉡의 간격 우위가 **선택성 때문인가** ─────────────────────────────
    # 사전등록 §"함께 보고하되 합격/불합격에 쓰지 않는 것"이 약속한 것이다.
    # ㉡은 극단 버킷이 훨씬 좁다(저평가 11.7% vs 현행 34.1%). 좁은 버킷은 간격이 커지기
    # 쉬우므로, **㉡과 똑같은 비중을 gap 순위로만 잘라** 같은 간격이 나오는지 본다.
    # 같으면 ㉡이 판 것은 '오차 문턱'이 아니라 '더 독한 선별'이다.
    # **이것은 채택 후보가 아니다** — 순위 절단이라 ㉢과 같은 이유로 주장이 바뀐다.
    print("\n■ 진단 — ㉡의 우위가 문턱 때문인가, 그냥 더 좁게 골라서인가")
    print("  (㉡과 **같은 비중**을 gap 순위로만 잘랐을 때. 채택 후보 아님)")
    for hz in ADJUDICATE:
        ret = f"fwd_{hz}"
        if ret not in d.columns:
            continue
        s = d[d[ret].notna()].copy()
        lo_share = float((s["㉡ 오차유도3등급"] == "고평가").mean())
        hi_share = float((s["㉡ 오차유도3등급"] == "저평가").mean())
        matched = pd.Series("적정 수준", index=s.index, dtype=object)
        for _t, idx in s.groupby("date").groups.items():
            g = s.loc[idx, "gap"]
            matched.loc[idx[g >= g.quantile(1 - hi_share)]] = "저평가"
            matched.loc[idx[g <= g.quantile(lo_share)]] = "고평가"
        s = s.assign(_matched=matched)
        mt = grade_table(s, "_matched", TRI, ret)
        et = grade_table(s, "㉡ 오차유도3등급", TRI, ret)
        msp, esp = spread(mt), spread(et)
        print(f"  {hz}: ㉡ {esp:+.1%}  vs  같은 비중 순위절단 {msp:+.1%}"
              f"   차이 {(esp - msp) * 100:+.1f}%p"
              f"   (고평가 {lo_share:.1%} · 저평가 {hi_share:.1%})")

    # 사전등록이 표본 정의를 틀리게 적었으므로, 그 영향이 결론을 뒤집는지 따로 찍는다.
    print("\n■ 완전관측 시점만으로 다시 보면 (사전등록 표본 정의 오류의 영향)")
    for hz in ADJUDICATE:
        if hz not in robust:
            continue
        base = robust[hz].get("㉠ 현행5등급")
        print(f"  {hz}:")
        for name, _o, ad in cands:
            fsp, fmono, fpos, ftot = robust[hz][name]
            note = ""
            if not name.startswith("㉠") and base and np.isfinite(base[0]):
                passes = fmono and fsp >= base[0] + EFFECT_FLOOR
                note = (f" → 같은 성공선으로 {'통과' if passes else '실패'}"
                        f"(필요 {base[0] + EFFECT_FLOOR:+.1%})"
                        + ("" if ad else " · 채택 후보 아님"))
            print(f"    {name:<16} 간격 {fsp:+.1%} · 단조 {'O' if fmono else 'X'}"
                  f" · 부호 {fpos}/{ftot}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
