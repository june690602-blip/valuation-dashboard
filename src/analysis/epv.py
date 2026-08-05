"""EPV(Earnings Power Value) 축 후보 — 순수 산식 (ADR-0016 결정 2).

**이 모듈은 판정에 쓰이지 않는다.** `compute_valuation`이 부르지 않고, 지금은
`scripts/check_epv_viability.py`만 import한다. EPV를 축으로 지을 값이 있는지 재는
것이 먼저이고(ADR-0016 결정 2: *"통과하지 못하면 짓지 않는다"*), 재는 코드가 곧
근거가 되게 `src/`에 둔다 — ADR-0022 결정 5가 `loo_leg_error`에 같은 선택을 했다.
스크립트에 두면 시험할 자리가 없어진다(이 저장소의 테스트는 `scripts/`를 import하는
것이 하나도 없다).

EPV는 *"지금 버는 만큼만 영원히 번다"*고 볼 때의 값이라 **성장 가정이 0개**다.
그것이 ADR-0016이 DCF 대신 EPV를 후보로 고른 이유이므로, Greenwald 원안의 유지보수
capex·일회성 손익·초과현금 조정은 넣지 않는다. 조정을 넣는 순간 가정이 생기고
고른 이유가 사라진다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis.valuation import NORMALIZE_MIN_YEARS, NORMALIZE_WINDOW


def normalized_operating_income(fin) -> tuple[float | None, int]:
    """(정상 영업이익, 평균에 쓴 연수). 창을 못 채우면 (None, 실제 개수).

    `_normalized_earnings`(⑤)와 **같은 창 규칙**을 영업이익에 쓴다 — 창 크기 상수를
    그쪽에서 가져오므로 한쪽만 바뀌는 일이 없다. **적자 연도를 빼지 않는 것도 그대로다.**
    빼면 정규화가 아니라 체리피킹이고, 사이클이 이 축이 감당해야 할 대상이다.
    """
    if fin is None or not hasattr(fin, "columns") or "operating_income" not in fin.columns:
        return None, 0
    win = pd.to_numeric(fin["operating_income"], errors="coerce").iloc[-NORMALIZE_WINDOW:]
    win = win[np.isfinite(win)]
    if len(win) < NORMALIZE_MIN_YEARS:
        return None, int(len(win))
    return float(win.mean()), int(len(win))


def epv_per_share(op_income: float | None, tax_rate: float | None,
                  wacc: float | None, net_debt: float,
                  shares: float | None) -> float | None:
    """EPV 주당가치. 설 수 없으면 **None** (ADR-0011: 오염된 값보다 계산 불가).

    ::

        기업가치 = 정상 영업이익 × (1 − 세율) ÷ WACC
        주주가치 = 기업가치 − 순부채
        주당     = 주주가치 ÷ 주식수

    `net_debt`은 총차입금 − 현금이다. 기존 EV/EBITDA 다리가 쓰는 다리 건너기와 같은
    식이라(`valuation.py`의 `m * ebitda_ps - debt_ps + cash_ps`) 같은 자리를 두 자로
    재지 않는다.

    할인율이 주주 요구수익률(k_e)이 아니라 **WACC**인 것은 세후 영업이익이 채권자와
    주주 모두에게 가는 흐름이기 때문이다. 그래서 순부채를 뒤에서 뺀다.

    안 서는 자리 넷 — 어느 것도 대체값으로 메우지 않는다:

    1. 정상 영업이익이 양수가 아님 (적자 회사는 이 축으로 못 구한다)
    2. WACC가 없거나 양수가 아님 (0 이하면 영구가치가 발산하거나 부호가 뒤집힌다)
    3. 세율이 없거나 [0, 1) 밖
    4. **주주가치가 양수가 아님** — 순부채가 기업가치보다 크다. 음수 적정주가는
       판정에 못 쓴다. ADR-0016의 `epv_ready`(정상 영업이익 > 0 & WACC 있음)에는
       없던 조건이라, 이번 커버리지가 그 문서의 64%보다 낮게 나올 수 있다
    """
    if op_income is None or not np.isfinite(op_income) or op_income <= 0:
        return None
    if wacc is None or not np.isfinite(wacc) or wacc <= 0:
        return None
    if tax_rate is None or not np.isfinite(tax_rate) or not 0.0 <= tax_rate < 1.0:
        return None
    if not shares or not np.isfinite(shares) or shares <= 0:
        return None
    equity = op_income * (1.0 - tax_rate) / wacc - float(net_debt or 0.0)
    if not np.isfinite(equity) or equity <= 0:
        return None
    return equity / float(shares)
