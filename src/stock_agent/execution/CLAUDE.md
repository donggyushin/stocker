# execution — Executor (신호 → 주문 → 체결 추적 → 상태 동기화 루프)

stock-agent 의 오케스트레이션 경계 모듈. `ORBStrategy` (시그널) + `RiskManager`
(게이팅·사이징·서킷브레이커) + `KisClient` (paper 주문·잔고·미체결) +
`RealtimeDataStore` (실전 키 시세·분봉) 4 모듈을 결합한다. 전략·리스크·브로커
의 단독 동작 계약이 이미 잠겨 있으므로 `Executor` 는 "조립" 만 한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`execution/__init__.py`)

`Executor`, `ExecutorConfig`, `OrderSubmitter`, `LiveOrderSubmitter`,
`DryRunOrderSubmitter`, `BalanceProvider`, `LiveBalanceProvider`, `BarSource`,
`ExecutorError`, `StepReport`, `ReconcileReport`

## 현재 상태 (2026-04-21 기준)

**Phase 3 첫 산출물 — `Executor` 단독 (코드·테스트 레벨) 완료** (2026-04-21).

Phase 3 PASS 선언은 모의투자 환경에서 **연속 10영업일 무중단 + 0 unhandled
error + 모든 주문이 SQLite 기록 + 텔레그램 알림 100% 수신** 후. 본 PR 은 그
조건 중 단 하나도 자동으로 충족하지 않는다 — 이번 PR 의 산출은 단위 테스트로
잠근 동작 계약뿐.

### 의도적으로 미포함 (후속 PR)

| 영역 | 위치 | 비고 |
|---|---|---|
| APScheduler 스케줄링 | `main.py` | `Executor.step(now)` 만 노출 — 호출 주기는 외부 책임 |
| 텔레그램 알림 | `monitor/notifier.py` | 진입·청산·에러·일일 요약 |
| SQLite 영속화 | `storage/db.py` | 체결·주문·일일 PnL |
| KIS 체결조회 API | `broker/` 확장 | 실체결가 정확도 향상 (현재 슬리피지 0.1% 가정) |
| 부분체결 잔량 처리 | `broker/cancel_order` 등 | 시장가 즉시 전량 체결 가정 |
| `config/strategy.yaml` 로더 | `main.py` 진입 시 | 현재 코드 상수 + 생성자 주입 |

## 핵심 결정 — Protocol 의존성 역전

`Executor` 는 `KisClient` / `RealtimeDataStore` 구체 타입을 직접 의존하지 않고
세 개의 Protocol(`OrderSubmitter`, `BalanceProvider`, `BarSource`) 만 받는다.
이유:

1. **드라이런 모드를 분기 없이 표현** — `if dry_run` 코드를 곳곳에 박지 않고
   `DryRunOrderSubmitter` 를 주입하기만 하면 KIS 접촉이 0 이 된다.
2. **단위 테스트의 명확성** — KisClient 목킹 부담을 제거하고 의도를 직접 표현
   하는 더블(`FakeOrderSubmitter` 등)을 쓸 수 있다.
3. **라이브 어댑터의 얇음** — `LiveOrderSubmitter` / `LiveBalanceProvider` 는
   `KisClient` 메서드를 한 줄씩 위임하는 구조라 추가 결정이 없다. `RealtimeDataStore`
   는 `get_minute_bars(symbol)` 시그니처가 이미 `BarSource` 를 자연스럽게
   만족하므로 별도 어댑터가 필요 없다.

이 분리는 plan.md 의 "신호 → 주문 → 체결 추적 → 상태 동기화 루프" 문구를 가장
검증 가능한 형태로 구현한 결과다.

## 공개 API

### `Executor`

