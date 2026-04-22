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
`ExecutorError`, `StepReport`, `ReconcileReport`, `EntryEvent`, `ExitEvent`,
`OpenPositionInput`
(총 14종)

## 현재 상태 (2026-04-21 기준)

**Phase 3 첫 산출물 — `Executor` 단독 (코드·테스트 레벨) 완료** (2026-04-21).

**Phase 3 세 번째 산출물(`monitor/notifier.py`) 완료(2026-04-21)에 따른 확장**:
`StepReport` 에 `entry_events`·`exit_events` 필드 추가 (기본값 `()` backward compat),
`EntryEvent`·`ExitEvent` DTO 신설 + `execution/__init__` 재노출,
`Executor.last_reconcile: ReconcileReport | None` 프로퍼티 신설.

**Phase 3 네 번째 산출물(`storage/db.py`) 완료(2026-04-22)에 따른 확장**:
`EntryEvent`·`ExitEvent` 에 `order_number: str` 필드 추가 (`__post_init__` 가드:
빈 문자열·naive timestamp·qty≤0·price≤0 → `RuntimeError`).
`_handle_entry`/`_handle_exit` 가 `ticket.order_number` 를 주입.

**Phase 3 다섯 번째 산출물(broker 체결조회 + 부분체결 정책, ADR-0015) 완료(2026-04-22)에 따른 확장**:
`OrderSubmitter` Protocol 에 `cancel_order(order_number: str) -> None` 추가.
`LiveOrderSubmitter.cancel_order` (KisClient 위임) + `DryRunOrderSubmitter.cancel_order` (info 로그 + no-op).
내부 `_FillOutcome` DTO 신설 (`filled_qty: int`, `status: Literal["full","partial","none"]`).
`_wait_fill` → `_resolve_fill(ticket) -> _FillOutcome` 교체 — 타임아웃 시 `cancel_order` 호출 + 부분/0 체결 수습.
`_handle_entry`: partial → `filled_qty` 만 `record_entry` + `EntryEvent.qty=filled_qty` + warning 로그.
zero → skip + info 로그 + `return False` (RiskManager 미기록).
`_handle_exit`: `status != "full"` → `ExecutorError` 승격 (운영자 개입 유도).
`StepReport` 구조는 변경 없음 — `entry_events.qty` 가 실체결 수량으로 해석됨(계약 의미 명확화).

Phase 3 PASS 선언은 모의투자 환경에서 **연속 10영업일 무중단 + 0 unhandled
error + 모든 주문이 SQLite 기록 + 텔레그램 알림 100% 수신** 후. 본 PR 은 그
조건 중 단 하나도 자동으로 충족하지 않는다 — 이번 PR 의 산출은 단위 테스트로
잠근 동작 계약뿐.

### 의도적으로 미포함 (후속 PR)

| 영역 | 위치 | 비고 |
|---|---|---|
| APScheduler 스케줄링 | `main.py` | `Executor.step(now)` 만 노출 — 호출 주기는 외부 책임 |
| ~~텔레그램 알림~~ | ~~`monitor/notifier.py`~~ | **완료 2026-04-21** — [monitor/CLAUDE.md](../monitor/CLAUDE.md) 참조 |
| ~~SQLite 영속화~~ | ~~`storage/db.py`~~ | **완료 2026-04-22** — [storage/CLAUDE.md](../storage/CLAUDE.md) 참조 |
| ~~KIS 체결조회 API~~ | ~~`broker/` 확장~~ | **완료 2026-04-22** (ADR-0015) — [broker/CLAUDE.md](../broker/CLAUDE.md) 참조 |
| ~~부분체결 잔량 처리~~ | ~~`broker/cancel_order` 등~~ | **완료 2026-04-22** (ADR-0015) — `_resolve_fill` + `cancel_order` 통합 |
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

### 재기동 복원용 구조적 타입 (Issue #33)

`OpenPositionInput` Protocol 은 `restore_session` 이 받는 포지션 항목의 최소 계약이다.

