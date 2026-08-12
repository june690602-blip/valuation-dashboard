"""로드 경로를 병렬로 바꿔도 **출력이 같아야 한다** (ADR-0045).

빨라졌다는 것은 초시계가 말해 준다. 어려운 쪽은 *아무것도 안 바뀌었다*는 증명이라,
이 파일이 지키는 것은 속도가 아니라 병렬화가 실제로 깨뜨리는 두 가지다.

**㉠ 경고 순서.** `d.warnings`는 화면 문구의 순서이면서, `serialize._peers`가 "피어 기준"
줄을 찾아 읽는 목록이기도 하다. 완료 순서로 이어붙이면 실행할 때마다 달라진다 —
빠른 응답이 먼저 붙는 날과 아닌 날의 화면이 다르다는 뜻이다.

**㉡ 실패의 의미.** 퓨처로 바꾸면 모든 실패가 `.result()`에서 똑같은 모양으로 올라온다.
그런데 US의 시세 실패는 **치명**(분석 중단)이고 KR의 미조정 시세 실패는 **경고**다.
그 구분이 사라지면 폐지 종목이 빈 화면을 받거나, 반대로 멀쩡한 종목이 통째로 죽는다.

실제 종목에서의 전수 비교는 `scripts/check_payload_parity.py`가 맡는다(네트워크 필요).
여기서는 네트워크 없이 **결정적으로** 잰다.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import pandas as pd

from src.data.parallel import Outcome, gather
from src.data.progress import bind_reporter, set_reporter

# 가짜 태스크 하나가 자는 시간. 병렬이면 총 시간이 합이 아니라 최댓값 쪽으로 간다.
NAP = 0.15

KRX_SECTOR = "통신 및 방송 장비 제조업"     # 회귀 계수표가 아는 거래소 라벨(ADR-0044)
AI_SECTOR = "반도체"                        # AI가 지어 주는 이름 — 표시·피어 전용


def _listing() -> pd.DataFrame:
    codes = ["005930", "000660", "009150", "011070", "005935", "042700"]
    return pd.DataFrame({
        "Code": codes,
        "Name": ["삼성전자", "SK하이닉스", "삼성전기", "LG이노텍", "삼성전자우", "한미반도체"],
        "Market": ["KOSPI"] * 6,
        "Sector": [KRX_SECTOR] * 6,
        "SubSector": ["휴대폰"] * 6,
        "Stocks": [5.9e9, 7.2e8, 7.4e7, 2.4e7, 8.2e8, 9.7e7],
        "Marcap": [4.2e14, 1.3e14, 1.1e13, 5.0e12, 4.0e13, 8.0e12],
    })


def _financials() -> pd.DataFrame:
    return pd.DataFrame({
        "revenue": [2.0e14, 2.3e14, 2.6e14],
        "operating_income": [2.0e13, 3.0e13, 3.5e13],
        "net_income": [1.5e13, 2.2e13, 2.6e13],
        "total_assets": [4.0e14, 4.3e14, 4.6e14],
        "total_equity": [3.0e14, 3.2e14, 3.4e14],
        "ocf": [4.0e13, 5.0e13, 6.0e13],
        "capex": [3.0e13, 3.2e13, 3.4e13],
        "eps": [2000.0, 3000.0, 3500.0],
    }, index=[2023, 2024, 2025])


def _prices() -> pd.Series:
    idx = pd.date_range("2025-01-01", periods=400, freq="D")
    return pd.Series([70000.0 + i for i in range(400)], index=idx, name="005930.KS")


def _peer_table() -> pd.DataFrame:
    idx = ["005930.KS", "000660.KS", "009150.KS", "011070.KS", "042700.KS"]
    return pd.DataFrame({
        "name": ["삼성전자", "SK하이닉스", "삼성전기", "LG이노텍", "한미반도체"],
        "market_cap": [4.2e14, 1.3e14, 1.1e13, 5.0e12, 8.0e12],
        "per": [12.0, 9.0, 14.0, 11.0, 30.0],
        "forward_per": [10.0, 8.0, 12.0, 10.0, 25.0],
        "pbr": [1.4, 1.6, 1.1, 0.9, 5.0],
        "roe": [0.11, 0.18, 0.08, 0.07, 0.20],
        "div_yield": [0.02, 0.01, 0.015, 0.02, 0.005],
        "is_self": [True, False, False, False, False],
    }, index=idx)


class _FakeTicker:
    """`yf.Ticker` 대역 — 태스크마다 새로 만들어지는 것이 정상이다(스레드 안전 때문).

    `info`가 빈 사전인 것이 요점이다: 상장목록에 주식수가 없을 때 타는 폴백 경로를
    네트워크 없이 재현한다.
    """

    def __init__(self, ticker):
        self.ticker, self.info = ticker, {}


class _Harness:
    """`kr_provider`의 네트워크 이름들을 통째로 가짜로 바꾼다.

    provider가 `from .base import ...`로 이름을 당겨 갔으므로 **provider 이름공간에**
    심는다 — `src.data.base` 쪽만 바꾸면 provider는 원본을 계속 본다(이 저장소가
    `check_load_timing.py`에서 한 번 밟은 함정이다).
    """

    def __init__(self, *, nap: float = 0.0, ai_codes=None, ai_err=None,
                 raw_ok: bool = True, naver_ok: bool = True, shares_ok: bool = True,
                 dart_ok: bool = True):
        self.nap = nap
        self.ai_codes = ai_codes
        self.ai_err = ai_err
        self.raw_ok, self.naver_ok = raw_ok, naver_ok
        self.shares_ok, self.dart_ok = shares_ok, dart_ok
        self._patches: list = []

    def _sleep(self):
        if self.nap:
            time.sleep(self.nap)

    # ── 가짜 원천들 ────────────────────────────────────────────────
    def _fin(self, tk):
        self._sleep()
        return _financials(), ["재무경고"]

    def _dart(self, code):
        self._sleep()
        if not self.dart_ok:
            raise RuntimeError("DART 죽음")
        return _financials(), "DART 연결재무제표", ["DART경고"]

    def _price_pair(self, yt, period="5y"):
        self._sleep()
        if self.raw_ok:
            return _prices(), _prices(), []
        from src.data.base import RAW_PRICE_WARNING
        return _prices(), None, [RAW_PRICE_WARNING]

    def _ttm(self, tk, shares):
        self._sleep()
        return pd.Series({"net_income": 2.6e13, "revenue": 2.6e14}), ["TTM경고"]

    def _naver(self, code):
        self._sleep()
        if not self.naver_ok:
            raise RuntimeError("네이버 죽음")
        return {"per": 12.0, "pbr": 1.4, "eps": 3500.0, "bps": 50000.0,
                "div_yield": 0.02, "dps": 1400.0, "market_cap": 4.2e14,
                "forward_per": 10.0, "forward_eps": 4200.0, "target_mean": 95000.0,
                "recomm_score": 4.2, "source": "네이버금융", "consensus_date": "2026-08"}

    def _index(self, symbol, period="5y"):
        self._sleep()
        return _prices()

    def _ai(self, name, hint, listing):
        self._sleep()
        if self.ai_codes:
            return AI_SECTOR, AI_SECTOR, list(self.ai_codes), None
        return None, None, None, self.ai_err

    def __enter__(self):
        listing = _listing()
        if not self.shares_ok:
            listing = listing.assign(Stocks=[None] * len(listing))
        targets = {
            "find_kr": lambda q: listing[listing["Code"] == q],
            "get_kr_listing": lambda: listing,
            "yahoo_ticker_kr": lambda code, market="KOSPI": f"{code}.KS",
            "select_peers_kr": lambda code, n=10: ["005930", "000660", "009150",
                                                   "011070", "042700"],
            "detect_financial": lambda sector, industry, market: False,
            "extract_financials": self._fin,
            "extract_ttm": self._ttm,
            "fetch_price_pair": self._price_pair,
            "fetch_index_prices": self._index,
            "fetch_naver_fundamental": self._naver,
            "get_dart_financials": self._dart,
            "_ai_classify_kr": self._ai,
            "build_peer_table": lambda tickers, self_t, labels=None: _peer_table(),
        }
        for name, fn in targets.items():
            p = patch(f"src.data.kr_provider.{name}", fn)
            p.start()
            self._patches.append(p)
        # 주식수가 없을 때만 타는 `tk.info` 폴백을 막는다(여기서 네트워크를 타면 안 된다).
        p = patch("src.data.kr_provider.yf.Ticker", _FakeTicker)
        p.start()
        self._patches.append(p)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _load(**kw):
    from src.data.kr_provider import KRProvider
    with _Harness(**kw):
        return KRProvider().load("005930", peer_count=9)


class WarningOrderTests(unittest.TestCase):
    """경고는 **순차 시절과 같은 순서**로 붙어야 한다 — 이 순서가 곧 화면 문구 순서다."""

    def test_happy_path_keeps_the_sequential_order(self):
        d = _load()
        self.assertEqual(d.warnings[:4], [
            "재무경고",                                   # 1. yfinance 연간 재무
            "DART경고",                                   # 2. DART 원본
            "재무제표: DART 연결재무제표 3개년 사용 "     # 3. 병합 사실
            "(EBITDA·차입금 등 일부는 yfinance로 보완)",
            "TTM경고",                                    # 4. 분기 합산
        ])
        # 피어 기준 줄은 늘 마지막 무리에 있다 — serialize._peers가 이 접두사로 찾는다.
        self.assertTrue(any(w.startswith("피어 기준") for w in d.warnings))

    def test_the_order_is_stable_across_runs(self):
        # 병렬 그룹의 완료 순서는 실행마다 다르다. 그래도 경고 순서는 같아야 한다.
        runs = {tuple(_load().warnings) for _ in range(5)}
        self.assertEqual(len(runs), 1, "실행마다 경고 순서가 달라졌다")

    def test_unadjusted_price_failure_is_a_warning_not_fatal(self):
        from src.data.base import RAW_PRICE_WARNING
        d = _load(raw_ok=False)
        self.assertIsNone(d.prices_raw)
        # 미조정 경고는 **재무 병합 뒤, TTM 앞**이다(순차 시절의 자리).
        self.assertEqual(d.warnings[3], RAW_PRICE_WARNING)
        self.assertEqual(d.warnings[4], "TTM경고")

    def test_naver_failure_degrades_instead_of_raising(self):
        d = _load(naver_ok=False)
        self.assertIn("네이버 금융 지표 조회 실패 — 재무제표 기반 계산값만 사용합니다.",
                      d.warnings)
        self.assertIsNone(d.consensus)

    def test_dart_failure_falls_back_to_yfinance(self):
        d = _load(dart_ok=False)
        self.assertNotIn("DART경고", d.warnings)
        self.assertEqual(d.official["재무출처"], "Yahoo Finance")

    def test_missing_shares_still_raises_with_the_stock_name(self):
        with self.assertRaises(ValueError) as ctx:
            _load(shares_ok=False)
        self.assertIn("상장주식수", str(ctx.exception))
        self.assertIn("삼성전자", str(ctx.exception))


class SectorLabelSurvivesParallelTests(unittest.TestCase):
    """ADR-0044가 병렬화에서 되돌아가지 않았는지 — 회귀는 거래소 라벨, 표시는 AI 라벨."""

    def test_ai_label_shows_but_regression_keeps_the_exchange_label(self):
        d = _load(ai_codes=["000660", "009150", "011070", "042700"])
        self.assertEqual(d.sector, AI_SECTOR)
        self.assertEqual(d.sector_official, KRX_SECTOR)

    def test_ai_failure_is_reported_to_the_user(self):
        d = _load(ai_err="할당량 초과")
        self.assertTrue(any("AI 업종분류를 시도했으나 실패" in w for w in d.warnings))


class ActuallyParallelTests(unittest.TestCase):
    def test_the_load_group_overlaps_instead_of_queueing(self):
        """일곱 태스크가 각각 NAP초 자면, 총 시간은 **합이 아니라** 그 근처여야 한다.

        피어 보정(`_patch_kr_peers`)은 조인 뒤에 따로 도는 단계라 여기서 빼고 잰다 —
        이 테스트가 보는 것은 병렬 그룹 하나다. (그 단계도 ADR-0046에서 병렬이 됐고,
        겹치는지는 `PeerPatchIsParallelTests`가 따로 지킨다.)
        """
        from src.data.kr_provider import KRProvider
        with patch.object(KRProvider, "_patch_kr_peers", staticmethod(lambda p, l: p)):
            t0 = time.perf_counter()
            _load(nap=NAP)
            elapsed = time.perf_counter() - t0
        sequential = 7 * NAP     # 그룹 구성원 수
        self.assertLess(elapsed, sequential * 0.7,
                        f"{elapsed:.2f}s — 순차({sequential:.2f}s)와 구별되지 않는다")


class GatherContractTests(unittest.TestCase):
    def test_unwrap_reraises_the_original_exception(self):
        """치명적인 태스크의 안내문은 갈아 끼우지 않는다 — 타입도 메시지도 그대로다."""
        got = gather([("boom", lambda: (_ for _ in ()).throw(ValueError("상장폐지 안내문")))])
        with self.assertRaises(ValueError) as ctx:
            got["boom"].unwrap()
        self.assertEqual(str(ctx.exception), "상장폐지 안내문")

    def test_or_else_keeps_the_rest_alive(self):
        got = gather([("bad", lambda: 1 / 0), ("good", lambda: "값")])
        self.assertEqual(got["bad"].or_else("폴백"), "폴백")
        self.assertEqual(got["good"].or_else("폴백"), "값")
        self.assertFalse(got["bad"].ok)

    def test_empty_task_list_is_not_an_error(self):
        self.assertEqual(gather([]), {})

    def test_outcome_without_error_unwraps_to_its_value(self):
        self.assertEqual(Outcome(value=3).unwrap(), 3)


class ProgressCrossesThreadsTests(unittest.TestCase):
    """`progress`는 `threading.local()`이라 심어 주지 않으면 워커에서 침묵한다."""

    def test_reporter_reaches_the_worker(self):
        from src.data.progress import report
        seen: list = []
        set_reporter(lambda stage, done, total: seen.append((stage, done, total)))
        try:
            gather([("t", bind_reporter(lambda: report("워커 안", 1, 1)))])
        finally:
            set_reporter(None)
        self.assertIn(("워커 안", 1, 1), seen)

    def test_worker_does_not_keep_the_reporter_after_finishing(self):
        # 풀 워커는 재사용된다 — 안 지우면 다음 태스크가 남의 리포터를 물려받는다.
        leaked: list = []
        set_reporter(lambda *a: leaked.append(a))
        try:
            wrapped = bind_reporter(lambda: None)
        finally:
            set_reporter(None)
        wrapped()
        from src.data.progress import current_reporter
        self.assertIsNone(current_reporter())

    def test_the_group_reports_its_stage(self):
        seen: list = []
        set_reporter(lambda stage, done, total: seen.append((stage, done, total)))
        try:
            gather([("a", lambda: 1), ("b", lambda: 2)], stage="자료 수집")
        finally:
            set_reporter(None)
        self.assertEqual(seen[0], ("자료 수집", 0, 2))
        self.assertEqual(seen[-1], ("자료 수집", 2, 2))


class NewsOrderTests(unittest.TestCase):
    """뉴스 셋을 동시에 받아도 **기업 → 산업 → 거시** 순서로 이어붙여야 한다."""

    @staticmethod
    def _item(title):
        return {"title": title, "link": "", "source": "", "date": ""}

    def test_dedup_keeps_the_first_occurrence_in_fixed_order(self):
        from src.web.serialize import _dedup_news
        company = [self._item("겹치는 기사"), self._item("기업 기사")]
        sector = [self._item("겹치는 기사"), self._item("산업 기사")]
        macro = [self._item("거시 기사")]
        out = _dedup_news([company, sector, macro])
        self.assertEqual([it["title"] for it in out],
                         ["겹치는 기사", "기업 기사", "산업 기사", "거시 기사"])

    def test_prefetch_result_matches_the_sequential_concatenation(self):
        import src.web.serialize as ser

        class _D:
            name, market, yahoo_ticker = "삼성전자", "KR", "005930.KS"
            sector, industry = AI_SECTOR, ""

        with patch.object(ser, "_company_news", lambda *a: [self._item("기업")]), \
             patch.object(ser, "_sector_news", lambda *a: [self._item("산업")]), \
             patch.object(ser, "_macro_news", lambda *a: [self._item("거시")]):
            pre = ser._start_news_for(name="삼성전자", market="KR", yahoo_ticker="005930.KS")
            try:
                got = pre.finish(_D())
            finally:
                pre.close()
        self.assertEqual([it["title"] for it in got], ["기업", "산업", "거시"])

    def test_prefetch_is_discarded_when_the_name_turns_out_different(self):
        """S&P500 밖 종목은 info를 봐야 이름이 정해진다 — 다른 질의로 받은 기사는 버린다."""
        import src.web.serialize as ser
        calls: list = []

        class _D:
            name, market, yahoo_ticker = "Palantir Technologies", "US", "PLTR"
            sector, industry = "Software", ""

        def _company(name, market, yt):
            calls.append(name)
            return [self._item(f"기업:{name}")]

        with patch.object(ser, "_company_news", _company), \
             patch.object(ser, "_sector_news", lambda *a: []), \
             patch.object(ser, "_macro_news", lambda *a: []):
            pre = ser._start_news_for(name="PLTR", market="US", yahoo_ticker="PLTR")
            try:
                got = pre.finish(_D())
            finally:
                pre.close()
        # 미리 받아 둔 'PLTR' 질의 결과는 버려지고 실제 종목명으로 다시 받는다.
        # (미리 받은 쪽이 언제 끝나는지는 스레드 사정이라 결과로만 판단한다.)
        self.assertEqual([it["title"] for it in got], ["기업:Palantir Technologies"])
        self.assertIn("Palantir Technologies", calls)


class PeerPatchIsParallelTests(unittest.TestCase):
    """피어 보정(`_patch_kr_peers`)의 네이버 조회도 겹쳐야 한다 (ADR-0046).

    ADR-0045가 병렬화한 것은 **조인 앞의 그룹**이고, 이 루프는 조인 뒤에 남아
    피어마다 네이버 페이지를 한 장씩 순서대로 받고 있었다. 캐시(12시간)가 차 있으면
    실측 0.01초라 안 보였는데, **캐시가 통째로 빈 첫 조회에서는 3.60초**였다
    (완전 콜드 12.33초 중 29.2% — `scripts/check_load_timing.py`).

    빨라졌다는 것은 초시계가 말하므로, 여기서 지키는 것은 **값이 그대로라는 것**이다.
    """

    COLS = ["market_cap", "per", "forward_per", "pbr", "div_yield", "roe"]

    def _frame(self, n: int) -> pd.DataFrame:
        codes = [f"{100000 + i:06d}" for i in range(n)]
        return pd.DataFrame(
            {c: [float("nan")] * n for c in self.COLS},
            index=[f"{c}.KS" for c in codes])

    def _listing(self, n: int) -> pd.DataFrame:
        codes = [f"{100000 + i:06d}" for i in range(n)]
        return pd.DataFrame(
            {"Name": [f"피어{i}" for i in range(n)],
             "Market": ["KOSPI"] * n,
             "Marcap": [1_000.0 + i for i in range(n)]},
            index=codes)

    @staticmethod
    def _nv(code: str) -> dict:
        seed = int(code) % 97
        return {"per": float(seed), "forward_per": float(seed) + 0.5,
                "pbr": float(seed) / 10, "div_yield": float(seed) / 100,
                "roe_approx": float(seed) / 5}

    def test_peer_lookups_overlap_instead_of_queueing(self):
        """피어 여덟이 각각 NAP초 자면 총 시간은 **합이 아니라** 그 근처여야 한다."""
        from src.data.kr_provider import KRProvider

        def slow(code):
            time.sleep(NAP)
            return self._nv(code)

        with patch("src.data.kr_provider.fetch_naver_fundamental", slow):
            t0 = time.perf_counter()
            KRProvider._patch_kr_peers(self._frame(8), self._listing(8))
            elapsed = time.perf_counter() - t0
        sequential = 8 * NAP
        self.assertLess(elapsed, sequential * 0.5,
                        f"{elapsed:.2f}s — 순차({sequential:.2f}s)와 구별되지 않는다")

    def test_the_values_are_the_ones_the_sequential_loop_produced(self):
        """겹쳐 받아도 **어느 값이 어느 행에 들어가는지**가 흔들리면 안 된다.

        피어 표는 규모 창 비교와 업종 상대점수의 재료라, 행이 한 칸 밀리면
        판정까지 조용히 달라진다.
        """
        from src.data.kr_provider import KRProvider

        n = 12
        with patch("src.data.kr_provider.fetch_naver_fundamental", self._nv):
            out = KRProvider._patch_kr_peers(self._frame(n), self._listing(n))

        for i in range(n):
            code = f"{100000 + i:06d}"
            yt, want = f"{code}.KS", self._nv(code)
            self.assertEqual(out.at[yt, "per"], want["per"], yt)
            self.assertEqual(out.at[yt, "forward_per"], want["forward_per"], yt)
            self.assertEqual(out.at[yt, "pbr"], want["pbr"], yt)
            self.assertEqual(out.at[yt, "div_yield"], want["div_yield"], yt)
            self.assertEqual(out.at[yt, "roe"], want["roe_approx"], yt)
            # 시총은 '결측 보완'이 아니라 KRX로 **덮어쓰기**다(ADR-0044 계열).
            self.assertEqual(out.at[yt, "market_cap"], 1_000.0 + i, yt)

    def test_one_failing_peer_does_not_cost_the_others(self):
        """무료 데이터라 결측이 흔하다 — 한 피어의 실패가 나머지를 데려가면 안 된다."""
        from src.data.kr_provider import KRProvider

        def flaky(code):
            if code == "100003":
                raise RuntimeError("naver down")
            return self._nv(code)

        with patch("src.data.kr_provider.fetch_naver_fundamental", flaky):
            out = KRProvider._patch_kr_peers(self._frame(6), self._listing(6))

        self.assertTrue(pd.isna(out.at["100003.KS", "per"]))
        # 실패해도 시총 덮어쓰기는 네트워크가 아니므로 그대로 적용된다.
        self.assertEqual(out.at["100003.KS", "market_cap"], 1_003.0)
        self.assertEqual(out.at["100005.KS", "per"], self._nv("100005")["per"])


if __name__ == "__main__":
    unittest.main()
