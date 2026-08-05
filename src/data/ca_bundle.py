r"""libcurl이 **비ASCII 경로의 인증서 파일**을 못 여는 것을 우회한다 (ADR-0027).

## 무슨 일이 있었나

yfinance는 1.5부터 통신을 `curl_cffi`(=libcurl, 네이티브 코드)로 한다. libcurl은
Windows에서 경로를 좁은 문자열로 다루기 때문에, **경로에 한글이 들어가면 파일을 열지
못한다.** 그런데 열어야 하는 파일이 하필 `certifi`의 `cacert.pem`이고, 가상환경이
한글 폴더 안에 있으면(이 저장소의 실제 배치: `.../투자지표/valuation-dashboard/.venv/...`)
그 경로가 한글이 된다.

    curl: (77) error setting certificate verify locations: CAfile: ...\투자지표\...\cacert.pem

## 왜 이 우회가 필요한가 — **증상이 원인을 가린다**

yfinance는 이 실패를 삼키고 이렇게 보고한다:

    $005930.KS: possibly delisted; no price data found

**상장폐지처럼 보이고, 레이트리밋과도 구분되지 않는다.** 실제로 이 저장소에서 그렇게
세 번 오진했다(*"yfinance가 스로틀링한다"* → 아니었다. Yahoo는 HTTP 200을 주고 있었고
`requests`로는 잘 받아졌다). 게다가 yfinance가 쿠키를 디스크에 캐시하므로 **쿠키가 살아
있는 동안은 멀쩡하다가 갱신이 필요한 순간 전 종목이 한꺼번에 죽는다.**

즉 이것은 "환경이 이상한 사람만 겪는 일"이 아니라 **조용히·한꺼번에·엉뚱한 메시지로**
터지는 종류라, 코드가 스스로 막는 편이 낫다.

## 무엇을 하나

인증서를 **ASCII 경로로 한 번 복사하고** `CURL_CA_BUNDLE`을 거기로 건다. 필요할 때만
움직인다 — 경로가 이미 ASCII이거나 사용자가 변수를 직접 걸었으면 **아무것도 안 한다.**
어느 단계에서 실패하든 조용히 물러선다(우회에 실패했다고 앱이 죽으면 본말전도다).

**검증 자체를 끄지 않는다.** `verify=False`는 한 줄이면 되지만 중간자 공격에 문을 연다.
같은 인증서를 읽을 수 있는 자리로 옮길 뿐, 검증은 그대로 한다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

CACHE_DIRNAME = "valuation-dashboard-ca"
ENV_VAR = "CURL_CA_BUNDLE"


def _is_ascii(path: str | os.PathLike) -> bool:
    return str(path).isascii()


def ascii_cache_path(cache_root: str | os.PathLike | None = None) -> Path | None:
    """인증서를 놓을 ASCII 경로. 임시 폴더 자체가 비ASCII면 **None**(우회 불가)."""
    root = Path(cache_root) if cache_root is not None else Path(tempfile.gettempdir())
    if not _is_ascii(root):
        return None
    return root / CACHE_DIRNAME / "cacert.pem"


def mirror_ca_bundle(source: str | os.PathLike,
                     cache_root: str | os.PathLike | None = None) -> Path | None:
    """`source` 인증서를 ASCII 경로로 복사하고 그 경로를 돌려준다.

    이미 최신 복사본이 있으면 다시 쓰지 않는다 — `certifi`가 갱신되면 원본이 더 새로우므로
    그때만 덮는다. 실패하면 **None**.
    """
    src = Path(source)
    dest = ascii_cache_path(cache_root)
    if dest is None:
        return None
    try:
        if not src.is_file():
            return None
        if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        return dest
    except OSError:
        return None


def install(env: dict | None = None, source: str | os.PathLike | None = None,
            cache_root: str | os.PathLike | None = None) -> str | None:
    """필요하면 `CURL_CA_BUNDLE`을 ASCII 경로로 건다. 건 경로 또는 **None**.

    아무것도 안 하는 경우 셋 — 셋 다 정상이다:

    1. 사용자가 이미 `CURL_CA_BUNDLE`을 걸었다 (**덮지 않는다**)
    2. `certifi` 경로가 이미 ASCII다 (문제가 없다)
    3. 임시 폴더까지 비ASCII거나 복사가 실패했다 (우회할 자리가 없다)
    """
    env = os.environ if env is None else env
    if env.get(ENV_VAR):
        return None
    if source is None:
        try:
            import certifi
            source = certifi.where()
        except Exception:
            return None
    if _is_ascii(source):
        return None
    dest = mirror_ca_bundle(source, cache_root)
    if dest is None:
        return None
    env[ENV_VAR] = str(dest)
    return str(dest)
