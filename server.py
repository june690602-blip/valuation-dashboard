"""투자지표 웹서버 — 정적 Meridian 페이지 + 실데이터 분석 API (표준 라이브러리만 사용).

실행:  python server.py           (기본 포트 5178)
        PORT=8000 python server.py

- 정적: web/ 폴더를 그대로 서빙 ('/' → stock.html)
- API : GET /api/analyze?market=KR&query=035420[&peer_count=9&news=0]
        → src.web.serialize.analyze() 결과 JSON (인프로세스 캐시 30분)

Flask 등 추가 의존성 없음 — Streamlit 앱이 쓰는 패키지(pandas·yfinance…)만 있으면 된다.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))

_CACHE: dict = {}
_AI_CACHE: dict = {}
_LOCK = threading.Lock()
_TTL = 1800     # 30분
_AI_TTL = 21600  # 6시간 (AI 결과는 헤드라인·펀더멘털이 크게 안 바뀌므로 길게 캐시)


def cached_analyze(market: str, query: str, peer_count: int, include_news: bool) -> dict:
    key = (market, query, peer_count, include_news)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    from src.web.serialize import analyze  # 지연 임포트(서버 기동을 빠르게)
    data = analyze(market, query, peer_count=peer_count, include_news=include_news)
    with _LOCK:
        _CACHE[key] = (now, data)
    return data


def cached_generic(key: str, fn, ttl: int = _TTL) -> dict:
    """범용 캐시 — 채권 곡선·히스토리 등 파라미터 적은 결과에 사용."""
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(("g", key))
        if hit and now - hit[0] < ttl:
            return hit[1]
    data = fn()
    with _LOCK:
        _CACHE[("g", key)] = (now, data)
    return data


def cached_ai(kind: str, market: str, query: str, peer_count: int) -> dict:
    """Gemini AI 결과 캐시. kind: 'news'(뉴스 분석) | 'opinion'(종합 투자평가)."""
    key = (kind, market, query, peer_count)
    now = time.time()
    with _LOCK:
        hit = _AI_CACHE.get(key)
        if hit and now - hit[0] < _AI_TTL:
            return hit[1]
    from src.web.serialize import ai_news, ai_opinion  # 지연 임포트
    fn = ai_news if kind == "news" else ai_opinion
    data = fn(market, query, peer_count=peer_count)
    with _LOCK:
        _AI_CACHE[key] = (now, data)
    return data


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def _send_json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/api/analyze":
            q = parse_qs(u.query)
            market = (q.get("market", ["KR"])[0] or "KR").upper()
            query = (q.get("query", [""])[0] or "").strip()
            try:
                peer_count = max(5, min(15, int(q.get("peer_count", ["9"])[0])))
            except ValueError:
                peer_count = 9
            include_news = q.get("news", ["1"])[0] != "0"
            if not query:
                return self._send_json({"error": "종목(query)을 입력하세요."}, 400)
            try:
                t0 = time.time()
                data = cached_analyze(market, query, peer_count, include_news)
                print(f"[api] {market} {query} → {data['meta']['name']} ({time.time() - t0:.1f}s)")
                return self._send_json(data)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path in ("/api/news_ai", "/api/opinion"):
            q = parse_qs(u.query)
            market = (q.get("market", ["KR"])[0] or "KR").upper()
            query = (q.get("query", [""])[0] or "").strip()
            try:
                peer_count = max(5, min(15, int(q.get("peer_count", ["9"])[0])))
            except ValueError:
                peer_count = 9
            if not query:
                return self._send_json({"error": "종목(query)을 입력하세요."}, 400)
            from src.data.gemini import is_available
            if not is_available():
                return self._send_json(
                    {"error": "Gemini API 키가 설정되지 않았습니다. .streamlit/secrets.toml에 "
                              "GEMINI_API_KEY를 넣으세요."}, 400)
            kind = "news" if u.path == "/api/news_ai" else "opinion"
            try:
                t0 = time.time()
                data = cached_ai(kind, market, query, peer_count)
                print(f"[ai:{kind}] {market} {query} ({time.time() - t0:.1f}s)")
                return self._send_json(data)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/risk-profile":
            try:
                from src.analysis.risk_profile import risk_profile_config
                return self._send_json(risk_profile_config())
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/market":
            try:
                from src.web.serialize import market_params
                return self._send_json(cached_generic("market", market_params, ttl=3600))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/bond":
            try:
                from src.web.serialize import bond_data
                return self._send_json(cached_generic("bond", bond_data))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/bond_history":
            q = parse_qs(u.query)
            market = (q.get("market", ["KR"])[0] or "KR").upper()
            try:
                tenor = int(q.get("tenor", ["10"])[0])
            except ValueError:
                tenor = 10
            try:
                from src.web.serialize import bond_history
                return self._send_json(
                    cached_generic(f"bh:{market}:{tenor}", lambda: bond_history(market, tenor)))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path in ("/", "/index.html"):
            self.path = "/home.html"   # 진입점 = 홈(랜딩). 주식 페이지는 nav·예시카드로 이동.
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/api/risk-profile":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                req = json.loads(body or "{}")
                if not isinstance(req, dict):
                    return self._send_json({"error": "JSON 객체가 필요합니다."}, 400)
                answers = req.get("answers")
                if not isinstance(answers, list):
                    return self._send_json({"error": "answers 배열이 필요합니다."}, 400)
                from src.analysis.risk_profile import grade, profile_to_dict
                return self._send_json(profile_to_dict(grade(answers)))
            except (ValueError, TypeError) as e:
                return self._send_json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/portfolio":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                req = json.loads(body or "{}")
            except Exception as e:  # noqa: BLE001
                return self._send_json({"error": f"요청 파싱 실패: {e}"}, 400)
            try:
                from src.web.serialize import portfolio_analyze
                t0 = time.time()
                data = cached_generic("pf:" + str(abs(hash(body))), lambda: portfolio_analyze(req))
                print(f"[pf] {len(req.get('assets', []))} assets ({time.time() - t0:.1f}s)")
                return self._send_json(data)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # 정적 요청 로그는 조용히
        if "/api/" not in (self.path or ""):
            return
        print("[web]", self.address_string(), fmt % args)


def main():
    port = int(os.environ.get("PORT", "5178"))
    # 모든 인터페이스에 바인딩(HOST 환경변수로 재정의 가능) — 프리뷰·컨테이너가
    # localhost가 아닌 주소로 접속해도 열리도록. 로컬 브라우저는 그대로 localhost로 접속.
    host = os.environ.get("HOST", "0.0.0.0")
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"투자지표 웹서버 실행 → http://localhost:{port}/  (바인딩 {host}:{port})")
    print(f"  API 예: http://localhost:{port}/api/analyze?market=KR&query=035420")
    print("  (첫 조회는 피어 수집으로 수십 초 걸릴 수 있습니다. Ctrl+C 로 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료.")


if __name__ == "__main__":
    main()
