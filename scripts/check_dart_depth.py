"""DART가 실제로 몇 년치를 주나 — `opendart.py`의 "6년"이 데이터 한계인지 쿼리 한계인지.

    python scripts/check_dart_depth.py --limit 12

`valuation.py`의 `NORMALIZE_WINDOW` 주석과 ADR-0015 한계 절이 둘 다 이렇게 적고 있다:

    DART 이력이 6년이라 그 이상은 만들 수 없다.

**그 6년은 DART가 아니라 `opendart.py`가 정한다.** 그쪽은 사업보고서를 **2개만** 받고
(최신, 최신−3) 마지막에 `.tail(6)`으로 자른다. 사업보고서 하나에 3년치가 들어 있으니
(당기·전기·전전기) 2개면 6년이 되는 구조다. 이 스크립트는 보고서를 더 받으면 이력이
실제로 늘어나는지, 늘어난 연도의 **품질이 유지되는지**를 잰다.

재는 것 셋:

  A. 이력 깊이 — 사업연도별 보고서가 응답하나, 재무 연도가 어디까지 내려가나
  B. 연도별 품질 — 옛 연도에 판정이 쓰는 항목이 실제로 채워지나
  C. 호출 비용 — 종목당 몇 번 부르나 (OpenDART 일 한도 20,000회)

**호출 비용을 반드시 함께 본다.** 이력을 늘리는 대가가 화면 로딩이기 때문이다.

네트워크와 `OPENDART_API_KEY`가 필요하다. CI가 아니라 check_epv_viability.py와 같은
수동 계열이다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.valuation import NORMALIZE_WINDOW                    # noqa: E402
from src.data.opendart import (_fetch_report, _parse_report,           # noqa: E402
                               get_api_key, get_corp_code_map)
from src.data.universe import get_kr_listing                           # noqa: E402

# 다섯 축이 실제로 읽는 항목. `total_debt`·`ebitda`는 **DART 매핑에 아예 없다** —
# yfinance가 채우는데 그쪽 이력이 짧아 이 표에서 늘 빠진다. 그 사실 자체가 결과다.
NEED = ["revenue", "operating_income", "net_income", "total_equity",
        "total_assets", "total_debt", "cash", "ebitda",
        "pretax_income", "tax_expense"]

# 사업보고서를 받아 볼 사업연도. OpenDART의 구조화 재무는 2015년부터다.
FIRST_YEAR = 2015


def _depth(key: str, corp: str, years: list[int]) -> tuple[list[int], pd.DataFrame, int]:
    """(응답한 보고서 연도, 연도별 재무 프레임, 호출 횟수)."""
    ok, data, calls = [], {}, 0
    for y in years:
        calls += 1
        j = _fetch_report(key, corp, y)
        if not j:
            continue
        ok.append(y)
        try:
            parsed = _parse_report(j, y)
        except Exception:
            continue
        # 최신 보고서가 먼저 오도록 정렬해 두면 재작성분이 우선한다 — 여기서는 옛것부터
        # 채우고 새것이 덮어쓰게 둔다(연도 순회가 오름차순이므로 자연히 그렇게 된다).
        for yr, vals in parsed.items():
            slot = data.setdefault(int(yr), {})
            for c, v in vals.items():
                if c in NEED and not pd.isna(v):
                    slot[c] = v
    df = pd.DataFrame.from_dict(data, orient="index").sort_index() if data else pd.DataFrame()
    return ok, df, calls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12, help="표본 종목 수 (시총 5분위 층화)")
    args = ap.parse_args()

    key = get_api_key()
    if not key:
        print("OPENDART_API_KEY가 없다 — 이 진단은 키가 필요하다.")
        return 1
    cmap = get_corp_code_map()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
    pool["층"] = pd.qcut(pool["Marcap"], 5,
                        labels=["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"])
    per = max(1, args.limit // 5)
    samp = (pool.groupby("층", observed=True)
                .apply(lambda g: g.sample(min(per, len(g)), random_state=11),
                       include_groups=False)
                .reset_index())

    years = list(range(FIRST_YEAR, _dt.date.today().year))
    print(f"사업연도 {years[0]}~{years[-1]} 보고서를 종목마다 {len(years)}번 조회한다.\n")

    rows, frames, total_calls = [], {}, 0
    for r in samp.itertuples():
        code = r.Code
        if code not in cmap.index:
            continue
        ok, df, calls = _depth(key, cmap.at[code, "corp_code"], years)
        total_calls += calls
        if df.empty:
            continue
        frames[code] = df
        rows.append({"code": code, "name": r.Name, "층": r.층,
                     "보고서": len(ok), "최초": int(df.index.min()),
                     "최신": int(df.index.max()), "연수": len(df)})
    if not rows:
        print("응답한 종목이 없다 — 키나 네트워크를 확인하라.")
        return 1
    res = pd.DataFrame(rows)

    # ── A. 이력 깊이 ──────────────────────────────────────────────
    print("A. 이력 깊이")
    print(f"    {'종목':<14}{'층':<10}{'보고서':>7}{'재무 연도':>14}{'연수':>6}")
    print("    " + "─" * 54)
    for r in res.itertuples():
        print(f"    {str(r.name)[:12]:<14}{str(r.층):<10}{r.보고서:>7}"
              f"{f'{r.최초}~{r.최신}':>14}{r.연수:>6}")
    med = float(res["연수"].median())
    print(f"\n    연수 중앙 {med:.0f} · 최소 {res['연수'].min()} · 최대 {res['연수'].max()}")
    print("    현행 `opendart.py`는 보고서 2개만 받고 .tail(6)으로 자른다 → **6년**")
    if med > 6:
        print(f"    ★ 중앙 {med:.0f}년 > 6년 — **DART가 아니라 쿼리가 한계다.**")
    else:
        print(f"    ★ 중앙 {med:.0f}년 — 6년이 데이터 한계일 수 있다. 표본을 늘려 다시 보라.")

    # ── B. 연도별 품질 ────────────────────────────────────────────
    # 옛 연도가 '있기만 하고 비어 있으면' 이력이 길어도 못 쓴다. 그것부터 확인한다.
    print(f"\nB. 연도별 품질 — 판정이 쓰는 {len(NEED)}개 항목 중 몇 개가 채워지나")
    print(f"    {'연도':<8}{'종목':>6}{'평균 채움':>12}   비는 항목(과반)")
    print("    " + "─" * 62)
    allyears = sorted({y for df in frames.values() for y in df.index})
    for y in allyears:
        have, miss_cnt, n = [], {c: 0 for c in NEED}, 0
        for df in frames.values():
            if y not in df.index:
                continue
            n += 1
            k = 0
            for c in NEED:
                if c in df.columns and pd.notna(df.at[y, c]):
                    k += 1
                else:
                    miss_cnt[c] += 1
            have.append(k)
        if not n:
            continue
        often = [c for c in NEED if miss_cnt[c] > n / 2]
        print(f"    {y:<8}{n:>6}{sum(have)/n:>9.1f}/{len(NEED)}   {' '.join(often) or '(없음)'}")
    print("\n    옛 연도의 채움이 최신과 비슷하면 **이력을 늘려도 품질이 안 나빠진다**는 뜻이다.")
    print("    `total_debt`·`ebitda`가 전 연도에서 비면 그것은 DART가 아니라 매핑 문제다 —")
    print("    `opendart.py`의 항목표에 그 둘이 없고, yfinance가 채우는데 이력이 짧다.")

    # ── C. 호출 비용 ──────────────────────────────────────────────
    per_stock = total_calls / len(res)
    print(f"\nC. 호출 비용 — 종목당 {per_stock:.0f}회 (현행 2회) · 이번 총 {total_calls:,}회")
    print(f"    OpenDART 일 한도는 20,000회다. 200종목 진단이면 {per_stock*200:,.0f}회로 여유가 있다.")
    print("    **다만 화면 로딩은 종목마다 이만큼 늘어난다** — 캐시로 풀 문제인지 따로 재라.")

    print(f"\n현행 창은 NORMALIZE_WINDOW = {NORMALIZE_WINDOW}년이다.")
    print("ADR-0015가 그 값을 고른 이유가 \"문헌은 8년, 우리는 5년. DART 이력이 6년이라")
    print("그 이상은 못 만든다\"였다. 위 A가 6년보다 크면 **그 전제가 틀린 것이다.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
