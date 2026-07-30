"""투자지표 웹서버 — 정적 Meridian 페이지 + 실데이터 분석 API (표준 라이브러리만 사용).

실행:  python server.py           (기본 포트 5178)
        PORT=8000 python server.py

- 정적: web/ 폴더를 그대로 서빙 ('/' → stock.html)
- API : GET /api/analyze?market=KR&query=035420[&peer_count=9&news=0]
        → src.web.serialize.analyze() 결과 JSON (인프로세스 캐시 30분)
        GET /api/analyze_etf?market=US&query=SPY
        → src.web.serialize.analyze_etf() 결과 JSON (인프로세스 캐시 30분, 미국 ETF만)
        POST /api/feedback  {message, email?, page?}
        → 사용자 의견 접수(로그인 불필요). src.web.feedback 참조

Flask 등 추가 의존성 없음 — Streamlit 앱이 쓰는 패키지(pandas·yfinance…)만 있으면 된다.
"""
from __future__ import annotations

import json
import math
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

# Windows 콘솔(cp949)이 못 그리는 문자(—·이모지 등)가 로그 print에서 예외를 던지면
# 요청 핸들러가 응답도 못 보내고 죽는다 → 인코딩 불가 문자는 '?'로 대체해 로그는 계속 흐르게.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

_CACHE: dict = {}
_AI_CACHE: dict = {}
_LOCK = threading.Lock()
_TTL = 1800     # 30분
_AI_TTL = 21600  # 6시간 (AI 결과는 헤드라인·펀더멘털이 크게 안 바뀌므로 길게 캐시)

# ── 관리 페이지 무차별 대입 방어 ──────────────────────────────────────
# 관리 토큰은 시도 제한이 없으면 길이만이 유일한 방어선이다. 실패를 IP별로 세어 잠그면
# 자동 대입이 사실상 불가능해진다(초당 수천 번 → 분당 몇 번). 서버는 단일 프로세스라
# 메모리 dict로 충분하고, 재시작하면 초기화된다.
_ADMIN_FAILS: dict = {}          # {ip: (연속 실패 횟수, 마지막 실패 시각)}
_ADMIN_LOCK = threading.Lock()
_ADMIN_MAX_FAILS = 5             # 이 횟수까지는 허용(오타 여유)
_ADMIN_LOCK_SEC = 60             # 넘으면 잠그는 시간(초)
_ADMIN_PRUNE_MAX = 512           # 기록이 이보다 많아지면 만료분을 청소


def _first_forwarded_ip(header: str | None) -> str | None:
    """X-Forwarded-For에서 최초 클라이언트 IP만 뽑는다(Render 등 프록시 뒤 배포용)."""
    if not header:
        return None
    first = header.split(",")[0].strip()
    return first or None


def _admin_reset_all() -> None:
    """실패 기록 전체 삭제(테스트·재시작용)."""
    with _ADMIN_LOCK:
        _ADMIN_FAILS.clear()


def _admin_lock_left(ip: str, now: float | None = None) -> int:
    """이 IP가 잠겨 있으면 남은 초, 아니면 0."""
    now = time.time() if now is None else now
    with _ADMIN_LOCK:
        fails, last = _ADMIN_FAILS.get(ip, (0, 0.0))
    if fails < _ADMIN_MAX_FAILS:
        return 0
    left = _ADMIN_LOCK_SEC - (now - last)
    return math.ceil(left) if left > 0 else 0


def _admin_note_fail(ip: str, now: float | None = None) -> None:
    """실패 1회 기록. 잠금이 이미 풀린 뒤의 실패는 1부터 다시 센다."""
    now = time.time() if now is None else now
    with _ADMIN_LOCK:
        fails, last = _ADMIN_FAILS.get(ip, (0, 0.0))
        if fails >= _ADMIN_MAX_FAILS and now - last > _ADMIN_LOCK_SEC:
            fails = 0
        _ADMIN_FAILS[ip] = (fails + 1, now)
        # 만료된 기록 청소 — 실패 IP가 계속 쌓여 메모리를 먹지 않게.
        if len(_ADMIN_FAILS) > _ADMIN_PRUNE_MAX:
            for k in [k for k, (_f, t) in _ADMIN_FAILS.items()
                      if now - t > _ADMIN_LOCK_SEC and k != ip]:
                del _ADMIN_FAILS[k]


def _admin_clear(ip: str) -> None:
    """인증 성공 — 이 IP의 실패 기록을 지운다."""
    with _ADMIN_LOCK:
        _ADMIN_FAILS.pop(ip, None)


