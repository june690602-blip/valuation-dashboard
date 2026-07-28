"""방문자 분석 — Microsoft Clarity(Data Export)·Google Analytics 4(Data API) 서버측 클라이언트.

관리 페이지(/admin.html)가 외부 대시보드에 로그인하지 않고도 방문 통계를 보게 하는 얇은 통로.
키는 전부 서버측(환경변수 또는 .streamlit/secrets.toml) — 브라우저에는 공개 추적 ID만 나간다.

- 추적(공개 ID): GA_MEASUREMENT_ID(G-…), CLARITY_PROJECT_ID
- 조회(비밀): CLARITY_API_TOKEN(Clarity Settings→Data Export에서 발급),
  GA_PROPERTY_ID(숫자)·GA_SA_KEY_JSON(GCP 서비스계정 JSON 전문) — google-auth로 토큰 발급
- Clarity Export API는 **프로젝트당 하루 10회** 제한 → file_cache 6시간(최대 4회/일).
  GA Data API는 여유 있어 1시간 캐시. 실패 시 {"error": …} 반환(캐시에 저장 안 함).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .cache import file_cache

ROOT = Path(__file__).resolve().parents[2]
_CLARITY_EXPORT = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
_GA_BASE = "https://analyticsdata.googleapis.com/v1beta/properties"


def _secret(name: str) -> str | None:
    """환경변수 우선, 없으면 .streamlit/secrets.toml (gemini.py와 동일 규약)."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    p = ROOT / ".streamlit" / "secrets.toml"
    if p.exists():
        try:
            import tomllib
            sv = tomllib.loads(p.read_text(encoding="utf-8")).get(name)
            return str(sv).strip() if sv else None
        except Exception:
            return None
    return None


# ── 추적(브라우저에 나가는 공개 ID) ─────────────────────────────────
def tracking_config() -> dict:
    """페이지가 주입할 추적 ID — 측정 ID는 원래 페이지 소스에 공개되는 값이라 안전."""
    return {"ga": _secret("GA_MEASUREMENT_ID") or None,
            "clarity": _secret("CLARITY_PROJECT_ID") or None}


def config_status() -> dict:
    """관리 페이지에 보여줄 설정 현황 — 어떤 키가 비었는지(값은 노출하지 않음)."""
    keys = ["GA_MEASUREMENT_ID", "CLARITY_PROJECT_ID", "CLARITY_API_TOKEN",
            "GA_PROPERTY_ID", "GA_SA_KEY_JSON", "ADMIN_TOKEN"]
    missing = [k for k in keys if not _secret(k)]
    return {"tracking_ga": bool(_secret("GA_MEASUREMENT_ID")),
            "tracking_clarity": bool(_secret("CLARITY_PROJECT_ID")),
            "api_clarity": bool(_secret("CLARITY_API_TOKEN")),
            "api_ga": bool(_secret("GA_PROPERTY_ID")) and bool(_secret("GA_SA_KEY_JSON")),
            "missing": missing}


def dashboard_links() -> dict:
    """외부 대시보드 딥링크 — 히트맵·세션 녹화는 임베드가 막혀 있어 링크로 연결한다."""
    pid = _secret("CLARITY_PROJECT_ID")
    base = f"https://clarity.microsoft.com/projects/view/{pid}" if pid else None
    return {"clarity_dashboard": f"{base}/dashboard" if base else None,
            "clarity_heatmaps": f"{base}/heatmaps" if base else None,
            "clarity_recordings": f"{base}/impressions" if base else None,
            "ga_home": "https://analytics.google.com/" if _secret("GA_MEASUREMENT_ID") else None}


# ── Clarity Data Export ─────────────────────────────────────────────
@file_cache("clarity_insights", ttl_hours=6,
            validate=lambda d: isinstance(d, dict) and not d.get("error"))
