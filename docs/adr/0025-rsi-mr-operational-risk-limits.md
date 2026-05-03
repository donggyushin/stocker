---
date: 2026-05-03
status: 승인됨
deciders: donggyu
related: [0019-phase2-backtest-fail-remediation.md, 0022-step-f-gate-redefinition.md, 0023-rsi-mr-strategy-adoption-conditional.md, 0024-walk-forward-pass-threshold.md]
---

# ADR-0025: RSI MR 운영 리스크 한도 재정의 — Phase 3 모의투자 기준값 확정

## 상태

승인됨 — 2026-05-03. ADR-0023 의 "결과" 섹션이 예고한 "운영 한도 재정의 ADR 작성 예정" 의 이행 ADR. ADR-0023 C1~C4 추가 검증 전원 통과(2026-05-03) 후 Phase 3 착수 직전에 확정한다.

## 맥락

### ORB 시절 RiskConfig 기본값의 유래

`RiskConfig` 의 현행 기본값은 일중 ORB(Opening Range Breakout) 전략 설계 산물이다.

| 필드 | 현행 기본값 | 유래 |
|---|---|---|
| `position_pct` | `Decimal("0.20")` | 종목당 세션 자본 20%, ORB 일중 집중 진입 가정 |
| `max_positions` | `3` | 장중 동시 3종목, ORB 스캘핑 포지션 수 |
| `daily_loss_limit_pct` | `Decimal("0.02")` | 일 -2% 서킷브레이커, ORB 시절 CLAUDE.md 승인값 |
| `daily_max_entries` | `10` | ORB 일중 빈번 진입 상정 |
| `min_notional_krw` | `100_000` | ORB 시절 최소 명목 한도, 전략 무관 |

`force_close_at` 은 `RiskConfig` 의 필드가 아니라 `StrategyConfig`(`strategy/orb.py`)의 필드로 ORB 가 자체 처리한다. RSI MR 전환에서는 main.py 의 `15:00 force_close cron` 자체를 비활성화하는 방식으로 다룬다 — 결과 섹션 PR3 액션 참조.

ADR-0019 사후 결과 보강(2026-05-01)은 "ORB 기반 일중 데이트레이딩 가정 폐기"를 명시했다. ADR-0022(2026-05-01)로 Step F 게이트가 일/월 단위 기준으로 재정의됐고, ADR-0023(2026-05-02)으로 F5 RSI 평균회귀(`RSIMRStrategy`)가 1차 채택 후보로 확정됐다.

### RSIMRStrategy 의 운영 모델

사용자(donggyu, 2026-05-03)가 채택한 운영 모델은 **EOD 1회 일봉 트리거 + 분봉 fill 추적**이다.

- 매일 장 마감 후(또는 장 마감 직전) 전일 일봉을 수신하면 `on_bar` 를 호출해 진입/청산 시그널을 결정한다.
- 시그널이 있으면 다음 장 개장 초 Executor 가 주문을 제출하고 분봉 단위로 체결을 추적한다.
- 일중 강제청산(`force_close_at`)은 일봉 전략에 적합하지 않다.

### RSIMRConfig.position_pct 와 RiskConfig.position_pct 의 의미 차이

두 필드는 이름이 같지만 의미 차원이 다르다. 혼동 방지를 위해 본 ADR 에 명시한다.

**`RSIMRConfig.position_pct` = `Decimal("1.0")`**

백테스트 내부 자금 배분 비율이다. `backtest/rsi_mr.py` 의 `compute_rsi_mr_baseline` 함수는 다음 산식으로 슬롯당 할당액을 계산한다 (`backtest/rsi_mr.py` 약 198행):

```python
alloc_per = cash * position_pct / available_slots
```

`position_pct = 1.0` 의 의미: "현재 보유 현금 전부를 남은 빈 슬롯에 균등 분배한다." 가용 슬롯이 `max_positions - 현재 보유 수` 이므로 진입할 때마다 남은 현금을 빈 슬롯 수로 나눈다. 이는 `max_positions = 10` 과 결합해 종목당 자본의 약 10% 씩 순차 배분하는 효과를 낸다.

**`RiskConfig.position_pct` = `Decimal("0.10")`**

운영 `RiskManager` 의 포지션 사이징 비율이다. `evaluate_entry` 는 다음 산식을 사용한다:

```text
target_notional_krw = int(starting_capital_krw × position_pct)
qty = int(Decimal(target_notional_krw) / signal.price)
```

`position_pct = 0.10` 의 의미: "세션 시작 자본의 10% 를 해당 종목 1개 포지션의 목표 명목으로 한다." `max_positions = 10` 과 결합하면 전체 자본의 최대 100% 를 10 슬롯에 고르게 배분할 수 있다.

두 값은 `max_positions = 10` 이라는 공통 축 하에서 **결과적으로 동등한 자본 배분**을 의도하지만 계산 경로가 다르다. 운영에서 `RSIMRConfig.position_pct = 1.0` 을 `RiskConfig` 에 그대로 적용하면 단일 진입에 세션 자본 전액이 배정되므로 오적용이다.

