"""R1(데이터 신뢰성) 검증: 수집한 숫자가 이름표와 일치하는가.

계산이 옳은지·가정이 타당한지는 보지 않는다(R2의 일). 오직 '이 값이 정말 그 값인가'만 본다.

    python scripts/check_data_integrity.py            # 정적 검사 + 대표 종목 패널
    python scripts/check_data_integrity.py --static   # 정적 검사만 (네트워크 불필요)
    python scripts/check_data_integrity.py KR 105560  # 종목 하나만

각 점검은 [확인] / [문제] / [불가] 중 하나로 결론 낸다.
- 확인: 이름표와 값이 일치함을 근거를 들어 확인
- 문제: 불일치를 발견 (조서 1·2번 바구니 후보)
- 불가: 무료 데이터로는 대조할 원본이 없음 (조서 3번 바구니 후보)
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OK, BAD, NA = "[확인]", "[문제]", "[불가]"
_tally = {OK: 0, BAD: 0, NA: 0}


def say(verdict: str, title: str, detail: str = ""):
    _tally[verdict] = _tally.get(verdict, 0) + 1
    print(f"  {verdict} {title}")
    for line in (detail or "").splitlines():
        if line.strip():
            print(f"         {line}")


def pct(a, b):
    """b 대비 a의 괴리율(%) — b가 0/None이면 None."""
    if a is None or b in (None, 0):
        return None
    return (float(a) / float(b) - 1.0) * 100.0


# ── 정적 검사: 가격 계열이 쓰이는 모든 지점을 전수 분류 ────────────────────
#
# 이슈 #35는 '수정종가를 가격 계산에 썼다'는 한 종류의 실수였고, 고친 곳은 4군데였다.
# 같은 실수가 남았는지 보려면 **가격 계열을 읽는 모든 함수**를 나열하고 각각이
# 어느 쪽이어야 하는지 못 박아야 한다. 아래 두 레지스트리가 그 못이다.

# 총수익 기준이 맞는 곳 — 수정종가(d.prices / fetch_prices / auto_adjust=True)를 써야 한다.
TOTAL_RETURN_SITES = {
    ("src/analysis/capital_cost.py", "compute_capital_cost"): "베타 회귀 = 총수익 수익률",
    ("src/analysis/backtest.py", "run_backtest"): "미래수익(fwd_*) = 배당 포함 총수익",
    ("src/analysis/backtest.py", "_annual_daily"): "거래일 축 정렬용(값 무관)",
    ("src/analysis/etf.py", "_relative"): "상대성과 = 총수익 비교",
    ("src/analysis/etf.py", "_tracking_error"): "추적오차 = 총수익 기준",
    ("src/analysis/etf.py", "_trend"): "추세(이동평균) = 총수익 계열",
    ("src/analysis/valuation.py", "_fundamental_daily"): "거래일 축 정렬용(값 무관)",
    ("src/web/serialize.py", "_etf_rel_series"): "벤치마크 누적성과 오버레이",
    ("src/ui/pages/stock.py", "render"): "상대성과 차트",
    ("src/ui/charts.py", "relative_perf_chart"): "상대성과 차트",
    ("src/analysis/capital_cost.py", "estimate_beta"): "베타 회귀 = 총수익 수익률",
    ("src/analysis/capital_cost.py", "_weekly_returns"): "주간 수익률 변환",
    ("src/analysis/portfolio.py", "monthly_returns_krw"): "포트폴리오 월수익률",
    ("src/analysis/etf.py", "_relative_ratio_pct"): "상대비율 = 총수익 비교",
    ("src/ui/pages/home.py", "_market_sigma"): "시장 변동성 = 수익률 표준편차",
    ("src/web/serialize.py", "_market_sigma_est"): "시장 변동성 = 수익률 표준편차",
    ("src/ui/pages/portfolio.py", "render"): "포트폴리오 수익률",
    ("src/ui/pages/portfolio.py", "_fetch_px"): "포트폴리오 시세 수집",
    ("src/ui/pages/portfolio.py", "_asset_prices"): "자산군 시세 수집",
    ("src/ui/pages/portfolio.py", "_asset_class_stats"): "자산군 수익률 통계",
}

# 계열을 직접 고르지 않고 아래 함수로 넘기기만 하는 조립부 — 판단 대상이 아니다.
PASSTHROUGH_SITES = {
    ("src/web/serialize.py", "analyze"),
    ("src/web/serialize.py", "analyze_etf"),
    ("src/web/serialize.py", "portfolio_analyze"),
}

# '그날 실제로 붙어 있던 가격'이 필요한 곳 — 미조정(actual_prices / prices_raw)을 써야 한다.
PRICE_LEVEL_SITES = {
    ("src/analysis/valuation.py", "_band"): "역사적 PER·PBR 밴드",
    ("src/analysis/backtest.py", "run_backtest"): "밴드 신호(주가/펀더멘털 배수)",
    ("src/analysis/backtest.py", "_rim_discount"): "RIM 저평가율의 분모",
    ("src/analysis/ai_analysis.py", "build_opinion_context"): "52주 최고·최저",
    ("src/web/serialize.py", "_price"): "주가차트의 52주 최고·최저·밴드 내 위치",
    ("src/web/serialize.py", "_etf_price_series"): "ETF 종가 라인차트(밴드와 같은 축이어야 함)",
    ("src/ui/pages/stock.py", "render_price_tab"): "주가차트에 찍히는 가격",
    ("src/analysis/etf.py", "_dividend_band"): "배당수익률 역사밴드",
    ("src/analysis/etf.py", "_trend"): "52주 밴드·밴드 내 위치",
}

# 속성 접근으로만 인정하는 이름 — 지역변수 prices(예: 채권 가격 배열)를 오탐하지 않기 위해서다.
_ADJ_ATTRS = {"prices", "index_prices"}
_RAW_ATTRS = {"prices_raw"}
# 호출 이름은 속성·이름 어느 쪽으로 나타나도 인정한다.
_ADJ_CALLS = {"fetch_prices", "fetch_ohlcv", "fetch_index_prices"}
_RAW_CALLS = {"actual_prices", "fetch_prices_raw"}


def _scan_module(path: Path) -> dict[str, tuple[bool, bool]]:
    """{함수명: (수정종가 사용, 미조정 사용)} — 중첩 함수는 바깥 함수에 귀속."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return {}
    out: dict[str, tuple[bool, bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        adj = raw = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute):
                attr, name = sub.attr, sub.attr
            elif isinstance(sub, ast.Name):
                attr, name = None, sub.id
            else:
                continue
            if attr in _RAW_ATTRS or name in _RAW_CALLS:
                raw = True
            elif attr in _ADJ_ATTRS or name in _ADJ_CALLS:
                adj = True
        if adj or raw:
            prev = out.get(node.name, (False, False))
            out[node.name] = (prev[0] or adj, prev[1] or raw)
    return out


def static_check():
    print("\n[정적] A. 가격 계열 — 수정종가 vs 실제 거래가 전수 확인")
    found: dict[tuple[str, str], tuple[bool, bool]] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for fn, flags in _scan_module(path).items():
            found[(rel, fn)] = flags

    # ① 미조정을 써야 하는데 안 쓰는 곳
    missing = [(k, why) for k, why in PRICE_LEVEL_SITES.items()
               if k in found and not found[k][1]]
    gone = [k for k in PRICE_LEVEL_SITES if k not in found]
    if gone:
        say(NA, "레지스트리에 있으나 코드에서 사라진 지점",
            "\n".join(f"{f}:{fn} — 함수가 이동·개명됐을 수 있음" for f, fn in gone))
    if missing:
        say(BAD, f"미조정을 써야 하는데 수정종가만 쓰는 지점 {len(missing)}곳",
            "\n".join(f"{f}:{fn} — {why}" for (f, fn), why in missing))
    else:
        say(OK, f"등록된 가격형 계산 {len(PRICE_LEVEL_SITES) - len(gone)}곳 모두 미조정 사용",
            "이슈 #35에서 고친 4곳 + ETF 2곳이 여전히 actual_prices/prices_raw를 읽는다")

    # ② 어느 레지스트리에도 없는 지점 = 사람이 판단해야 하는 미분류
    known = set(TOTAL_RETURN_SITES) | set(PRICE_LEVEL_SITES) | PASSTHROUGH_SITES
    unknown = sorted(k for k in found
                     if k not in known and not k[0].startswith("src/data/"))
    if unknown:
        say(BAD, f"미분류 — 어느 계열이어야 하는지 못 박히지 않은 지점 {len(unknown)}곳",
            "\n".join(f"{f}:{fn}" for f, fn in unknown))
    else:
        say(OK, "가격 계열을 읽는 모든 분석·표현 함수가 레지스트리에 등록됨")


# ── 라이브 검사 ────────────────────────────────────────────────────────────
def _cache_age_note(yahoo_ticker: str) -> str:
    """두 가격 계열의 캐시 상태를 읽어 괴리의 원인을 짚는다.

    fetch_prices(수정종가)는 캐시가 없어 호출할 때마다 최신이고,
    fetch_prices_raw_df(미조정)는 12시간 file_cache가 걸려 있다. 장중에 캐시가 채워지면
    그 시점의 **미확정 장중가**가 '종가'로 최대 12시간 남는다 → 같은 날짜에 두 개의 가격.
    """
    import time

    import pandas as pd

    from src.data.cache import CACHE_DIR, _key

    lines = ["fetch_prices(수정종가)는 캐시 없음 · fetch_prices_raw_df(미조정)는 12시간 캐시"]
    try:
        key = _key("prices_raw", [yahoo_ticker, "5y"], {})
        for path in CACHE_DIR.glob(key + ".*"):
            age = (time.time() - path.stat().st_mtime) / 3600
            cached = pd.read_parquet(path)
            lines.append(f"미조정 캐시: {age:.2f}시간 전 저장 · 마지막행 "
                         f"{cached.index[-1].date()} {cached.iloc[-1, 0]:,.0f}")
    except Exception:
        lines.append("미조정 캐시 상태를 읽지 못했습니다")
    return "\n".join(lines)


def check_prices(d, label):
    print(f"\n[{label}] A. 가격 계열")
    raw = getattr(d, "prices_raw", None)
    adj = d.prices
    if raw is None or len(raw) == 0:
        say(NA, "미조정 시세 없음 — 폴백 경로", "prices_raw를 못 받아 모든 가격형 계산이 수정종가로 폴백")
        return
    # 최근일은 조정 대상이 아니므로 두 계열이 같아야 한다 → d.price(현재가)의 안전성 근거
    last_gap = pct(adj.iloc[-1], raw.iloc[-1])
    if last_gap is not None and abs(last_gap) < 0.5:
        say(OK, f"현재가는 두 계열이 일치 (괴리 {last_gap:+.3f}%)",
            f"d.price={d.price:,.2f} — 최근일은 배당·분할 조정 대상이 아니라 수정종가여도 실거래가와 같다")
    else:
        say(BAD, f"같은 날짜의 현재가가 두 계열에서 다름 (괴리 {last_gap:+.3f}%)",
            f"수정종가 {adj.iloc[-1]:,.0f}({adj.index[-1].date()}) vs "
            f"미조정 {raw.iloc[-1]:,.0f}({raw.index[-1].date()})\n"
            + _cache_age_note(d.yahoo_ticker))

    # 5년 전 시점의 괴리 = 배당 조정 누적 편향 (#35가 만든 왜곡의 크기)
    common = adj.index.intersection(raw.index)
    if len(common) > 0:
        first = common[0]
        gap0 = pct(adj.loc[first], raw.loc[first])
        say(OK, f"과거 조정폭 실측: {first.date()} 기준 {gap0:+.1f}%",
            "이 크기만큼 과거 배수가 낮게 깔린다 — 가격형 계산에 수정종가를 쓰면 그대로 편향이 된다")

    # 52주 최고·최저를 두 계열로 각각 계산 → 화면 표시값이 어느 쪽인지 드러낸다
    a52, r52 = adj.tail(252), raw.tail(252)
    if len(a52) > 100 and len(r52) > 100:
        lo_gap = pct(a52.min(), r52.min())
        hi_gap = pct(a52.max(), r52.max())
        cur = float(raw.iloc[-1])
        pos_adj = (float(adj.iloc[-1]) - a52.min()) / (a52.max() - a52.min()) * 100
        pos_raw = (cur - r52.min()) / (r52.max() - r52.min()) * 100
        detail = (f"수정종가 기준 저/고 {a52.min():,.0f}/{a52.max():,.0f} (밴드 내 {pos_adj:.0f}%)\n"
                  f"실거래가 기준 저/고 {r52.min():,.0f}/{r52.max():,.0f} (밴드 내 {pos_raw:.0f}%)\n"
                  f"저점 괴리 {lo_gap:+.2f}% · 고점 괴리 {hi_gap:+.2f}% · 위치 차이 {pos_adj - pos_raw:+.1f}%p")
        if abs(pos_adj - pos_raw) >= 1.0:
            say(BAD, "52주 밴드가 두 계열에서 다르게 나온다", detail)
        else:
            say(OK, "52주 밴드는 두 계열 차이가 무시할 수준", detail)


def check_units(d, info, label):
    print(f"\n[{label}] B. 단위·통화")
    fin_ccy = (info or {}).get("financialCurrency")
    if fin_ccy:
        if str(fin_ccy).upper() == d.currency.upper():
            say(OK, f"재무 통화 = 표시 통화 ({fin_ccy})")
        else:
            say(BAD, f"재무 통화({fin_ccy}) ≠ 표시 통화({d.currency})",
                "EPS·BPS는 재무 통화, 주가는 표시 통화라 PER·PBR이 환율배만큼 틀어진다")
    else:
        say(NA, "financialCurrency 미제공 — 재무 통화를 대조할 수 없음")

    calc = d.shares_outstanding * d.price
    gap = pct(d.market_cap, calc)
    if gap is not None and abs(gap) < 1.0:
        say(OK, f"시가총액 = 주식수 × 현재가 (괴리 {gap:+.2f}%)",
            f"{d.market_cap:,.0f} {d.currency}")
    else:
        say(BAD, f"시가총액이 주식수 × 현재가와 다름 (괴리 {gap:+.2f}%)")

    official_cap = (d.official or {}).get("시가총액")
    g2 = pct(d.market_cap, official_cap)
    if official_cap:
        verdict = OK if g2 is not None and abs(g2) < 5.0 else BAD
        say(verdict, f"공시 시가총액 대조: {official_cap:,.0f} vs 계산 {d.market_cap:,.0f} ({g2:+.2f}%)",
            "5% 넘게 벌어지면 주식수 기준(보통주/우선주 합산) 차이를 의심")
    else:
        say(NA, "공시 시가총액을 받지 못해 외부 대조 불가")

    # 피어 배당수익률: 소수(0.021)와 퍼센트(2.1)가 섞이면 업종 비교가 통째로 뒤집힌다
    peers = d.peers
    if peers is not None and "div_yield" in peers.columns:
        dy = peers["div_yield"].dropna()
        odd = dy[dy > 0.25]
        if len(odd):
            say(BAD, f"피어 배당수익률에 퍼센트 단위 의심값 {len(odd)}건",
                ", ".join(f"{k}={v:.3f}" for k, v in odd.items()))
        elif len(dy):
            say(OK, f"피어 배당수익률 {len(dy)}건 모두 소수 단위 (최대 {dy.max():.4f})")
        else:
            say(NA, "피어 배당수익률이 전부 결측")


def check_timing(d, label):
    print(f"\n[{label}] C. 시점 정합")
    fin = d.financials
    if "fiscal_end" in fin.columns and len(fin):
        fe = fin["fiscal_end"].dropna()
        if len(fe):
            last_fe = fe.iloc[-1]
            asof = d.prices.index[-1]
            months = (asof - last_fe).days / 30.44
            month = getattr(last_fe, "month", None)
            detail = (f"최근 결산 {last_fe.date()} (결산월 {month}월) · 주가 기준 {asof.date()} "
                      f"→ {months:.1f}개월 차이")
            if d.ttm is not None:
                say(OK, "TTM(최근 4분기)으로 시점 격차를 줄임", detail)
            else:
                say(BAD, "TTM 없이 연간 재무를 현재 주가와 비교", detail + "\nPER = 오늘 주가 ÷ 최대 1년 이상 지난 EPS")
            if month != 12:
                say(NA, f"결산월이 12월이 아님({month}월) — 롤링 TTM 창의 정합을 외부 대조 없이 확인 불가")
    else:
        say(NA, "fiscal_end 컬럼이 없어 재무 시점을 알 수 없음")

    own_per = (d.price / d.latest("eps")) if d.latest("eps") else None
    off_per = (d.official or {}).get("PER")
    if own_per and off_per:
        g = pct(own_per, off_per)
        verdict = OK if abs(g) < 15 else BAD
        say(verdict, f"자체 PER {own_per:.2f} vs 공시 PER {off_per:.2f} ({g:+.1f}%)",
            "공시 PER은 직전 결산 EPS 기준이 흔해 TTM과 차이가 난다 — 15% 넘으면 기준 불일치를 의심")
    else:
        say(NA, "자체·공시 PER 중 하나가 없어 교차검증 불가")


def check_retro(d, label):
    print(f"\n[{label}] D. 소급·생존 편향")
    from src.analysis.valuation import _fundamental_daily

    daily = _fundamental_daily(d, "eps", per_share=False)
    fe = d.financials["fiscal_end"].dropna() if "fiscal_end" in d.financials.columns else None
    if daily is not None and fe is not None and len(fe):
        applied = daily.dropna()
        if len(applied):
            first_applied = applied.index[0]
            # 계단은 '회계연도 종료 + 90일'부터 적용돼야 한다 (사업보고서 법정 기한)
            eligible = [f for f in fe if f + __import__("pandas").Timedelta(days=90) <= first_applied]
            lag = (first_applied - eligible[-1]).days if eligible else None
            if lag is not None and lag >= 90:
                say(OK, f"펀더멘털 계단이 결산 +{lag}일부터 적용 — 룩어헤드 없음",
                    "사업보고서 법정 기한(90일)을 지나 적용되므로 그 시점에 알 수 없던 값을 쓰지 않는다")
            else:
                say(BAD, f"펀더멘털이 결산 +{lag}일부터 적용 — 공시 전 데이터 사용 의심")
    else:
        say(NA, "펀더멘털 계단을 만들 수 없어 룩어헤드 확인 불가")

    n = 0 if d.peers is None else len(d.peers)
    say(NA, f"피어 {n}개는 '오늘' 기준 구성",
        "과거 시점 상장·업종 구성 목록이 무료로 없어, 상장폐지·편입탈락 종목이 빠진 생존자만 남는다.\n"
        "지금은 현재 시점 횡단면 비교에만 쓰므로 편향이 결과에 들어가지 않지만,\n"
        "피어 기반 지표를 과거로 되돌리는 계산을 추가하면 그때부터 생존편향이 생긴다.")


def check_missing(d, label):
    print(f"\n[{label}] E. 결측 처리")
    import pandas as pd
    from src.data.models import FIN_COLUMNS

    fin = d.financials
    n = len(fin)
    empty = [c for c in FIN_COLUMNS if c not in fin.columns or fin[c].isna().all()]
    partial = {c: int(fin[c].isna().sum()) for c in FIN_COLUMNS
               if c in fin.columns and 0 < fin[c].isna().sum() < n}
    say(OK, f"연간 재무 {n}개년 · 전부 결측 {len(empty)}개 항목 · 일부 결측 {len(partial)}개 항목",
        (f"전부 결측: {', '.join(empty)}" if empty else "전부 결측 없음"))

    # 0은 '값이 0'과 '값이 없어 0으로 채워짐'을 구분할 수 없다 — 가장 위험한 결측 처리
    zeros = {c: int((fin[c] == 0).sum()) for c in FIN_COLUMNS
             if c in fin.columns and (fin[c] == 0).any()}
    if zeros:
        say(BAD, f"정확히 0인 셀이 있는 항목 {len(zeros)}개",
            ", ".join(f"{c}×{k}" for c, k in zeros.items())
            + "\n0은 '실제 0'과 '결측을 0으로 채움'을 구분하지 못한다 — 원천을 확인해야 함")
    else:
        say(OK, "재무 항목에 0으로 채워진 셀 없음 (결측은 NaN으로 남음)")

    _check_interest_expense(d, fin)
    if pd.notna(d.market_cap) and d.market_cap <= 0:
        say(BAD, "시가총액이 0 이하")


def _check_interest_expense(d, fin):
    """interest_expense가 '손익의 이자비용'인지 '현금흐름의 이자지급'인지 대조한다.

    FIN_COLUMNS는 이 컬럼을 '이자비용(양수)'로 못 박았고 capital_cost가 k_d = 이자비용 /
    평균차입금으로 쓴다. 그런데 opendart.DART_MAP은 이 컬럼을 현금흐름표의 '이자의지급'
    (ifrs-full_InterestPaidClassifiedAsOperatingActivities)에 매핑하고, merge_financials가
    DART를 우선하므로 yfinance의 손익 항목을 덮어쓴다. 두 값은 발생주의/현금주의 차이만큼
    다르다 — 이름표와 값이 어긋나는지 실제 숫자로 확인한다.
    """
    if "interest_expense" not in fin.columns:
        say(NA, "interest_expense 컬럼 없음")
        return
    ie = fin["interest_expense"].dropna()
    if not len(ie):
        say(NA, "interest_expense 전부 결측 — k_d가 가정값(R_f+2%p)으로 폴백")
        return
    if int((ie < 0).sum()):
        say(BAD, f"interest_expense에 음수 {int((ie < 0).sum())}개 — '양수' 규약 위반")

    if d.market != "KR":
        say(OK, f"interest_expense = yfinance 손익 항목 (최근 {ie.iloc[-1]:,.0f})")
        return
    try:
        from src.data.opendart import get_dart_financials
        dart, src, _ = get_dart_financials(d.ticker)
    except Exception:
        dart, src = None, ""
    dart_ie = (dart["interest_expense"].dropna()
               if dart is not None and "interest_expense" in dart.columns else None)
    if dart_ie is None or not len(dart_ie):
        say(OK, "DART에 이자 항목이 없어 yfinance 손익 이자비용이 그대로 쓰임",
            f"출처={src or 'Yahoo Finance'} · 최근값 {ie.iloc[-1]:,.0f}")
        return

    debt = fin["total_debt"].dropna()
    avg_debt = float(debt.tail(2).mean()) if len(debt) else None
    lines = [f"DART(현금흐름 '이자의지급') {dart_ie.iloc[-1]:,.0f}",
             f"→ merge_financials가 DART를 우선해 이 값이 k_d의 분자가 된다"]
    if avg_debt:
        lines.append(f"k_d = {dart_ie.iloc[-1] / avg_debt * 100:.2f}% "
                     f"(평균차입금 {avg_debt:,.0f} 기준)")
    say(BAD, "interest_expense가 손익의 '이자비용'이 아니라 현금흐름의 '이자지급'",
        "\n".join(lines))


def run_one(market: str, query: str):
    import yfinance as yf

    if market.upper() == "KR":
        from src.data.kr_provider import KRProvider
        p = KRProvider()
    else:
        from src.data.us_provider import USProvider
        p = USProvider()
    d = p.load(query, peer_count=8)
    label = f"{d.name}({d.ticker})"
    print(f"\n{'=' * 72}\n{label} · {market.upper()} · 현재가 {d.price:,.2f} {d.currency}\n{'=' * 72}")
    try:
        info = yf.Ticker(d.yahoo_ticker).info or {}
    except Exception:
        info = {}
    check_prices(d, label)
    check_units(d, info, label)
    check_timing(d, label)
    check_retro(d, label)
    check_missing(d, label)


# 대표 종목 — 고배당/저배당 · 한국/미국 · 금융/제조 · ADR(재무 통화가 다른 경우)
PANEL = [("KR", "105560"),   # KB금융 — 한국·금융·고배당 (#35의 실측 대상)
         ("KR", "005930"),   # 삼성전자 — 한국·제조·저배당
         ("US", "KO"),       # 코카콜라 — 미국·제조·고배당
         ("US", "AAPL"),     # 애플 — 미국·기술·저배당
         ("US", "TSM")]      # TSMC ADR — 재무 통화(TWD) ≠ 표시 통화(USD)


def main(argv):
    static_only = "--static" in argv
    argv = [a for a in argv if not a.startswith("--")]
    static_check()
    if static_only:
        pass
    elif len(argv) >= 2:
        run_one(argv[0], argv[1])
    else:
        for market, q in PANEL:
            try:
                run_one(market, q)
            except Exception as e:
                print(f"\n  [불가] {market} {q} 수집 실패: {type(e).__name__}: {e}")
                _tally[NA] = _tally.get(NA, 0) + 1
    print(f"\n{'=' * 72}")
    print(f"합계  확인 {_tally[OK]} · 문제 {_tally[BAD]} · 불가 {_tally[NA]}")
    print("'문제'는 조서 1·2번 바구니, '불가'는 3번 바구니(한계) 후보다.")


if __name__ == "__main__":
    main(sys.argv[1:])
