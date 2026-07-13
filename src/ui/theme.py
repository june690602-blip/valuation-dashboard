"""전역 UI 테마 — 증권사·자산관리 리서치 톤(심플+고급).

원칙:
- 한국어 조판: 단어(어절) 단위 줄바꿈(keep-all) — 창 크기가 바뀌어도 단어 중간에서 끊기지 않음.
- 서체: Pretendard(로드 실패 시 시스템 한글 폰트로 자연 폴백). 숫자는 tabular-nums로 정렬.
- 색: 차트 판정색(파랑=저평가·빨강=고평가)은 건드리지 않고, UI 뼈대는 네이비+웜그레이 헤어라인.
- 공간: 카드·헤어라인(1px)·여백으로 구획. 그림자는 거의 없이(0~4% 알파) 절제.
"""

GLOBAL_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');

:root {
  --ink: #22252b;        /* 본문 잉크 */
  --ink2: #4c4f56;       /* 보조 텍스트 */
  --muted: #83817a;      /* 캡션·라벨 */
  --line: #e7e5df;       /* 헤어라인 */
  --line-soft: #efede8;
  --surface: #ffffff;    /* 카드 표면 */
  --navy: #1c5cab;       /* 액센트(팔레트 blue_deep과 동일) */
}

/* ── 한국어 조판: 단어 단위 줄바꿈 + 균형 잡힌 행 나눔 ───────────────
   Streamlit이 마크다운 요소에 word-break: break-word(단어 중간 끊김)를 직접 걸므로
   !important로 어절 단위(keep-all)를 강제한다. 넘치는 긴 영문·URL만 overflow-wrap이 처리. */
html, body,
[data-testid="stAppViewContainer"] *,
[data-testid="stSidebar"] *,
[data-testid="stHeader"] * {
  word-break: keep-all !important;
  overflow-wrap: break-word;
}
h1, h2, h3, h4, h5, h6 { text-wrap: balance; }
p, li { text-wrap: pretty; }

/* ── 서체 (아이콘·코드 글꼴은 보존) ────────────────────────────────── */
*:not([data-testid="stIconMaterial"]):not(.material-symbols-rounded):not(code):not(pre):not(kbd) {
  font-family: 'Pretendard Variable', Pretendard, -apple-system, 'Segoe UI',
               'Malgun Gothic', sans-serif !important;
}
code, pre, kbd {
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace !important;
}

/* ── 타이포 위계 ───────────────────────────────────────────────────── */
h1 { letter-spacing: -0.02em; font-weight: 800; }
h2, h3 { letter-spacing: -0.015em; font-weight: 750; color: var(--ink); }
[data-testid="stMarkdownContainer"] h5 {
  font-size: 1.02rem; font-weight: 700; letter-spacing: -0.005em;
  color: var(--ink); margin-bottom: 0.3rem;
}
[data-testid="stCaptionContainer"] p, small { color: var(--muted); }

/* 섹션 오버라인(영문 소제목) — 리서치 리포트의 눈썹 라벨 */
.eyebrow {
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.16em;
  color: var(--muted); text-transform: uppercase; margin: 0 0 0.1rem 0;
}
/* 섹션 제목 옆 보조 설명 */
.sec-desc { color: var(--muted); font-size: 0.78rem; font-weight: 400; margin-left: 0.45rem; }
/* 뉴스 카테고리 컬러 도트 */
.cat-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 2px;
  margin-right: 7px; vertical-align: 1px;
}

/* ── 기업 소개 카드 ────────────────────────────────────────────────── */
.intro-card {
  background: var(--surface); border: 1px solid var(--line);
  border-left: 3px solid var(--navy); border-radius: 12px;
  padding: 1.05rem 1.25rem 0.9rem;
}
.intro-card p { margin: 0 0 0.55rem 0; color: var(--ink2); line-height: 1.8; }
.intro-meta { color: var(--muted); font-size: 0.8rem; }

/* ── 메트릭: 카드화 + 창 폭에 따라 숫자 크기 자동 축소(겹침 방지) ──── */
[data-testid="stMetric"] {
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
  padding: 0.8rem 1rem 0.75rem; box-shadow: 0 1px 2px rgba(23, 32, 52, 0.04);
}
[data-testid="stMetricLabel"] p {
  font-size: 0.78rem !important; font-weight: 600; color: var(--muted);
  letter-spacing: 0.02em;
}
[data-testid="stMetricValue"] {
  font-weight: 700; letter-spacing: -0.01em; line-height: 1.3;
  font-size: clamp(1.02rem, 0.55rem + 0.95vw, 1.5rem);
  font-variant-numeric: tabular-nums; color: var(--ink);
  /* nowrap 대신 keep-all(전역) — '1,666조'는 절대 안 끊기고,
     '362,500 / 59,124'처럼 공백 있는 값만 슬래시에서 두 줄로 접힘(말줄임표 방지) */
}
[data-testid="stMetricDelta"] { font-size: 0.85rem; }

/* ── 탭: 언더라인 스타일 ───────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: 0.1rem; border-bottom: 1px solid var(--line);
}
[data-testid="stTabs"] button[role="tab"] { padding: 0.45rem 0.85rem 0.5rem; }
[data-testid="stTabs"] button[role="tab"] p {
  font-size: 0.92rem !important; color: var(--ink2);
  white-space: nowrap;  /* 탭 라벨은 두 줄로 꺾지 않음(좁으면 탭바가 가로 스크롤) */
}
[data-testid="stTabs"] button[aria-selected="true"] p { color: var(--navy); font-weight: 700; }
[data-baseweb="tab-highlight"] { background-color: var(--navy) !important; height: 2.5px; }
[data-baseweb="tab-border"] { background-color: transparent; }

/* ── 컨테이너류: 헤어라인 + 절제된 라운드 ──────────────────────────── */
.stButton button, .stDownloadButton button, .stLinkButton a, .stFormSubmitButton button {
  border-radius: 10px; font-weight: 600;
}
[data-testid="stExpander"] details {
  border: 1px solid var(--line); border-radius: 10px; background: var(--surface);
}
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 10px; }
[data-testid="stAlert"] { border-radius: 10px; }
hr { border-color: var(--line-soft); }

/* ── 사이드바·헤더 ─────────────────────────────────────────────────── */
[data-testid="stSidebar"] { background: #f7f6f1; border-right: 1px solid var(--line); }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { font-size: 0.9rem; }
[data-testid="stHeader"] { background: rgba(252, 252, 251, 0.8); backdrop-filter: blur(4px); }

/* 우상단 기본 'Running' 상태 위젯 숨김(로딩 안내는 st.spinner로만) */
[data-testid="stStatusWidget"] { display: none !important; }
</style>
"""
