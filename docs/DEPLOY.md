# 배포 (Render + Cloudflare)

> 운영 절차서입니다. **무엇을 만들었는지**는 [README](../README.md)에, **왜 그렇게 만들었는지**는
> [ADR](adr/)에 있습니다.

살아있는 파이썬 백엔드(`/api/*`)가 있어 정적 호스팅(GitHub Pages 등)으로는 안 되고,
Render·Railway 같은 PaaS가 맞습니다. `server.py`는 `PORT`·`HOST` 환경변수를 읽습니다.

리포에 포함된 준비물:

- **`render.yaml`** — 빌드·시작 명령과 `HOST=0.0.0.0`을 담은 Render 청사진(연결하면 자동 인식)
- **`.env.example`** — 키 템플릿(`OPENDART_API_KEY`·`GEMINI_API_KEY`)
- **`scripts/prewarm_cache.py`** — 캐시를 손으로 데우는 CLI(특정 종목만 데울 때).
  **배포 때는 안 돌려도 됩니다** — 서버가 기동 직후 같은 일을 스스로 합니다(아래).

## 클릭 순서

1. [Render](https://render.com) 가입 → **New → Blueprint** → 이 GitHub 리포 연결
   (`render.yaml` 자동 감지).
2. **Environment**에 키 입력: `OPENDART_API_KEY`, `GEMINI_API_KEY`
   (`HOST=0.0.0.0`은 `render.yaml`에 이미 있습니다).
3. 도메인: [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/)에서 도메인 구매
   → DNS에서 Render 주소로 **CNAME** → Cloudflare 프록시로 SSL·캐싱.

## 기동 예열 — 첫 방문자가 콜드를 겪지 않게

[ADR-0048](adr/0048-warm-the-showcase-on-boot-not-a-disk.md).
Render는 디스크를 붙이지 않으면 파일시스템이 휘발성이라 **배포·재시작마다 `data/cache/`가
통째로 빕니다.** 실측한 완전 콜드는 **12.33초**이고, 예열이 없으면 그 12초는 언제나
방문자가 냅니다. 서버가 기동 직후 백그라운드 데몬 스레드로 캐시를 채우며, 그동안에도
정상 응답합니다.

실측(빈 캐시에서 기동): 예열 **56초**(서버가 냄) → 첫 방문자 **쇼케이스 2.46초 ·
쇼케이스 밖 7.89초**. 재현: `python scripts/check_load_timing.py KR 005930`

**디스크는 일부러 안 붙였습니다** — Render 디스크는 인스턴스를 하나로 묶고 **무중단 배포를
없앱니다**(새 인스턴스를 띄우기 전에 기존 것을 멈춥니다). 예열은 그 둘을 잃지 않습니다.

## 플랜 선택

`render.yaml`의 `plan: starter`(월 $7, 항상 켜짐)를 `free`로 바꿀 수 있지만, 15분 방치 시
잠들어 첫 방문자가 콜드스타트(~50초)를 겪습니다. 상시 공개 링크라면 항상 켜짐을 권합니다.

## 원천 접근에 관한 주의

yfinance·네이버는 **데이터센터 IP에서 더 자주 막힙니다**(OpenDART는 정식 키 API라 안정적).
기동 예열이 쇼케이스 종목을 미리 채우므로 첫인상에서 실패를 피할 수 있습니다.

같은 이유로 업종 회귀 계수는 **밤에 GitHub Actions가 미리 구워 별도 브랜치에 둡니다**
([ADR-0049](adr/0049-the-build-must-not-destroy-what-it-could-not-rebuild.md) ·
[ADR-0051](adr/0051-ci-had-no-memory.md)). 그 빌드가 실패해도 **이전 계수를 파괴하지 않고
이어받으며**, 이어받은 날은 워크플로가 빨간불로 알립니다.

- Render 서버 자체는 KRX에 막히지 않습니다(2026-08-13 확인 · Render Shell에서 200).
- 계수 브랜치 상태는 `data-coefficients` 브랜치의 `meta.json`에서 시장별 생성 시각으로 확인합니다.