def cached_analyze(market: str, query: str, peer_count: int, include_news: bool,
                   exclude: str = "", extra: str = "") -> dict:
    key = (market, query, peer_count, include_news, exclude, extra)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    from src.web.serialize import analyze  # 지연 임포트(서버 기동을 빠르게)
    data = analyze(market, query, peer_count=peer_count, include_news=include_news,
                   exclude=exclude, extra=extra)
    with _LOCK:
        _CACHE[key] = (now, data)
    return data


def cached_analyze_etf(market: str, query: str) -> dict:
    key = ("etf", market, query)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    from src.web.serialize import analyze_etf  # 지연 임포트(서버 기동을 빠르게)
    data = analyze_etf(market, query)
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


_ERR_MSG = "서버 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. (원인은 서버 콘솔 로그에 기록됩니다)"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def end_headers(self):  # noqa: N802
        # 정적 파일은 항상 재검증(no-cache) — 코드 수정·배포 직후 브라우저가
        # 옛 CSS/JS를 계속 보여주는 문제 방지. API는 _send_json이 no-store를 보냄.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

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
            exclude = (q.get("exclude", [""])[0] or "").strip()
            extra = (q.get("add", [""])[0] or "").strip()
            if not query:
                return self._send_json({"error": "종목(query)을 입력하세요."}, 400)
            try:
                t0 = time.time()
                data = cached_analyze(market, query, peer_count, include_news, exclude, extra)
                print(f"[api] {market} {query} → {data['meta']['name']} ({time.time() - t0:.1f}s)")
                return self._send_json(data)
            except ValueError as e:  # 종목 미탐색 등 사용자 입력 오류 — 안내 문구를 그대로 전달(서버 오류 아님)
                from src.data.models import IsETFError
                print(f"[api] {market} {query} → 입력 오류: {e}")
                payload = {"error": str(e)}
                if isinstance(e, IsETFError):
                    payload["kind"] = "etf"   # 프런트가 이걸 보고 ETF 분석으로 자동 재요청
                return self._send_json(payload, 400)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/analyze_etf":
            q = parse_qs(u.query)
            market = (q.get("market", ["US"])[0] or "US").upper()
            query = (q.get("query", [""])[0] or "").strip()
            if not query:
                return self._send_json({"error": "ETF 티커를 입력하세요."}, 400)
            try:
                t0 = time.time()
                data = cached_analyze_etf(market, query)
                if isinstance(data, dict) and data.get("error"):
                    return self._send_json(data, 400)
                print(f"[api-etf] {market} {query} → {data.get('name')} ({time.time() - t0:.1f}s)")
                return self._send_json(data)
            except ValueError as e:  # 티커 미탐색 등 사용자 입력 오류 — 안내 문구를 그대로 전달
                print(f"[api-etf] {market} {query} → 입력 오류: {e}")
                return self._send_json({"error": str(e)}, 400)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
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
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/progress":
            # 분석 진행 폴링(피어 수집 n/m) — 진행 중이 아니면 빈 객체
            q = parse_qs(u.query)
            market = (q.get("market", ["KR"])[0] or "KR").upper()
            query = (q.get("query", [""])[0] or "").strip()
            try:
                from src.web.serialize import get_progress
                return self._send_json(get_progress(market, query) or {})
            except Exception:  # noqa: BLE001
                return self._send_json({})
        if u.path == "/api/risk-profile":
            try:
                from src.analysis.risk_profile import risk_profile_config
                return self._send_json(risk_profile_config())
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/market":
            try:
                from src.web.serialize import market_params
                return self._send_json(cached_generic("market", market_params, ttl=3600))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/bond":
            try:
                from src.web.serialize import bond_data
                return self._send_json(cached_generic("bond", bond_data))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
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
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/analytics-config":
            # 추적 스니펫용 공개 ID(GA 측정 ID·Clarity 프로젝트 ID) — 원래 페이지에 공개되는 값
            try:
                from src.data.analytics import tracking_config
                return self._send_json(cached_generic("anacfg", tracking_config, ttl=600))
            except Exception:  # noqa: BLE001
                return self._send_json({"ga": None, "clarity": None})
        if u.path == "/api/admin/stats":
            # 관리 페이지 통계 — ADMIN_TOKEN 필수(미설정이면 잠금). 키·토큰은 서버 밖으로 안 나간다.
            import hmac
            q = parse_qs(u.query)
            supplied = (q.get("token", [""])[0] or self.headers.get("X-Admin-Token", "")).strip()
            expected = (os.environ.get("ADMIN_TOKEN", "") or "").strip()
            if not expected:
                try:
                    from src.data.analytics import _secret
                    expected = (_secret("ADMIN_TOKEN") or "").strip()
                except Exception:  # noqa: BLE001
                    expected = ""
            if not expected:
                return self._send_json({"error": "ADMIN_TOKEN이 설정되지 않아 관리 페이지가 잠겨 있습니다. "
                                                 "서버 환경변수(또는 secrets.toml)에 ADMIN_TOKEN을 넣어 주세요."}, 403)
            # 무차별 대입 방어 — 프록시(Render) 뒤에서는 X-Forwarded-For가 실제 방문자 IP다.
            ip = (_first_forwarded_ip(self.headers.get("X-Forwarded-For"))
                  or self.client_address[0])
            left = _admin_lock_left(ip)
            if left:
                return self._send_json(
                    {"error": f"로그인 시도가 너무 많습니다. {left}초 후 다시 시도해 주세요."}, 429)
            if not supplied or not hmac.compare_digest(supplied, expected):
                _admin_note_fail(ip)
                return self._send_json({"error": "관리 토큰이 올바르지 않습니다."}, 401)
            _admin_clear(ip)
            try:
                from src.data.analytics import admin_stats
                # 인프로세스 5분 캐시 — 새로고침 연타가 외부 API(특히 Clarity 10회/일)를 때리지 않게
                return self._send_json(cached_generic("adminstats", admin_stats, ttl=300))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/suggest":
            # 종목 자동완성 — 타이핑마다 호출되므로 (market,q)로 짧게 캐시. 실패해도 빈 목록.
            q = parse_qs(u.query)
            market = (q.get("market", ["KR"])[0] or "KR").upper()
            term = (q.get("q", [""])[0] or "").strip()
            if not term:
                return self._send_json({"items": []})
            try:
                from src.web.serialize import suggest
                items = cached_generic(f"sug:{market}:{term.lower()}",
                                       lambda: suggest(market, term), ttl=300)
                return self._send_json({"items": items})
            except Exception:  # noqa: BLE001
                return self._send_json({"items": []})
        if u.path in ("/", "/index.html"):
            self.path = "/home.html"   # 진입점 = 홈(랜딩). 주식 페이지는 nav·예시카드로 이동.
        # 디자인 시안(preview-*.html)은 화면이 아니라 작업 흔적이다 — 링크는 없지만
        # web/ 폴더를 통째로 서빙하므로 주소를 치면 그대로 열린다. 홈 리디자인 시안 6장이
        # 옛 면책 문구를 단 채 열리던 게 R3 발견 11이었다(#70). 지금은 .gitignore가
        # 저장소에서 막고 있어 배포본에는 없지만, **로컬에 남아 있으면 여전히 열린다.**
        # 규칙을 파일 유무가 아니라 서버에 둔다 — 새 시안을 만들어도 자동으로 막힌다.
        if Path(u.path).name.startswith("preview-"):
            self.send_error(404, "Not Found")
            return None
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
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        if u.path == "/api/feedback":
            # 사용자 의견 접수 — 로그인 없이 누구나. 본문이 커도 서버가 흔들리지 않게 상한을 둔다.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > 16384:
                return self._send_json({"error": "내용이 너무 깁니다."}, 413)
            try:
                body = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
                req = json.loads(body or "{}")
            except Exception:  # noqa: BLE001
                return self._send_json({"error": "잘못된 요청 형식입니다."}, 400)
            from src.web.feedback import RateLimited, submit
            try:
                return self._send_json(submit(req, self.address_string()))
            except RateLimited as e:
                return self._send_json({"error": str(e)}, 429)
            except ValueError as e:
                return self._send_json({"error": str(e)}, 400)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
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
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._send_json({"error": _ERR_MSG}, 500)
        return self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # 정적 요청 로그는 조용히
        if "/api/" not in (self.path or ""):
            return
        print("[web]", self.address_string(), fmt % args)


def main():
    port = int(os.environ.get("PORT", "5178"))
    # 기본은 로컬 전용(127.0.0.1) — 로컬 분석 도구가 같은 네트워크에 노출되지 않게.
    # 프리뷰·컨테이너·배포처럼 외부 접속이 필요하면 HOST=0.0.0.0 으로 명시해 연다.
    host = os.environ.get("HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"투자지표 웹서버 실행 → http://localhost:{port}/  (바인딩 {host}:{port})")
    if host == "127.0.0.1":
        print("  외부(다른 기기)에서 접속하려면: HOST=0.0.0.0 로 실행")
    print(f"  API 예: http://localhost:{port}/api/analyze?market=KR&query=035420")
    print("  (첫 조회는 피어 수집으로 수십 초 걸릴 수 있습니다. Ctrl+C 로 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료.")


if __name__ == "__main__":
    main()