```python
Executor(
    *,
    symbols: tuple[str, ...],
    strategy: ORBStrategy,
    risk_manager: RiskManager,
    bar_source: BarSource,
    order_submitter: OrderSubmitter,
    balance_provider: BalanceProvider,
    config: ExecutorConfig | None = None,
    clock: Callable[[], datetime] | None = None,   # KST aware datetime 반환
    sleep: Callable[[float], None] | None = None,
)

start_session(session_date: date, starting_capital_krw: int) -> None
step(now: datetime) -> StepReport          # 1 sweep
force_close_all(now: datetime) -> StepReport
reconcile() -> ReconcileReport
is_halted (property)                       # _halt or risk_manager.is_halted
```

### `ExecutorConfig` 기본값

| 필드 | 기본값 | 설명 |
|---|---|---|
| `cash_buffer_pct` | `Decimal("0.005")` | `withdrawable` 의 0.5% 를 수수료/세금 버퍼로 차감해 RiskManager 에 전달 |
| `order_fill_timeout_s` | `30.0` | 시장가 체결 대기 타임아웃(초). 초과 시 `ExecutorError` |
| `order_poll_interval_s` | `0.5` | 미체결 폴링 주기(초) |
| `slippage_rate` | `Decimal("0.001")` | 체결가 추정 슬리피지. 백테스트와 동일 |
| `commission_rate` | `Decimal("0.00015")` | 매수·매도 대칭 0.015%. 백테스트와 동일 |
| `sell_tax_rate` | `Decimal("0.0018")` | 매도 거래세 0.18%. 백테스트와 동일 |
| `backoff_max_attempts` | `3` | KisClientError 재시도 최대. 총 시도 = `max_attempts + 1` |
| `backoff_initial_s` | `0.1` | 백오프 초기 지연. 지수 증가 (0.1 → 0.2 → 0.4) |

`__post_init__` 검증 — 비율 음수 / timeout·interval·initial 0 이하 /
max_attempts 0 이하 → `RuntimeError` (broker/strategy/risk 와 동일 기조).

### `StepReport` / `ReconcileReport`

`@dataclass(frozen=True, slots=True)`. 필드:

```python
StepReport(processed_bars: int, orders_submitted: int, halted: bool, reconcile: ReconcileReport)
ReconcileReport(broker_holdings: dict[str, int], risk_holdings: dict[str, int], mismatch_symbols: tuple[str, ...])
```

## `step(now)` 흐름

```text
1. 가드: now naive → RuntimeError, 세션 미시작 → RuntimeError.
2. reconcile() — 잔고 vs RiskManager 비교. 불일치 시 _halt = True + critical 로그.
3. for symbol in symbols:
     bars = bar_source.get_minute_bars(symbol)
     for bar in bars:
         if bar.bar_time <= _last_processed_bar_time[symbol]: continue   # idempotent
         signals = strategy.on_bar(bar)
         _process_signals(signals)
         _last_processed_bar_time[symbol] = bar.bar_time
4. signals = strategy.on_time(now)
   _process_signals(signals)
5. return StepReport(...)
```

`force_close_all(now)` 는 위에서 3 단계(분봉 처리)만 생략 — 15:00 KST 단발성
강제청산 진입점.

## EntrySignal 처리

1. `is_halted` True → 스킵 (`logger.warning`).
2. `available_cash = max(0, int(balance.withdrawable * (1 - cash_buffer_pct)))`.
3. `risk_manager.evaluate_entry(signal, available_cash)` — 거부면 종료
   (RiskManager 가 이미 사유 로그).
4. 승인이면 `_with_backoff(submit_buy)` → `OrderTicket`.
5. `_wait_fill(ticket)` — 시장가 즉시 체결 가정, timeout → `ExecutorError`.
6. `entry_fill_price = buy_fill_price(signal.price, slippage_rate)` —
   `backtest.costs.buy_fill_price` 재사용.
7. `risk_manager.record_entry(symbol, entry_fill_price, qty, now)` +
   `_open_lots[symbol] = _OpenLot(entry_fill_price, qty)`.

## ExitSignal 처리

1. `_open_lots[symbol]` 없으면 `RiskManager.active_positions` 에서 fallback —
   외부에서 직접 `record_entry` 한 경우(테스트·수동 시나리오) 호환. 둘 다 없으면
   `ExecutorError` (전략-Executor 동기화 위반).
