"""EPV를 축으로 지을 값이 있나 — 관문 셋 (ADR-0016 결정 2, ADR-0023 예정).

    python scripts/check_epv_viability.py KR --limit 200
    python scripts/check_epv_viability.py US --limit 200

ADR-0016은 EPV를 **채택**한 것이 아니라 **후보로 지정**했다. 원문이 조건을 달았다 —
*"짓기 전에 검사 2를 EPV에 대해 돌린다 … 통과하지 못하면 짓지 않는다."* 같은 문서가
DCF를 그렇게 기각했다(검사 1 미달 29%). **기각도 정당한 결과다.**

관문 셋. 재기 전에 정한 값이고, 나온 숫자를 보고 바꾸지 않는다:

  검사 1  RIM이 빠진 종목 중 EPV가 서는 비율          ≥ 50%          ADR-0016 원문
  검사 2  기존 4방법과의 상관 최대 |피어슨| · 1.5배 밖  ≤ 0.5 · ≥ 25%  ADR-0016 원문
  검사 3  EPV 넣기 전/후 n_eff 중앙값                  안 내려갈 것    ADR-0023에서 신설

**검사 3은 ADR-0022가 만든 조건이다.** EPV는 ③ RIM과 같은 칸(이익 기반 영구가치)이고,
미국에서 그 칸의 두 방법(③ RIM ↔ ⑤ 정규화 이익)이 **+0.723**이다. 설계효과 식은 평균
상관만 보므로 EPV가 검사 2를 통과해도 `n_eff`를 내릴 수 있다 — ADR-0022 한계 절의
*"방법을 더 쓴 종목이 더 낮게 나올 수 있다"*.

**시장마다 따로 잰다.** ADR-0022에서 시장이 결론을 두 번 뒤집었다(①↔⑤ 한국 +0.263 대
미국 +0.763 · ③↔⑤ −0.015 대 +0.723). 한쪽 값을 다른 쪽에 옮겨 쓰지 않는다(ADR-0017).

네트워크가 필요하다. CI가 아니라 check_dcf_viability.py·check_confidence.py와 같은
수동 계열이다. 종료 코드 1은 "관문 미달"이라는 뜻이고, 그것도 결과다.
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from check_confidence import sample_by_size                            # noqa: E402
from check_dcf_viability import gate_of                                # noqa: E402
from src.analysis.capital_cost import compute_capital_cost             # noqa: E402
from src.analysis.epv import (epv_per_share,                           # noqa: E402
                              normalized_operating_income)
from src.analysis.indicators import compute_indicators                 # noqa: E402
from src.analysis.valuation import (FUNDAMENTAL_METHODS,               # noqa: E402
                                    METHOD_WEIGHTS, compute_valuation)
from src.analysis.warranted import METHOD_RHO, effective_axes          # noqa: E402
from src.data.kr_provider import KRProvider                            # noqa: E402
from src.data.universe_multiples import coefficients_or_none           # noqa: E402
from src.data.us_provider import USProvider                            # noqa: E402

# check_analysis.py·check_confidence.py와 **같은 가정**. 갈라지면 진단이 화면과 다른
# 자본비용으로 재게 되고, EPV는 WACC에 직접 매달린다.
RF_MRP = {"KR": (0.035, 0.06), "US": (0.045, 0.05)}
MAX_WORKERS = 2                # yfinance 한도를 덜 때린다
RETRIES, BACKOFF = 3, 4.0      # 401 Invalid Crumb 재시도 (초)
MIN_PAIR_N = 30                # 이보다 적은 쌍은 상관을 내지 않는다 (ADR-0011)

EPV = "EPV"                    # 상관표·n_eff에서 쓸 방법 이름
RIM = "수익가치(RIM)"

# 관문 — 앞의 둘은 ADR-0016이 정한 값을 그대로 옮긴 것이다.
COMPLEMENT_FLOOR = 0.50        # 검사 1
CORR_CEILING = 0.50            # 검사 2
DIVERGE_FLOOR = 0.25           # 검사 2
DIVERGE_FOLD = 1.5             # '갈렸다'고 볼 배수


def _one(code: str, coef, market: str) -> dict | None:
    """한 종목의 판정 + 방법별 로그 괴리율 + EPV. 실패하면 None.

    **재시도가 있는 이유가 있다.** yfinance가 `401 Invalid Crumb`으로 자주 거절하는데,
    재시도 없이 돌렸더니 50종목 중 **0곳**이 나온 적이 있다(check_confidence.py 주석).
    """
    provider = KRProvider() if market == "KR" else USProvider()
    rf, mrp = RF_MRP[market]
    for attempt in range(RETRIES):
        try:
            d = provider.load(code, peer_count=9)
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
            v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
            break
        except Exception:
            if attempt == RETRIES - 1:
                return None
            time.sleep(BACKOFF * (attempt + 1))
    if v.verdict is None or not d.price or not v.fundamental_only:
        return None

    oi, oi_years = normalized_operating_income(d.financials)
    net_debt = (d.latest("total_debt") or 0.0) - (d.latest("cash") or 0.0)
    epv = epv_per_share(oi, cc.tax_rate, cc.wacc, net_debt, d.shares_outstanding)

    mids = {e.method: e.mid for e in v.estimates if e.method in FUNDAMENTAL_METHODS}
    has_rim = RIM in mids
    row = {
        "code": code, "mcap": d.market_cap, "fair_mid": v.fair_mid,
        "has_rim": has_rim,
        "gate": "" if has_rim else gate_of(
            next((r for m, r in v.skipped if m == RIM), "")),
        # 성립 조건을 갈라서 센다 — 어디서 막혔는지 모르면 고칠 자리를 못 찾는다
        "oi_pos": bool(oi is not None and oi > 0),
        "wacc_ok": cc.wacc is not None,
        "epv": epv,
        "epv_ready": epv is not None,
        "methods": [m for m in (v.weights or {}) if m in FUNDAMENTAL_METHODS],
        "oi_years": oi_years,
    }
    # **로그 괴리율.** 로그인 이유는 ADR-0014·0022와 같다 — 배수는 곱셈 스케일이라
    # 원 스케일에서 재면 비싼 종목의 큰 괴리가 상관을 끌고 간다.
    for m, mid in mids.items():
        if mid and mid > 0:
            row[m] = float(np.log(mid / d.price))
    if epv and epv > 0:
        row[EPV] = float(np.log(epv / d.price))
    return row


def _collect(market: str, limit: int, coef) -> tuple[pd.DataFrame, int]:
    """(수집 결과, 시도 종목 수). 표본 틀은 check_confidence.py와 **같은 것**을 쓴다."""
    samp = sample_by_size(market, limit)
    print(f"{market} {len(samp)}종목 수집 중…\n")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        rows = [r for r in ex.map(lambda c: _one(c, coef, market), samp["code"]) if r]
    if not rows:
        return pd.DataFrame(), len(samp)
    return pd.DataFrame(rows).merge(samp, on="code", how="left"), len(samp)


def _print_sample(df: pd.DataFrame, tried: int) -> None:
    """표본이 어디서 왔나. 성공률을 반드시 함께 찍는다 — 원천이 불안정해 실행마다 다르고,
    그 사실을 모르면 숫자를 과신하게 된다."""
    print(f"판정 {len(df)}종목 / 시도 {tried}종목 (성공률 {len(df)/tried:.0%})")
    # 층이 실제로 규모를 갈랐나 — **주장하지 않고 실현 시총으로 보인다.** 미국은 층을
    # 시총이 아니라 지수로 만들었으므로 이 확인이 특히 필요하다.
    print(f"\n{'표본 층':<12}{'종목':>6}{'시총 중앙값(10억)':>20}")
    print("─" * 38)
    for tier, g in df.groupby("층", observed=True):
        print(f"{tier:<12}{len(g):>6}{g['mcap'].median() / 1e9:>20,.1f}")


def _check1(df: pd.DataFrame) -> tuple[bool, float]:
    """검사 1 — RIM이 빠진 자리에서 EPV가 서는가. (합불, 비율)"""
    no_rim = df[~df["has_rim"]]
    has = df[df["has_rim"]]
    print("\n[검사 1] 커버리지 상보성 — EPV는 RIM이 빠진 그 자리에서 서는가")
    print(f"    RIM이 선 종목 {int(df['has_rim'].sum())} · "
          f"빠진 종목 {len(no_rim)} ({1 - df['has_rim'].mean():.0%})")
    if len(no_rim):
        print("\n    ③ RIM 제외 사유:")
        for label, n in no_rim["gate"].value_counts().items():
            print(f"      {n:>3}곳 ({n / len(df):>4.0%})  {label}")
    print(f"\n    {'항목':<26}{'전체':>10}{'RIM 빠짐':>12}{'RIM 있음':>12}")
    print("    " + "─" * 60)
    for label, col in [("정상 영업이익 > 0", "oi_pos"), ("WACC 계산됨", "wacc_ok"),
                       ("EPV 설 수 있음", "epv_ready")]:
        b = no_rim[col].mean() if len(no_rim) else np.nan
        c = has[col].mean() if len(has) else np.nan
        print(f"    {label:<26}{df[col].mean():>10.0%}{b:>12.0%}{c:>12.0%}")
    comp = float(no_rim["epv_ready"].mean()) if len(no_rim) else float("nan")
    ok = bool(pd.notna(comp) and comp >= COMPLEMENT_FLOOR)
    print(f"\n    ★ RIM이 빠진 {len(no_rim)}종목 중 EPV가 서는 비율 {comp:.0%}  "
          f"{'[확인]' if ok else '[문제]'} (기준선 {COMPLEMENT_FLOOR:.0%})")
    print("      ADR-0016은 이 검사로 DCF를 기각했다(29%). 같은 문서 표로 EPV를 계산하면\n"
          "      31/75 = 41%인데, 그 문서는 이 각도로 EPV를 본 적이 없다.\n"
          "      **여기 나온 값이 그 41%보다 우선한다** — 순부채 조건이 더 붙었다.")
    return ok, comp


def _corr_table(df: pd.DataFrame) -> tuple[dict, list]:
    """방법 쌍별 (피어슨, 스피어만, n). EPV를 포함한 5방법 전 쌍.

    키 순서는 `effective_axes`가 만드는 것과 같아야 한다 — 그쪽이 `sorted(set(methods))`
    뒤 `(ms[i], ms[j])`로 찾으므로 여기서도 정렬 뒤 조합한다. 어긋나면 상관을 넣어 놓고
    '모르는 쌍'으로 처리돼 상한이 조용히 꺼진다(ADR-0022 결정 3).
    """
    table, rows = {}, []
    for a, b in itertools.combinations(sorted((*FUNDAMENTAL_METHODS, EPV)), 2):
        if a not in df.columns or b not in df.columns:
            rows.append((a, b, 0, None, None))
            continue
        both = df[[a, b]].dropna()
        if len(both) < MIN_PAIR_N:
            rows.append((a, b, len(both), None, None))
            continue
        r = float(both[a].corr(both[b]))
        s = float(both[a].rank().corr(both[b].rank()))
        table[(a, b)] = r
        rows.append((a, b, len(both), r, s))
    return table, rows


def _check2(df: pd.DataFrame, rows: list) -> tuple[bool, dict]:
    """검사 2 — EPV가 기존 축과 다른 말을 하는가. (합불, 메타)

    **'1.5배 밖'은 ADR-0016과 다른 통계량이다.** 그 문서는 DCF 값을 못 내 재료의 흩어짐을
    쟀다(`log(정상FCF/정상순이익)`, std 1.040 · 59%). EPV는 가정이 0개라 축 자체를 낼 수
    있으므로, 이 축이 판정을 실제로 움직이는지를 직접 잰다. 기준선 25%는 그대로 둔다 —
    재기 전에 정한 값을 나온 숫자를 보고 바꾸지 않는다.
    """
    print("\n[검사 2] 독립성 — EPV가 기존 축과 다른 말을 하는가")
    print(f"    {'상대 축':<16}{'표본':>7}{'피어슨':>10}{'스피어만':>11}")
    print("    " + "─" * 44)
    epv_rows = [r for r in rows if EPV in (r[0], r[1])]
    worst, measured, missing = 0.0, 0, []
    for a, b, n, r, s in epv_rows:
        other = b if a == EPV else a
        if r is None:
            missing.append(other)
            print(f"    {other:<16}{n:>7}{'표본부족':>10}{'—':>11}")
            continue
        measured += 1
        worst = max(worst, abs(r))
        print(f"    {other:<16}{n:>7}{r:>+10.3f}{s:>+11.3f}")
    if missing:
        print(f"    ※ 표본이 {MIN_PAIR_N} 미만이라 뺀 쌍: {' · '.join(missing)}")

    # **`to_numeric`가 필요하다.** `epv` 열은 EPV가 안 서는 종목에서 None이라 dtype이
    # object가 되고, object 열에 `> 0`을 하면 TypeError가 난다.
    sub = pd.DataFrame({"epv": pd.to_numeric(df["epv"], errors="coerce"),
                        "fair_mid": pd.to_numeric(df["fair_mid"], errors="coerce")}).dropna()
    sub = sub[(sub["epv"] > 0) & (sub["fair_mid"] > 0)]
    far = (float((np.abs(np.log(sub["epv"] / sub["fair_mid"])) > np.log(DIVERGE_FOLD)).mean())
           if len(sub) else float("nan"))
    print(f"\n    EPV가 종합 판정과 {DIVERGE_FOLD}배 넘게 갈린 비율 "
          f"{far:.0%}  (n={len(sub)})")

    if measured == 0:
        print("\n    [문제] EPV 쌍이 전부 표본부족이다 — 검사 2는 **판정 불가**이고 "
              "통과로 치지 않는다.")
        return False, {"worst": None, "far": far, "measured": 0}
    ok = bool(worst <= CORR_CEILING and pd.notna(far) and far >= DIVERGE_FLOOR)
    print(f"    {'[확인]' if ok else '[문제]'} 최대 |피어슨| {worst:.3f} (≤{CORR_CEILING}) · "
          f"{DIVERGE_FOLD}배 밖 {far:.0%} (≥{DIVERGE_FLOOR:.0%})")
    print("      상관이 높으면 EPV는 기존 축과 같은 말을 한다 — 축을 더해도 정보가 안 는다.\n"
          "      안 갈리면 판정이 안 바뀐다. ADR-0015가 ⑤에 걸었던 것과 같은 조건이다.")
    return ok, {"worst": worst, "far": far, "measured": measured}


def _print_all_pairs(rows: list) -> None:
    """기존 6쌍도 함께 찍는다 — **EPV에만 0.5를 들이대고 있다는 사실이 보여야 한다.**
    ADR-0022 실측에서 ③RIM↔① 한국 +0.787 · 미국 +0.706으로 이미 상한을 넘는다.
    합불은 이 줄들로 가르지 않는다."""
    print("\n[참고] 기존 6쌍 — 합불에는 안 쓴다")
    print(f"    {'방법 A':<16}{'방법 B':<16}{'표본':>7}{'피어슨':>10}")
    print("    " + "─" * 49)
    for a, b, n, r, _s in rows:
        if EPV in (a, b):
            continue
        print(f"    {a:<16}{b:<16}{n:>7}"
              f"{(f'{r:+.3f}' if r is not None else '표본부족'):>10}")


def _check3(df: pd.DataFrame, table: dict, market: str) -> tuple[bool, dict]:
    """검사 3 — EPV를 넣으면 실질 축 수가 내려가지 않는가. (합불, 메타)

    ADR-0022가 만든 조건이다. EPV는 ③ RIM과 같은 칸이고 미국에서 그 칸의 두 방법이
    +0.723이다. 설계효과 식은 **평균 상관**만 보므로, 겹치는 축을 더하면 `n_eff`가
    내려갈 수 있다 — 그 문서 한계 절의 *"방법을 더 쓴 종목이 더 낮게 나올 수 있다"*.

    `effective_axes`를 **다시 구현하지 않고 그대로 부른다.** 갈라지면 "진단은 통과인데
    화면은 다른 말"이 된다(check_confidence.py가 `confidence_grade`에 같은 선택을 했다).
    """
    print("\n[검사 3] 정직도 — EPV를 넣으면 실질 축 수가 내려가지 않는가")
    need = [(a, b) for a, b in table if EPV in (a, b)]
    if len(need) < len(FUNDAMENTAL_METHODS):
        print(f"    [문제] EPV 쌍 {len(FUNDAMENTAL_METHODS)}개 중 {len(need)}개만 쟀다 — "
              "모르는 쌍이 있으면 `effective_axes`가 상한을 통째로 끄므로(ADR-0022 결정 3)\n"
              "    비교가 뜻을 잃는다. **판정 불가**이고 통과로 치지 않는다.")
        return False, {"before": None, "after": None}

    # 기존 6쌍은 이미 채택된 `METHOD_RHO`를 쓴다. 이번에 잰 값으로 바꾸면 검사 3이
    # 상관 재측정까지 겸하게 되고, 그건 이 관문이 묻는 것이 아니다.
    merged = dict(METHOD_RHO.get(market.upper(), {}))
    merged.update({k: v for k, v in table.items() if EPV in k})

    # **EPV가 서는 종목에서만 비교한다.** 안 서는 종목은 정의상 전=후라 중앙값을 희석시킨다.
    sub = df[df["epv_ready"]].copy()
    if len(sub) < MIN_PAIR_N:
        print(f"    [문제] EPV가 서는 종목이 {len(sub)}곳뿐이다 — 비교할 표본이 아니다.")
        return False, {"before": None, "after": None}

    before, after = [], []
    for ms in sub["methods"]:
        b, b_ok = effective_axes(ms, market, merged)
        a, a_ok = effective_axes([*ms, EPV], market, merged)
        before.append(b if b_ok else np.nan)
        after.append(a if a_ok else np.nan)
    sub["before"], sub["after"] = before, after
    sub["n"] = sub["methods"].map(len)
    sub = sub.dropna(subset=["before", "after"])
    m_before, m_after = float(sub["before"].median()), float(sub["after"].median())
    up = int((sub["after"] > sub["before"] + 1e-9).sum())
    down = int((sub["after"] < sub["before"] - 1e-9).sum())

    print(f"    대상 {len(sub)}종목 (EPV가 서는 종목)")
    print(f"    {'방법 수':>7}{'종목':>7}{'전 중앙':>10}{'후 중앙':>10}{'차이':>10}")
    print("    " + "─" * 44)
    for n, g in sub.groupby("n"):
        print(f"    {n:>7}{len(g):>7}{g['before'].median():>10.2f}"
              f"{g['after'].median():>10.2f}"
              f"{g['after'].median() - g['before'].median():>+10.2f}")
    print(f"\n    전체 중앙 {m_before:.2f} → {m_after:.2f} "
          f"({m_after - m_before:+.2f}) · 오른 종목 {up} · 내린 종목 {down}")
    ok = bool(m_after >= m_before)
    print(f"    {'[확인]' if ok else '[문제]'} 중앙값이 "
          f"{'내려가지 않았다' if ok else '내려갔다'}")
    print("      내려가면 EPV는 축을 늘리면서 '이 판정이 독립적 근거 위에 있다'는 말을\n"
          "      오히려 약하게 만든다. 그 자리에서 축을 더할 이유가 없다.")
    return ok, {"before": m_before, "after": m_after, "up": up, "down": down}


# EPV의 가중은 **③과 같은 칸이므로 ③의 값을 가정으로 쓴다.** 지어낸 값이 아니라 '같은
# 성격의 방법에 이미 준 값'이고, ADR-0016의 WCOL이 같은 가정을 썼다. 가정임을 밝힌다.
EPV_WEIGHT = METHOD_WEIGHTS[RIM]
INTRINSIC = {RIM, EPV}


def _abs_share(methods, with_epv: bool) -> float:
    """이 종목의 **실효** 절대가치 비중 (빠진 방법은 재정규화)."""
    w = {m: METHOD_WEIGHTS[m] for m in methods}
    if with_epv:
        w[EPV] = EPV_WEIGHT
    s = sum(w.values())
    if not s:
        return float("nan")
    return sum(v for m, v in w.items() if m in INTRINSIC) / s


def _print_share(df: pd.DataFrame) -> None:
    """합불은 안 가르지만 ADR에 적을 것 — EPV가 절대가치 비중을 얼마나 올리나."""
    print("\n[참고] 절대가치 실효 비중 — 합불에는 안 쓴다")
    print(f"    {'축 구성':<18}{'절대축 있음':>12}{'실효 비중 평균':>16}{'중앙':>8}")
    print("    " + "─" * 54)
    for label, with_epv in (("현재 ①②③⑤", False), ("현재 + EPV", True)):
        have, shares = [], []
        for _i, r in df.iterrows():
            ms = list(r["methods"])
            ok_epv = with_epv and bool(r["epv_ready"])
            have.append(bool(RIM in ms or ok_epv))
            shares.append(_abs_share(ms, ok_epv))
        s = pd.Series(shares)
        print(f"    {label:<18}{np.mean(have):>12.0%}{s.mean():>16.1%}{s.median():>8.1%}")
    print(f"    ※ EPV 가중은 ③의 {EPV_WEIGHT}을 빌린 **가정**이다(ADR-0016과 같다).")

    left = df[~df["has_rim"] & ~df["epv_ready"]]
    if len(left):
        print(f"\n    끝내 절대가치가 없는 {len(left)}곳({len(left)/len(df):.0%})의 정체:")
        for label, n in left["gate"].value_counts().items():
            print(f"      {n:>3}곳  {label}")
        print("      모형으로 풀 자리가 아니다 — 화면이 그 사실을 말해야 한다(ADR-0016 결정 4).")


def _print_rho_lines(table: dict, market: str) -> None:
    """**지을 때 붙여넣을 줄.** 이번에 축을 짓지는 않지만, 측정을 코드로 남기는 것이
    이 저장소의 규칙이다 — LEG_MAE의 원래 값을 만든 스크립트가 없어서 ADR-0014의 β
    불일치를 끝내 못 밝힌 적이 있다(ADR-0022 결정 5)."""
    pairs = {k: v for k, v in table.items() if EPV in k}
    if not pairs:
        return
    print(f'\n[E] EPV를 지을 때 `warranted.METHOD_RHO["{market}"]`에 더할 줄')
    for (a, b), r in pairs.items():
        print(f'        ("{a}", "{b}"): {r:.3f},')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", nargs="?", default="KR", choices=["KR", "US"])
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    market = args.market

    coef = coefficients_or_none(market)
    if coef is None:
        print("계수를 만들지 못했다 — 회귀 경로가 아니면 이 측정은 뜻이 없다.")
        return 1
    df, tried = _collect(market, args.limit, coef)
    if len(df) < MIN_PAIR_N:
        print(f"판정이 난 종목이 {len(df)}곳뿐이다 — 관문을 걸 표본이 아니다.")
        return 1
    _print_sample(df, tried)
    ok1, _ = _check1(df)
    table, rows = _corr_table(df)
    ok2, _m2 = _check2(df, rows)
    _print_all_pairs(rows)
    ok3, _m3 = _check3(df, table, market)
    _print_share(df)
    _print_rho_lines(table, market)

    bad = sum(0 if ok else 1 for ok in (ok1, ok2, ok3))
    print(f"\n문제 {bad}건 — {market}에서 EPV를 지을 근거가 서려면 셋 다 [확인]이어야 한다.")
    print("이 결과는 ADR로 남긴다. 통과든 아니든, 재고 나서 정했다는 것이 기록이다.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
