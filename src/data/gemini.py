"""Google Gemini(생성형 언어 API) 얇은 REST 클라이언트.

- 키: 환경변수 GEMINI_API_KEY / GOOGLE_API_KEY 또는 .streamlit/secrets.toml
- 모델: 정확한 버전명을 모를 수 있어(예: 'flash 3.1') ListModels로 사용 가능한
  flash 계열을 자동 선택한다. secrets의 GEMINI_MODEL로 힌트/고정 가능.
- 키가 없으면 is_available()=False → 호출부는 AI 기능을 비활성 상태로 안내.
"""
from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from pathlib import Path

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"
ROOT = Path(__file__).resolve().parents[2]

# 요청 하나의 상한. **이것만으로는 방문자의 대기가 안 묶인다** — `generate_text`가
# 모델 후보를 순서대로 시도하므로 최악은 60초가 아니라 **60초 × 후보 수**다(후보는
# 보통 5개 이상). 그래서 총 예산(`budget`)을 따로 둔다 (ADR-0052).
REQUEST_TIMEOUT = 60.0


def _from_secrets(key_name: str) -> str | None:
    p = ROOT / ".streamlit" / "secrets.toml"
    if not p.exists():
        return None
    try:
        import tomllib
        return tomllib.loads(p.read_text(encoding="utf-8")).get(key_name)
    except Exception:
        return None


def get_api_key() -> str | None:
    for env in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(env):
            return os.environ[env].strip()
    try:  # Streamlit Cloud는 비밀을 st.secrets로 제공
        import streamlit as st
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            v = st.secrets.get(k)
            if v:
                return str(v).strip()
    except Exception:
        pass
    v = _from_secrets("GEMINI_API_KEY")
    return str(v).strip() if v else None


def is_available() -> bool:
    return bool(get_api_key())


def failure_reason(exc: BaseException) -> str:
    """예외를 화면에 띄울 짧은 사유로 바꾼다.

    **예외 원문을 그대로 쓰면 안 된다.** 이 모듈은 API 키를 URL 질의 파라미터로
    넘기므로(`params={"key": key}`) requests 예외 문자열에 키가 통째로 섞여 나올 수
    있고, 그것이 화면·로그·스크린숏으로 새어 나간다. 그래서 원문은 버리고 미리
    정한 몇 가지 사유 중 하나만 돌려준다.

    사유를 나누는 이유 — 할당량 초과는 기다리면 풀리고 네트워크는 재시도하면 되며
    그 밖은 설정 문제일 수 있다. 사용자가 할 일이 서로 다르다.
    """
    t = f"{type(exc).__name__} {exc}".lower()
    if "429" in t or "quota" in t or "resource_exhausted" in t or "rate" in t:
        return "무료 할당량 초과"
    if any(k in t for k in ("timeout", "timed out", "connection", "network",
                            "dns", "unreachable", "ssl")):
        return "네트워크 오류"
    if "401" in t or "403" in t or "permission" in t or "unauthenticated" in t:
        return "인증 거부 — API 키를 확인하세요"
    return "호출 오류"


def _model_hint() -> str:
    return str(os.environ.get("GEMINI_MODEL") or _from_secrets("GEMINI_MODEL") or "flash").strip()


def _list_models(key: str) -> list[str]:
    r = requests.get(f"{BASE}/models", params={"key": key}, timeout=20)
    r.raise_for_status()
    out = []
    for m in r.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(m["name"].split("/")[-1])
    return out


def _score_model(name: str) -> float:
    """안정판·최신·고버전 선호 점수."""
    s = 0.0
    low = name.lower()
    if "exp" in low or "preview" in low:
        s -= 5
    if "-8b" in low or "lite" in low:
        s -= 1  # 초경량은 분석 품질이 떨어져 후순위
    if "latest" in low:
        s += 0.5
    v = re.findall(r"(\d+\.\d+|\d+)", name)
    if v:
        try:
            s += float(v[0])
        except ValueError:
            pass
    return s


# 무료티어 접근성·품질을 고려한 선호 순서 (앞쪽부터 시도)
_PREFERRED = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite",
              "gemini-2.0-flash-lite", "gemini-flash-latest"]


