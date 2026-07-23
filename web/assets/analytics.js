/* 방문자 분석 로더 — GA4(gtag)·Microsoft Clarity 스니펫 주입.
   ID는 서버(/api/analytics-config, 환경변수)에서 받아와 코드에 하드코딩하지 않는다.
   미설정이면 아무 것도 하지 않는다(로컬 개발 기본 = 추적 없음).
   본인 제외: 주소 뒤에 ?notrack=1 로 한 번 접속하면 이 브라우저는 계속 제외(?track=1 로 해제). */
(function () {
  'use strict';
  try {
    var qs = new URLSearchParams(location.search);
    if (qs.get('notrack') === '1') { localStorage.setItem('vd_notrack', '1'); return; }
    if (qs.get('track') === '1') localStorage.removeItem('vd_notrack');
    if (localStorage.getItem('vd_notrack') === '1') return;
  } catch (e) { /* localStorage 불가 환경 — 추적만 진행 */ }

  fetch('api/analytics-config').then(function (r) { return r.json(); }).then(function (c) {
    if (c && c.ga) {
      var s = document.createElement('script');
      s.async = true; s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(c.ga);
      document.head.appendChild(s);
      window.dataLayer = window.dataLayer || [];
      window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
      window.gtag('js', new Date());
      window.gtag('config', c.ga);
    }
    if (c && c.clarity) {
      (function (w, d, a, r, i) {
        w[a] = w[a] || function () { (w[a].q = w[a].q || []).push(arguments); };
        var t = d.createElement(r); t.async = 1; t.src = 'https://www.clarity.ms/tag/' + encodeURIComponent(i);
        var y = d.getElementsByTagName(r)[0]; y.parentNode.insertBefore(t, y);
      })(window, document, 'clarity', 'script', c.clarity);
    }
  }).catch(function () { /* 분석은 편의 기능 — 실패해도 페이지에 영향 없음 */ });
})();
