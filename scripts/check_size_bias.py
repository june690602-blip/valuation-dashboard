"""규모 편향 진단: 적정가 판정이 '싸다'가 아니라 '작다'를 재고 있지 않은가.

    python scripts/check_size_bias.py              # 전 종목(약 3분)
    python scripts/check_size_bias.py --limit 400  # 빠른 확인(약 30초)
    python scripts/check_size_bias.py --prices     # 베타·밴드 방향지표까지(+30초)

API 키는 필요 없지만 **네트워크는 필요하다**(네이버 공시지표 + FinanceDataReader).
그래서 CI에 걸지 않는다 — 전 종목이면 요청이 수천 건이라 PR마다 돌릴 물건이 아니고,
원천이 잠깐 흔들리면 관계없는 PR이 빨간불이 된다. check_analysis.py와 같은 **수동** 계열이다.

## 무엇을 재는가

판정은 ①업종 상대가치 ②역사적 밴드 ③수익가치(RIM)의 가중평균이다(ADR-0006).
셋이 서로 다른 관점이라는 것이 '삼각측량'의 전제인데, 셋 다 결국 PER·PBR을 통과한다:

    ① 피어의 PER·PBR 중앙값 × 자사 EPS·BPS
    ② 자기 5년 PER·PBR 분위 × 현재 EPS·BPS
    ③ 장부가(B)를 닻으로 삼는다 — ROE≈r이면 V≈B이므로 괴리율 ≈ 1/PBR − 1

그런데 국내 시장에서 PBR은 시가총액을 따라 크게 갈린다. 그러면 세 방법이 같은
방향으로 같은 오차를 내고, 세 값이 일치할수록 '신뢰도 높음'으로 읽혀 **오차가 확신으로
포장된다**. 이 스크립트는 그 편향의 크기를 시총 구간별로 잰다.

## 판정 기준

시총과 괴리율의 순위상관 |rho|를 본다. 0이면 규모와 무관하게 판정한다는 뜻이다.
방법이 규모를 재고 있으면 음수가 된다(작을수록 저평가). 기준선은 아래 KNOWN_RHO.

무엇이 '옳은' 값인지는 이 스크립트가 정하지 않는다 — PBR 0.5인 종목이 정말 싼 것일
수도 있다(코리아 디스카운트). 다만 **소형주의 60% 이상에 저평가가 뜨면 그것은 종목
선별이 아니라 자산군 진술**이고, 그 사실은 화면에 드러나야 한다.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.scoring import _BOUNDS          # noqa: E402  피어 유효범위 — 원본을 그대로 쓴다
from src.analysis.valuation import _rim           # noqa: E402  RIM 공식 — 복사하지 않고 호출한다
from src.data.naver import fetch_naver_fundamental  # noqa: E402
from src.data.universe import get_kr_listing      # noqa: E402

OK, BAD, NA = "[확인]", "[문제]", "[불가]"
_tally = {OK: 0, BAD: 0, NA: 0}

# 기준선 — 2026-08-03 실측. 고치면 함께 내린다.
#   ① rho −0.261 · ③ rho −0.44 수준. 회귀만 막고, 개선은 [확인]으로 알린다.
KNOWN_RHO = 0.30

QLABEL = ["Q1 최소형", "Q2 소형", "Q3 중형", "Q4 대형", "Q5 최대형"]
SIZE_FACTOR = 20.0          # scoring.comparable_peers 기본값
RIM_W = 0.8                 # valuation._rim 중심 시나리오
W_REL, W_RIM = 0.385, 0.231  # ADR-0006 판정 가중(①②③ 재정규화)
RF, MRP = 0.035, 0.06       # KRProvider 기본 가정
WORKERS = 8


def say(verdict: str, title: str, detail: str = "") -> None:
    _tally[verdict] += 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def head(title: str) -> None:
    print(f"\n{title}\n" + "─" * 72)


def spearman(x: pd.Series, y: pd.Series) -> float:
    """순위 피어슨상관. scipy를 새 의존성으로 들이지 않으려고 직접 쓴다."""
    d = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    return float(d["x"].rank().corr(d["y"].rank())) if len(d) >= 5 else float("nan")


# ══════════════════════════════════════════════════════════════════════
# 수집 — 네이버 공시지표(PER/PBR/EPS/BPS). file_cache를 타므로 재실행은 무료.
# ══════════════════════════════════════════════════════════════════════
def collect(limit: int | None) -> pd.DataFrame:
    L = get_kr_listing()
    for c in ("Marcap", "Close"):
        L[c] = pd.to_numeric(L.get(c), errors="coerce")
    if "is_common" in L.columns:
        L = L[L["is_common"].fillna(True)]          # 우선주는 같은 회사의 중복 표본이다
    L = L.dropna(subset=["Marcap", "Close", "Sector"])
    L = L[(L["Marcap"] > 0) & (L["Close"] > 0)].reset_index(drop=True)
    if limit:
        L = L.sort_values("Marcap", ascending=False).iloc[::max(1, len(L) // limit)].head(limit)

    def one(code: str):
        try:
            f = fetch_naver_fundamental(code)
        except Exception:                            # noqa: BLE001  한 종목 실패로 전수를 멈추지 않는다
            return None
        return {"Code": code, "per": f.get("per"), "pbr": f.get("pbr"),
                "eps": f.get("eps"), "bps": f.get("bps")}

    rows = []
    print(f"수집 {len(L)}종목 …", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fu in as_completed([ex.submit(one, c) for c in L["Code"]]):
            if (r := fu.result()):
                rows.append(r)

    df = L.merge(pd.DataFrame(rows), on="Code", how="inner")
    for col in ("per", "pbr"):                       # scoring.sanitize_peer_frame과 같은 범위
        lo, hi = _BOUNDS[col]
        v = pd.to_numeric(df[col], errors="coerce")
        df[col] = v.where((v >= lo) & (v <= hi))
    for col in ("eps", "bps"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["bucket"] = pd.qcut(df["Marcap"], 5, labels=QLABEL)
    df["roe"] = np.where(df["bps"] > 0, df["eps"] / df["bps"], np.nan)
    return df


# ══════════════════════════════════════════════════════════════════════
# ① 업종 상대가치 — scoring.comparable_peers + peer_median + valuation._rel_fairs
#    피어 프레임을 종목마다 만들면 수천 번 DataFrame을 짓게 되므로 업종 단위로
#    벡터화한다. 규칙(시총 1/f~f배 · 자사 제외 · 표본 min_n)은 원본과 같다.
# ══════════════════════════════════════════════════════════════════════
def relative_gap(df: pd.DataFrame, factor: float | None) -> pd.DataFrame:
    out = []
    for sector, g in df.groupby("Sector", sort=False):
        mc, per, pbr = (g["Marcap"].to_numpy(), g["per"].to_numpy(float), g["pbr"].to_numpy(float))
        for i in range(len(g)):
            row, others = g.iloc[i], np.ones(len(g), bool)
            others[i] = False

            def med(vals, mask, min_n):
                v = vals[mask & others]
                v = v[~np.isnan(v)]
                return float(np.median(v)) if len(v) >= min_n else None

            win = (others & (mc >= row.Marcap / factor) & (mc <= row.Marcap * factor)
                   if factor else others)
            pm, bm, n_win = med(per, win, 2), med(pbr, win, 2), int(win.sum())
            fairs = []
            if pm and row.eps and row.eps > 0:
                fairs.append(pm * row.eps)
            if bm and row.bps and row.bps > 0:
                fairs.append(bm * row.bps)
            if not fairs:                            # _relative_value의 '전체 피어' 폴백
                pm, bm = med(per, others, 3), med(pbr, others, 3)
                if pm and row.eps and row.eps > 0:
                    fairs.append(pm * row.eps)
                if bm and row.bps and row.bps > 0:
                    fairs.append(bm * row.bps)
            if not fairs:
                continue
            out.append({"Code": row.Code, "Sector": sector, "Marcap": row.Marcap,
                        "bucket": row.bucket, "n_peer": n_win,
                        "peer_mc_ratio": float(np.median(mc[win]) / row.Marcap) if n_win else np.nan,
                        "per_ratio": pm / row.per if (pm and row.per) else np.nan,
                        "gap": float(np.median(fairs)) / row.Close - 1})
    return pd.DataFrame(out)


def rim_gap(df: pd.DataFrame, sp: dict | None = None) -> pd.Series:
    """③ 괴리율. sp를 주면 k_e에 시총 구간별 규모 프리미엄을 더한다(반사실 실험)."""
    vals = []
    for t in df.itertuples():
        r = RF + 1.0 * MRP + (sp or {}).get(t.bucket, 0.0)   # β=1 근사 — 아래 [불가] 참조
        fv, _ = _rim(t.bps, t.roe if pd.notna(t.roe) else None, r)
        vals.append(fv.mid / t.Close - 1 if fv and t.Close > 0 else np.nan)
    return pd.Series(vals, index=df.index)


# ══════════════════════════════════════════════════════════════════════
def report_material(df: pd.DataFrame) -> None:
    head("A. 원재료 — 멀티플 자체가 규모를 따라 갈리는가")
    t = df.groupby("bucket", observed=True).agg(
        n=("Code", "size"), 시총중앙=("Marcap", "median"),
        PER=("per", "median"), PBR=("pbr", "median"))
    t["시총중앙"] = (t["시총중앙"] / 1e8).round(0)
    print(t.round(2).to_string() + "\n         (시총 단위: 억원)")
    lo, hi = t["PBR"].iloc[0], t["PBR"].iloc[-1]
    if pd.notna(lo) and pd.notna(hi) and lo > 0:
        say(NA if hi / lo < 1.3 else BAD, f"PBR이 최소형↔최대형 {hi / lo:.1f}배로 갈린다",
            "①②③이 모두 이 축을 지난다. 여기서 갈리면 세 방법이 같은 방향으로 틀린다.")


def report_relative(df: pd.DataFrame) -> pd.DataFrame:
    head("B. ① 업종 상대가치 — 피어 규모창이 대칭인가")
    res = {f: relative_gap(df, f) for f in (SIZE_FACTOR, 5.0, 3.0, None)}
    tab = pd.DataFrame({(f"factor={int(f)}" if f else "필터없음"):
                        r.groupby("bucket", observed=True)["gap"].median() * 100
                        for f, r in res.items()})
    print(tab.round(1).to_string() + "\n         (괴리율 중앙값 %, 양수 = 저평가 쪽)")

    base = res[SIZE_FACTOR]
    rho = spearman(np.log(base["Marcap"]), base["gap"])
    within = np.nanmedian([spearman(np.log(g["Marcap"]), g["gap"])
                           for _, g in base.groupby("Sector") if len(g) >= 15])
    say(BAD if abs(rho) > KNOWN_RHO else OK, f"① 시총-괴리율 순위상관 rho = {rho:+.3f}",
        f"업종 안에서만 봐도 {within:+.3f} — 업종 구성 탓이 아니다.\n"
        f"기준선 |rho| ≤ {KNOWN_RHO}. factor를 3으로 좁히면 "
        f"{spearman(np.log(res[3.0]['Marcap']), res[3.0]['gap']):+.3f}로 줄고, "
        f"필터를 빼면 {spearman(np.log(res[None]['Marcap']), res[None]['gap']):+.3f}로 커진다.")

    a = base.groupby("bucket", observed=True).agg(
        피어수중앙=("n_peer", "median"), 피어시총배수=("peer_mc_ratio", "median"),
        피어PER대비자사=("per_ratio", "median"))
    print(a.round(2).to_string())
    r0 = a["피어시총배수"].iloc[0]
    say(BAD if r0 > 1.2 else OK, f"최소형주의 피어 시총 중앙값이 자사의 {r0:.2f}배",
        f"시총 분포가 오른쪽으로 길어 ±{SIZE_FACTOR:g}배 창이 위로만 열린다.\n"
        "최대형주 쪽은 반대로 자기보다 작은 피어만 남아 반대 방향으로 기운다.")
    return base


def report_rim(df: pd.DataFrame) -> pd.Series:
    head("C. ③ 수익가치(RIM) — 장부가 닻이 PBR을 되읽고 있지 않은가")
    g3 = rim_gap(df)
    t = pd.DataFrame({
        "n": g3.groupby(df["bucket"], observed=True).count(),
        "③괴리": g3.groupby(df["bucket"], observed=True).median() * 100,
        "1/PBR−1": (1 / df.groupby("bucket", observed=True)["pbr"].median() - 1) * 100,
    })
    print(t.round(1).to_string() + "\n         (단위 %)")
    d = pd.concat([g3.rename("g"), df["pbr"]], axis=1).dropna()
    corr = float(d["g"].rank().corr((1 / d["pbr"]).rank()))
    say(BAD if corr > 0.9 else OK, f"③ 괴리율과 1/PBR의 순위상관 = {corr:+.3f}",
        "1에 가까울수록 RIM이 독립된 관점이 아니라 PBR의 역수를 되읽는다는 뜻이다.\n"
        "ROE≈r이면 V≈B가 되어 괴리율이 정의상 1/PBR−1로 수렴하기 때문이다.")
    return g3


def report_blend(df: pd.DataFrame, rel: pd.DataFrame, g3: pd.Series) -> None:
    head("D. 종합 기여도 — 어느 방법이 편향을 가장 많이 넣는가")
    g1 = rel.groupby("bucket", observed=True)["gap"].median() * 100
    t = pd.DataFrame({"①기여": g1 * W_REL,
                      "③기여": g3.groupby(df["bucket"], observed=True).median() * 100 * W_RIM})
    t["②기여"] = np.nan
    print(t.round(1).to_string() + "\n         (판정 가중 적용 %p · ②는 과거 EPS·BPS가 없어 미측정)")

    sp = dict(zip(QLABEL, [0.030, 0.020, 0.012, 0.006, 0.0]))
    fix = (rim_gap(df, sp).groupby(df["bucket"], observed=True).median()
           - g3.groupby(df["bucket"], observed=True).median()) * 100 * W_RIM
    say(NA, f"규모 프리미엄(최소형 +3.0%p)을 k_e에 넣으면 종합이 {fix.iloc[0]:+.1f}%p 움직인다",
        f"③ 기여 {t['③기여'].iloc[0]:+.1f}%p 중 {abs(fix.iloc[0] / t['③기여'].iloc[0]) * 100:.0f}%만 걷힌다.\n"
        "V≈B라는 구조가 남기 때문이다 — 할인율 정교화(파마-프렌치 포함)로는 닿지 않는 자리다.")


def declare_limits() -> None:
    head("F. 이 스크립트가 판정하지 않는 것")
    say(NA, "② 역사적 밴드의 편향",
        "자기 5년 PER·PBR 분위를 만들려면 과거 EPS·BPS 시계열이 필요한데 무료 원천에 없다.\n"
        "--prices의 '현재가÷5년 주가중앙값'은 밴드가 기울 조건일 뿐 밴드 자체가 아니다.")
    say(NA, "③의 베타 — 여기서는 β=1로 근사한다",
        "종목별 회귀 베타는 5년 주가가 종목마다 필요해 전수로는 비싸다. 실측(250종목)에서\n"
        "k_e가 7.0~8.5%로 사실상 평평했으므로 구간 간 비교에는 영향이 작다.")
    say(NA, "피어 집합",
        "운영은 Gemini 업종분류로 ~10종목을 고르고, 여기서는 KRX 업종 전체를 후보로 쓴다.\n"
        "소형주에 업종 대표주를 붙이는 AI 특성상 실제 편향은 이보다 클 수 있다.")
    say(NA, "낮은 PBR이 정말 저평가인가",
        "이 스크립트는 '규모와 판정이 붙어 있다'까지만 말한다. 그것이 시장의 오류인지\n"
        "구조적 할인인지는 데이터가 아니라 관점의 문제다.")


def report_prices(df: pd.DataFrame, n_each: int) -> None:
    """②가 기울 조건 — 현재가가 자기 5년 주가중앙값 아래인가."""
    import FinanceDataReader as fdr

    head("E. ② 방향지표 — 자기 과거를 기준으로 삼는 밴드가 이미 기울어 있는가")
    samp = df.groupby("bucket", observed=True, group_keys=False).sample(
        n=min(n_each, int(df["bucket"].value_counts().min())), random_state=7)

    def one(code):
        try:
            px = fdr.DataReader(code, "2021-08-01")["Close"].dropna()
            return (code, float(px.iloc[-1] / px.median())) if len(px) > 250 and px.median() > 0 else None
        except Exception:                            # noqa: BLE001
            return None

    got = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for fu in as_completed([ex.submit(one, c) for c in samp["Code"]]):
            if (r := fu.result()):
                got[r[0]] = r[1]
    samp = samp.assign(ratio=samp["Code"].map(got)).dropna(subset=["ratio"])
    g = samp.groupby("bucket", observed=True)["ratio"]
    print(pd.DataFrame({"n": g.size(), "현재가÷5년중앙": g.median(),
                        "1미만비율": g.apply(lambda s: (s < 1).mean())}).round(3).to_string())
    lo = g.median().iloc[0]
    say(BAD if lo < 0.8 else OK, f"최소형주 현재가가 자기 5년 중앙값의 {lo:.2f}배",
        "밴드가 자기 과거를 기준으로 삼는 이상, 이 상태에서는 '싸다' 외의 답이 나오기 어렵다.\n"
        "종목의 저평가가 아니라 자산군의 재평가를 종목 신호로 읽을 위험이다.")


def main() -> int:
    ap = argparse.ArgumentParser(description="적정가 판정의 규모 편향 진단")
    ap.add_argument("--limit", type=int, help="표본을 시총 등간격으로 줄여 빠르게 확인")
    ap.add_argument("--prices", action="store_true", help="② 방향지표까지(주가 5년 추가 수집)")
    ap.add_argument("--sample", type=int, default=50, help="--prices의 구간별 표본 수")
    a = ap.parse_args()

    df = collect(a.limit)
    print(f"표본 {len(df):,}종목 · 업종 {df['Sector'].nunique()}개")

    report_material(df)
    rel = report_relative(df)
    g3 = report_rim(df)
    report_blend(df, rel, g3)
    if a.prices:
        report_prices(df, a.sample)
    else:
        head("E. ② 방향지표 — 건너뜀")
        say(NA, "--prices를 주면 5년 주가를 받아 밴드가 이미 기울어 있는지 본다",
            "종목당 5년 일봉이 필요해 기본에서는 빼 둔다(구간별 50종목, 약 30초).")
    declare_limits()

    print("\n" + "=" * 72)
    print(f"확인 {_tally[OK]} · 문제 {_tally[BAD]} · 불가 {_tally[NA]}")
    print("=" * 72)
    if _tally[BAD]:
        print(f"\n규모 편향이 남아 있다 — 위 [문제] {_tally[BAD]}건. "
              "고치면 KNOWN_RHO와 이 스크립트의 기준선을 함께 내릴 것.")
    return 0                                         # 수동 진단이라 종료코드로 막지 않는다


if __name__ == "__main__":
    raise SystemExit(main())
