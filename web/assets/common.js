/* ══════════════════════════════════════════════════════════════════════
   공용 헬퍼 — 모든 화면이 같은 한 벌을 쓴다 (이슈 #83 · R5 조서 발견 ㉲)

   왜 만들었나: 같은 함수가 파일마다 복사돼 있었다 — esc 6벌 · $ 5벌 · el 3벌 ·
   wireSeg 3벌 · tiles 3벌. 복사본은 조용히 갈라진다. 실제로 갈라져 있었다:
   - esc: test.js만 홑따옴표를 이스케이프했다 (R5에서 넷은 맞췄지만 home·admin은 남아 있었다)
   - wireSeg: stock.js만 aria-pressed를 관리했다 (스크린리더가 선택 상태를 못 읽었다)
   - el: var() 폴백 처리의 이유를 적은 주석이 한 벌에만 있었다
   - tiles: admin만 세 번째 줄(.s)을 지원했다

   빌드 도구가 없으므로(순수 정적 서빙) window에 이름 하나만 얹는다.
   stock-price-chart.js가 쓴 방식(window.makePriceChart + 인자 주입)과 같은 관례다.

   쓰는 쪽:
     var DV = window.DV;
     var esc = DV.esc, el = DV.el, $ = DV.$;
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── DOM ── */
  function $(id) { return document.getElementById(id); }

  /* HTML 이스케이프 — 홑따옴표(')까지 포함한다.
     지금은 속성을 " 로만 감싸서 없어도 악용되지 않지만, 같은 이름의 함수가 자리마다
     다른 안전성을 뜻하는 상태를 남기지 않는다. 인라인 핸들러(onclick=)는 저장소에
     한 곳도 없으므로 &#39;이 JS 문맥으로 새어 들어갈 자리가 없다. */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  /* ── SVG/HTML 문자열 빌더 ── */
  var ATTR = {
    strokeWidth: 'stroke-width', strokeDasharray: 'stroke-dasharray',
    strokeLinecap: 'stroke-linecap', strokeLinejoin: 'stroke-linejoin',
    strokeOpacity: 'stroke-opacity', fillOpacity: 'fill-opacity',
    textAnchor: 'text-anchor', fontFamily: 'font-family', fontSize: 'font-size',
    fontWeight: 'font-weight', className: 'class'
  };
  function kebab(s) { return s.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }
  function styleStr(o) { var s = ''; for (var k in o) s += kebab(k) + ':' + o[k] + ';'; return s; }
  function el(tag, attrs) {
    var kids = Array.prototype.slice.call(arguments, 2);
    attrs = attrs || {};
    var style = {};
    if (attrs.style) for (var sk in attrs.style) style[sk] = attrs.style[sk];
    var s = '<' + tag;
    for (var k in attrs) {
      if (k === 'style' || attrs[k] == null) continue;
      var val = attrs[k];
      // var()는 프레젠테이션 속성에서 Firefox/Safari가 해석하지 못한다 → 인라인 style로.
      if (typeof val === 'string' && val.indexOf('var(') >= 0) { style[k] = val; continue; }
      s += ' ' + (ATTR[k] || k) + '="' + String(val).replace(/"/g, '&quot;') + '"';
    }
    var st = styleStr(style);
    if (st) s += ' style="' + st + '"';
    s += '>';
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (c == null || c === false) continue;
      s += Array.isArray(c) ? c.join('') : c;
    }
    return s + '</' + tag + '>';
  }

  /* ── 차트 축 ── */
  /* 눈금 간격을 1·2·5·10 계열의 읽기 좋은 값으로 — 화면이 다르다고 눈금이 달라지면 안 된다. */
  function niceStep(range, target) {
    var raw = Math.max(range, 1e-9) / target;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / mag;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
  }

  /* ── 컨트롤 배선 ── */
  /* 세그먼트(하나만 선택). aria-pressed로 선택 상태를 스크린리더에 알린다 —
     시각적으로는 .on 클래스가 말하지만 그것은 소리로 전달되지 않는다. */
  function wireSeg(id, onChange) {
    var seg = $(id);
    if (!seg) return;
    seg.querySelectorAll('button').forEach(function (x) {
      x.setAttribute('aria-pressed', x.classList.contains('on') ? 'true' : 'false');
    });
    seg.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      seg.querySelectorAll('button').forEach(function (x) {
        x.classList.remove('on'); x.setAttribute('aria-pressed', 'false');
      });
      b.classList.add('on'); b.setAttribute('aria-pressed', 'true');
      onChange(b.getAttribute('data-val'));
    });
  }

  /* ── 지표 타일 스트립 ── */
  /* items = [[라벨, 값HTML, 부제?], …]. 라벨·부제는 이스케이프하고 값은 그대로 둔다
     (값에는 부호 색 span 같은 마크업이 들어온다).

     주의: 이 함수는 `.tile`·`.tile .v`(·`.s`) 클래스가 있다고 전제한다. 지금 그 값은
     페이지마다 다르다 — admin 150px/24px · bond 130px/22px · portfolio 120px/22px.
     눈금을 하나로 모으는 일은 #74(타이포·간격 토큰)와 #82(stock.js의 인라인 타일 6벌)의
     것이라 여기서는 손대지 않는다. */
  function tiles(container, items) {
    container.innerHTML = items.map(function (t, i) {
      return '<div class="tile" style="padding:' + (i === 0 ? '0 16px 0 0' : '0 16px')
        + (i ? ';border-left:1px solid var(--line)' : '') + '">'
        + '<div class="kick">' + esc(t[0]) + '</div>'
        + '<div class="v">' + t[1] + '</div>'
        + (t[2] ? '<div class="s">' + esc(t[2]) + '</div>' : '')
        + '</div>';
    }).join('');
  }

  /* ── 포트폴리오 바스켓 (localStorage) ── */
  /* 'invportfolio'는 주식·ETF·채권·포트폴리오 네 화면이 공유하는 계약이다.
     레코드 모양: { name, yahoo, ticker, type, currency, class }
     읽고 쓰는 자리를 한 곳으로 모아, 키나 모양을 바꿀 때 한 군데만 보면 되게 한다. */
  var BASKET_KEY = 'invportfolio';
  function loadBasket() {
    try { return JSON.parse(localStorage.getItem(BASKET_KEY) || '{}'); } catch (e) { return {}; }
  }
  function saveBasket(b) { localStorage.setItem(BASKET_KEY, JSON.stringify(b)); }

  window.DV = {
    $: $, esc: esc,
    ATTR: ATTR, kebab: kebab, styleStr: styleStr, el: el,
    niceStep: niceStep, wireSeg: wireSeg, tiles: tiles,
    BASKET_KEY: BASKET_KEY, loadBasket: loadBasket, saveBasket: saveBasket
  };
})();
