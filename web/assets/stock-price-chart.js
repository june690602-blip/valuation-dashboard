/* 주가차트 (Canvas · 리서치 차트형 시간축 탐색) — stock.js에서 떼어냈다(이슈 #79 ㉮의 1단계).
   원래부터 전역 페이로드·state를 읽지 않고 인자로 받던 유일한 덩어리라 첫 분리 대상이 됐다.
   밖의 것은 다섯 개만 쓴다(esc·fmtPrice·fmtSigned·$·CUR). 복사본을 만들면 같은 이름이
   파일마다 다른 뜻이 되므로(R5 조서 2번 바구니) 복사하지 않고 fmt 인자로 주입받는다.
   CUR은 통화 코드라 값으로 넘긴다 — 차트는 렌더마다 새로 만들어지고 CUR은 그 전에 정해진다. */
(function () {
  /* ── 주가차트 (Canvas · 리서치 차트형 시간축 탐색) ────────────────
     선 차트의 직관성은 유지하되, 시간축만 확대·이동하고 보이는 구간에 맞춰
     가격축을 자동 조정한다. 교차선의 상세값은 상단 시세 스트립과 축 태그에 표시. */
  var CVAR_CACHE = {};
  function cvar(name) { if (CVAR_CACHE[name] == null) { CVAR_CACHE[name] = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim() || '#000'; } return CVAR_CACHE[name]; }
  var CH_FONT_SANS = '"IBM Plex Sans KR", system-ui, sans-serif';
  var CH_FONT_MONO = '"Noto Sans KR", system-ui, monospace';

  /* `payload`는 **주식 응답(/api/analyze)만** 받는다 — ETF 응답에서 `price`는
     시계열 객체가 아니라 현재가 숫자다. 호출부가 `Dstock`을 넘겨 그 구분을 지킨다(이슈 #80). */
  window.makePriceChart = function (container, payload, state, fmt) {
    var esc = fmt.esc, fmtPrice = fmt.fmtPrice, fmtSigned = fmt.fmtSigned, $ = fmt.$, CUR = fmt.CUR;
    var d = payload.price;
    var rel = state.priceMode === 'rel';
    var fullClose = Array.isArray(d.close) ? d.close : [];
    var N = fullClose.length;
    function emptyChart(message) {
      container.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:24px 0;border-top:1px solid var(--line)">' + esc(message) + '</div>';
      return { destroy: function () {}, reset: function () {} };
    }
    var hasFiniteClose = fullClose.some(function (v) { return v != null && isFinite(v); });
    if (!N || !hasFiniteClose) return emptyChart('표시할 유효 주가 데이터가 없습니다.');
    function parsedDate(s) {
      var m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
    }
    function calendarCutoff(last, months, years) {
      var first = new Date(last.getFullYear() - (years || 0), last.getMonth() - (months || 0), 1);
      var lastDay = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
      return new Date(first.getFullYear(), first.getMonth(), Math.min(last.getDate(), lastDay));
    }
    function periodStart() {
      if (!N) return 0;
      var datesAll = d.dates || [], last = parsedDate(datesAll[N - 1]), cutoff = null;
      if (last) {
        if (state.pricePeriod === 'YTD') cutoff = new Date(last.getFullYear(), 0, 1);
        else if (state.pricePeriod === '1M') cutoff = calendarCutoff(last, 1, 0);
        else if (state.pricePeriod === '3M') cutoff = calendarCutoff(last, 3, 0);
        else if (state.pricePeriod === '6M') cutoff = calendarCutoff(last, 6, 0);
        else if (state.pricePeriod === '1Y') cutoff = calendarCutoff(last, 0, 1);
        else if (state.pricePeriod === '3Y') cutoff = calendarCutoff(last, 0, 3);
        else if (state.pricePeriod === '5Y') cutoff = calendarCutoff(last, 0, 5);
        if (cutoff) {
          for (var di = 0; di < N; di++) { var dt = parsedDate(datesAll[di]); if (dt && dt >= cutoff) return di; }
        }
      }
      var sessions = { '1M': 21, '3M': 63, '6M': 126, 'YTD': 252, '1Y': 252, '3Y': 756, '5Y': 1260 }[state.pricePeriod] || 252;
      return Math.max(0, N - sessions);
    }
    var offset = periodStart();
    function selected(a, fallback) {
      a = Array.isArray(a) ? a : null;
      var out = [];
      for (var ai = offset; ai < N; ai++) out.push(a && a[ai] != null ? a[ai] : (fallback ? fallback[ai] : null));
      return out;
    }
    var close = selected(fullClose), dates = selected(d.dates || []), vol = selected(d.vol || []);
    /* 결측 OHLC는 종가로 추정하지 않는다. 데이터가 없으면 상태줄에 정직하게 —로 표시한다. */
    var open = selected(d.open || []), high = selected(d.high || []), low = selected(d.low || []);
    var ma20 = selected(d.ma20 || []), ma60 = selected(d.ma60 || []), ma120 = selected(d.ma120 || []), bench = selected(d.bench || []);
    var n = close.length;
    if (!n) return emptyChart('표시할 주가 데이터가 없습니다.');
    var latestIndex = n - 1; while (latestIndex > 0 && close[latestIndex] == null) latestIndex--;

    /* 비교 모드는 선택 기간의 첫 공통 거래일을 100으로 맞춘다. */
    var stockY = null, benchY = null, compareBase = 0;
    if (rel) {
      while (compareBase < n && (close[compareBase] == null || bench[compareBase] == null)) compareBase++;
      if (compareBase >= n) {
        $('priceStatusName').textContent = (payload.meta.name || payload.meta.ticker || '종목') + ' · 상대성과';
        $('priceStatusMeta').textContent = '벤치마크와 공통으로 유효한 거래일이 없습니다.';
        $('priceStatusPrice').textContent = '—'; $('priceStatusChange').textContent = '비교 불가';
        $('priceStatusChange').style.color = 'var(--ink-3)'; $('priceStatusMetrics').innerHTML = '';
        return emptyChart('벤치마크 데이터가 부족해 상대성과를 계산할 수 없습니다.');
      }
      var c0 = close[compareBase], b0 = bench[compareBase];
      stockY = close.map(function (v, i) { return i < compareBase || v == null ? null : v / c0 * 100; });
      benchY = bench.map(function (v, i) { return i < compareBase || v == null ? null : v / b0 * 100; });
    }

    /* 레이아웃(CSS px) */
    var cssW = Math.max(260, Math.round(container.clientWidth || 700));
    var padL = 8, padR = cssW < 560 ? 58 : 72, plotT = 14;
    var cssH, plotH, volTop, volH;
    if (rel) { cssH = Math.max(280, Math.round(cssW * 0.42)); plotH = cssH - plotT - 34; }
    else { cssH = Math.max(330, Math.round(cssW * 0.50)); volH = Math.round(cssH * 0.17); var volGap = 18; plotH = cssH - plotT - 34 - volH - volGap; volTop = plotT + plotH + volGap; }
    var xw = cssW - padL - padR;

    /* x축만 탐색하고, y축은 현재 보이는 데이터에 자동 맞춤. */
    var viewStart = 0, viewEnd = Math.max(0, n - 1), hover = null, ymin = 0, ymax = 1, vmax = 1;
    var minSpan = Math.min(Math.max(1, n - 1), 14);
    function clampView() {
      var maxSpan = Math.max(1, n - 1), span = viewEnd - viewStart;
      if (span < minSpan) { var mid = (viewStart + viewEnd) / 2; viewStart = mid - minSpan / 2; viewEnd = mid + minSpan / 2; }
      if (span > maxSpan) { viewStart = 0; viewEnd = n - 1; }
      if (viewStart < 0) { viewEnd -= viewStart; viewStart = 0; }
      if (viewEnd > n - 1) { viewStart -= viewEnd - (n - 1); viewEnd = n - 1; }
      viewStart = Math.max(0, viewStart); viewEnd = Math.min(n - 1, viewEnd);
    }
    function visibleBounds() { return [Math.max(0, Math.floor(viewStart)), Math.min(n - 1, Math.ceil(viewEnd))]; }
    function scaleVisible() {
      var b = visibleBounds(), vals = [], series = rel ? [stockY, benchY] : [close];
      if (!rel && state.ma.m20) series.push(ma20);
      if (!rel && state.ma.m60) series.push(ma60);
      if (!rel && state.ma.m120) series.push(ma120);
      for (var si = 0; si < series.length; si++) for (var vi = b[0]; vi <= b[1]; vi++) if (series[si][vi] != null && isFinite(series[si][vi])) vals.push(+series[si][vi]);
      if (!vals.length) vals = [0, 1];
      ymin = Math.min.apply(null, vals); ymax = Math.max.apply(null, vals);
      var padv = (ymax - ymin) * 0.08 || Math.max(Math.abs(ymax) * 0.02, 1); ymin -= padv; ymax += padv;
      vmax = 1;
      if (!rel) for (var vv = b[0]; vv <= b[1]; vv++) if (vol[vv] != null && isFinite(vol[vv])) vmax = Math.max(vmax, +vol[vv]);
    }
    function xDoc(i) { var span = Math.max(1, viewEnd - viewStart); return padL + (i - viewStart) / span * xw; }
    function yDoc(v) { return plotT + (1 - (v - ymin) / (ymax - ymin)) * plotH; }
    function valAtY(sy) { return ymin + (1 - (sy - plotT) / plotH) * (ymax - ymin); }
    function idxAtX(sx) { var i = Math.round(viewStart + (sx - padL) / xw * (viewEnd - viewStart)); return Math.max(0, Math.min(n - 1, i)); }
    function zoomAt(sx, factor) {
      var span = Math.max(1, viewEnd - viewStart), next = Math.max(minSpan, Math.min(n - 1, span * factor));
      var ratio = Math.max(0, Math.min(1, (sx - padL) / xw)), anchor = viewStart + ratio * span;
      viewStart = anchor - ratio * next; viewEnd = viewStart + next; clampView();
    }
    function panPixels(dx) { var shift = -dx / xw * (viewEnd - viewStart); viewStart += shift; viewEnd += shift; clampView(); }

    /* 캔버스(HiDPI) */
    var dpr = window.devicePixelRatio || 1;
    var cv = document.createElement('canvas');
    cv.style.width = cssW + 'px'; cv.style.height = cssH + 'px'; cv.style.display = 'block'; cv.style.touchAction = 'pan-y'; cv.style.cursor = 'crosshair';
    cv.tabIndex = 0; cv.setAttribute('role', 'img'); cv.setAttribute('aria-describedby', 'priceCaption');
    cv.setAttribute('aria-label', payload.meta.name + ' 일봉 주가 차트. 좌우 화살표로 이동하고 더하기와 빼기로 확대·축소하며 Home 또는 Escape로 전체 보기를 할 수 있습니다.');
    cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
    container.innerHTML = ''; container.appendChild(cv);
    var ctx = cv.getContext('2d');
    var COL = { ink: cvar('--ink'), ink2: cvar('--ink-2'), ink3: cvar('--ink-3'), line: cvar('--line'), lineStrong: cvar('--line-strong'), paper: cvar('--paper'), fill: cvar('--paper-3'), gold: cvar('--dv-gold'), slate: cvar('--dv-slate'), plum: cvar('--dv-plum'), clay: cvar('--dv-clay'), positive: cvar('--dv-positive'), negative: cvar('--dv-negative') };

    function fmtChartPrice(v) { if (v == null || !isFinite(v)) return '—'; return CUR === 'KRW' ? Math.round(v).toLocaleString('en-US') : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    function displayDate(v) { return String(v || '—').replace(/-/g, '.'); }
    function signedPctPoint(v) { return v == null || !isFinite(v) ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
    function metric(label, value) { return '<div class="chart-status-metric"><dt>' + esc(label) + '</dt><dd>' + esc(value) + '</dd></div>'; }
    function updateStatus(i) {
      i = Math.max(0, Math.min(n - 1, i == null ? latestIndex : i));
      var name = payload.meta.name || payload.meta.ticker || '종목', source = d.source || '', delay = d.delay_note || '';
      $('priceStatusName').textContent = name + (rel ? ' · 상대성과' : '');
      var metaText = ['일봉', payload.meta.currency || CUR, '기준일 ' + displayDate(dates[i]), source, delay].filter(Boolean).join(' · ');
      $('priceStatusMeta').textContent = metaText; $('priceStatusMeta').title = metaText;
      if (rel) {
        var sv = stockY[i] == null ? null : stockY[i] - 100, bv = benchY[i] == null ? null : benchY[i] - 100;
        var excess = sv == null || bv == null ? null : sv - bv, benchName = payload.meta.benchmark_name || payload.meta.benchmark || '벤치마크';
        $('priceStatusPrice').textContent = signedPctPoint(sv);
        $('priceStatusChange').textContent = excess == null ? '초과수익률 —' : '초과 ' + (excess >= 0 ? '+' : '') + excess.toFixed(2) + '%p';
        $('priceStatusChange').style.color = excess == null ? 'var(--ink-3)' : excess >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
        $('priceStatusMetrics').innerHTML = metric(name, signedPctPoint(sv)) + metric(benchName, signedPctPoint(bv)) + metric('초과수익률', excess == null ? '—' : (excess >= 0 ? '+' : '') + excess.toFixed(2) + '%p');
      } else {
        var gi = offset + i, prev = i === latestIndex && d.prev_close != null ? d.prev_close : (gi > 0 ? fullClose[gi - 1] : null);
        var delta = i === latestIndex && d.change != null ? d.change : (prev == null ? null : close[i] - prev);
        var pct = i === latestIndex && d.change_pct != null ? d.change_pct : (prev ? delta / prev : null);
        $('priceStatusPrice').textContent = fmtPrice(close[i]);
        $('priceStatusChange').textContent = delta == null ? '전일 대비 —' : (delta >= 0 ? '+' : '') + (CUR === 'KRW' ? fmtChartPrice(delta) + '원' : '$' + fmtChartPrice(delta)) + '  ' + fmtSigned(pct);
        $('priceStatusChange').style.color = delta == null ? 'var(--ink-3)' : delta >= 0 ? 'var(--dv-positive)' : 'var(--dv-negative)';
        $('priceStatusMetrics').innerHTML = metric('시가', fmtChartPrice(open[i])) + metric('고가', fmtChartPrice(high[i])) + metric('저가', fmtChartPrice(low[i])) + metric('종가', fmtChartPrice(close[i])) + metric('거래량', vol[i] == null ? '—' : Math.round(vol[i]).toLocaleString('en-US'));
      }
    }

    function strokeArr(arr, color, w) {
      var b = visibleBounds(); ctx.beginPath(); var started = false;
      for (var i = Math.max(0, b[0] - 1); i <= Math.min(n - 1, b[1] + 1); i++) { var v = arr[i]; if (v == null) { started = false; continue; } var sx = xDoc(i), sy = yDoc(v); if (!started) { ctx.moveTo(sx, sy); started = true; } else ctx.lineTo(sx, sy); }
      ctx.strokeStyle = color; ctx.lineWidth = w; ctx.lineJoin = 'round'; ctx.stroke();
    }
    /* 봉(캔들) 차트 — 시가 대비 종가로 양봉(초록)·음봉(빨강). 색은 앱 전반과 통일(초록=상승). */
    function drawCandles() {
      var b = visibleBounds(), visN = Math.max(1, viewEnd - viewStart);
      var step = visN > 520 ? 3 : visN > 260 ? 2 : 1, bw = Math.max(1, xw / visN * 0.62 * step);
      for (var i = Math.max(0, b[0]); i <= Math.min(n - 1, b[1]); i += step) {
        var o = open[i], hh = high[i], ll = low[i], c = close[i];
        if (c == null) continue;
        var up = (o == null) ? true : c >= o, col = up ? COL.positive : COL.negative, x = xDoc(i);
        if (hh != null && ll != null) { ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x, yDoc(hh)); ctx.lineTo(x, yDoc(ll)); ctx.stroke(); }
        ctx.fillStyle = col;
        if (o != null) { var yO = yDoc(o), yC = yDoc(c), top = Math.min(yO, yC); ctx.fillRect(x - bw / 2, top, bw, Math.max(1, Math.abs(yC - yO))); }
        else ctx.fillRect(x - bw / 2, yDoc(c) - 0.75, bw, 1.5);
      }
    }
    function axisTag(text, x, y, align, bg, fg) {
      ctx.font = '10.5px ' + CH_FONT_MONO; var tw = Math.ceil(ctx.measureText(text).width) + 12, th = 19;
      var bx = align === 'right' ? x : x - tw / 2, by = y - th / 2;
      if (align !== 'right') bx = Math.max(padL, Math.min(padL + xw - tw, bx));
      ctx.fillStyle = bg; ctx.fillRect(Math.round(bx), Math.round(by), tw, th);
      ctx.fillStyle = fg; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, bx + tw / 2, by + th / 2 + 0.5); ctx.textBaseline = 'alphabetic';
    }
    function draw() {
      scaleVisible();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, cssW, cssH);
      /* 가로 그리드 + 자동 가격축 */
      ctx.strokeStyle = COL.line; ctx.lineWidth = 1; ctx.font = '10.5px ' + CH_FONT_MONO; ctx.textAlign = 'left';
      for (var g = 0; g <= 4; g++) { var gy = plotT + g / 4 * plotH; ctx.beginPath(); ctx.moveTo(padL, gy + 0.5); ctx.lineTo(padL + xw, gy + 0.5); ctx.stroke(); ctx.fillStyle = COL.ink3; ctx.fillText(rel ? valAtY(gy).toFixed(1) : fmtChartPrice(valAtY(gy)), padL + xw + 6, gy + 3.5); }
      /* 시리즈 — 플롯 영역 클립 */
      ctx.save(); ctx.beginPath(); ctx.rect(padL, plotT, xw, (rel ? plotH : volTop + volH - plotT)); ctx.clip();
      if (!rel) {
        var vb = visibleBounds(), visibleN = Math.max(1, viewEnd - viewStart), step = visibleN > 520 ? 3 : visibleN > 260 ? 2 : 1, bw = Math.max(1, xw / visibleN * 0.62 * step);
        for (var i = vb[0]; i <= vb[1]; i += step) {
          if (vol[i] == null) continue;
          var h = vol[i] / vmax * volH, volumeColor = COL.slate;
          if (i > 0 && close[i] != null && close[i - 1] != null) volumeColor = close[i] < close[i - 1] ? COL.negative : COL.positive;
          ctx.globalAlpha = 0.34; ctx.fillStyle = volumeColor;
          ctx.fillRect(xDoc(i) - bw / 2, volTop + volH - h, bw, Math.max(0.5, h));
        }
        ctx.globalAlpha = 1;
        if (state.chartType === 'candle') {
          drawCandles();
          if (state.ma.m120) strokeArr(ma120, COL.plum, 1.3);
          if (state.ma.m60) strokeArr(ma60, COL.slate, 1.3);
          if (state.ma.m20) strokeArr(ma20, COL.gold, 1.3);
        } else {
          if (state.ma.m120) strokeArr(ma120, COL.plum, 1.3);
          if (state.ma.m60) strokeArr(ma60, COL.slate, 1.3);
          if (state.ma.m20) strokeArr(ma20, COL.gold, 1.3);
          strokeArr(close, COL.ink, 1.9);
        }
      } else { strokeArr(benchY, COL.clay, 1.4); strokeArr(stockY, COL.ink, 1.9); }
      /* 현재가 점선과 최신 데이터 포인트 */
      var latest = d.cur != null ? d.cur : close[latestIndex], lastVisible = latestIndex >= viewStart && latestIndex <= viewEnd;
      if (!rel && latest != null && latest >= ymin && latest <= ymax) {
        var lastY = yDoc(latest); ctx.strokeStyle = COL.ink2; ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(padL, lastY); ctx.lineTo(padL + xw, lastY); ctx.stroke(); ctx.setLineDash([]);
        if (lastVisible) { ctx.fillStyle = COL.ink; ctx.beginPath(); ctx.arc(xDoc(latestIndex), lastY, 3.4, 0, Math.PI * 2); ctx.fill(); }
      }
      if (hover != null && hover >= 0 && hover < n) {
        var yv = rel ? stockY[hover] : close[hover], hx = xDoc(hover), hy = yv == null ? null : yDoc(yv);
        ctx.strokeStyle = COL.ink3; ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(hx, plotT); ctx.lineTo(hx, rel ? plotT + plotH : volTop + volH); if (hy != null) { ctx.moveTo(padL, hy); ctx.lineTo(padL + xw, hy); } ctx.stroke(); ctx.setLineDash([]);
        if (hy != null) { ctx.fillStyle = COL.ink; ctx.beginPath(); ctx.arc(hx, hy, 3.2, 0, Math.PI * 2); ctx.fill(); }
      }
      ctx.restore();
      /* x축 날짜 라벨 */
      ctx.fillStyle = COL.ink3; ctx.font = '10px ' + CH_FONT_MONO; ctx.textAlign = 'center';
      var ly = (rel ? plotT + plotH : volTop + volH) + 15;
      for (var t = 0; t <= 4; t++) { var lx = padL + t / 4 * xw, labelDate = displayDate(dates[idxAtX(lx)]); ctx.fillText(viewEnd - viewStart > 300 ? labelDate.slice(2, 7) : labelDate.slice(5), lx, ly); }
      if (!rel) { ctx.fillStyle = COL.ink3; ctx.font = '10px ' + CH_FONT_SANS; ctx.textAlign = 'left'; ctx.fillText('거래량', padL, volTop - 4); }
      if (rel) { var benchName = payload.meta.benchmark_name || payload.meta.benchmark || '벤치마크'; ctx.textAlign = 'left'; ctx.font = '11px ' + CH_FONT_SANS; ctx.fillStyle = COL.ink; ctx.fillText(payload.meta.name, padL + 2, plotT + 12); ctx.fillStyle = COL.clay; ctx.fillText(benchName, padL + 2 + ctx.measureText(payload.meta.name).width + 10, plotT + 12); }
      /* 축 태그는 마크 위, 축 여백에 고정 */
      if (!rel && latest != null && latest >= ymin && latest <= ymax) axisTag(fmtChartPrice(latest), padL + xw + 3, yDoc(latest), 'right', COL.ink, COL.paper);
      if (hover != null && hover >= 0 && hover < n) {
        var hoverValue = rel ? stockY[hover] : close[hover], hoverX = xDoc(hover);
        if (hoverValue != null) axisTag(rel ? hoverValue.toFixed(2) : fmtChartPrice(hoverValue), padL + xw + 3, yDoc(hoverValue), 'right', COL.ink3, COL.paper);
        axisTag(displayDate(dates[hover]), hoverX, (rel ? plotT + plotH : volTop + volH) + 23, 'center', COL.ink3, COL.paper);
      }
    }

    /* 입력 응답은 즉시 그린다. 플링만 시간 기반으로 처리한다. */
    var raf = (window.requestAnimationFrame || function (f) { return setTimeout(f, 16); });
    var caf = (window.cancelAnimationFrame || clearTimeout);
    function dirty() { draw(); }

    /* 좌우 플링(관성) */
    var flingId = 0;
    function stopFling() { if (flingId) { caf(flingId); flingId = 0; } }
    function startFling(vx) {
      if (Math.abs(vx) < 0.6) return; stopFling();
      function stepF() { vx *= 0.92; var before = viewStart; panPixels(vx); if (viewStart === before) vx = 0; draw(); if (Math.abs(vx) > 0.25) flingId = raf(stepF); else flingId = 0; }
      flingId = raf(stepF);
    }
    function resetView() { stopFling(); viewStart = 0; viewEnd = n - 1; hover = null; updateStatus(latestIndex); draw(); }

    /* 이벤트 */
    function pos(e) { var r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    function onWheel(e) { e.preventDefault(); stopFling(); var p = pos(e); var factor = Math.exp(e.deltaY * (e.ctrlKey ? 0.009 : 0.0018)); factor = Math.max(0.58, Math.min(1.72, factor)); zoomAt(p[0], factor); dirty(); }
    var dragging = false, moved = false, lx = 0, vX = 0;
    function onDown(e) { cv.focus({ preventScroll: true }); cv.setPointerCapture && cv.setPointerCapture(e.pointerId); dragging = true; moved = false; lx = e.clientX; vX = 0; hover = null; updateStatus(latestIndex); stopFling(); }
    function onMove(e) {
      var p = pos(e);
      if (dragging) { var dx = e.clientX - lx; if (Math.abs(dx) > 1) moved = true; panPixels(dx); vX = dx; lx = e.clientX; dirty(); }
      else { if (p[0] < padL || p[0] > padL + xw || p[1] < plotT || p[1] > (rel ? plotT + plotH : volTop + volH)) { if (hover != null) { hover = null; updateStatus(latestIndex); dirty(); } } else { var i = idxAtX(p[0]); if (i !== hover) { hover = i; updateStatus(i); dirty(); } } }
    }
    function onUp() { if (dragging) { dragging = false; if (moved) startFling(vX); } }
    function onLeave() { if (!dragging && hover != null) { hover = null; updateStatus(latestIndex); dirty(); } }
    function onDbl(e) { e.preventDefault(); resetView(); }
    function onKey(e) {
      var handled = true, reset = false;
      if (e.key === 'ArrowLeft') panPixels(xw * 0.10);
      else if (e.key === 'ArrowRight') panPixels(-xw * 0.10);
      else if (e.key === '+' || e.key === '=') zoomAt(padL + xw / 2, 0.72);
      else if (e.key === '-' || e.key === '_') zoomAt(padL + xw / 2, 1.38);
      else if (e.key === 'Home' || e.key === 'Escape') { resetView(); reset = true; }
      else handled = false;
      if (handled) { e.preventDefault(); if (!reset) draw(); }
    }
    cv.addEventListener('wheel', onWheel, { passive: false });
    cv.addEventListener('pointerdown', onDown);
    cv.addEventListener('pointermove', onMove);
    cv.addEventListener('pointerup', onUp);
    cv.addEventListener('pointercancel', onUp);
    cv.addEventListener('pointerleave', onLeave);
    cv.addEventListener('dblclick', onDbl);
    cv.addEventListener('keydown', onKey);

    updateStatus(latestIndex); draw();
    return { reset: resetView, destroy: function () { stopFling(); cv.removeEventListener('wheel', onWheel); cv.removeEventListener('pointerdown', onDown); cv.removeEventListener('pointermove', onMove); cv.removeEventListener('pointerup', onUp); cv.removeEventListener('pointercancel', onUp); cv.removeEventListener('pointerleave', onLeave); cv.removeEventListener('dblclick', onDbl); cv.removeEventListener('keydown', onKey); } };
  };
})();
