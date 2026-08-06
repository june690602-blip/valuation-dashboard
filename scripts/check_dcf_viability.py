"""DCF 축을 더할 값이 있나 — 커버리지 상보성과 독립성 (짓기 전에 재는 값).

    python scripts/check_dcf_viability.py
    python scripts/check_dcf_viability.py --limit 60

DCF를 **만들기 전에** 두 가지를 잰다. ADR-0015가 ⑤를 넣기 전에 쟀던 것과 같은 성격이다
— 축을 더해도 판정이 안 바뀌거나 기존 축과 같은 말을 하면 더할 이유가 없다.

  검사 1 (커버리지 상보성) — ③ RIM은 장부가 게이트(ADR-0007·0010)에 걸려 자주 빠진다
      (`check_valuation_basis.py` 실측 59%). DCF는 장부가를 안 쓰니 **RIM이 빠진 바로
      그 자리에서** 서야 절대가치 비중을 실제로 올린다. 둘이 같이 빠지면 DCF는 목적을
      달성하지 못한다. 이 검사는 그 교차표다.

  검사 2 (독립성) — DCF가 기존 축과 갈리는 지점은 `현금흐름 ≠ 이익`이다. 그 크기를
      log(정상FCF / 정상순이익)로 재고, 기존 축의 괴리율과의 상관을 본다. ⑤가 같은
      자리에서 std 0.815 · 1.5배 밖 47% · 상관 −0.407이었다(ADR-0015).

  비교 (섹션 D) — DCF만 놓고 보면 "기준선을 넘었나"밖에 못 묻는다. **같은 자리를 노리는
      다른 모형과 견줘야** 짓는 값이 정해진다. 그래서 EPV(정상 영업이익 × (1−t) ÷ WACC,
      성장 가정 0개)를 같은 표본에서 나란히 재고, 절대가치 축이 하나라도 있는 종목
      비율이 구성별로 어떻게 달라지는지 본다. EPV의 재료도 이미 저장소에 있다.

여기서 재는 것은 **DCF의 값이 아니라 재료**다. 성장률·기간 가정이 필요한 계산은 하지
않는다 — 그 가정을 정하기 전에 "지을 값이 있나"를 먼저 답하는 것이 이 스크립트다.

부수적으로 **RIM 제외 사유를 게이트별로 분해**한다(docs/HANDOFF-NEXT.md 2번). 검사 1의
교차표가 그것을 필요로 하기 때문이지, 별개의 일을 겸하는 것이 아니다.

판정 기준 — 아래 임계는 데이터로 추정한 값이 아니라 **판단값**이다(ADR-0003의 가중치와
같은 성격). 근거는 각 줄에 적었다.
  검사 1: RIM이 빠진 종목 중 DCF 재료가 있는 비율 ≥ 50%
          (ADR-0015가 ⑤에 쓴 커버리지 기준선과 같은 값. 절반도 못 채우면 '빈자리를
           채운다'는 말을 못 한다)
  검사 2: |상관| ≤ 0.5 이고 1.5배 밖 비율 ≥ 25%
          (상관이 높으면 같은 말을 두 번 하는 것이고, 안 갈리면 판정이 안 바뀐다)

네트워크가 필요하다(전 종목 재무·피어). CI가 아니라 check_size_bias.py와 같은 수동 계열이다.
KR만 잰다 — 표본 틀(get_kr_listing)이 한국뿐이라 US는 이 결과 밖이다.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost              # noqa: E402
from src.analysis.indicators import compute_indicators                  # noqa: E402
from src.analysis.valuation import (METHOD_WEIGHTS, NORMALIZE_MIN_YEARS,  # noqa: E402
                                    NORMALIZE_WINDOW, compute_valuation)
from src.data.kr_provider import KRProvider                             # noqa: E402
from src.data.universe import get_kr_listing                            # noqa: E402
from src.data.universe_multiples import coefficients_or_none            # noqa: E402

RF, MRP = 0.035, 0.06          # KRProvider 기본 가정. rf·mrp는 필수 인자다.

COMPLEMENT_FLOOR = 0.50        # 검사 1 통과선
CORR_CEILING = 0.50            # 검사 2 — 이보다 상관이 높으면 같은 말
DIVERGE_FLOOR = 0.25           # 검사 2 — 1.5배 밖이 이보다 적으면 판정이 안 바뀐다
DIVERGE_FOLD = 1.5             # '갈렸다'고 볼 배수 (ADR-0015와 같은 기준)

# RIM 제외 사유를 게이트로 되돌린다. `_book_quality`가 만든 short 문장에서 **판별에 쓴
# 수치가 아닌 부분**만 잡는다(수치는 종목마다 다르다). 어디에도 안 걸리면 '미분류'로
# 두고 원문을 그대로 찍는다 — 문구가 바뀌었을 때 조용히 오분류되느니 눈에 띄어야 한다.
GATES = [
    ("ADR-0007 무형자산 ≥15%", "무형자산이 자산의"),
    ("ADR-0007 자사주 ≥30%", "누적 자사주매입이"),
    ("ADR-0007 극단 ROE >60%", "자기자본이 이익 대비"),
    ("ADR-0007 진입했으나 판별자료 없음", "원인 판별 자료 없음"),
    ("ADR-0010 시장이 장부가를 오래 거부", "자본비용 미달"),
    ("ROE ≤ 0 (적자) — 게이트가 아니라 계산 불가", "ROE ≤ 0"),
    ("자기자본을 못 받음 — 게이트가 아니라 결측", "자기자본을 확인하지 못함"),
]


def _gate_of(reason: str) -> str:
    for label, needle in GATES:
        if needle in reason:
            return label
    return f"미분류: {reason}"


def _window_mean(fin, col: str) -> tuple[float | None, int]:
    """(창 평균, 쓴 연수) — `_normalized_earnings`와 **같은 창 규칙**을 다른 열에 쓴다.

    적자·음수 연도를 빼지 않는 것도 그대로다. 설비투자가 몰린 해의 음수 FCF를 빼면
    정규화가 아니라 체리피킹이고, DCF가 감당해야 할 사이클이 바로 그것이다.
    """
    if fin is None or not hasattr(fin, "columns") or col not in fin.columns:
        return None, 0
    win = pd.to_numeric(fin[col], errors="coerce").iloc[-NORMALIZE_WINDOW:]
    win = win[np.isfinite(win)]
    if len(win) < NORMALIZE_MIN_YEARS:
        return None, int(len(win))
    return float(win.mean()), int(len(win))


def _one(code: str, coef) -> dict | None:
    try:
        d = KRProvider().load(code, peer_count=9)
        ind = compute_indicators(d)
        cc = compute_capital_cost(d, rf=RF, mrp=MRP)
        v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
    except Exception:
        return None
    if v.verdict is None or not d.price:
        return None

    mids = {e.method: e.mid for e in v.estimates}
    has_rim = "수익가치(RIM)" in mids
    rim_reason = next((r for m, r in v.skipped if m == "수익가치(RIM)"), "")

    fcf_n, fcf_years = _window_mean(d.financials, "fcf")
    ocf_n, _ = _window_mean(d.financials, "ocf")
    ni_n, _ = _window_mean(d.financials, "net_income")
    oi_n, _ = _window_mean(d.financials, "operating_income")
    # DCF가 설 수 있는 최소 조건 — 할인할 현금흐름이 (평균적으로) 양수이고 할인율이 있다.
    # 단년 FCF가 아니라 창 평균을 쓴다. 설비투자 사이클 한 해로 축이 서고 빠지면
    # 그 축은 회사가 아니라 그 해를 재는 것이다.
    dcf_ready = bool(fcf_n is not None and fcf_n > 0 and cc.wacc is not None)

    # 검사 2의 재료 — 현금흐름이 이익과 갈리는 크기. 둘 다 양수여야 로그가 선다.
    term = (float(np.log(fcf_n / ni_n))
            if fcf_n is not None and ni_n is not None and fcf_n > 0 and ni_n > 0
            else np.nan)

    def gap(method):
        return (mids[method] / d.price - 1) if method in mids else np.nan

    # EPV(Greenwald) — 정상 영업이익 × (1−t) ÷ WACC. 여기서는 **값이 아니라 설 수 있는가**만
    # 본다. 세율은 값을 낼 때만 필요하고 성립 여부를 가르지 않는다(1−t는 항상 양수다).
    epv_ready = bool(oi_n is not None and oi_n > 0 and cc.wacc is not None)

    return {
        "code": code, "name": d.name,
        "has_rim": has_rim,
        "gate": "" if has_rim else _gate_of(rim_reason),
        # 축별 커버리지 — 판정에 실제로 선 방법만 estimates에 들어온다.
        "a1": "업종 상대가치" in mids, "a2": "역사적 밴드" in mids,
        "a5": "정규화 이익" in mids,
        "epv_ready": epv_ready,
        "ocf": d.latest("ocf") is not None,
        "fcf": d.latest("fcf") is not None,
        "fcf_pos_now": (d.latest("fcf") or 0) > 0,
        "fcf_pos_norm": bool(fcf_n is not None and fcf_n > 0),
        # 임계 하나에 결론이 걸리지 않게 느슨한 변형도 같이 센다. 정상 OCF가 양수인데
        # 정상 FCF가 음수면 '설비투자가 현금흐름을 넘는 회사'라, DCF를 유지보수 capex로
        # 다시 짜면 살 수도 있다는 뜻이다. 정상 순이익은 ⑤의 커버리지(67.8%)와 견주는 닻이다.
        "ocf_pos_norm": bool(ocf_n is not None and ocf_n > 0),
        "ni_pos_norm": bool(ni_n is not None and ni_n > 0),
        "fcf_years": fcf_years,
        "wacc": cc.wacc is not None,
        "dcf_ready": dcf_ready,
        "term": term,
        "gap1": gap("업종 상대가치"), "gap2": gap("역사적 밴드"),
        "gap5": gap("정규화 이익"),
        "gap_v": (v.fair_mid / d.price - 1) if v.fair_mid else np.nan,
    }


# 실효 비중을 셈할 때 쓰는 가중. ①②⑤③은 실제 값을 가져오고, 아직 없는 축(⑥·EPV)은
# **③과 같은 칸이므로 ③의 가중을 가정으로 쓴다.** 지어낸 값이 아니라 '같은 성격의 방법에
# 이미 준 값'이다 — 다른 값을 넣고 싶으면 여기만 바꾸면 표 전체가 따라온다.
WCOL = {"a1": METHOD_WEIGHTS["업종 상대가치"], "a2": METHOD_WEIGHTS["역사적 밴드"],
        "a5": METHOD_WEIGHTS["정규화 이익"], "has_rim": METHOD_WEIGHTS["수익가치(RIM)"],
        "dcf_ready": METHOD_WEIGHTS["수익가치(RIM)"],
        "epv_ready": METHOD_WEIGHTS["수익가치(RIM)"]}
ABSOLUTE = {"has_rim", "dcf_ready", "epv_ready"}


def _abs_share(row, cols) -> float:
    """주어진 축 구성에서 이 종목의 **실효** 절대가치 비중 (빠진 방법은 재정규화)."""
    w = {c: WCOL[c] for c in cols if row[c]}
    s = sum(w.values())
    if not s:
        return np.nan
    return sum(v for c, v in w.items() if c in ABSOLUTE) / s


def _corr(df: pd.DataFrame, col: str) -> tuple[float, float, int]:
    """(피어슨, 스피어만, n). 괴리율은 꼬리가 두꺼워 순위상관을 같이 본다.

    스피어만은 `method="spearman"`이 아니라 **순위의 피어슨**으로 직접 낸다 — 그쪽은
    scipy를 끌어들이는데, 진단 하나 때문에 의존성을 늘릴 이유가 없다(수식은 같다).
    """
    sub = df[["term", col]].dropna()
    if len(sub) < 8:
        return np.nan, np.nan, len(sub)
    return (float(sub["term"].corr(sub[col])),
            float(sub["term"].rank().corr(sub[col].rank())), len(sub))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="표본 종목 수 (시총 5분위에 고르게 배분)")
    ap.add_argument("--dump", type=str, default=None,
                    help="종목별 원자료를 CSV로 저장 (표본 순회가 비싸 다시 재기 어렵다)")
    args = ap.parse_args()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
    pool["q"] = pd.qcut(pool["Marcap"], 5, labels=["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"])
    per_q = max(1, args.limit // 5)
    samp = (pool.groupby("q", observed=True)
                .apply(lambda g: g.sample(min(per_q, len(g)), random_state=11),
                       include_groups=False)
                .reset_index())

    coef = coefficients_or_none("KR")
    with ThreadPoolExecutor(6) as ex:
        rows = [r for r in ex.map(lambda c: _one(c, coef), samp["Code"]) if r]
    if not rows:
        print("판정이 나온 종목이 없다 — 네트워크나 캐시를 확인하라.")
        return 1

    df = pd.DataFrame(rows)
    if args.dump:
        df.to_csv(args.dump, index=False, encoding="utf-8-sig")
        print(f"원자료 {len(df)}행을 {args.dump}에 저장했다.\n")
    no_rim = df[~df["has_rim"]]
    print(f"표본 {len(samp)}종목 · 판정이 나온 종목 {len(df)}\n")

    # ── [A] RIM이 왜 빠지는가 (HANDOFF-NEXT 2번) ──
    print("[A] ③ RIM 제외 사유 분해")
    print(f"    RIM이 선 종목 {df['has_rim'].sum()} · 빠진 종목 {len(no_rim)} "
          f"({1 - df['has_rim'].mean():.0%})")
    if len(no_rim):
        for label, n in no_rim["gate"].value_counts().items():
            print(f"      {n:>3}곳 ({n / len(df):>4.0%})  {label}")
    print("    게이트(0007·0010)가 몫의 대부분이면 게이트를 다시 볼 일이고,\n"
          "    적자·결측이 대부분이면 게이트가 아니라 모형의 범위가 좁은 것이다.\n")

    # ── [B] 검사 1 — DCF는 그 빈자리에서 서는가 ──
    print("[B] 검사 1 — DCF 재료 가용성 (RIM 제외 여부별)")
    print(f"    {'항목':<22}{'전체':>10}{'RIM 빠짐':>12}{'RIM 있음':>12}")
    print("    " + "─" * 56)
    has = df[df["has_rim"]]
    for label, col in [("OCF 있음", "ocf"), ("FCF 있음", "fcf"),
                       ("FCF > 0 (단년)", "fcf_pos_now"),
                       ("FCF > 0 (창 평균)", "fcf_pos_norm"),
                       ("(참고) OCF > 0 (창 평균)", "ocf_pos_norm"),
                       ("(참고) 정상 순이익 > 0", "ni_pos_norm"),
                       ("WACC 계산됨", "wacc"),
                       ("DCF 설 수 있음", "dcf_ready")]:
        a = df[col].mean()
        b = no_rim[col].mean() if len(no_rim) else np.nan
        c = has[col].mean() if len(has) else np.nan
        print(f"    {label:<22}{a:>10.0%}{b:>12.0%}{c:>12.0%}")

    comp = no_rim["dcf_ready"].mean() if len(no_rim) else np.nan
    ok1 = bool(pd.notna(comp) and comp >= COMPLEMENT_FLOOR)
    print(f"\n    ★ RIM이 빠진 {len(no_rim)}종목 중 DCF가 설 수 있는 비율 "
          f"{comp:.0%}  {'[확인]' if ok1 else '[문제]'} (기준선 {COMPLEMENT_FLOOR:.0%})")
    print("      높으면 DCF가 비어 있는 절대가치 자리를 채운다.\n"
          "      낮으면 같은 종목에서 RIM과 함께 빠져 실효 비중이 안 오른다.\n")

    # ── [C] 검사 2 — 기존 축과 다른 말을 하는가 ──
    t = df["term"].dropna()
    print(f"[C] 검사 2 — 독립성  log(정상FCF / 정상순이익), n={len(t)}")
    if len(t) < 8:
        print("    표본이 8곳 미만이라 재지 않는다 — --limit을 올려라.\n")
        return 1 if not ok1 else 0
    far = float(((t.abs()) > np.log(DIVERGE_FOLD)).mean())
    print(f"    흩어짐 std {t.std():.3f} · {DIVERGE_FOLD}배 밖 {far:.0%} "
          f"(비교 — ⑤ 정규화 항: std 0.815 · 47%)")
    print(f"    {'기존 축':<16}{'피어슨':>10}{'스피어만':>11}{'n':>6}")
    print("    " + "─" * 43)
    worst = 0.0
    for label, col in [("① 업종 상대가치", "gap1"), ("② 역사적 밴드", "gap2"),
                       ("⑤ 정규화 이익", "gap5"), ("종합 판정", "gap_v")]:
        p, s, n = _corr(df, col)
        worst = max(worst, abs(p) if pd.notna(p) else 0.0)
        ps = f"{p:>10.3f}" if pd.notna(p) else f"{'—':>10}"
        ss = f"{s:>11.3f}" if pd.notna(s) else f"{'—':>11}"
        print(f"    {label:<16}{ps}{ss}{n:>6}")
    ok2 = bool(worst <= CORR_CEILING and far >= DIVERGE_FLOOR)
    print(f"\n    {'[확인]' if ok2 else '[문제]'} 최대 |피어슨| {worst:.3f} "
          f"(≤{CORR_CEILING}) · {DIVERGE_FOLD}배 밖 {far:.0%} (≥{DIVERGE_FLOOR:.0%})")
    print("      상관이 높으면 DCF는 기존 축과 같은 말을 한다 — 축을 더해도 정보가 안 는다.\n"
          "      안 갈리면 판정이 안 바뀐다 — ADR-0015가 ⑤에 걸었던 것과 같은 조건이다.\n")

    if len(t):
        print("현금흐름과 이익이 가장 크게 갈린 5곳 — DCF가 다른 말을 할 후보:")
        for r in df.dropna(subset=["term"]).reindex(
                df["term"].abs().sort_values(ascending=False).index).head(5).itertuples():
            print(f"  {r.code} {str(r.name)[:12]:<14} FCF/순이익 {np.exp(r.term):>6.2f}배  "
                  f"RIM {'있음' if r.has_rim else '빠짐'}  "
                  f"DCF {'가능' if r.dcf_ready else '불가'}")

    # ── [D] 같은 자리를 노리는 모형끼리 견준다 ──
    print("\n[D] 절대가치 축 커버리지 — DCF만 보면 '기준선을 넘었나'밖에 못 묻는다")
    print(f"    {'축':<20}{'성격':<7}{'커버리지':>9}")
    print("    " + "─" * 36)
    for lab, col, kind in [("① 업종 상대가치", "a1", "상대"), ("② 역사적 밴드", "a2", "상대"),
                           ("⑤ 정규화 이익", "a5", "상대"), ("③ 수익가치(RIM)", "has_rim", "절대"),
                           ("⑥ DCF (가정)", "dcf_ready", "절대"), ("EPV (가정)", "epv_ready", "절대")]:
        print(f"    {lab:<20}{kind:<7}{df[col].mean():>9.0%}")

    print(f"\n    {'축 구성':<22}{'절대축 있음':>12}{'실효 절대비중 평균':>19}{'중앙':>8}")
    print("    " + "─" * 61)
    for lab, cols in [("①②③ (⑤ 넣기 전)", ["a1", "a2", "has_rim"]),
                      ("①②③⑤ (현재)", ["a1", "a2", "has_rim", "a5"]),
                      ("현재 + ⑥ DCF", ["a1", "a2", "has_rim", "a5", "dcf_ready"]),
                      ("현재 + EPV", ["a1", "a2", "has_rim", "a5", "epv_ready"]),
                      ("현재 + 둘 다", ["a1", "a2", "has_rim", "a5", "dcf_ready", "epv_ready"])]:
        have = df[[c for c in cols if c in ABSOLUTE]].any(axis=1)
        sh = df.apply(lambda r: _abs_share(r, cols), axis=1)
        print(f"    {lab:<22}{have.mean():>12.0%}{sh.mean():>19.1%}{sh.median():>8.1%}")

    if len(no_rim):
        d_only = int((no_rim["dcf_ready"] & ~no_rim["epv_ready"]).sum())
        e_only = int((~no_rim["dcf_ready"] & no_rim["epv_ready"]).sum())
        both_n = int((no_rim["dcf_ready"] & no_rim["epv_ready"]).sum())
        left = no_rim[~no_rim["dcf_ready"] & ~no_rim["epv_ready"]]
        print(f"\n    RIM이 빠진 {len(no_rim)}곳에서 누가 구하나 — "
              f"DCF만 {d_only}곳 · EPV만 {e_only}곳 · 둘 다 {both_n}곳 · 못 구함 {len(left)}곳")
        print("      고유 기여가 작은 쪽은 지어도 얻는 것이 적다. 그것이 이 절의 요지다.")
        if len(left):
            print("      끝내 절대가치가 없는 종목의 정체:")
            for label, n in left["gate"].value_counts().items():
                print(f"        {n:>3}곳  {label}")
            print("      여기는 모형으로 풀 자리가 아니다 — 화면이 그 사실을 말해야 한다.")

    bad = (0 if ok1 else 1) + (0 if ok2 else 1)
    print(f"\n문제 {bad}건 — 둘 다 [확인]이어야 DCF 축을 지을 근거가 선다.")
    print("이 결과는 ADR로 남긴다. 통과든 아니든, 재고 나서 정했다는 것이 기록이다.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