### Phase 3 진입 게이팅 해제 사실

ADR-0023 의 C1~C4 추가 검증이 2026-05-03 전원 통과했다. Phase 2 PASS 가 공식 선언됐고 Phase 3 착수가 재허가됐다. `execution/`, `main.py`, `monitor/`, `storage/` 코드 산출물은 즉시 재사용 가능하다. 본 ADR 은 이 코드 산출물에 주입할 `RiskConfig` 값을 확정한다.

## 결정

Phase 3 모의투자 운영에 적용할 `RiskConfig` 및 `RSIMRConfig` 기준값을 다음과 같이 확정한다.

### 확정 기준값 표

| 필드 | 값 | 적용 대상 | 근거 |
|---|---|---|---|
| `RSIMRConfig.max_positions` | `10` | Strategy | ADR-0023 C4 sensitivity grid 96 조합 64/96 PASS, 현행값 PASS. ADR-0023 C1 기준 trades=177 |
| `RiskConfig.max_positions` | `10` | RiskManager | Strategy 와 일치. 동시 보유 상한 통일 |
| `RSIMRConfig.position_pct` | `Decimal("1.0")` | Strategy (백테스트 의미) | "남은 현금을 빈 슬롯에 균등 분배" 백테스트 산식. 변경 시 백테스트 결과 재현 불가 |
| `RiskConfig.position_pct` | `Decimal("0.10")` | RiskManager (운영 의미) | "세션 시작 자본의 10% = 종목당 목표 명목". max_positions=10 과 결합해 전체 자본 균등 분배 |
| `RSIMRConfig.stop_loss_pct` | `Decimal("0.03")` | Strategy | ADR-0023 C4 96 조합 중 `stop_loss_pct=0.03` 이 게이트 통과율 최고. 백테스트 기준값 |
| `RiskConfig.daily_loss_limit_pct` | `Decimal("0.02")` | RiskManager | ORB 시절 한도 유지. 일 -2% 서킷브레이커는 전략 무관한 자본 보호 게이트. 변경 근거 부재로 보존 |
| `RiskConfig.daily_max_entries` | `5` | RiskManager | RSI MR 백테스트 평균 trades 175/년 ≈ 0.7건/일. 동시 진입 신호 폭주 방지용 상한. ADR-0023 C1 기준 trades=177 동일 |
| `RiskConfig.min_notional_krw` | `100_000` | RiskManager | ORB 시절 한도 유지. 한국 시장 호가 단위 + 자본 200만원 가정 정합 |

`RiskConfig` 에는 `stop_loss_pct` 또는 `force_close_at` 필드가 존재하지 않는다(`risk/manager.py:86-90`). 손절 발동과 강제청산 정책은 RiskManager 의 책임이 아니므로 본 ADR 의 RiskConfig 결정 표에 포함하지 않는다. 손절은 Strategy 가 stop_price 를 산출해 EntrySignal 에 실어 보내고 Executor 가 실시간 분봉으로 발동한다(아래 "일중 stop_loss 발동 정책" 참조). 강제청산은 RSI MR 일봉 전략 특성상 운영 미사용으로 결정한다(아래 "force_close_at 운영 미사용" 참조).

### 일중 stop_loss 발동 정책

백테스트(`compute_rsi_mr_baseline`)는 **일봉 low** 기준으로 `bar.low ≤ stop_price` 를 판정한다. 운영 Executor 는 **분봉 low** 기준으로 동일 조건을 판정하고 즉시 시장가 청산 주문을 제출한다.

운영이 백테스트보다 더 빠른 손절을 실행한다 — 백테스트 가정 대비 보수적이다. 분봉 step 에서 `bar.low ≤ stop_price` 도달 시 해당 분봉 close 를 청산 참고가로 `ExitSignal(reason="stop_loss")` 를 생성하고 Executor 가 시장가 주문을 낸다.

### force_close_at 운영 미사용

ORB 시절 main.py 의 `15:00 KST` cron `on_force_close` 는 `Executor.force_close_all` 을 호출해 잔존 long 포지션을 강제청산했다. RSI MR 일봉 전략에서는 다음 영업일 시초가 또는 분봉 stop_loss 가드로 청산 시점이 자연 결정되므로 일중 강제청산이 부적합하다.

운영 결정: main.py 의 `15:00 force_close` cron 자체를 RSI MR 모드에서 등록하지 않거나 noop 으로 만든다(아래 결과 섹션 PR3 액션 참조). `RSIMRStrategy.on_time` 이 항상 빈 리스트를 반환하므로(`rsi_mr.py:269-272`) 호출되더라도 부작용 없음 — cron 비활성화는 의미적 정합성 확보 목적.

