/* ══════════════════════════════════════════════════════════════════════
   공용 헬퍼 — 모든 화면이 같은 한 벌을 쓴다 (이슈 #83 · R5 조서 발견 ㉲)

   왜 만들었나: 같은 함수가 파일마다 복사돼 있었다 — esc 6벌 · $ 5벌 · el 3벌 ·
   wireSeg 3벌 · tiles 3벌. 복사본은 조용히 갈라진다. 실제로 갈라져 있었다 — esc는 한 벌만
   홑따옴표를 막았고, wireSeg는 한 벌만 aria-pressed를 관리했다(나머지 화면에서는
   스크린리더가 선택 상태를 못 읽었다). 벌마다 무엇이 달랐는지는 이슈 #83에 적어 뒀다.

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
  /* items = [[라벨, 값HTML, 부제?, 옵션?], …]. 라벨·부제는 이스케이프하고 값은 그대로 둔다
     (값에는 부호 색 span 같은 마크업이 들어온다). 띠 옵션은 { lead: true } 하나 —
     테두리 박스 안에 놓이는 결론 띠(150/24). 칸 옵션(넷째 자리, 전부 선택):
       vColor·kickColor  글자색. **숫자의 부호이거나 축 자체인 자리 전용**이다 — 판정을
                         색으로 말하지 않는다(R4). 값이 정해져 있지 않아 여기만 인라인이다.
       subHtml           부제에 마크업을 넣는다(esc 안 함). 외부 문자열은 t[2]로 줄 것.
       long              값이 길어 한 단 작게(.v.long) · accent 결론 칸의 지면(.tile.accent)

     폭·글자·패딩·세로선은 전부 `meridian.css`의 `.tile`이 갖는다 — 그 값을 부르는 쪽에서
     적는 것이 열세 자리로 갈라졌던 경로였다(#82). */
  function tilesHtml(items, opts) {
    var lead = opts && opts.lead ? ' lead' : '';
    function ink(c) { return c ? ' style="color:' + c + '"' : ''; }
    return items.map(function (t) {
      var o = t[3] || {};
      return '<div class="tile' + lead + (o.accent ? ' accent' : '') + '">'
        + '<div class="kick"' + ink(o.kickColor) + '>' + esc(t[0]) + '</div>'
        + '<div class="v' + (o.long ? ' long' : '') + '"' + ink(o.vColor) + '>' + t[1] + '</div>'
        + (o.subHtml ? '<div class="s">' + o.subHtml + '</div>' : t[2] ? '<div class="s">' + esc(t[2]) + '</div>' : '')
        + '</div>';
    }).join('');
  }
  /* 띠를 문자열로 조립해 다른 마크업과 함께 넣는 자리가 있어 둘로 나눠 둔다. */
  function tiles(container, items, opts) { container.innerHTML = tilesHtml(items, opts); }

  /* ── 접힘 상자 ── */
  /* 공식·인용·출처·근거 접기 — 화면 위에 늘 펼쳐 두면 결론을 읽으러 온 사람에게는 소음이고,
     지워 버리면 검증하러 온 사람이 근거를 잃는다. 접어서 둘 다 만족시킨다.
     네이티브 <details>라 innerHTML로 꽂아도 배선이 필요 없다 — 렌더 경로가 늘어도
     "여기서 wireCollapse를 잊었다"가 생기지 않는다. 키보드·스크린리더도 공짜로 따라온다.
     html은 우리가 조립한 문자열이라 esc()를 걸지 않는다(라벨만 이스케이프한다).

     stock.js에 있던 것을 여기로 옮겼다 — 접는 자리가 늘어나는 중이라 두 벌째가 생기기
     직전이었고, 그것이 R5가 적은 '공용 헬퍼 9종이 최대 4벌'로 가는 첫 걸음이다. */
  function fold(label, html) {
    return '<details class="srcfold"><summary>' + esc(label) + '</summary>' +
      '<div class="srcfold-body">' + html + '</div></details>';
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

  /* ── 공용 셸 동작 — 지금 있는 화면을 메뉴에서 보이게 (#73) ── */
  /* 좁은 화면에서 메뉴는 가로로 스크롤된다. 그런데 '지금 여기' 표시가 붙은 항목이
     목록 뒤쪽이면 화면 밖에 있다 — 실측(390px, 사용설명서): 항목은 356~434인데
     보이는 구간은 0~362였다. 표시를 해 두고 안 보이면 표시가 없는 것과 같다.

     scrollIntoView 대신 scrollLeft를 직접 계산한다 — 전자는 가로 스크롤을 맞추면서
     페이지를 세로로도 끌어당겨 헤더가 잘린다. */
  function revealCurrentNav() {
    var nav = document.querySelector('.site-nav');
    if (!nav) return;
    var cur = nav.querySelector('[aria-current="page"]');
    if (!cur || nav.scrollWidth <= nav.clientWidth) return;
    var left = cur.offsetLeft, right = left + cur.offsetWidth;
    if (right > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = right - nav.clientWidth + 12;
    else if (left < nav.scrollLeft) nav.scrollLeft = Math.max(0, left - 12);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', revealCurrentNav);
  } else {
    revealCurrentNav();
  }

  window.DV = {
    $: $, esc: esc,
    ATTR: ATTR, kebab: kebab, styleStr: styleStr, el: el,
    niceStep: niceStep, wireSeg: wireSeg, tiles: tiles, tilesHtml: tilesHtml, fold: fold,
    BASKET_KEY: BASKET_KEY, loadBasket: loadBasket, saveBasket: saveBasket,
    revealCurrentNav: revealCurrentNav
  };
})();