2. `_with_backoff(submit_sell)` → `OrderTicket`.
3. `_wait_fill(ticket)`.
4. `exit_fill_price = sell_fill_price(signal.price, slippage_rate)`.
5. PnL 계산 — 백테스트 엔진과 동일 산식 (`buy_commission` + `sell_commission`
   + `sell_tax` 모두 `backtest.costs` 재사용):
   ```text
   gross = int(sell_notional) - int(buy_notional)
   net   = gross - buy_comm - sell_comm - tax
   ```
6. `risk_manager.record_exit(symbol, net_pnl)` + `_open_lots.pop(symbol, None)`.

## 재동기화 — `reconcile()`

```python
balance = balance_provider.get_balance()
broker_holdings = {h.symbol: h.qty for h in balance.holdings}
risk_holdings   = {p.symbol: p.qty for p in risk_manager.active_positions}
mismatch_symbols = tuple(s for s in sorted(...) if broker[s] != risk[s])
if mismatch_symbols:
    logger.critical(...)
    self._halt = True
```

자동 복구는 하지 않는다 — 잘못 보정하면 이중 주문·미청산 위험이 더 크다. `_halt`
는 `start_session` 재호출 전까지 유지된다 (운영자 개입 + 다음 세션 리셋 경로).
`is_halted` 는 `_halt or risk_manager.is_halted` — 서킷브레이커 발동도 함께 차단.

## 에러 정책

| 예외 | 발생 조건 | 호출자 행동 |
|---|---|---|
| `RuntimeError` | 입력 오류 (naive datetime, 세션 미시작 등) | 즉시 실패, 재시도 금지 |
| `RiskManagerError` | 호출 순서 위반 (전파) | 그대로 전파 |
| `ExecutorError` | 체결 타임아웃·전략 무결성 오류·KIS 백오프 한계 초과 | 운영자 개입 — 자동 재시도 금지 |
| `KisClientError` (broker) | 네트워크·API 일시 장애 | `_with_backoff` 안에서 좁은 지수 백오프 (기본 100→200→400 ms, 총 4회 시도). 한계 초과 시 `ExecutorError` 로 승격 (`__cause__` 보존) |

generic `except Exception` 금지. `assert` 대신 명시적 예외. broker/strategy/risk
와 동일 기조.

## `_with_backoff` 정책

- `KisClientError` **만** 잡는다. `RuntimeError` / `RiskManagerError` /
  타입 오류는 즉시 전파 (백오프 미적용).
- 총 시도 = `backoff_max_attempts + 1` 회 (첫 시도 + max_attempts 재시도).
- 매 재시도 직전 `sleep(backoff_initial_s × 2**attempt)` — 지수 증가.
- 한계 초과 시 `ExecutorError(...) from last_KisClientError`.

`get_balance` / `get_pending_orders` / `submit_buy` / `submit_sell` 모두
이 래퍼를 통과한다 — 일시적 네트워크 장애가 단발 step 을 자동 죽이지 않게.

## 시장가 체결가 추정 (V0)

현재 PR 은 KIS 체결조회 API 통합을 의도적으로 분리한다. 체결가는 시그널 가격
(분봉 close, 참고가) 에 슬리피지 ±0.1% 를 적용해 추정한다 — 백테스트 비용
모델과 동일 가정으로, 백테스트 결과의 실전 괴리를 추적 가능한 한 가지 변수로
유지하기 위해서. 모의투자 2주 운영에서 측정된 실제 슬리피지가 0.1% 와 크게
다르면 후속 PR 에서 (a) 실체결가 회수 API 도입 또는 (b) `slippage_rate`
설정값 조정을 진행한다.

## 분봉 처리 idempotent

`_last_processed_bar_time[symbol]` 마커로 동일 step 두 번 호출 시 같은 bar 가
재처리되지 않게 한다. APScheduler 트리거 중복·재실행 안전성을 위해.

