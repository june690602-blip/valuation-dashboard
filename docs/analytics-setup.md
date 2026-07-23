# 방문자 분석 설정 — Google Analytics 4 + Microsoft Clarity

방문자가 **어디서 왔고, 무엇을 봤고, 어떻게 행동했는지**(히트맵·세션 녹화 포함)를 수집하고,
외부 대시보드에 로그인하지 않아도 사이트 안의 **관리 페이지(`/admin.html`)** 에서 숫자를 봅니다.

- 추적(수집): 모든 페이지가 `assets/analytics.js`로 GA4·Clarity 스니펫을 주입 — **ID가 설정된 경우에만**.
  로컬 개발 기본값은 미설정 = 추적 없음.
- 조회(관리 페이지): 서버가 GA Data API·Clarity Data Export API를 대신 호출(키는 서버에만 존재).
- 한계 한 가지: **히트맵·세션 녹화 "화면" 자체는 Clarity가 임베드를 막아** 관리 페이지의 버튼으로
  Clarity에서 열린다. 숫자(트래픽·스크롤 깊이·데드/분노 클릭 등)는 관리 페이지에 직접 나온다.

## 필요한 키 6개 (전부 Render 환경변수 또는 `.streamlit/secrets.toml`)

| 키 | 용도 | 어디서 얻나 |
|---|---|---|
| `GA_MEASUREMENT_ID` | GA 추적 (공개 ID) | GA4 속성 → 데이터 스트림 → 측정 ID `G-XXXXXXX` |
| `CLARITY_PROJECT_ID` | Clarity 추적 (공개 ID) | Clarity 프로젝트 설정 → 프로젝트 ID |
| `ADMIN_TOKEN` | 관리 페이지 잠금 | 직접 정한 긴 무작위 문자열 |
| `CLARITY_API_TOKEN` | 관리 페이지의 Clarity 숫자 | Clarity → Settings → **Data Export** → Generate new API token |
| `GA_PROPERTY_ID` | 관리 페이지의 GA 숫자 | GA 관리 → 속성 설정 → **속성 ID** (숫자만) |
| `GA_SA_KEY_JSON` | 관리 페이지의 GA 숫자 (인증) | 아래 3단계에서 받은 서비스계정 **JSON 파일 내용 전체** |

## 1. Google Analytics 4 (10분)

1. [analytics.google.com](https://analytics.google.com) → 관리(⚙) → **계정/속성 만들기** (속성 이름: 투자지표).
2. 데이터 스트림 → **웹** → 배포 URL 입력 → 만들면 **측정 ID `G-…`** 가 보인다 → `GA_MEASUREMENT_ID`.
3. 같은 화면의 관리 → 속성 설정에서 **속성 ID(숫자)** 확인 → `GA_PROPERTY_ID`.

## 2. Microsoft Clarity (5분)

1. [clarity.microsoft.com](https://clarity.microsoft.com) → **새 프로젝트** → 사이트 URL 입력.
2. 설치 방법 묻는 화면은 **건너뛰어도 됨**(스니펫은 이 리포가 주입) — 프로젝트 설정에서
   **프로젝트 ID** 복사 → `CLARITY_PROJECT_ID`.
3. Settings → **Data Export** → **Generate new API token** → `CLARITY_API_TOKEN`.
   (이 API는 **하루 10회** 제한이라 서버가 6시간 캐시로 아껴 쓴다.)

## 3. GA 숫자를 관리 페이지로 — 서비스계정 (15분, 가장 낯선 단계)

GA 데이터는 구글 로그인 없이 못 읽으므로, "로봇 계정"(서비스계정)을 만들어 GA에 초대한다.

1. [console.cloud.google.com](https://console.cloud.google.com) → 새 프로젝트(이름 아무거나).
2. `API 및 서비스` → 라이브러리 → **Google Analytics Data API** 검색 → **사용 설정**.
3. `IAM 및 관리자` → **서비스 계정** → 만들기(이름: analytics-reader, 역할은 안 줘도 됨).
4. 만든 계정 → **키** 탭 → 키 추가 → **JSON** → 파일 다운로드.
   파일 내용 **전체를 그대로** `GA_SA_KEY_JSON` 값으로(여러 줄 JSON 그대로 붙여넣기).
   ⚠️ 이 파일은 절대 리포에 커밋하지 말 것.
5. 서비스계정 이메일(`…@….iam.gserviceaccount.com`)을 복사 →
   GA 관리 → 속성 → **속성 액세스 관리** → ➕ → 이메일 붙여넣고 역할 **뷰어**로 추가.

## 4. 서버에 키 넣기

**배포(Render)**: 대시보드 → 서비스 → Environment → 위 6개 키 입력 → 재배포.
**로컬**: `.streamlit/secrets.toml`(gitignore 됨)에 추가 — 로컬은 안 넣으면 추적이 꺼진 채로 동작(정상).

```toml
GA_MEASUREMENT_ID = "G-XXXXXXXXXX"
CLARITY_PROJECT_ID = "xxxxxxxxxx"
ADMIN_TOKEN = "아주-길고-무작위인-문자열"
CLARITY_API_TOKEN = "..."
GA_PROPERTY_ID = "123456789"
GA_SA_KEY_JSON = '''{ "type": "service_account", ... }'''
```

## 5. 확인

1. 배포 사이트 접속 → GA 실시간 보고서·Clarity 대시보드에 방문이 찍히는지 확인(수 분 소요).
2. `https://<배포주소>/admin.html` → `ADMIN_TOKEN` 입력 → 숫자가 나오는지 확인.
   친구에게는 **이 URL + ADMIN_TOKEN**만 알려주면 된다(구글/MS 계정 불필요).
3. 본인 방문 제외: 아무 페이지나 `?notrack=1` 붙여 한 번 접속(그 브라우저는 계속 제외, 해제는 `?track=1`).

## 자주 걸리는 것

- 관리 페이지 GA 칸이 "GA 접근 거부" → 3-5단계(속성 액세스에 서비스계정 추가)를 빠뜨린 경우가 대부분.
- Clarity 칸이 비어 있음 → 데이터가 쌓이기 전(하루 이내)이거나 일일 10회 한도. 몇 시간 뒤 갱신.
- GA 데이터는 수집 후 보고서 반영까지 **24~48시간** 걸릴 수 있다(실시간 카드만 즉시).
