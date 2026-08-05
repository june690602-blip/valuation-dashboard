# ADR — 설계 결정 기록 (Architecture Decision Records)

"왜 이렇게 만들었나"를 남기는 짧은 기록입니다. 코드에는 결과만 남고 이유는 사라지기 때문에,
중요한 설계 선택마다 **맥락 → 결정 → 근거 → 검토한 대안 → 한계**를 한 장으로 정리합니다.

규칙:

- 결정 하나당 파일 하나, 번호 순서대로. 한 번 채택된 ADR은 수정하지 않고,
  뒤집을 때는 새 ADR을 만들어 이전 것을 "대체됨"으로 표시합니다.
- 논의는 GitHub 이슈에서 하고, 결론이 나면 여기로 옮긴 뒤 이슈를 닫습니다.

| 번호 | 제목 | 상태 |
|---|---|---|
| [0001](0001-fair-value-simple-average.md) | 적정가 종합은 동일가중 산술평균으로 한다 | 대체됨 (→ 0003) |
| [0002](0002-backtest-statistics.md) | 백테스트 통계는 비중복 표본과 순위상관으로 계산한다 | 채택됨 |
| [0003](0003-fair-value-weighted-average.md) | 적정가 종합은 가격 설명력 순위 기반 가중평균으로 한다 | 대체됨 (→ 0006) |
| [0004](0004-backtest-reconstructable-composite.md) | 백테스트 신호는 복원 가능한 ②+③ 가중 종합으로 한다 | 채택됨 |
| [0005](0005-peer-ranking-weights.md) | 업종 내 종합 순위는 가치 60 · 수익성 40 백분위 가중으로 한다 | 채택됨 |
| [0006](0006-fundamental-verdict-consensus-alongside.md) | 판정은 펀더멘털 3방법으로 내고, 컨센서스 반영값은 나란히 병기한다 | 채택됨 |
| [0007](0007-book-quality-gate-for-rim.md) | RIM 적용 여부는 PBR이 아니라 '장부가가 작아진 흔적'으로 가른다 | 채택됨 |
| [0008](0008-backtest-observation-not-statistics.md) | 백테스트 탭은 통계를 주장하지 않고 과거 신호를 관찰만 한다 | 대체됨 (→ 0009) |
| [0009](0009-backtest-tab-indefinitely-deferred.md) | 백테스트 탭을 무기한 보류한다 (코드는 남긴다) | 채택됨 |
| [0010](0010-book-rejected-gate-for-rim.md) | 시장이 장부가를 오래 거부했으면 RIM을 쓰지 않는다 (0007의 대칭) | 채택됨 |
| [0012](0012-historical-band-measures-multiple-not-price.md) | ②는 '배수를 재는 밴드'일 때만 판정에 쓴다 | 채택됨 |
| [0011](0011-peer-size-window-and-no-fallback.md) | ①은 규모 비교가능 피어(1/5~5배)만 쓰고 부족하면 제외한다 | 채택됨 |
| [0013](0013-peer-selection-by-size-proximity.md) | 피어 후보를 '업종 시총 상위 N'이 아니라 '업종 내 시총 인접 N'으로 뽑는다 | 채택됨 |
| [0014](0014-relative-value-by-regression.md) | ①을 피어 중앙값이 아니라 업종·규모·수익성 회귀로 낸다 | 채택됨 |
| [0015](0015-normalized-earnings-axis.md) | 정규화 이익(장기 평균)을 판정의 네 번째 축으로 더한다 | 채택됨 (창 5년 한계는 [0025]가 대체) |
| [0016](0016-no-dcf-epv-instead.md) | DCF를 짓지 않는다 — EPV가 더 넓고, 축을 늘리기 전에 할 것이 있다 | 채택됨 |
| [0017](0017-estimation-error-and-margin-of-safety.md) | 추정 오차와 안전마진을 화면에 낸다 (판정 문턱은 그대로) | 채택됨 |
| [0018](0018-declare-what-the-verdict-rests-on.md) | 이 판정이 무엇에 기대는지 화면에 밝힌다 | 채택됨 |
| [0019](0019-client-math-parity-by-executing-the-browser-code.md) | 이식된 수식은 한 파일에 모으고, 브라우저가 받는 그 파일을 실행해 대조한다 | 채택됨 |
| [0020](0020-one-ruler-for-multiples-on-screen.md) | 화면의 배수는 공시값 한 벌로 재고, 자가 다르면 비교하지 않는다 | 채택됨 |
| [0021](0021-size-term-spline.md) | 적정 배수의 규모 항을 직선 하나가 아니라 마디 있는 스플라인으로 낸다 | 채택됨 |
| [0022](0022-confidence-counts-effective-axes.md) | 신뢰도는 방법의 개수가 아니라 실질 축 수로 상한을 씌운다 | 채택됨 |
| [0023](0023-no-epv-earnings-axes-overlap.md) | EPV를 짓지 않는다 — 두 시장 모두 이익 기반 축과 겹쳤다 | 채택됨 (결정 1은 [0024]가 대체) |
| [0024](0024-independence-is-not-a-correlation-ceiling.md) | 축의 독립성은 상관 상한으로 재지 않는다 — 재려던 해악을 ADR-0022가 이미 값 매긴다 | 채택됨 |
| [0025](0025-normalize-window-eight-years.md) | 정규화 이익의 창을 8년으로 — 5년으로 둔 이유가 사실이 아니었다 | 채택됨 |
| [0026](0026-band-window-is-a-decision-not-a-default.md) | ② 역사적 밴드의 창을 명시적으로 7년으로 — 창은 결정이지 기본 인자가 아니다 | 채택됨 |