## 결정론·스레드

- 외부 I/O = `KisClient` (LiveOrderSubmitter/LiveBalanceProvider 경유), `loguru`
  로그, `time.sleep` (테스트에서는 주입). `datetime.now` 는 `clock` 주입으로만
  외부와 결합.
- 단일 프로세스 전용. `step` / `force_close_all` / `reconcile` 동시 호출 금지.
  broker/strategy/risk/data 와 동일 기조.

## 테스트 정책

- 실제 KIS 네트워크·실주문·텔레그램·SQLite·HTTP 에 절대 접촉하지 않는다.
- `OrderSubmitter` / `BalanceProvider` / `BarSource` 는 모두 더블로 주입.
- `ORBStrategy` / `RiskManager` 는 외부 의존 0 인 순수 클래스이므로 실 인스턴스
  사용 (목보다 정확).
- `clock` 은 `lambda` 또는 list+pop 주입으로 결정론 확보. `sleep` 은 `MagicMock`
  또는 콜백 누적으로 호출 횟수·인자 검증.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root
  CLAUDE.md 하드 규칙, `.claude/hooks/tests-writer-guard.sh` fail-closed).
- 관련 테스트 파일: `tests/test_executor.py` (63 케이스 — 공개 심볼 11종,
  ExecutorConfig 검증 10종, 생성·세션 4종, step 가드 2종, EntrySignal 승인 3종,
  EntrySignal 거부 1종, ExitSignal 승인 3종, ExitSignal 무결성 1종, force_close
  3종, 체결 대기 3종, 드라이런 5종, reconcile 4종, halt 3종, 백오프 3종, 에러
  좁힘 1종, 멀티 심볼 1종, idempotent 1종, Report 구조 4종).

## 소비자 참고

- **`main.py`** (Phase 3 두 번째 산출물, 미착수): `APScheduler` 로
  09:00 시작·09:30 OR 확정·장중 `step(now)` 폴링·15:00 `force_close_all`·
  15:30 일일 리포트. `LiveOrderSubmitter` / `LiveBalanceProvider` 를 주입.
- **`monitor/notifier.py`** (Phase 3 세 번째 산출물, 미착수): `Executor`
  반환 `StepReport` / `ReconcileReport` 와 logger sink 를 받아 텔레그램 알림.
- **`storage/db.py`** (Phase 3 네 번째 산출물, 미착수): `RiskManager` 의 체결
  통지 및 `Executor` 의 PnL 계산 결과를 SQLite 에 영속화.
- **드라이런 검증**: `DryRunOrderSubmitter` 를 주입하면 `Executor` 는 KIS 에
  단 한 번도 접촉하지 않는다 (paper 모드 healthcheck 와 별개의 안전 벨트).

## 범위 제외 (의도적 defer)

- **APScheduler 도입** — `step(now)` 만 노출. 외부 스케줄러 책임.
- **텔레그램 알림** — `monitor/notifier.py` (후속 PR).
- **SQLite 영속화** — `storage/db.py` (후속 PR).
- **부분체결 잔량 취소·재발주** — `KisClient.cancel_order` 미구현. 시장가 주문
  통상 즉시 전량 체결되는 KIS 동작 가정. 후속 PR.
- **KIS 체결조회 API 통합** — 실체결가 정확도 향상. broker 확장 별도 PR.
  현재는 슬리피지 0.1% 추정.
- **자동 포지션 복구** — reconcile 불일치 시 자동 보정 금지. 운영자 개입 +
  다음 세션 `start_session` 으로 리셋만 허용.
- **`config/strategy.yaml` YAML 로더** — `main.py` 착수 시 도입. 현재는
  `ExecutorConfig` 생성자 주입.
- **멀티프로세스·스레드 safe** — 단일 프로세스 전용 (broker/strategy/risk/data
  와 동일).
- **호가 단위 라운딩** — 현재 `Decimal` 원시 그대로. KRX 호가 단위 적용은
  Phase 5 재설계 범위.
