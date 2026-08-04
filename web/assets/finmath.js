/* ══════════════════════════════════════════════════════════════════════
   투자지표 — 두 언어에 사는 수식 한 벌 (Meridian).

   브라우저는 파이썬을 실행하지 못한다. 슬라이더를 움직일 때마다 서버를 왕복하지
   않으려고 몇몇 계산은 JS로 이식돼 있는데, 그러면 같은 개념이 두 곳에서 계산된다.
   실제로 한 번 갈렸다 — 업종 중앙값을 브라우저는 자사를 포함해 내고 서버는 빼고 내서
   삼성전자 기준 12.77배 vs 11.66배였다(#78). 값이 갈린 것도 문제지만, **갈린 줄
   아무도 몰랐다는 것**이 진짜 문제였다.

   그래서 이식된 수식은 전부 이 파일 한 곳에 산다. 브라우저와 Node가 **같은 이 파일**을
   읽고, CI가 Node로 이것을 실행해 파이썬 쌍둥이와 전 격자 대조한다
   (`scripts/check_client_math.py`). 손으로 옮겨 적은 참조 구현이 아니라 사용자가 실제로
   받는 코드를 실행하므로, 아래 함수를 고치면 파이썬과 어긋나는 순간 CI가 잡는다.

   여기에 함수를 더할 때는 파이썬 쌍둥이를 함께 등록해야 한다 — 등록 없이 더하면
   대조 검사가 실패한다. 화면을 그리는 코드는 두지 않는다(순수 함수만).
   ══════════════════════════════════════════════════════════════════════ */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;   // Node — 대조 검사
  else root.DVMath = api;                                                    // 브라우저
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ── 채권 (쌍둥이: src/analysis/bond_math.py) ────────────────────────
     관례: 금리는 소수(0.04=4%), 이표는 연 freq회 지급, 만기 일시상환 액면채. */

  // (기간 인덱스 k=1..N, 각 기간 현금흐름의 PV). N은 최소 1.
  function cashflowPV(face, coupon, ytm, years, freq) {
    var n = Math.max(Math.round(years * freq), 1), y = ytm / freq, k = [], pv = [];
    for (var i = 1; i <= n; i++) {
      var cf = face * coupon / freq + (i === n ? face : 0);
      k.push(i); pv.push(cf / Math.pow(1 + y, i));
    }
    return { k: k, pv: pv, n: n };
  }

  function bondPrice(face, coupon, ytm, years, freq) {
    var c = cashflowPV(face, coupon, ytm, years, freq), s = 0;
    for (var i = 0; i < c.pv.length; i++) s += c.pv[i];
    return s;
  }

  // 가격·맥컬리/수정듀레이션·볼록성·DV01을 한 번에.
  function bondMetrics(face, coupon, ytm, years, freq) {
    var c = cashflowPV(face, coupon, ytm, years, freq), price = 0, y = ytm / freq, mac = 0, conv = 0, i;
    for (i = 0; i < c.pv.length; i++) price += c.pv[i];
    for (i = 0; i < c.k.length; i++) {
      var t = c.k[i] / freq;
      mac += t * c.pv[i];
      conv += c.pv[i] * c.k[i] * (c.k[i] + 1);
    }
    mac /= price;
    var modd = mac / (1 + y);
    conv = conv / (price * Math.pow(1 + y, 2) * freq * freq);
    return { price: price, macaulay: mac, modified: modd, convexity: conv, dv01: price * modd * 1e-4 };
  }

  // 금리 충격별 가격 변화 — 듀레이션 근사 vs 볼록성 보정 vs 정확 재계산.
  var SHOCKS_BP = [-100, -50, -25, 25, 50, 100];
  function rateScenarios(face, coupon, ytm, years, freq, shocksBp) {
    var m = bondMetrics(face, coupon, ytm, years, freq);
    return (shocksBp || SHOCKS_BP).map(function (bp) {
      var dy = bp / 1e4, exact = bondPrice(face, coupon, Math.max(ytm + dy, 0), years, freq);
      return {
        shock_bp: bp,
        exact_price: exact,
        exact_pct: exact / m.price - 1,
        dur_pct: -m.modified * dy,
        durconv_pct: -m.modified * dy + 0.5 * m.convexity * dy * dy
      };
    });
  }

  /* ── 접점 포트폴리오 (쌍둥이: src/analysis/risk_profile.py::tangency_point) ──
     머튼 비율 y* = (E(Rm)−Rf)/(A·σm²). 서버는 CML 가정(rf·er_m·sigma_m)만 보내고
     접점은 프런트가 그린다. 파이썬 쪽은 유한하지 않은 입력에 예외를 던지는데, 부르는
     쪽(portfolio.js)이 Number.isFinite로 먼저 거르므로 여기서는 그 자리에 닿지 않는다.
     파이썬은 utility·sharpe·mrs도 함께 돌려주지만 화면이 쓰지 않아 옮기지 않았다. */
  function tangency(erM, rf, sigM, A) {
    var y = (A <= 0 || sigM <= 0) ? 0 : (erM - rf) / (A * sigM * sigM);
    return { y: y, sigma_p: Math.abs(y) * sigM, er_p: rf + y * (erM - rf) };
  }

  /* ── 시나리오 케이스 가격 (쌍둥이: src/analysis/scenario.py::case_price) ──
     EPS 조정과 멀티플 조정을 각각 곱한다. 곱하는 순서를 파이썬과 똑같이 묶어 두는데,
     부동소수는 묶는 방식이 다르면 마지막 자리가 달라지기 때문이다. */
  function scenarioCasePrice(epsBase, epsDelta, multiple, multAdjust) {
    return (epsBase * (1 + epsDelta)) * (multiple * (1 + (multAdjust || 0)));
  }

  /* ── 업종 중앙값 (쌍둥이: src/analysis/scoring.py::peer_median) ────────
     자사를 뺀 피어만으로 낸다. 자사를 넣으면 중앙선이 자사 쪽으로 끌려 실제보다 업종에
     가까워 보이고, 표본이 홀수면 중앙값이 자사 자신이 되어 가로선이 자기 점 위에
     그려진다. 표본이 minN개 미만이면 **중앙값을 만들지 않고 null**을 돌려준다 —
     파이썬 쪽이 그렇게 하고, 몇 개 안 되는 표본의 중앙값은 업종을 대표하지 못한다.
     points: [{ [key]: 숫자, self: 자사 여부 }] (파이썬 쪽 is_self 열에 해당) */
  function peerMedian(points, key, excludeSelf, minN) {
    if (excludeSelf === undefined) excludeSelf = true;
    if (minN === undefined) minN = 3;
    var pool = excludeSelf ? points.filter(function (p) { return !p.self; }) : points;
    var v = pool.map(function (p) { return p[key]; })
                .filter(function (x) { return x != null && isFinite(x); });
    if (v.length < minN) return null;
    var s = v.slice().sort(function (a, b) { return a - b; }), m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  return {
    cashflowPV: cashflowPV,
    bondPrice: bondPrice,
    bondMetrics: bondMetrics,
    rateScenarios: rateScenarios,
    tangency: tangency,
    scenarioCasePrice: scenarioCasePrice,
    peerMedian: peerMedian
  };
});
