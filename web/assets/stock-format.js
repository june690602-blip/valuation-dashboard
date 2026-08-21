/* 값을 글자로, 판정을 어휘로 — stock.js에서 떼어냈다(이슈 #79 ㉮).

   R5 조서가 지목한 경계 표의 두 번째 줄이다(*"포맷터 9개 + 판정 어휘 헬퍼 3개 —
   밖으로 나가는 의존은 모듈 전역 `CUR` 하나"*). 첫 줄(DOM·문자열 빌더)은 common.js가,
   네 번째 줄(주가 캔버스 차트)은 stock-price-chart.js가 이미 가져갔다.

   **분할을 막는 것은 빌드 도구가 아니라 전역이었다.** 이 덩어리에서 전역은 `CUR`
   하나뿐이라 먼저 뗀다 — 그 하나를 어떻게 넘길지 정하는 것이 이 파일의 실제 내용이다.

   ## `CUR`의 주인을 여기로 옮긴다

   예전에는 stock.js의 맨몸 전역 `var CUR = 'KRW'`였고 `renderAll()`·`renderEtf()`
   **두 곳이 각자 대입**했다. 포맷터를 떼어내려면 그 값을 넘겨야 하는데, 60여 개
   호출부에 통화 인자를 추가하는 것은 이 작업이 피하려는 종류의 변경이다.
   그래서 **소유자를 이 모듈로 옮기고** 대입은 `setCurrency()` 하나로 모았다.
   stock.js는 읽을 때 `currency()`를 부른다.

   ⚠ 이 파일은 ES 모듈이 아니다 — common.js·finmath.js·stock-price-chart.js와 같은
   IIFE + `window.X` 방식이다. `<script type="module">`로 바꾸면 로딩이 defer로 바뀌어
   실행 시점이 달라진다. 이번 분할의 합격선은 **동작이 한 글자도 안 바뀌는 것**이라
   그 변경은 여기 섞지 않는다.

   밖의 것은 `esc` 하나만 쓴다(common.js). 사본을 만들면 같은 이름이 파일마다 다른
   뜻이 된다(R5 2번 바구니) — 복사하지 않고 window.DV에서 가져온다. */
(function () {
  'use strict';

  var esc = window.DV.esc;

  /* ── 통화 ── */
  var CUR = 'KRW';
  function setCurrency(c) { CUR = c || 'KRW'; }
  function currency() { return CUR; }

  /* ── 포맷터 ── */
  function won(v) { return v == null ? '—' : Math.round(v).toLocaleString('en-US'); }
  function fmtPrice(v) { if (v == null) return '—'; return CUR === 'KRW' ? won(v) + '원' : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function fmtMoney(v) {
    if (v == null) return '—'; var a = Math.abs(v);
    if (CUR === 'KRW') { if (a >= 1e12) return (v / 1e12).toFixed(1) + '조원'; if (a >= 1e8) return Math.round(v / 1e8).toLocaleString('en-US') + '억원'; return won(v) + '원'; }
    if (a >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B'; if (a >= 1e6) return '$' + (v / 1e6).toFixed(0) + 'M'; return '$' + won(v);
  }
  function fmtPct(v, d) { return v == null ? '—' : (v * 100).toFixed(d == null ? 1 : d) + '%'; }
  function fmtX(v) { if (v == null) return '—'; return (v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : v.toFixed(0)) + '×'; }
  function fmtSigned(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }
  function fmtMult(key, v) { if (v == null) return '—'; if (key === 'div_yield') return (v * 100).toFixed(1) + '%'; if (key === 'peg') return v.toFixed(2); return fmtX(v); }
  /* 빈 값 '—'에 이유 말풍선을 붙인다(순수 CSS 툴팁, .na) — 왜 없는지 짧게 알린다. */
  function na(reason) { return '<span class="na" tabindex="0" data-tip="' + esc(reason) + '">—</span>'; }
  function compactWon(v) { if (v == null) return '—'; return CUR === 'KRW' ? Math.round(v / 1000).toLocaleString('en-US') + '천' : '$' + Math.round(v).toLocaleString('en-US'); }

  /* ── 판정 어휘 ── */
  /* 판정 3등급 (ADR-0042). 파이썬 `valuation.VERDICTS`와 **순서까지** 같아야 한다 —
     `tests/test_analysis_accuracy.py`의 등급 목록 대조가 이 파일을 읽어 지킨다.
     옛 5등급에서 줄었고, 사라진 것은 '크게'가 붙던 양 끝과 그 안쪽 둘이다
     (백테스트에서 가운데 셋이 구별되지 않았다 · ADR-0028). */
  var VERDICTS = ['저평가', '적정 수준', '고평가'];
  function vIdx(v) { var i = VERDICTS.indexOf(v); return i < 0 ? 1 : i; }
  // 괴리율이 없을 때만 쓰는 자리 — 각 칸의 **중앙**이다(칸 경계가 25·75이므로).
  function vPos(v) { return [12.5, 50, 87.5][vIdx(v)]; }
  function vTone(v) { var i = vIdx(v); return i === 0 ? 'positive' : i === 1 ? 'neutral' : 'negative'; }

  window.DVFmt = {
    setCurrency: setCurrency, currency: currency,
    won: won, fmtPrice: fmtPrice, fmtMoney: fmtMoney, fmtPct: fmtPct, fmtX: fmtX,
    fmtSigned: fmtSigned, fmtMult: fmtMult, na: na, compactWon: compactWon,
    VERDICTS: VERDICTS, vIdx: vIdx, vPos: vPos, vTone: vTone
  };
})();
