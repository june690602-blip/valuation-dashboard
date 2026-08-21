/* stock.js 분할(이슈 #79)의 합격선을 재는 도구 — **화면이 한 글자도 안 바뀌었는가.**

   R5 조서가 이 분할을 여러 PR로 끊어 가라고 하면서 남긴 어려움이 *"동작 무변경을
   증명하기 어렵다"*였다. 이 파일이 그 증명 수단이다. 단계마다 새로 짜지 말고 이걸 쓴다.

   ## 어떻게 재나

   옛 판과 새 판을 각각 **격리된 VM 컨텍스트**에 올려 같은 입력을 먹이고 결과 문자열을
   바이트로 견준다. 브라우저를 띄우지 않으므로 CI에서도 돌지만, 그만큼 **DOM 시늉만
   낸다** — 레이아웃·CSS·이벤트는 못 본다. 잡는 것은 "만들어 내는 문자열이 달라졌나"다.

   `init()`이 돌지 않게 `document.readyState`를 'loading'으로 둔다(돌면 `load()`가
   네트워크를 탄다). 내부 함수는 IIFE 끝에 훅을 주입해 꺼낸다.

   ## 쓰는 법

       node scripts/check_js_refactor_parity.js <옛-stock.js> [모드...]

       git show <머지된-커밋>:web/assets/stock.js > /tmp/before.js
       node scripts/check_js_refactor_parity.js /tmp/before.js

   모드를 안 주면 셋 다 돈다: `format` · `header` · `basket`.
   종료코드 0이면 전부 동일. 불일치가 있으면 첫 차이 위치를 찍고 1로 끝난다.

   ## 한계 — 인용 전에 읽을 것

   - **고정 입력이 닿는 곳만 잰다.** 아래 CASES에 없는 분기는 통과해도 아무 말이 아니다.
     단계마다 그 단계가 건드리는 경로의 입력을 CASES에 **먼저 추가하고** 재라.
   - **옛 판도 새 모듈을 실은 채로 돈다**(`stock-format.js` 등). 옛 stock.js는 그것을
     무시하므로 결과가 오염되지 않지만, 새 모듈이 전역을 덮어쓰면 그때는 오염된다.
   - CI 관문이 아니다(`quality.yml`에 없다). 옛 판 파일을 인자로 받아야 해서 자동화가
     어렵다 — **분할 단계마다 손으로 돌리고 그 결과를 PR에 적는다.** */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const ASSETS = path.join(REPO, 'web', 'assets');
// stock.js보다 먼저 실려야 하는 것들 — stock.html의 <script> 순서와 같아야 한다.
const PRELOAD = ['common.js', 'finmath.js', 'stock-format.js'];

function fakeEl(id) {
  return {
    id, innerHTML: '', textContent: '', style: {}, className: '',
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    addEventListener() {}, querySelectorAll: () => [], querySelector: () => null,
    appendChild() {}, focus() {}, closest: () => null,
  };
}

