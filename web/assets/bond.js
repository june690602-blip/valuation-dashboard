/* ══════════════════════════════════════════════════════════════════════
   투자지표 — 채권(Meridian). 수익률곡선·금리추이·시나리오 분석기·뉴스.
   곡선/히스토리/뉴스는 /api/bond·/api/bond_history, 시나리오 수학은 클라이언트(즉시 반응).
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── SVG/HTML 빌더 (stock.js와 동일 규약) ── */
  var ATTR = { strokeWidth: 'stroke-width', strokeDasharray: 'stroke-dasharray', strokeLinecap: 'stroke-linecap', strokeLinejoin: 'stroke-linejoin', fillOpacity: 'fill-opacity', textAnchor: 'text-anchor', fontFamily: 'font-family', fontSize: 'font-size', fontWeight: 'font-weight', className: 'class' };
  function kebab(s) { return s.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }
  function styleStr(o) { var s = ''; for (var k in o) s += kebab(k) + ':' + o[k] + ';'; return s; }
  function el(tag, attrs) {
    var kids = Array.prototype.slice.call(arguments, 2); attrs = attrs || {}; var style = {};
    if (attrs.style) for (var sk in attrs.style) style[sk] = attrs.style[sk];
    var s = '<' + tag;
    for (var k in attrs) { if (k === 'style' || attrs[k] == null) continue; var val = attrs[k]; if (typeof val === 'string' && val.indexOf('var(') >= 0) { style[k] = val; continue; } s += ' ' + (ATTR[k] || k) + '="' + String(val).replace(/"/g, '&quot;') + '"'; }
    var st = styleStr(style); if (st) s += ' style="' + st + '"'; s += '>';
    for (var i = 0; i < kids.length; i++) { var c = kids[i]; if (c == null || c === false) continue; s += Array.isArray(c) ? c.join('') : c; }
    return s + '</' + tag + '>';
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (m) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]; }); }
  function $(id) { return document.getElementById(id); }

  /* ── 채권 수학 (bond_math.py 이식) ── */
  var FACE = 100.0;
  function cashflowPV(face, coupon, ytm, years, freq) {
    var n = Math.max(Math.round(years * freq), 1), y = ytm / freq, k = [], pv = [];
    for (var i = 1; i <= n; i++) { var cf = face * coupon / freq + (i === n ? face : 0); k.push(i); pv.push(cf / Math.pow(1 + y, i)); }
    return { k: k, pv: pv, n: n };
  }
  function bondPrice(face, coupon, ytm, years, freq) { var c = cashflowPV(face, coupon, ytm, years, freq), s = 0; for (var i = 0; i < c.pv.length; i++) s += c.pv[i]; return s; }
  function bondMetrics(face, coupon, ytm, years, freq) {
    var c = cashflowPV(face, coupon, ytm, years, freq), price = 0, y = ytm / freq, mac = 0, conv = 0;
    for (var i = 0; i < c.pv.length; i++) price += c.pv[i];
    for (i = 0; i < c.k.length; i++) { var t = c.k[i] / freq; mac += t * c.pv[i]; conv += c.pv[i] * c.k[i] * (c.k[i] + 1); }
    mac /= price; var modd = mac / (1 + y); conv = conv / (price * Math.pow(1 + y, 2) * freq * freq);
    return { price: price, macaulay: mac, modified: modd, convexity: conv, dv01: price * modd * 1e-4 };
  }
  function rateScenarios(face, coupon, ytm, years, freq) {
    var shocks = [-100, -50, -25, 25, 50, 100], m = bondMetrics(face, coupon, ytm, years, freq);
    return shocks.map(function (bp) { var dy = bp / 1e4, exact = bondPrice(face, coupon, Math.max(ytm + dy, 0), years, freq); return { shock_bp: bp, exact_price: exact, exact_pct: exact / m.price - 1, dur_pct: -m.modified * dy, durconv_pct: -m.modified * dy + 0.5 * m.convexity * dy * dy }; });
  }

  /* ── 상태 ── */
  var TENORS = { KR: [1, 2, 3, 5, 10, 20, 30], US: [1, 2, 3, 5, 7, 10, 20, 30] };
  // mkts: 다중 선택 — 한국만/미국만/둘 다 (둘 다면 곡선·추이를 한 차트에 겹쳐 그림)
  var state = { mkts: { KR: true, US: true }, histTenor: 10, scMkt: 'KR', scTenor: 10, scFreq: 2 };
  var BOND = null;
  // 차트 hover 툴팁용 렌더 컨텍스트(마지막으로 그린 차트의 데이터·스케일)
  var HIST_CTX = null, CURVE_CTX = null;

  function activeMkts() { return ['KR', 'US'].filter(function (m) { return state.mkts[m]; }); }
  function histTenorList() {
    var ms = activeMkts(), ts = TENORS[ms[0]] || [];
    if (ms.length > 1) ts = ts.filter(function (t) { return TENORS[ms[1]].indexOf(t) >= 0; });
    return ts;
  }

  function fmtSigned(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'; }
  function curveYield(c, t) { if (!c || !c.tenors || !c.tenors.length) return null; var best = null, bd = 1e9; for (var i = 0; i < c.tenors.length; i++) { var d = Math.abs(c.tenors[i] - t); if (d < bd) { bd = d; best = c.yields[i]; } } return best; }
  function curveYieldExact(c, t) { if (!c || !c.tenors) return null; for (var i = 0; i < c.tenors.length; i++) if (Math.abs(c.tenors[i] - t) < 0.01) return c.yields[i]; return null; }

  /* ══════════ 차트 ══════════ */
  function yieldCurveChart(curves) {
    var series = []; for (var nm in curves) { var c = curves[nm]; if (c && c.tenors && c.tenors.length) series.push({ name: nm, c: c }); }
    if (!series.length) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px', padding: '20px 0' } }, '수익률곡선 데이터를 가져오지 못했습니다. 아래 시나리오 분석기는 금리를 직접 넣어 계속 쓸 수 있습니다.');
    var W = 900, padL = 42, padR = 60, top = 16, plotH = 250, xw = W - padL - padR;
    var maxT = 30, tvals = [1, 2, 3, 5, 10, 20, 30];
    var ally = []; series.forEach(function (s) { s.c.yields.forEach(function (v) { if (v != null) ally.push(v); }); });
    var ymin = Math.min.apply(null, ally), ymax = Math.max.apply(null, ally); var pad = (ymax - ymin) * 0.15 || 0.2; ymin -= pad; ymax += pad;
    var X = function (t) { return padL + Math.sqrt(t / maxT) * xw; };          // √스케일: 단기 구간을 넓게
    var Y = function (v) { return top + (1 - (v - ymin) / (ymax - ymin)) * plotH; };
    // 색은 선택 조합과 무관하게 국가 고정: 한국=남색, 미국=클레이
    var colorOf = function (nm) { return nm.indexOf('한국') >= 0 ? 'var(--dv-navy)' : 'var(--dv-clay)'; };
    var els = [];
    for (var g = 0; g <= 3; g++) { var yy = top + g / 3 * plotH, val = ymax - (ymax - ymin) * g / 3; els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL - 8, y: yy + 4, fontSize: 11.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, val.toFixed(2) + '%')); }
    tvals.forEach(function (t) { els.push(el('text', { x: X(t), y: top + plotH + 18, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, t + 'Y')); });
    var ctxSeries = [];
    series.forEach(function (s) {
      var col = colorOf(s.name);
      var pairs = s.c.tenors.map(function (t, i) { return [t, s.c.yields[i]]; }).filter(function (p) { return p[1] != null; }).sort(function (a, b) { return a[0] - b[0]; });
      ctxSeries.push({ name: s.name, col: col, pairs: pairs });
      var p = ''; pairs.forEach(function (pr, i) { p += (i ? 'L' : 'M') + X(pr[0]).toFixed(1) + ' ' + Y(pr[1]).toFixed(1) + ' '; });
      els.push(el('path', { d: p, fill: 'none', stroke: col, strokeWidth: 2.2 }));
      pairs.forEach(function (pr) { els.push(el('circle', { cx: X(pr[0]), cy: Y(pr[1]), r: 3.6, fill: col })); });
    });
    CURVE_CTX = { series: ctxSeries, maxT: maxT, padL: padL, xw: xw, top: top, plotH: plotH, W: W, ymin: ymin, ymax: ymax };
    // 범례
    var lg = el('div', { style: { display: 'flex', gap: '18px', marginTop: '8px' } }, series.map(function (s) { return el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '7px', fontSize: '12.5px', color: 'var(--ink-2)' } }, el('span', { style: { width: '14px', height: '2px', background: colorOf(s.name), display: 'inline-block' } }), s.name); }).join(''));
    els.push(el('text', { x: padL + xw, y: top + plotH + 34, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, '만기 →'));
    return el('div', {}, el('svg', { viewBox: '0 0 ' + W + ' ' + (top + plotH + 42), style: { width: '100%', height: 'auto', display: 'block' } }, els), lg);
  }

  function yieldHistoryChartMulti(series) {
    // 여러 시장을 실제 날짜축(시간 스케일)에 겹쳐 그린다 — 시장별 표본 길이가 달라도 정렬이 맞는다
    var COLOR = { KR: 'var(--dv-navy)', US: 'var(--dv-clay)' };
    var pts = series.map(function (s) {
      var t = [], y = [];
      (s.h.dates || []).forEach(function (d, i) { var ts = Date.parse(d), v = s.h.yields[i]; if (!isNaN(ts) && v != null) { t.push(ts); y.push(v); } });
      return { mkt: s.mkt, label: s.h.label, t: t, y: y, col: COLOR[s.mkt] || 'var(--dv-navy)' };
    }).filter(function (p) { return p.t.length > 1; });
    if (!pts.length) return el('div', { style: { color: 'var(--ink-3)', fontSize: '13px', padding: '20px 0' } }, '이 만기의 시계열을 가져오지 못했습니다.');
    var tmin = Infinity, tmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    pts.forEach(function (p) {
      p.t.forEach(function (v) { if (v < tmin) tmin = v; if (v > tmax) tmax = v; });
      p.y.forEach(function (v) { if (v < ymin) ymin = v; if (v > ymax) ymax = v; });
    });
    var pad = (ymax - ymin) * 0.12 || 0.1; ymin -= pad; ymax += pad;
    var W = 900, padL = 42, padR = 16, top = 14, plotH = 200, xw = W - padL - padR;
    var X = function (t) { return padL + (tmax <= tmin ? 0 : (t - tmin) / (tmax - tmin) * xw); };
    var Y = function (v) { return top + (1 - (v - ymin) / (ymax - ymin)) * plotH; };
    var els = [];
    for (var g = 0; g <= 3; g++) { var yy = top + g / 3 * plotH, val = ymax - (ymax - ymin) * g / 3; els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL - 8, y: yy + 4, fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, val.toFixed(2) + '%')); }
    pts.forEach(function (pnt) {
      var c = COLOR[pnt.mkt] || 'var(--dv-navy)';
      var p = ''; for (var i = 0; i < pnt.t.length; i++) p += (i ? 'L' : 'M') + X(pnt.t[i]).toFixed(1) + ' ' + Y(pnt.y[i]).toFixed(1) + ' ';
      els.push(el('path', { d: p, fill: 'none', stroke: c, strokeWidth: 1.9 }));
      els.push(el('circle', { cx: X(pnt.t[pnt.t.length - 1]), cy: Y(pnt.y[pnt.y.length - 1]), r: 3.4, fill: c }));
    });
    for (var t = 0; t <= 4; t++) {
      var tv = tmin + (tmax - tmin) * t / 4, d = new Date(tv);
      var lb = String(d.getFullYear()).slice(2) + '.' + ('0' + (d.getMonth() + 1)).slice(-2);
      els.push(el('text', { x: X(tv), y: top + plotH + 16, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, lb));
    }
    var lg = el('div', { style: { display: 'flex', gap: '18px', marginTop: '8px', flexWrap: 'wrap' } }, pts.map(function (pnt) {
      var last = pnt.y[pnt.y.length - 1];
      return el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '7px', fontSize: '12.5px', color: 'var(--ink-2)' } },
        el('span', { style: { width: '14px', height: '2px', background: COLOR[pnt.mkt] || 'var(--dv-navy)', display: 'inline-block' } }),
        esc(pnt.label) + ' · 현재 ' + last.toFixed(2) + '%');
    }).join(''));
    HIST_CTX = { pts: pts, tmin: tmin, tmax: tmax, ymin: ymin, ymax: ymax, W: W, padL: padL, xw: xw, top: top, plotH: plotH };
    return el('div', {}, el('svg', { viewBox: '0 0 ' + W + ' ' + (top + plotH + 24), style: { width: '100%', height: 'auto', display: 'block' } }, els), lg);
  }

  function priceYieldChart(ytm, coupon, years, freq, price, modified) {
    var span = Math.max(0.02, ytm * 0.9), lo = Math.max(ytm - span, 0.0005), hi = ytm + span, N = 60;
    var xs = [], exact = [], tang = [];
    for (var i = 0; i < N; i++) { var y = lo + (hi - lo) * i / (N - 1); xs.push(y); exact.push(bondPrice(FACE, coupon, y, years, freq)); tang.push(price * (1 - modified * (y - ytm))); }
    var W = 560, H = 300, padL = 44, padR = 14, top = 14, plotH = H - 48, xw = W - padL - padR;
    var allp = exact.concat(tang, [price]); var pmin = Math.min.apply(null, allp), pmax = Math.max.apply(null, allp); var pd = (pmax - pmin) * 0.06 || 1; pmin -= pd; pmax += pd;
    var X = function (y) { return padL + (y - lo) / (hi - lo) * xw; }, Y = function (p) { return top + (1 - (p - pmin) / (pmax - pmin)) * plotH; };
    var els = [];
    for (var g = 0; g <= 3; g++) { var yy = top + g / 3 * plotH, val = pmax - (pmax - pmin) * g / 3; els.push(el('line', { x1: padL, x2: padL + xw, y1: yy, y2: yy, stroke: 'var(--line)', strokeWidth: 1 })); els.push(el('text', { x: padL - 6, y: yy + 4, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'end' }, val.toFixed(1))); }
    function line(arr, color, w, dash) { var p = ''; for (var i = 0; i < N; i++) p += (i ? 'L' : 'M') + X(xs[i]).toFixed(1) + ' ' + Y(arr[i]).toFixed(1) + ' '; return el('path', { d: p, fill: 'none', stroke: color, strokeWidth: w, strokeDasharray: dash || 'none' }); }
    els.push(line(tang, 'var(--dv-slate)', 1.8, '5 4'));
    els.push(line(exact, 'var(--dv-navy)', 2.4));
    els.push(el('circle', { cx: X(ytm), cy: Y(price), r: 5, fill: 'var(--dv-clay)', stroke: 'var(--paper)', strokeWidth: 1.5 }));
    els.push(el('text', { x: X(ytm), y: Y(price) - 10, fontSize: 11, fill: 'var(--dv-clay)', fontFamily: 'var(--font-sans)', fontWeight: 600, textAnchor: 'middle' }, '현재'));
    for (var t = 0; t <= 4; t++) { var yv = lo + (hi - lo) * t / 4; els.push(el('text', { x: X(yv), y: top + plotH + 16, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-mono)', textAnchor: 'middle' }, (yv * 100).toFixed(1) + '%')); }
    els.push(el('text', { x: padL + xw, y: H - 2, fontSize: 10.5, fill: 'var(--ink-3)', fontFamily: 'var(--font-sans)', textAnchor: 'end' }, 'YTM →'));
    var lg = el('div', { style: { display: 'flex', gap: '16px', marginTop: '6px' } },
      el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--ink-2)' } }, el('span', { style: { width: '14px', height: '2px', background: 'var(--dv-navy)', display: 'inline-block' } }), '실제 가격(볼록)'),
      el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--ink-2)' } }, el('span', { style: { width: '14px', height: '2px', background: 'var(--dv-slate)', display: 'inline-block' } }), '듀레이션 근사(접선)'));
    return el('div', {}, el('svg', { viewBox: '0 0 ' + W + ' ' + H, style: { width: '100%', height: 'auto', display: 'block' } }, els), lg);
  }

  /* ── 차트 hover 툴팁 (삽입된 SVG에 십자선+말풍선을 얹는다) ── */
  function attachChartHover(boxId, W, resolve) {
    var box = $(boxId); if (!box) return; var svg = box.querySelector('svg'); if (!svg) return;
    box.style.position = 'relative';
    var NS = 'http://www.w3.org/2000/svg';
    var g = document.createElementNS(NS, 'g'); g.setAttribute('pointer-events', 'none'); g.style.display = 'none'; svg.appendChild(g);
    var tip = document.createElement('div');
    tip.style.cssText = 'position:absolute;pointer-events:none;display:none;background:var(--paper);border:1px solid var(--line-strong);border-radius:6px;padding:6px 9px;font-size:11.5px;color:var(--ink);box-shadow:0 3px 10px rgba(30,25,18,.12);white-space:nowrap;z-index:6';
    box.appendChild(tip);
    svg.addEventListener('mousemove', function (e) {
      var rect = svg.getBoundingClientRect(); if (!rect.width) return;
      var vbX = (e.clientX - rect.left) / rect.width * W;
      var r = resolve(vbX);
      if (!r) { g.style.display = 'none'; tip.style.display = 'none'; return; }
      var parts = '<line x1="' + r.cx.toFixed(1) + '" x2="' + r.cx.toFixed(1) + '" y1="' + r.y1 + '" y2="' + r.y2 + '" stroke="var(--ink-3)" stroke-width="1" stroke-dasharray="3 3"/>';
      r.dots.forEach(function (d) { parts += '<circle cx="' + d.x.toFixed(1) + '" cy="' + d.y.toFixed(1) + '" r="3.8" fill="' + d.col + '" stroke="var(--paper)" stroke-width="1.5"/>'; });
      g.innerHTML = parts; g.style.display = '';
      tip.innerHTML = '<div style="color:var(--ink-3);margin-bottom:3px">' + esc(r.title) + '</div>' + r.rows.map(function (row) {
        return '<div style="display:flex;gap:7px;align-items:center;margin-top:2px"><span style="width:8px;height:8px;border-radius:50%;background:' + row.col + ';display:inline-block"></span>' + esc(row.label) + ' <b class="mono">' + row.v + '</b></div>';
      }).join('');
      var bx = box.getBoundingClientRect(); tip.style.display = 'block';
      var left = e.clientX - bx.left + 14; if (left + tip.offsetWidth > bx.width) left = e.clientX - bx.left - tip.offsetWidth - 14; if (left < 0) left = 2;
      var topPx = e.clientY - bx.top + 12; if (topPx + tip.offsetHeight > bx.height) topPx = e.clientY - bx.top - tip.offsetHeight - 12; if (topPx < 0) topPx = 2;
      tip.style.left = left + 'px'; tip.style.top = topPx + 'px';
    });
    svg.addEventListener('mouseleave', function () { g.style.display = 'none'; tip.style.display = 'none'; });
  }
  function wireHistHover() {
    var c = HIST_CTX; if (!c) return;
    var Xf = function (t) { return c.padL + (c.tmax <= c.tmin ? 0 : (t - c.tmin) / (c.tmax - c.tmin) * c.xw); };
    var Yf = function (v) { return c.top + (1 - (v - c.ymin) / (c.ymax - c.ymin)) * c.plotH; };
    function near(arr, t) { var lo = 0, hi = arr.length - 1; while (lo < hi) { var m = (lo + hi) >> 1; if (arr[m] < t) lo = m + 1; else hi = m; } if (lo > 0 && Math.abs(arr[lo - 1] - t) < Math.abs(arr[lo] - t)) lo--; return lo; }
    attachChartHover('histChart', c.W, function (vbX) {
      var frac = (vbX - c.padL) / c.xw; if (frac < 0) frac = 0; if (frac > 1) frac = 1;
      var tt = c.tmin + frac * (c.tmax - c.tmin), anchor = null, dots = [], rows = [];
      c.pts.forEach(function (p) { if (!p.t.length) return; var i = near(p.t, tt), pt = p.t[i], pv = p.y[i]; dots.push({ x: Xf(pt), y: Yf(pv), col: p.col }); rows.push({ label: p.label.replace(/ ·.*/, ''), v: pv.toFixed(2) + '%', col: p.col }); if (anchor == null || Math.abs(pt - tt) < Math.abs(anchor - tt)) anchor = pt; });
      if (anchor == null) return null;
      var d = new Date(anchor);
      return { cx: Xf(anchor), y1: c.top, y2: c.top + c.plotH, dots: dots, title: d.getFullYear() + '.' + ('0' + (d.getMonth() + 1)).slice(-2) + '.' + ('0' + d.getDate()).slice(-2), rows: rows };
    });
  }
  function wireCurveHover() {
    var c = CURVE_CTX; if (!c) return;
    var Xf = function (t) { return c.padL + Math.sqrt(t / c.maxT) * c.xw; };
    var Yf = function (v) { return c.top + (1 - (v - c.ymin) / (c.ymax - c.ymin)) * c.plotH; };
    attachChartHover('curveChart', c.W, function (vbX) {
      var fr = (vbX - c.padL) / c.xw; if (fr < 0) fr = 0; if (fr > 1) fr = 1;
      var tt = c.maxT * fr * fr, best = null, bd = 1e9;
      c.series.forEach(function (s) { s.pairs.forEach(function (pr) { var d = Math.abs(pr[0] - tt); if (d < bd) { bd = d; best = pr[0]; } }); });
      if (best == null) return null;
      var dots = [], rows = [];
      c.series.forEach(function (s) { var m = null; s.pairs.forEach(function (pr) { if (Math.abs(pr[0] - best) < 0.01) m = pr[1]; }); if (m != null) { dots.push({ x: Xf(best), y: Yf(m), col: s.col }); rows.push({ label: s.name, v: m.toFixed(2) + '%', col: s.col }); } });
      if (!rows.length) return null;
      var lbl = best < 1 ? Math.round(best * 12) + '개월' : best + '년';
      return { cx: Xf(best), y1: c.top, y2: c.top + c.plotH, dots: dots, title: '만기 ' + lbl, rows: rows };
    });
  }

  /* ══════════ 렌더 ══════════ */
  function tiles(container, items) { container.innerHTML = items.map(function (t, i) { return '<div class="tile" style="padding:' + (i === 0 ? '0 16px 0 0' : '0 16px') + (i ? ';border-left:1px solid var(--line)' : '') + '"><div class="kick">' + t[0] + '</div><div class="v">' + t[1] + '</div></div>'; }).join(''); }

  function renderRates() {
    // 국가 선택(mkts)에 따라 타일·곡선·스프레드 노트를 함께 필터링한다
    var p = BOND.policy || {};
    var tileItems = [], curves = {}, notes = [];
    if (state.mkts.KR) {
      var kr = BOND.kr, kr10 = curveYield(kr, 10), kr3 = curveYield(kr, 3);
      tileItems.push(['한국은행 기준금리', p['한국은행'] != null ? p['한국은행'].toFixed(2) + '%' : '—']);
      tileItems.push(['한국 국고채 10년', kr10 != null ? kr10.toFixed(3) + '%' : '—']);
      curves['🇰🇷 한국 국고채'] = kr;
      if (kr10 != null && kr3 != null) { var sp = (kr10 - kr3) * 100; notes.push('한국 10−3년 스프레드 <b class="mono">' + (sp >= 0 ? '+' : '') + sp.toFixed(0) + 'bp</b> (' + (sp > 0 ? '정상(우상향)' : '역전 — 침체 신호로 자주 해석') + ')'); }
    }
    if (state.mkts.US) {
      var us = BOND.us, us10 = curveYield(us, 10), us2 = curveYield(us, 2);
      tileItems.push(['미국 연준 기준금리', p['미국 연준'] != null ? p['미국 연준'].toFixed(2) + '%' : '—']);
      tileItems.push(['미국 국채 10년', us10 != null ? us10.toFixed(3) + '%' : '—']);
      curves['🇺🇸 미국 국채'] = us;
      if (us10 != null && us2 != null) { var sp2 = (us10 - us2) * 100; notes.push('미국 10−2년 스프레드 <b class="mono">' + (sp2 >= 0 ? '+' : '') + sp2.toFixed(0) + 'bp</b> (' + (sp2 > 0 ? '정상' : '역전') + ')'); }
    }
    tiles($('rateTiles'), tileItems);
    $('curveChart').innerHTML = yieldCurveChart(curves);
    wireCurveHover();
    var asof = (BOND.kr && BOND.kr.asof) || (BOND.us && BOND.us.asof) || '';
    $('curveNote').innerHTML = (notes.join(' · ') + (asof ? '  |  기준일 ' + asof : '')) || '';
    $('asof').textContent = asof ? '기준일 ' + asof : '';
  }

  function renderHistTenorSeg() {
    var ts = histTenorList();
    if (ts.indexOf(state.histTenor) < 0) state.histTenor = 10;
    $('histTenor').innerHTML = ts.map(function (t) { return '<button class="' + (t === state.histTenor ? 'on' : '') + '" data-val="' + t + '">' + t + 'Y</button>'; }).join('');
  }
  function loadHistory() {
    var ms = activeMkts();
    $('histChart').innerHTML = '<div style="color:var(--ink-3);font-size:12.5px;padding:20px 0">금리 시계열 불러오는 중…</div>';
    Promise.all(ms.map(function (m) {
      return fetch('api/bond_history?market=' + m + '&tenor=' + state.histTenor)
        .then(function (r) { return r.json(); }).catch(function () { return { error: 1 }; });
    })).then(function (hs) {
      var series = [];
      hs.forEach(function (h, i) { if (h && !h.error && h.yields && h.yields.length > 1) series.push({ mkt: ms[i], h: h }); });
      if (!series.length) { $('histChart').innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">불러오기 실패.</div>'; $('histNote').textContent = ''; return; }
      $('histChart').innerHTML = yieldHistoryChartMulti(series);
      wireHistHover();
      $('histNote').innerHTML = series.map(function (s) {
        var h = s.h;
        return h.change_bp != null
          ? esc(h.label) + ' — 표시 구간 변화 <b class="mono">' + (h.change_bp >= 0 ? '+' : '') + h.change_bp.toFixed(0) + 'bp</b> · 표본 ' + h.n + '일 · 출처 ' + esc(h.source)
          : '';
      }).filter(Boolean).join('<br/>');
    }).catch(function () { $('histChart').innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">서버 연결 실패.</div>'; });
  }

  function renderScTenorSeg() {
    var ts = TENORS[state.scMkt]; if (ts.indexOf(state.scTenor) < 0) state.scTenor = 10;
    $('scTenor').innerHTML = ts.map(function (t) { return '<option value="' + t + '"' + (t === state.scTenor ? ' selected' : '') + '>' + t + '년</option>'; }).join('');
  }
  function prefillYtm() {
    var curve = state.scMkt === 'KR' ? BOND.kr : BOND.us;
    var cur = curveYieldExact(curve, state.scTenor); if (cur == null) cur = curveYield(curve, state.scTenor);
    var def = cur != null ? cur : (state.scMkt === 'KR' ? 3.5 : 4.5);
    $('scYtm').value = def.toFixed(3); $('scCpn').value = def.toFixed(3); $('scYrs').value = state.scTenor;
  }
  function renderScenario() {
    var ytm = (parseFloat($('scYtm').value) || 0) / 100, cpn = (parseFloat($('scCpn').value) || 0) / 100, yrs = parseFloat($('scYrs').value) || 1, freq = state.scFreq;
    var m = bondMetrics(FACE, cpn, ytm, yrs, freq);
    tiles($('scTiles'), [
      ['가격 (액면 100)', m.price.toFixed(2)],
      ['맥컬리 듀레이션', m.macaulay.toFixed(2) + '년'],
      ['수정 듀레이션', m.modified.toFixed(2)],
      ['볼록성', m.convexity.toFixed(1)],
      ['DV01', (m.dv01 * 100).toFixed(2) + 'bp상당']
    ]);
    var rows = rateScenarios(FACE, cpn, ytm, yrs, freq);
    var head = '<div class="row head" style="grid-template-columns:1fr 1fr 1fr 1fr 1fr"><span class="col-label">금리 충격</span><span class="col-label r">정확 가격</span><span class="col-label r">정확 변화율</span><span class="col-label r">듀레이션 근사</span><span class="col-label r">+볼록성</span></div>';
    var body = rows.map(function (r, i) {
      var last = i === rows.length - 1, col = r.exact_pct >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
      return '<div class="row" style="grid-template-columns:1fr 1fr 1fr 1fr 1fr;font-family:var(--font-mono);font-size:12.5px' + (last ? ';border-bottom:none' : '') + '"><span style="font-family:var(--font-sans);font-weight:600">' + (r.shock_bp >= 0 ? '+' : '') + r.shock_bp + 'bp</span><span class="r">' + r.exact_price.toFixed(2) + '</span><span class="r" style="color:' + col + '">' + fmtSigned(r.exact_pct) + '</span><span class="r" style="color:var(--ink-3)">' + fmtSigned(r.dur_pct) + '</span><span class="r">' + fmtSigned(r.durconv_pct) + '</span></div>';
    }).join('');
    $('scTable').innerHTML = head + body;
    $('pyChart').innerHTML = priceYieldChart(ytm, cpn, yrs, freq, m.price, m.modified);
    updateAddBondNote();
  }

  /* ── 포트폴리오 담기 (localStorage 공유) ── */
  function loadBasket() { try { return JSON.parse(localStorage.getItem('invportfolio') || '{}'); } catch (e) { return {}; } }
  function saveBasket(b) { localStorage.setItem('invportfolio', JSON.stringify(b)); }
  function bondEtfProxy(market, years) {
    if (market === 'KR') return years <= 5 ? { name: 'KODEX 국고채3년', yahoo: '114260.KS', ticker: '114260.KS', type: '국내기타ETF', currency: 'KRW', class: '채권' } : { name: 'KOSEF 국고채10년', yahoo: '148070.KS', ticker: '148070.KS', type: '국내기타ETF', currency: 'KRW', class: '채권' };
    return years <= 7 ? { name: 'iShares 미국채 7-10년 (IEF)', yahoo: 'IEF', ticker: 'IEF', type: '해외ETF', currency: 'USD', class: '채권' } : { name: 'iShares 미국채 20년+ (TLT)', yahoo: 'TLT', ticker: 'TLT', type: '해외ETF', currency: 'USD', class: '채권' };
  }
  function updateAddBondNote() {
    var yrs = parseFloat($('scYrs').value) || 1, proxy = bondEtfProxy(state.scMkt, yrs), basket = loadBasket();
    $('addBondNote').innerHTML = (proxy.yahoo in basket) ? '🧺 담겨 있어요: ' + esc(proxy.name) : '만기에 맞춰 <b>' + esc(proxy.name) + '</b>로 편입(개별 국채는 일별 시세가 없어 ETF 프록시로 통계 계산).';
  }

  function renderNews() {
    var news = BOND.news || [];
    if (!news.length) { $('bondNews').innerHTML = '<div style="font-size:13px;color:var(--ink-3)">관련 뉴스를 찾지 못했습니다.</div>'; return; }
    $('bondNews').innerHTML = news.map(function (it) {
      var tags = (it.tags || []).map(function (t) { return '<span style="font-family:var(--font-mono);font-size:10px;color:#fff;background:var(--dv-navy);border-radius:2px;padding:1px 6px;margin-left:5px">' + esc(t) + '</span>'; }).join('');
      var meta = [it.source, it.date].filter(Boolean).join(' · ');
      return '<a href="' + esc(it.link || '#') + '" target="_blank" rel="noopener" style="display:block;font-size:13.5px;margin-top:12px;line-height:1.5">' + esc(it.title) + tags + '</a><div style="font-size:11px;color:var(--ink-3);margin-top:3px">' + esc(meta) + '</div>';
    }).join('') + '<div style="font-size:11px;color:var(--ink-3);margin-top:14px">태그는 PEST(정책·경제·사회·기술) 관점의 키워드 분류입니다.</div>';
  }

  /* ══════════ 인터랙션 ══════════ */
  function wireSeg(id, onChange) { var seg = $(id); if (!seg) return; seg.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; seg.querySelectorAll('button').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); onChange(b.getAttribute('data-val')); }); }
  // 다중 선택 세그먼트 — 버튼별 on/off 토글, 최소 1개는 켜진 상태 유지
  function wireMultiSeg(id, onChange) {
    var seg = $(id); if (!seg) return;
    seg.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.classList.contains('on') && seg.querySelectorAll('button.on').length <= 1) return;
      b.classList.toggle('on');
      state.mkts[b.getAttribute('data-val')] = b.classList.contains('on');
      onChange();
    });
  }

  function init() {
    // 국가 다중 선택 — 곡선·타일·추이가 함께 반응
    wireMultiSeg('mktSeg', function () { renderRates(); renderHistTenorSeg(); loadHistory(); });
    $('histTenor').addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; state.histTenor = +b.getAttribute('data-val'); renderHistTenorSeg(); loadHistory(); });
    // 시나리오
    wireSeg('scMkt', function (v) { state.scMkt = v; renderScTenorSeg(); prefillYtm(); renderScenario(); });
    wireSeg('scFreq', function (v) { state.scFreq = +v; renderScenario(); });
    $('scTenor').addEventListener('change', function () { state.scTenor = +this.value; prefillYtm(); renderScenario(); });
    ['scYtm', 'scCpn', 'scYrs'].forEach(function (id) { $(id).addEventListener('input', renderScenario); });
    $('addBondBtn').addEventListener('click', function () {
      var yrs = parseFloat($('scYrs').value) || 1, proxy = bondEtfProxy(state.scMkt, yrs), basket = loadBasket();
      basket[proxy.yahoo] = proxy; saveBasket(basket); updateAddBondNote();
      $('addBondNote').innerHTML = '🧺 담았습니다: <b>' + esc(proxy.name) + '</b> — 포트폴리오 페이지에서 비중을 정하세요.';
    });

    // 데이터 로드
    $('status').classList.add('on');
    fetch('api/bond').then(function (r) { return r.json(); }).then(function (d) {
      $('status').classList.remove('on');
      if (d.error) { $('curveChart').innerHTML = '<div style="color:var(--danger);font-size:13px">금리 데이터를 불러오지 못했습니다: ' + esc(d.error) + '</div>'; return; }
      BOND = d;
      renderRates();
      renderHistTenorSeg(); loadHistory();
      renderScTenorSeg(); prefillYtm(); renderScenario();
      renderNews();
    }).catch(function (e) { $('status').classList.remove('on'); $('curveChart').innerHTML = '<div style="color:var(--danger);font-size:13px">서버 연결 실패: ' + esc(e.message) + '</div>'; });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