def clarity_insights(num_days: int = 3) -> dict:
    """최근 1~3일 집계 지표(트래픽·스크롤 깊이·데드/레이지 클릭 등). 하루 10회 제한 주의.

    응답은 [{"metricName": …, "information": [...]}] 목록 — 스키마 변화에 강하도록
    원본을 그대로 담고, 프런트가 아는 지표만 골라 그린다.
    """
    token = _secret("CLARITY_API_TOKEN")
    if not token:
        return {"error": "CLARITY_API_TOKEN 미설정"}
    try:
        r = requests.get(_CLARITY_EXPORT, timeout=20,
                         params={"numOfDays": max(1, min(3, int(num_days)))},
                         headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            return {"error": "Clarity 토큰 인증 실패 — Settings→Data Export에서 재발급 필요"}
        if r.status_code == 403:
            return {"error": "Clarity API 접근 거부(토큰 권한 확인)"}
        if r.status_code == 429:
            return {"error": "Clarity API 일일 한도(10회) 초과 — 몇 시간 뒤 다시"}
        r.raise_for_status()
        return {"num_days": num_days, "metrics": r.json()}
    except requests.RequestException as e:
        return {"error": f"Clarity API 요청 실패: {e.__class__.__name__}"}


# ── GA4 Data API ────────────────────────────────────────────────────
def _ga_token() -> tuple[str | None, str | None]:
    """서비스계정 JSON → OAuth 액세스 토큰. (token, error)"""
    raw = _secret("GA_SA_KEY_JSON")
    if not raw:
        return None, "GA_SA_KEY_JSON 미설정"
    try:
        info = json.loads(raw)
    except ValueError:
        return None, "GA_SA_KEY_JSON이 올바른 JSON이 아님(파일 내용 전체를 넣어야 함)"
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/analytics.readonly"])
        creds.refresh(Request())
        return creds.token, None
    except Exception as e:
        return None, f"GA 토큰 발급 실패: {e.__class__.__name__}"


def _rows(report: dict) -> list[list]:
    """runReport 응답 → [[dim…, metric…], …] (문자열 그대로)."""
    out = []
    for row in report.get("rows", []) or []:
        out.append([d.get("value") for d in row.get("dimensionValues", [])]
                   + [m.get("value") for m in row.get("metricValues", [])])
    return out


@file_cache("ga_snapshot", ttl_hours=1,
            validate=lambda d: isinstance(d, dict) and not d.get("error"))
def ga_snapshot(days: int = 28) -> dict:
    """최근 N일 요약 — 일별 추이·유입 출처·인기 페이지·국가·기기 (batchRunReports 1회)."""
    prop = _secret("GA_PROPERTY_ID")
    if not prop:
        return {"error": "GA_PROPERTY_ID 미설정"}
    token, err = _ga_token()
    if err:
        return {"error": err}
    rng = [{"startDate": f"{days - 1}daysAgo", "endDate": "today"}]
    reqs = [
        {"dateRanges": rng, "dimensions": [{"name": "date"}],
         "metrics": [{"name": "activeUsers"}, {"name": "sessions"}, {"name": "screenPageViews"}],
         "orderBys": [{"dimension": {"dimensionName": "date"}}], "limit": str(days)},
        {"dateRanges": rng, "dimensions": [{"name": "sessionSource"}, {"name": "sessionMedium"}],
         "metrics": [{"name": "sessions"}, {"name": "activeUsers"}],
         "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}], "limit": "10"},
        {"dateRanges": rng, "dimensions": [{"name": "pagePath"}],
         "metrics": [{"name": "screenPageViews"}, {"name": "activeUsers"}],
         "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}], "limit": "10"},
        {"dateRanges": rng, "dimensions": [{"name": "country"}],
         "metrics": [{"name": "activeUsers"}],
         "orderBys": [{"metric": {"metricName": "activeUsers"}, "desc": True}], "limit": "10"},
        {"dateRanges": rng, "dimensions": [{"name": "deviceCategory"}],
         "metrics": [{"name": "activeUsers"}],
         "orderBys": [{"metric": {"metricName": "activeUsers"}, "desc": True}], "limit": "5"},
    ]
    try:
        r = requests.post(f"{_GA_BASE}/{prop}:batchRunReports", timeout=25,
                          headers={"Authorization": f"Bearer {token}"},
                          json={"requests": reqs})
        if r.status_code == 403:
            return {"error": "GA 접근 거부 — GA 속성에 서비스계정 이메일을 '뷰어'로 추가했는지, "
                             "Analytics Data API를 활성화했는지 확인"}
        r.raise_for_status()
        reports = r.json().get("reports", [])
        get = lambda i: _rows(reports[i]) if i < len(reports) else []
        return {"days": days, "daily": get(0), "sources": get(1), "pages": get(2),
                "countries": get(3), "devices": get(4)}
    except requests.RequestException as e:
        return {"error": f"GA API 요청 실패: {e.__class__.__name__}"}


@file_cache("ga_realtime", ttl_hours=0.08,  # ≈5분
            validate=lambda d: isinstance(d, dict) and not d.get("error"))
def ga_realtime() -> dict:
    """지금 접속 중인 사용자 수(실시간)."""
    prop = _secret("GA_PROPERTY_ID")
    if not prop:
        return {"error": "GA_PROPERTY_ID 미설정"}
    token, err = _ga_token()
    if err:
        return {"error": err}
    try:
        r = requests.post(f"{_GA_BASE}/{prop}:runRealtimeReport", timeout=15,
                          headers={"Authorization": f"Bearer {token}"},
                          json={"metrics": [{"name": "activeUsers"}]})
        r.raise_for_status()
        rows = r.json().get("rows", [])
        n = int(rows[0]["metricValues"][0]["value"]) if rows else 0
        return {"active_users": n}
    except requests.RequestException as e:
        return {"error": f"GA 실시간 요청 실패: {e.__class__.__name__}"}


def admin_stats() -> dict:
    """관리 페이지 한 번에 내려줄 묶음 — 각 소스는 독립 실패(하나 죽어도 나머지 표시)."""
    return {"configured": config_status(), "links": dashboard_links(),
            "ga": ga_snapshot(), "realtime": ga_realtime(),
            "clarity": clarity_insights()}