```python
class OpenPositionInput(Protocol):
    @property
    def symbol(self) -> str: ...
    @property
    def qty(self) -> int: ...
    @property
    def entry_price(self) -> Decimal: ...
    @property
    def entry_ts(self) -> datetime: ...
    @property
    def order_number(self) -> str: ...
```

`storage.OpenPositionRow` 가 이 Protocol 을 구조적으로 만족한다. `execution` 이
`storage` 를 직접 import 하면 단방향 의존 계층(`data → strategy → risk →
execution`) 이 깨지므로, Protocol 을 `execution` 쪽에 선언해 역방향 import
를 원천 차단한다. `main.py` 가 양쪽을 알고 있는 유일한 조립 지점이다.

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
    # _last_reconcile = None 으로 초기화 (Issue #33 추가)
restore_session(
    session_date: date,
    starting_capital_krw: int,
    *,
    open_positions: Sequence[OpenPositionInput],
    closed_symbols: Sequence[str] = (),
    entries_today: int,
    daily_realized_pnl_krw: int,
) -> None
step(now: datetime) -> StepReport          # 1 sweep
force_close_all(now: datetime) -> StepReport
reconcile() -> ReconcileReport
is_halted (property)                       # _halt or risk_manager.is_halted
```

### `ExecutorConfig` 필드

기본값은 **코드(`executor.py`) 가 정본**. 여기서는 필드의 의미만 설명한다 — 값을 두 곳에 적으면 rot 위험.

| 필드 | 의미 |
|---|---|
| `cash_buffer_pct` | `withdrawable` 에서 수수료/세금 버퍼로 차감할 비율 (`[0, 1)`) |
| `order_fill_timeout_s` | 시장가 체결 대기 타임아웃(초). 초과 시 `ExecutorError` |
| `order_poll_interval_s` | 미체결 폴링 주기(초) |
| `slippage_rate` | 체결가 추정 슬리피지. **백테스트(`backtest/engine.py`)와 동일 가정** |
| `commission_rate` | 매수·매도 대칭 수수료율. **백테스트와 동일 가정** |
| `sell_tax_rate` | 매도 거래세율. **백테스트와 동일 가정** |
| `backoff_max_attempts` | KisClientError 재시도 최대 횟수. 총 시도 = `max_attempts + 1` 회 |
| `backoff_initial_s` | 백오프 초기 지연(초). 지수 증가 — `initial × 2^attempt` |

`__post_init__` 검증 — 비율 음수 / `cash_buffer_pct ≥ 1` / timeout·interval·initial 0 이하 / max_attempts 0 이하 → `RuntimeError` (broker/strategy/risk 와 동일 기조).

### `StepReport` / `ReconcileReport`

`@dataclass(frozen=True, slots=True)`. 필드:

```python
StepReport(
    processed_bars: int,
    orders_submitted: int,
    halted: bool,
    reconcile: ReconcileReport,
    entry_events: tuple[EntryEvent, ...] = (),   # Phase 3 세 번째 산출물에서 추가
    exit_events: tuple[ExitEvent, ...] = (),     # 기본값 () — backward compat
)
ReconcileReport(
    broker_holdings: Mapping[str, int],
    risk_holdings: Mapping[str, int],
    mismatch_symbols: tuple[str, ...],
)
```

`ReconcileReport.broker_holdings` / `risk_holdings` 는 `MappingProxyType` 으로 래핑된 읽기 전용 뷰 — `report.broker_holdings["AAA"] = 99` 같은 외부 mutation 시도는 `TypeError`.

`entry_events` / `exit_events` 는 `_handle_entry` / `_handle_exit` 체결 확정 후
`_sweep_entry_events` / `_sweep_exit_events` 에 append 된 뒤 sweep 완료 시점에
tuple 로 고정된다 — `main.py` 콜백이 소비해 `runtime.notifier.notify_*` 를 호출.

### `EntryEvent` / `ExitEvent`

`@dataclass(frozen=True, slots=True)`. `execution/__init__` 재노출.

| DTO | 핵심 필드 |
|---|---|
| `EntryEvent` | `symbol: str`, `qty: int`, `fill_price: Decimal`, `ref_price: Decimal`, `order_number: str`, `timestamp: datetime` |
| `ExitEvent` | `symbol: str`, `qty: int`, `fill_price: Decimal`, `reason: ExitReason`, `net_pnl_krw: int`, `order_number: str`, `timestamp: datetime` |

소비자: `monitor/notifier.py` 의 `TelegramNotifier.notify_entry` / `notify_exit`.

### `Executor` 추가 공개 API (Phase 3 세 번째 산출물)

```python
last_reconcile: ReconcileReport | None   # property, read-only
```

`reconcile()` 호출 시마다 내부 캐시 갱신. `_on_daily_report` 콜백이 추가 네트워크
호출 없이 최신 mismatch 상태를 `DailySummary.mismatch_symbols` 에 담을 수 있도록.
`start_session` 및 `restore_session` 에서 `None` 으로 초기화.

### `restore_session` 흐름 (Issue #33)

```text
1. RiskManager.restore_session(session_date, starting_capital_krw,
       open_positions=..., entries_today=..., daily_realized_pnl_krw=...) 위임.