`RiskConfig` 에는 `force_close_at` 필드가 존재하지 않으므로 RiskConfig 변경은 불필요하다. ORB 시절에도 강제청산 시각은 `StrategyConfig.force_close_at`(`strategy/orb.py`) 가 보유했고 RiskManager 는 시각 정책에 관여하지 않았다.

### 동일 세션 재진입 차단

`RSIMRStrategy` 가 `_last_exit_date` 딕셔너리(`rsi_mr.py` 155행)로 처리한다. 당일 청산한 심볼은 같은 날 재진입 시그널을 내지 않는다(`rsi_mr.py` 241행). `RiskConfig` 에 별도 재진입 차단 가드 불필요.

## 결과

### 후속 PR 액션

**PR2 (main.py RiskConfig 주입)**

`main.py` 의 `RiskConfig()` 호출에 본 ADR 확정값을 명시 주입한다.

```python
RiskConfig(
    position_pct=Decimal("0.10"),
    max_positions=10,
    daily_loss_limit_pct=Decimal("0.02"),
    daily_max_entries=5,
)
```

`RSIMRConfig` 는 기존 기본값(`stop_loss_pct=Decimal("0.03")`, `max_positions=10`, `position_pct=Decimal("1.0")`)을 그대로 사용한다. ADR-0023 C4 sensitivity grid 검증값이 기본값과 일치하므로 별도 명시 주입 불필요. universe YAML 경로만 `config/universe.yaml` 로 주입한다.

**PR3 (main.py 15:00 force_close cron 비활성화)**

`main.py` 의 `_install_jobs` 에서 `15:00 KST` `on_force_close` cron 등록을 RSI MR 모드일 때 생략한다(또는 콜백을 noop 으로 교체). `RiskConfig` 변경 없음. 백테스트 회귀 0 검증을 `uv run pytest -x` 로 수행 후 머지.

**PR3 (Executor 분봉 step stop_loss 가드)**

Executor 분봉 step 에서 활성 포지션의 `stop_price` 를 추적하고 `bar.low ≤ stop_price` 조건 도달 시 즉시 시장가 매도 주문을 제출하는 가드를 추가한다. `stop_price` 는 EOD 트리거 시점 RSIMRStrategy 가 EntrySignal 에 실어 보낸 값을 Executor 가 보존한다. RSIMRStrategy 의 `on_bar` 가 일봉을 소비하는 구조라 분봉 레벨 stop_loss 는 Executor 가 독립적으로 처리한다 — Strategy 호출 없이.

### 10영업일 모의투자 후 재검토 항목

Phase 3 모의투자 무중단 운영 10영업일 후 다음을 회고하고 필요 시 ADR-0026 으로 갱신한다.

- `daily_max_entries = 5` 가 실제 일별 진입 빈도와 정합하는지. 연간 trades ≈ 0.7건/일 기준치보다 훨씬 낮으면 하향, 폭주 사례 발생 시 현행 유지.
- `RiskConfig.position_pct = 0.10` × `max_positions = 10` 조합이 실제 자본 배분에서 의도한 균등 배분을 실현하는지.
- 분봉 stop_loss 발동 빈도가 백테스트 stop_loss 비율(64.6%, ADR-0023 PR5)과 유사한지.

### 리스크 고지

본 ADR 의 수치는 1년(2024-04-01~2026-04-21) 한국 KOSPI 200 일봉 백테스트 기준이다. 백테스트 수익률은 미래 수익을 보장하지 않는다. 슬리피지·체결 지연·VI·상하한가 등 실제 운영 비용이 백테스트 가정을 초과할 수 있다. Phase 3 는 **모의투자** 단계이며 실전 자본 투입 전 페이퍼트레이딩을 선행한다 (root CLAUDE.md 리스크 고지 원칙).

### 문서 동기화 예정

본 ADR 초안 단계에서는 `CLAUDE.md` "확정된 결정" 리스크 한도 항목, `README.md` 리스크 한도 표, `plan.md` Phase 3 리스크 한도 섹션을 갱신하지 않는다. PR2 코드 변경(main.py RiskConfig 주입)과 동시에 세 정본 문서를 동기화한다.

## 추적

- 코드 (PR2 예정): `src/stock_agent/main.py` — `RiskConfig` 명시 주입, `RSIMRStrategy` import + 인스턴스 교체.
- 코드 (PR3 완료, #111, 2026-05-03): `src/stock_agent/main.py` — `15:00 force_close` cron 비활성화. `src/stock_agent/execution/executor.py` — 분봉 stop_loss 가드 추가. (`src/stock_agent/risk/manager.py` 변경 없음)
- 관련 ADR: [ADR-0019](./0019-phase2-backtest-fail-remediation.md), [ADR-0022](./0022-step-f-gate-redefinition.md), [ADR-0023](./0023-rsi-mr-strategy-adoption-conditional.md), [ADR-0024](./0024-walk-forward-pass-threshold.md).
- 도입 PR: PR3 (머지 완료, 2026-05-03).
- 후속 ADR: ADR-0026 (10영업일 모의투자 회고 후 한도 갱신, 조건부).
