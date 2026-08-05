# ADR-0027: 인증서를 ASCII 경로로 비춰 둔다 — 한글 폴더에서 시세가 통째로 죽는 것을 막는다

- 상태: **채택됨** · 2026-08-06
- 관련: [ADR-0011](0011-peer-size-window-and-no-fallback.md)(값을 지어내느니 계산 불가를
  표시한다 — **이 문서는 그 반대편이다**: 원인을 잘못 표시하는 것을 막는다)
- 관련 코드: `src/data/ca_bundle.py` · `src/data/base.py`(import 순서) · `tests/test_ca_bundle.py`

## 맥락 — 증상이 원인을 가렸다

이 저장소는 한글 폴더 안에 있다: `C:\Users\<user>\OneDrive\Desktop\투자지표\valuation-dashboard`.
가상환경도 그 아래이므로 `certifi`의 `cacert.pem` **경로에 한글이 들어간다.**

yfinance는 1.5부터 통신을 `curl_cffi`(=libcurl, 네이티브 코드)로 한다. libcurl은 Windows에서
경로를 좁은 문자열로 다뤄 **비ASCII 경로의 파일을 열지 못한다.**

```
curl: (77) error setting certificate verify locations:
      CAfile: ...\투자지표\...\site-packages\certifi\cacert.pem
```

**그런데 yfinance가 이 실패를 삼킨다.** 밖으로 나오는 말은 이것이다:

```
$005930.KS: possibly delisted; no price data found
```

상장폐지처럼 보이고, 레이트리밋과도 구분되지 않는다. **실제로 이 저장소에서 세 번
오진했다** — *"yfinance가 대량 호출을 스로틀링한다"*고 판단해 진단 스크립트를
FinanceDataReader로 갈아타기까지 했다. 재보니 Yahoo는 멀쩡히 **HTTP 200**을 주고 있었고
`requests`(순수 파이썬)로는 잘 받아졌다. 막힌 것은 서버가 아니라 **우리 쪽 파일 열기**였다.

**한꺼번에 죽는다는 것도 중요하다.** yfinance는 Yahoo 쿠키를 디스크에 캐시한다. 쿠키가
살아 있는 동안은 새로 받을 일이 없어 문제가 안 드러나고, **쿠키를 갱신해야 하는 순간
전 종목이 동시에** 죽는다. 그래서 "어제까지 됐는데 갑자기 안 된다"로 나타난다.

즉 이것은 환경 하나가 이상한 문제가 아니라 **조용히 · 한꺼번에 · 엉뚱한 메시지로**
터지는 종류다. 진단이 거의 불가능하다는 점이 이 결정의 이유다.

## 결정

**인증서를 ASCII 경로로 한 번 복사하고 `CURL_CA_BUNDLE`을 거기로 건다**
(`src/data/ca_bundle.py`의 `install()`). `src/data/base.py`가 **yfinance를 import하기 전에**
부른다.

**필요할 때만 움직인다.** 아무것도 안 하는 경우 셋 — 셋 다 정상이다:

1. 사용자가 이미 `CURL_CA_BUNDLE`을 걸었다 → **덮지 않는다**
2. `certifi` 경로가 이미 ASCII다 → 문제가 없다
3. 임시 폴더까지 비ASCII이거나 복사가 실패했다 → 우회할 자리가 없다

`certifi`가 갱신되면(원본이 더 새로우면) 복사본도 갱신한다 — 옛 인증서를 붙들면
검증이 틀어진다.

**어느 단계에서 실패하든 조용히 물러선다.** 우회에 실패했다고 앱이 죽으면 본말전도다.

## 검토한 대안

- **`verify=False`로 검증을 끈다.** 한 줄이면 되지만 중간자 공격에 문을 연다.
  **이 우회는 검증을 끄지 않는다** — 같은 인증서를 읽을 수 있는 자리로 옮길 뿐이다.
- **프로젝트를 영문 폴더로 옮긴다.** 근본 해결이지만 `대시보드실행.bat`·OneDrive 경로·
  바로가기가 전부 바뀐다. 그리고 **다른 사람의 한글 사용자명**(`C:\Users\홍길동\...`)에서는
  프로젝트를 옮겨도 다시 터진다 — 옮기는 것으로는 이 문제가 끝나지 않는다.
- **`CURL_CA_BUNDLE`을 사용자가 직접 환경변수로 건다.** 지금 이 PC는 고쳐지지만
  새 PC·친구 PC에서 같은 오진이 반복된다. 저장소를 따라다니는 쪽을 택했다.
- **yfinance를 `requests` 세션으로 되돌린다.** 상위 라이브러리 내부 구현에 손대는
  일이고 버전이 오르면 다시 깨진다.
- **아무것도 안 하고 문서에만 적는다.** 실패 메시지가 *"possibly delisted"*라 문서를
  읽어도 이 문서를 떠올리지 못한다. 그것이 이미 세 번 일어난 일이다.

## 한계 — 숨기지 말 것

- **`curl_cffi`가 인증서 말고 다른 파일을 비ASCII 경로에서 열려 하면 또 터진다.**
  이 우회는 인증서 하나만 다룬다. 같은 원인의 다른 증상이 나오면 같은 방식으로 늘려야 한다.
- **임시 폴더가 비ASCII면 못 고친다**(한글 사용자명 + `TEMP`가 사용자 폴더 아래). 그때는
  조용히 물러서므로 **증상이 예전 그대로**다. 그 경우 `CURL_CA_BUNDLE`을 손으로 걸어야 한다.
- **프로세스 환경변수를 건드린다.** 같은 프로세스의 다른 libcurl 사용자에게도 적용된다.
  이미 걸려 있으면 덮지 않는 것으로 그 위험을 줄였지만, 없애지는 못한다.
- **근본 원인은 상위 라이브러리에 있다.** libcurl/`curl_cffi`가 Windows에서 넓은 문자열
  경로를 지원하면 이 파일은 필요 없어진다. 그때 지울 수 있게 한 파일에 모아 뒀다.
- **왜 하필 오늘 터졌는지는 정확히 재지 못했다.** 쿠키 캐시(`cookies.db`)가 7월 14일자였고
  갱신이 필요해진 시점에 드러났다는 것이 가장 그럴듯한 설명이지만, 그 순간을 직접
  관측한 것은 아니다.

## 재현

```bash
# 우회 없이 터지는 것을 보려면 (원인 확인용)
python -c "from curl_cffi import requests as r; r.get('https://fc.yahoo.com', impersonate='chrome')"
# → curl: (77) error setting certificate verify locations   (경로에 한글이 있을 때)

python -m unittest tests.test_ca_bundle      # 언제 움직이고 언제 가만히 있는지
python scripts/check_analysis.py KR 005930   # 시세가 실제로 들어오는지
```
