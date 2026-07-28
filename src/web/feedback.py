"""사용자 의견 접수 — 로그인 없이 받은 한 줄 피드백을 기록하고 운영자에게 전달한다.

설계 의도:
- **가입·로그인 요구 없음.** 방문자가 텍스트 한 줄만 적으면 끝나야 한다.
- **메일 주소를 공개 HTML에 두지 않는다.** 프런트는 자기 서버(/api/feedback)에만 보내고,
  실제 메일 전달은 서버가 `FEEDBACK_ENDPOINT`(무료 폼 릴레이 URL)로 중계한다.
  주소를 페이지에 박으면 스팸 수집기에 그대로 긁힌다.
- **키가 없어도 죽지 않는다.** 릴레이가 설정 안 됐거나 실패해도 접수 자체는 성공으로 처리하고
  서버 로그 + `data/feedback.jsonl`에 남긴다(프로젝트 규칙: 결측이어도 크래시 금지).

환경변수:
  FEEDBACK_ENDPOINT  의견을 중계할 폼 서비스 URL. 예: https://formsubmit.co/ajax/<주소 또는 해시>
                     비워두면 릴레이 없이 로그·파일에만 남는다.

스팸 방어는 세 겹 — 허니팟 필드(사람은 비워둠) · IP당 호출 제한 · 길이 상한.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

MAX_MESSAGE = 2000
MAX_EMAIL = 200
MAX_PAGE = 300

# IP당 10분에 5건, 전체는 1시간에 200건 — 개인 포트폴리오 트래픽에는 넉넉하고 봇에는 좁다.
_PER_IP_WINDOW = 600
_PER_IP_LIMIT = 5
_GLOBAL_WINDOW = 3600
_GLOBAL_LIMIT = 200

_LOCK = threading.Lock()
_BY_IP: dict[str, deque] = {}
_GLOBAL: deque = deque()

_STORE = Path(__file__).resolve().parents[2] / "data" / "feedback.jsonl"


class RateLimited(Exception):
    """짧은 시간에 너무 많이 보낸 경우 — 사용자에게는 안내 문구만 보여준다."""


def _allow(ip: str) -> bool:
    """호출 빈도 확인. 창(window)을 벗어난 기록은 흘려보낸다."""
    now = time.time()
    with _LOCK:
        while _GLOBAL and now - _GLOBAL[0] > _GLOBAL_WINDOW:
            _GLOBAL.popleft()
        if len(_GLOBAL) >= _GLOBAL_LIMIT:
            return False
        q = _BY_IP.setdefault(ip, deque())
        while q and now - q[0] > _PER_IP_WINDOW:
            q.popleft()
        if len(q) >= _PER_IP_LIMIT:
            return False
        q.append(now)
        _GLOBAL.append(now)
        # 오래된 IP 항목 정리(메모리 누수 방지)
        if len(_BY_IP) > 5000:
            for k in [k for k, v in _BY_IP.items() if not v]:
                _BY_IP.pop(k, None)
    return True


def _record(entry: dict) -> None:
    """로그 + 파일 적재. 파일 쓰기는 부가기능이라 실패해도 접수는 성공으로 둔다.
    (Render 같은 PaaS는 디스크가 휘발성이므로 파일은 로컬 확인용 보조 수단.)"""
    print(f"[feedback] {entry['at']} {entry['page']} :: {entry['message'][:160]}"
          + (f" (회신 {entry['email']})" if entry["email"] else ""))
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        with _STORE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[feedback] 파일 적재 실패(무시): {e}")


def _relay(entry: dict) -> bool:
    """폼 릴레이 서비스로 전달. 미설정·실패 모두 False를 돌려주되 예외는 삼킨다."""
    url = (os.environ.get("FEEDBACK_ENDPOINT") or "").strip()
    if not url:
        return False
    payload = {
        "_subject": "[투자지표] 사용자 의견",
        "message": entry["message"],
        "email": entry["email"] or "(미기재)",
        "page": entry["page"],
        "at": entry["at"],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 (URL은 운영자 환경변수)
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[feedback] 릴레이 응답 {resp.status}")
            return ok
    except Exception as e:  # noqa: BLE001 — 릴레이 실패가 사용자 접수를 막으면 안 된다
        print(f"[feedback] 릴레이 실패(로그·파일에는 남음): {e}")
        return False


def submit(payload: dict, client_ip: str = "?") -> dict:
    """의견 한 건 접수. 사용자 입력 오류는 ValueError, 과다 호출은 RateLimited."""
    if not isinstance(payload, dict):
        raise ValueError("잘못된 요청 형식입니다.")

    # 허니팟 — 화면에서 숨긴 필드라 사람은 절대 채우지 않는다. 봇이면 조용히 성공한 척.
    if (payload.get("company") or "").strip():
        return {"ok": True}

    message = (payload.get("message") or "").strip()
    if len(message) < 2:
        raise ValueError("의견을 한 줄만 적어주세요.")
    if len(message) > MAX_MESSAGE:
        raise ValueError(f"의견은 {MAX_MESSAGE}자까지 보낼 수 있습니다.")

    email = (payload.get("email") or "").strip()[:MAX_EMAIL]
    if email and ("@" not in email or " " in email):
        raise ValueError("이메일 형식을 확인해 주세요. (비워두셔도 됩니다)")

    if not _allow(client_ip):
        raise RateLimited("잠시 후 다시 보내주세요. 짧은 시간에 여러 건이 접수됐습니다.")

    entry = {
        "at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
        "email": email,
        "page": (payload.get("page") or "")[:MAX_PAGE],
    }
    _record(entry)
    return {"ok": True, "relayed": _relay(entry)}