@lru_cache(maxsize=1)
def resolve_candidates() -> tuple:
    """호출 시도 순서 후보 모델 목록. 힌트가 명시되면 맨 앞에 둔다."""
    key = get_api_key()
    hint = _model_hint()
    try:
        available = set(_list_models(key)) if key else set()
    except Exception:
        available = set()
    ordered: list[str] = []
    # 1) 명시 힌트가 실제 모델명이면 최우선
    if hint and hint != "flash" and (not available or hint in available):
        ordered.append(hint)
    # 2) 선호 목록 중 사용 가능한 것
    for m in _PREFERRED:
        if (not available or m in available) and m not in ordered:
            ordered.append(m)
    # 3) 그 외 사용 가능한 flash 계열(고버전 우선)
    for m in sorted(available, key=_score_model, reverse=True):
        if "flash" in m.lower() and m not in ordered:
            ordered.append(m)
    return tuple(ordered or ["gemini-2.0-flash"])


@lru_cache(maxsize=1)
def resolve_model() -> str:
    return resolve_candidates()[0]


def generate_text(prompt: str, temperature: float = 0.4, max_tokens: int = 2048,
                  json_out: bool = False, budget: float | None = None) -> str:
    """프롬프트 → 텍스트. 접근 가능한 모델을 순서대로 시도. 실패 시 명확한 RuntimeError.

    `budget` — **전체 시도에 허용된 총 시간(초).** 방문자를 기다리게 하는 호출은 이걸 준다.
    None이면 종전대로 모델마다 `REQUEST_TIMEOUT`을 다 쓴다(온디맨드 해설 버튼처럼
    사람이 누르고 기다리는 자리는 그대로 둔다 — 긴 답을 중간에 자르면 그게 더 나쁘다).
    """
    key = get_api_key()
    if not key:
        raise RuntimeError("Gemini API 키가 설정되지 않았습니다.")
    cfg = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_out:
        cfg["responseMimeType"] = "application/json"

    started = time.monotonic()
    denied, exhausted, last = [], False, ""
    for model in resolve_candidates():
        timeout = REQUEST_TIMEOUT
        if budget is not None:
            left = budget - (time.monotonic() - started)
            if left <= 0:
                last = f"시간 예산 {budget:.0f}초를 다 썼다(모델 {len(denied) + 1}개 시도)"
                break
            timeout = min(REQUEST_TIMEOUT, left)
        # Gemini 2.5 계열은 기본적으로 추론(thinking) 토큰을 소모하는데, 그게
        # maxOutputTokens 예산을 다 먹어 본문(특히 JSON)이 잘려 나온다(finishReason=MAX_TOKENS).
        # 분석용 짧은 응답에는 추론이 불필요하므로 2.5 계열에서만 thinking을 끈다.
        model_cfg = dict(cfg)
        if "2.5" in model:
            model_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": model_cfg}
        r = requests.post(f"{BASE}/models/{model}:generateContent",
                          params={"key": key}, json=body, timeout=timeout)
        if r.status_code == 200:
            parts = (r.json().get("candidates", [{}])[0].get("content", {}) or {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
            last = "빈 응답(안전필터 가능)"
            continue
        if r.status_code in (403, 404):      # 이 모델은 이 프로젝트에서 불가 → 다음 모델
            denied.append(model)
            continue
        if r.status_code == 429:             # 할당량 소진은 프로젝트 전역 → 중단
            exhausted = True
            break
        last = f"{r.status_code}: {r.text[:150]}"

    if exhausted:
        raise RuntimeError(
            "Gemini 무료 할당량이 소진됐습니다(429). 하루 사용량을 넘었거나 이 프로젝트에 "
            "무료 쿼터가 없습니다. 내일 다시 시도하거나, aistudio.google.com/app/apikey에서 "
            "**새 프로젝트**로 표준 키('AIza…')를 발급해 보세요.")
    if denied:
        raise RuntimeError(
            f"이 키의 프로젝트가 사용 가능한 생성 모델이 없습니다(403). 시도한 모델: {', '.join(denied)}. "
            "aistudio.google.com/app/apikey에서 **새 프로젝트**로 표준 키('AIza…')를 발급하고 "
            "지원 지역(VPN 해제)에서 사용하세요.")
    raise RuntimeError(f"Gemini 호출 실패: {last or '알 수 없는 오류'}")