2. open_positions 순회 → _open_lots[symbol] = _OpenLot(entry_price, qty).
3. open_positions 의 각 symbol 에 대해 strategy.restore_long_position(symbol, ...) 호출.
4. closed_symbols \ open_symbols 에 대해 strategy.mark_session_closed(symbol, session_date) 호출.
5. _halt = False, _last_reconcile = None 리셋.
```

`main.py` 의 `_on_session_start` 콜백이 `recorder.load_open_positions(today)` +
`recorder.load_daily_pnl(today)` 를 먼저 호출해 재기동 여부를 판단하고,
재기동이면 `restore_session`, 아니면 `start_session` 을 선택한다.

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
5. `outcome = _resolve_fill(ticket)` — 타임아웃 시 `cancel_order` 호출 + 부분/0 체결
   수습 (ADR-0015). `status == "none"` → 즉시 skip, `return False` (RiskManager 미기록).
6. `entry_fill_price = buy_fill_price(signal.price, slippage_rate)` —
   `backtest.costs.buy_fill_price` 재사용.
7. `risk_manager.record_entry(symbol, entry_fill_price, outcome.filled_qty, now)` +
   `_open_lots[symbol] = _OpenLot(entry_fill_price, outcome.filled_qty)`. `status ==
   "partial"` 이면 warning 로그 (잔량 취소 완료, 체결분만 기록).

## ExitSignal 처리

1. `_open_lots[symbol]` 없으면 `RiskManager.active_positions` 에서 fallback —
   외부에서 직접 `record_entry` 한 경우(테스트·수동 시나리오) 호환. 둘 다 없으면
   `ExecutorError` (전략-Executor 동기화 위반).
2. `_with_backoff(submit_sell)` → `OrderTicket`.
3. `outcome = _resolve_fill(ticket)` — `status != "full"` 이면 `self._halt = True`
   선제 설정 + `ExecutorError` 승격 (ADR-0015 — 브로커 잔고가 일부만 감소한 상태로
   남아 다음 `reconcile()` 가 mismatch 감지까지 기다리지 않고 즉시 halt).
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

## 시장가 체결가 추정 및 부분체결 정책 (ADR-0015)

체결가는 시그널 가격(분봉 close, 참고가) 에 슬리피지 ±0.1% 를 적용해 추정한다 —
백테스트 비용 모델과 동일 가정으로, 백테스트 결과의 실전 괴리를 추적 가능한
한 가지 변수로 유지하기 위해서.

부분체결·미체결 정책 (ADR-0015 적용):
- `_resolve_fill(ticket) -> _FillOutcome` 이 `get_pending_orders()` 폴링으로 체결 상태를 확인.
- 타임아웃 시 `cancel_order(order_number)` 호출 후 잔량 취소 + 부분/0 체결 수습.
- 진입 부분체결: `filled_qty` 만 원장 기록 + warning 로그. 0 체결: skip + info 로그.
- 청산 부분/0 체결: `ExecutorError` 승격 (운영자 개입 유도 — 미청산 포지션 리스크).

모의투자 2주 운영에서 측정된 실제 슬리피지가 0.1% 와 크게 다르면 후속 PR 에서
`slippage_rate` 설정값 조정을 진행한다.

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
- 관련 테스트 파일: `tests/test_executor.py`. 카테고리: 공개 심볼 노출,
  ExecutorConfig 검증, 생성·세션, step 가드, EntrySignal 승인·거부·로그,
  ExitSignal 승인·무결성·fallback 로그, force_close, 체결 확정(_resolve_fill),
  DryRunOrderSubmitter, reconcile(일치·mismatch·critical 로그·ReconcileReport
  immutability), halt 가시성·영속성·start_session 리셋, KisClientError 백오프,
  에러 좁힘, 멀티 심볼, 분봉 idempotent, StepReport/ReconcileReport 구조,
  net_pnl 정확값 회귀, end-to-end 정상 경로(`_open_lots` hit). 절대 케이스 수는
  의도적으로 적지 않는다 — root CLAUDE.md "현재 상태" 의 총합 카운트만 정본으로
  유지.

## 소비자 참고

- **`main.py`** (Phase 3 두 번째 산출물, **완료 2026-04-21**): `APScheduler` 로
  09:00 시작·장중 `step(now)` 폴링·15:00 `force_close_all`·15:30 일일 리포트.
  `LiveOrderSubmitter` / `LiveBalanceProvider` 를 주입.
- **`monitor/notifier.py`** (Phase 3 세 번째 산출물, **완료 2026-04-21**):
  `Executor` 반환 `StepReport.entry_events` / `exit_events` 와
  `Executor.last_reconcile` 를 소비해 텔레그램 알림.
  → [src/stock_agent/monitor/CLAUDE.md](../monitor/CLAUDE.md) 참조.
- **`storage/db.py`** (Phase 3 네 번째 산출물, **완료 2026-04-22**): `EntryEvent`·`ExitEvent`·`DailySummary`
  를 SQLite(`data/trading.db`)에 영속화. `main.py` 콜백이 notifier 와 나란히 호출.
  → [src/stock_agent/storage/CLAUDE.md](../storage/CLAUDE.md) 참조.
- **드라이런 검증**: `DryRunOrderSubmitter` 를 주입하면 `Executor` 는 KIS 에
  단 한 번도 접촉하지 않는다 (paper 모드 healthcheck 와 별개의 안전 벨트).

## 범위 제외 (의도적 defer)

- **APScheduler 도입** — `step(now)` 만 노출. 외부 스케줄러 책임.
- **텔레그램 알림** — `monitor/notifier.py` 완료 (2026-04-21).
- **부분체결 잔량 취소·재발주** — `_resolve_fill` + `cancel_order` 통합으로 완료 (2026-04-22, ADR-0015).
- **KIS 체결조회 API 통합** — `PendingOrder.qty_filled` + `cancel_order` 통합으로 완료 (2026-04-22, ADR-0015). 현재 슬리피지 0.1% 추정은 유지 — 모의투자 2주 운영 후 실측값으로 재검토.
- **자동 포지션 복구** — reconcile 불일치 시 자동 보정 금지. 운영자 개입 +
  다음 세션 `start_session` 으로 리셋만 허용.
- **`config/strategy.yaml` YAML 로더** — `main.py` 착수 시 도입. 현재는
  `ExecutorConfig` 생성자 주입.
- **멀티프로세스·스레드 safe** — 단일 프로세스 전용 (broker/strategy/risk/data
  와 동일).
- **호가 단위 라운딩** — 현재 `Decimal` 원시 그대로. KRX 호가 단위 적용은
  Phase 5 재설계 범위.