/** 옛/새 stock.js를 격리 컨텍스트에 올리고 내부를 꺼내 온다. */
function build(stockPath) {
  const els = {};
  const mk = (id) => (els[id] = els[id] || fakeEl(id));
  const store = {};
  const timers = [];
  const sandbox = {
    console,
    document: {
      readyState: 'loading',          // init()을 재우다 — 돌면 네트워크를 탄다
      getElementById: mk, querySelector: () => null, querySelectorAll: () => [],
      createElement: () => mk('_tmp'), addEventListener() {},
      body: mk('_body'), documentElement: mk('_html'),
    },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    location: { search: '', href: 'http://x/', pathname: '/stock' },
    history: { replaceState() {} },
    // setTimeout을 잡아 둔다 — 되먹임의 '되돌리는 문구'와 지연까지 견주려고.
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() {},
    fetch: () => new Promise(() => {}),
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    navigator: { userAgent: 'node' },
    URLSearchParams,
    addEventListener() {},
    makePriceChart: () => {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  for (const f of PRELOAD) {
    vm.runInContext(fs.readFileSync(path.join(ASSETS, f), 'utf8'), sandbox, { filename: f });
  }

  let src = fs.readFileSync(stockPath, 'utf8');
  const at = src.lastIndexOf('})();');
  if (at < 0) throw new Error('IIFE 끝을 못 찾음: ' + stockPath);
  // 훅 주입. `typeof`로 갈라 **분할 전후 어느 판이든** 같은 훅으로 다룬다.
  const FMTS = ['won', 'fmtPrice', 'fmtMoney', 'fmtPct', 'fmtX', 'fmtSigned', 'na', 'compactWon'];
  src = src.slice(0, at) + '  window.__probe = {\n' +
    `    fns: { ${FMTS.map((n) => `${n}: ${n}`).join(', ')} },\n` +
    '    VERDICTS: VERDICTS, vIdx: vIdx, vPos: vPos, vTone: vTone,\n' +
    '    header: renderHeader, etfHeader: renderEtfHeader,\n' +
    '    stockBasket: addToBasket, etfBasket: addEtfToBasket,\n' +
    '    setCur: function (c) { if (typeof FMT !== "undefined") { FMT.setCurrency(c); } else { CUR = c; } },\n' +
    '    setD: function (d, kind) {\n' +
    '      if (typeof Dstock !== "undefined") {\n' +
    '        if (kind === "etf") { Detf = d; Dstock = null; } else { Dstock = d; Detf = null; }\n' +
    '      } else { D = d; } } };\n' + src.slice(at);
  vm.runInContext(src, sandbox, { filename: stockPath });
  if (!sandbox.__probe) throw new Error('훅 주입 실패: ' + stockPath);
  return { probe: sandbox.__probe, els, store, timers };
}

/* ── 고정 입력 ──────────────────────────────────────────────────────
   ⚠ 단계마다, 그 단계가 건드리는 경로의 입력을 **먼저 여기 추가하고** 재라. */
const NUMS = [null, 0, 1, -1, 0.5, -0.5, 1234, 1234.5678, 9999, 12345,
  1e6, 1.5e8, 9.9e11, 1e12, 3.14e12, -1e9, 0.0101, 0.25, 123.456, 1e-4];

const stockPayload = (verdict, gap, extra) => ({
  computed_at: '2026-08-20 09:00',
  meta: { ticker: '005930', name: '삼성전자', market: 'KR', benchmark: 'KOSPI',
    price: 231000, currency: 'KRW', asof: '2026-08-19', is_financial: false,
    yahoo_ticker: '005930.KS', fin_year: 2025 },
  verdict: Object.assign({
    verdict: verdict, gap: gap, fair_mid: 194466, fair_mid_consensus: 386686,
    dispersion: 0.433, n_eff: 2.43, fundamental_only: true,
    weights: { '업종 상대가치': 0.385, '수익가치(RIM)': 0.231, '정규화 이익': 0.385 },
    estimates: [
      { method: '업종 상대가치', low: 186682, mid: 262717, high: 263266 },
      { method: '수익가치(RIM)', low: 83957, mid: 87448, high: 104352 },
      { method: '정규화 이익', low: 96514, mid: 122892, high: 149030 },
    ],
    notes: [], leg_mae: { PBR: 0.44 }, norm_years: 8,
  }, extra || {}),
});

const etfPayload = (verdict, gap, extra) => Object.assign({
  symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', price: 512.4, currency: 'USD',
  type_label: '주식형', verdict: verdict, gap: gap, primary: 'dividend',
  axes: [{ key: 'dividend', value: '배당수익률이 5년 분포의 42번째 백분위입니다.' }],
  metrics: { bench_label: '미국 전체시장(VTI)', div_yield: 0.0101, basket_pe: 24.1 },
  asOf: { price: '2026-08-19' }, masked: [], trend: { w52_pos: 88 },
}, extra || {});

const clone = (o) => JSON.parse(JSON.stringify(o));

/* ── 모드별 스냅숏 ─────────────────────────────────────────────────── */
const MODES = {
  // 포맷터·판정 어휘 — 두 통화로 두들긴다. 헤더 파리티만으로는 통화 전환이 안 걸린다.
  format(env) {
    const p = env.probe, out = [];
    for (const cur of ['KRW', 'USD']) {
      p.setCur(cur);
      for (const n of Object.keys(p.fns)) {
        for (const v of NUMS) {
          let r;
          try { r = String(p.fns[n](v)); } catch (e) { r = 'ERR:' + e.message; }
          out.push(`${cur}|${n}(${JSON.stringify(v)})=${r}`);
        }
      }
      for (const d of [0, 1, 2, 3]) out.push(`${cur}|fmtPct(0.12345,${d})=${p.fns.fmtPct(0.12345, d)}`);
    }
    for (const v of ['저평가', '적정 수준', '고평가', '없는등급', null, undefined]) {
      out.push(`vIdx(${v})=${p.vIdx(v)} vPos=${p.vPos(v)} vTone=${p.vTone(v)}`);
    }
    out.push('VERDICTS=' + JSON.stringify(p.VERDICTS));
    return out;
  },

  // 헤더 카드 — 주식·ETF의 `hv-B` innerHTML을 통째로.
  header(env) {
    const cases = [
      ['주식 · 저평가', 'header', stockPayload('저평가', 0.42)],
      ['주식 · 적정 수준', 'header', stockPayload('적정 수준', 0.03)],
      ['주식 · 고평가', 'header', stockPayload('고평가', -0.158)],
      ['주식 · 판정충돌', 'header', stockPayload('고평가', -0.158,
        { conflict: { short: '아래 근거는 <b>반대</b>를 가리킵니다.' } })],
      ['주식 · 괴리율 결측', 'header', stockPayload('적정 수준', null)],
      ['ETF · 판정 있음', 'etfHeader', etfPayload('다소 비싼 구간', 0.12)],
      ['ETF · 보류(상대위치 있음)', 'etfHeader',
        etfPayload(null, null, { relative: { stance: '시장 대비 비싼 편', pos: 78 } })],
      ['ETF · 보류(상대위치 없음)', 'etfHeader', etfPayload(null, null, {})],
    ];
    return cases.map(([label, kind, payload]) => {
      env.probe.setD(clone(payload), kind === 'etfHeader' ? 'etf' : 'stock');
      env.probe[kind]();
      return `${label}\n${env.els['hv-B'].innerHTML}`;
    });
  },

  // 포트폴리오 담기 — 저장 레코드 + 버튼 되먹임(되돌리는 문구·지연 포함).
  basket(env) {
    const cases = [
      ['주식 · KR', 'stockBasket', 'basketBtn', 'stock',
        { meta: { name: '삼성전자', ticker: '005930', yahoo_ticker: '005930.KS', market: 'KR', currency: 'KRW' } }],
      ['주식 · US', 'stockBasket', 'basketBtn', 'stock',
        { meta: { name: 'Apple', ticker: 'AAPL', yahoo_ticker: 'AAPL', market: 'US', currency: 'USD' } }],
      ['ETF · KR(.KS)', 'etfBasket', 'etfBasketBtn', 'etf',
        { symbol: '069500', name: 'KODEX 200', currency: 'KRW' }],
      ['ETF · US', 'etfBasket', 'etfBasketBtn', 'etf',
        { symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', currency: 'USD' }],
    ];
    return cases.map(([label, fn, btnId, kind, payload]) => {
      env.probe.setD(clone(payload), kind);
      env.timers.length = 0;
      env.probe[fn]();
      const btn = env.els[btnId], t = env.timers[0] || {};
      const after = t.fn ? (t.fn(), btn.textContent) : null;
      return `${label}|${env.store['invportfolio']}|${btn.textContent}|${t.ms}|${after}`;
    });
  },
};

/* ── 실행 ──────────────────────────────────────────────────────────── */
const before = process.argv[2];
if (!before) {
  console.error('쓰는 법: node scripts/check_js_refactor_parity.js <옛-stock.js> [format|header|basket ...]');
  process.exit(2);
}
const modes = process.argv.slice(3).filter((m) => m in MODES);
const run = modes.length ? modes : Object.keys(MODES);

let bad = 0;
for (const mode of run) {
  // 모드마다 컨텍스트를 새로 만든다 — 앞 모드가 남긴 통화·바스켓이 새지 않게.
  const a = MODES[mode](build(before));
  const b = MODES[mode](build(path.join(ASSETS, 'stock.js')));
  let diff = 0;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (a[i] !== b[i]) {
      diff++;
      if (diff <= 3) {
        const x = a[i] || '', y = b[i] || '';
        let k = 0; while (k < Math.min(x.length, y.length) && x[k] === y[k]) k++;
        console.log(`  ✗ [${mode}] 첫 차이 @${k}`);
        console.log(`     옛: …${x.slice(Math.max(0, k - 50), k + 80)}`);
        console.log(`     새: …${y.slice(Math.max(0, k - 50), k + 80)}`);
      }
    }
  }
  bad += diff;
  console.log(diff === 0 ? `✓ ${mode} — ${a.length}건 전부 동일` : `✗ ${mode} — ${a.length}건 중 ${diff}건 다름`);
}
console.log(bad === 0
  ? '\n전부 동일 — 만들어 내는 문자열이 한 글자도 안 바뀌었습니다.'
  : `\n불일치 ${bad}건`);
process.exit(bad === 0 ? 0 : 1);
