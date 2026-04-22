---
date: 2026-04-22
status: 승인됨
deciders: donggyu
related: [0013-sqlite-trading-ledger.md, 0011-apscheduler-adoption-single-process.md, 0012-monitor-notifier-design.md, 0008-single-process-only.md, 0003-runtime-error-propagation.md]
---

# ADR-0014: 세션 중간 재기동 시 오픈 포지션·RiskManager 상태 DB 복원 경로

## 상태

승인됨 — 2026-04-22.

## 맥락

Phase 3 PASS 기준(plan.md) 은 "모의투자 연속 10영업일 무중단 + 0 unhandled error + 모든 주문이 SQLite 기록 + 텔레그램 알림 100% 수신" 이다. `storage/db.py` (ADR-0013) 가 체결·PnL 을 `data/trading.db` 에 append-only 로 기록하지만, **`main.py` 재기동 시 당일 상태(오픈 포지션·일일 실현 PnL·진입 횟수·청산 완료 심볼)를 DB 에서 복원하는 경로가 없었다** (Issue #33).

운영 현장에서 아래 이벤트 하나만 발생해도 장중 `Runtime` 인메모리 상태(`RiskManager.active_positions` / `RiskManager.daily_realized_pnl_krw` / `RiskManager.entries_today` / `ORBStrategy._states` / `Executor._open_lots`) 가 유실된다.

- 맥북 절전/화면보호기 → 네트워크 리셋.
- WebSocket 연결 드랍 후 프로세스 재시작.
- OS 업데이트 재부팅.

상태 유실 시 나타나는 구체 위험:

| 유실 상태 | 재진입 시 발생 위험 |
|---|---|
| `active_positions` 비어 있음 | RiskManager `duplicate_symbol` 게이트가 무효 — 같은 종목 **중복 진입**. |
| `entries_today = 0` | 일일 10회 진입 한도 (`daily_max_entries`) 가 리셋되어 추가 진입 발생. |
| `daily_realized_pnl_krw = 0` | -2% 서킷브레이커(`halted_daily_loss`) 초기화 → 한도 초과 손실로 진행. |
| `ORBStrategy._states` 비어 있음 | 이미 closed 처리된 심볼이 `flat` 상태로 리셋돼 재돌파에 **재진입 발생**. |
| `_open_lots` 비어 있음 | ExitSignal 도달 시 `_handle_exit` 의 `RiskManager.active_positions` 폴백 경로로 빠지며 PnL 계산 부정확. |

"연속 10영업일 무중단" PASS 기준 자체가 재기동 한 번으로 카운트 리셋되기 때문에 복원 경로 없이는 Phase 3 PASS 선언이 **구조적으로 불가능**했다.

ADR-0013 의 `orders` 테이블은 이미 복원에 필요한 원시 정보를 모두 보유(`session_date`, `symbol`, `side CHECK IN ('buy','sell')`, `qty`, `fill_price TEXT`, `net_pnl_krw`, `filled_at`). 추가 스키마 변경 없이 **재생(replay)** 만으로 상태 복원 가능하다는 점이 본 ADR 설계의 출발점.

검토한 대안:

- **별도 `session_snapshot` 테이블 주기 기록**: 01~15분 주기로 active_positions/entries/pnl 을 시리얼라이즈. 구현 간단하나 동시성·락 경합 + append-only 원장 철학 위배 + 마지막 스냅샷 이후 이벤트는 여전히 유실.
- **재시작 시 KIS 잔고 조회 + 보수적 halt**: KIS `get_balance()` 로 실제 holdings 를 가져와 `_halt=True` 로만 표시. DB 를 안 봐도 되지만 `entries_today` / `daily_realized_pnl_krw` / 청산 완료 심볼 정보가 **전혀 복원되지 않아** 재진입 한도·서킷브레이커·1일 1심볼 계약이 모두 해제. KIS 잔고는 본 ADR 도입 후 `Executor.reconcile()` 단계에서 여전히 교차 검증 자산.
- **`Executor.restore_session` 이 `record_entry`/`record_exit` 를 재생**: 기존 `start_session` 후 record 메서드들을 DB 순서대로 재호출. 구현 대칭성이 크지만 (a) executor 가 PnL 을 "다시" 계산해야 하고 (b) 외부에서 manually `record_entry` 했던 케이스(테스트·수동 시나리오) 와의 구분이 어려워 제거.
- **ORBStrategy 상태 복원 제외**: RiskManager 만 복원하고 ORB 상태는 그대로 빈 상태로 둠. 이 경우 on_bar 가 돌아갈 때 `position_state='flat'` → OR 구간 지났으면 `or_missing_warned` 로그 1회 후 당일 포기 — 기존 오픈 포지션의 손절/익절 시그널이 생성되지 않아 force_close 까지 강제청산 불가. 안전 동작이지만 "진입은 했으나 전략 기반 청산은 불가" 상태가 되므로 거부.

## 결정

1. **모듈 경계 확장** — 4개 레이어에 공개 API 추가:
   - `storage/db.py` — `OpenPositionRow` (frozen DTO), `DailyPnlSnapshot` (`has_state` 프로퍼티 포함), `TradingRecorder.load_open_positions(session_date)` / `load_daily_pnl(session_date)` (Protocol + Sqlite/Null 구현).
   - `risk/manager.py` — `RiskManager.restore_session(session_date, starting_capital_krw, *, open_positions: Sequence[PositionRecord], entries_today, daily_realized_pnl_krw)`.
   - `strategy/orb.py` — `ORBStrategy.restore_long_position(symbol, entry_price, entry_ts)` + `mark_session_closed(symbol, session_date)`.
   - `execution/executor.py` — `OpenPositionInput` Protocol (storage 역방향 import 회피용 구조적 타입) + `Executor.restore_session(...)`.

2. **재생 방식 — `orders` 테이블 replay** — `SqliteTradingRecorder.load_open_positions` 는 `session_date` 의 buy/sell 을 `filled_at ASC, rowid ASC` 로 정렬해 순회하며 buy 는 map 에 추가, sell 은 map 에서 제거. 종료 시 남은 entries 가 오픈 포지션. `load_daily_pnl` 은 같은 테이블에서 buy 개수(`entries_today`), sell 의 `net_pnl_krw` 합계(`realized_pnl_krw`), sell symbol 집합(`closed_symbols`, 정렬 tuple) 을 반환. 별도 스냅샷 테이블·마이그레이션 불필요 — 스키마 v1 유지.

3. **재기동 감지 주체는 `main._on_session_start`** — 매일 09:00 KST 세션 시작 콜백이 `runtime.recorder.load_open_positions(today)` 와 `load_daily_pnl(today)` 를 호출하고 `bool(open_positions) or snapshot.has_state` 로 판정. True 면 `Executor.restore_session(...)`, False 면 기존 `Executor.start_session(...)`. 분기 로직은 이 한 곳에만 존재. `session_status.started` 갱신·`logger.info restart={r}` 형식은 양 분기 공통.

4. **`DailyPnlSnapshot.has_state` 프로퍼티** — `entries_today > 0 or closed_symbols or realized_pnl_krw != 0` 로 재기동 감지를 한 줄로 표현. Python truthiness 에 의존하지 않는 명시 프로퍼티 — MagicMock 으로 spec 된 테스트 더블에서도 명확히 제어 가능.

5. **ORBStrategy 복원 정책** — open_positions 에 포함된 심볼은 `restore_long_position` 으로 `position_state='long'` + `stop_price`/`take_price` 를 **현재 `StrategyConfig` 로 재계산**. `or_confirmed=True` 로 표시해 flat 재진입 경로 차단. 청산 완료 심볼(`closed_symbols \ open_symbols`) 은 `mark_session_closed` 로 `position_state='closed'` — 재돌파에도 재진입 금지 (1일 1심볼 1회 진입 계약 보존). `or_high`/`or_low` 는 복원하지 않는다 — long 상태에서는 참조되지 않고, closed 상태에서는 재진입 분기를 타지 않기 때문. `last_close` 도 복원하지 않아 force_close 강제청산 시 `entry_price` 폴백 warning 이 발생하는데, 이는 "재기동 직후 force_close 시각까지 분봉이 한 번도 안 들어온 예외 경로" 가시성 유지 목적.

6. **`Executor.restore_session` 흐름** — (1) `OpenPositionRow` → `PositionRecord` 변환 후 `RiskManager.restore_session`. (2) `_open_lots` 를 open_positions 로 재구성 (`_OpenLot(entry_price, qty)`). (3) `ORBStrategy.restore_long_position` 당 심볼별 호출. (4) `_halt`/`_last_reconcile` 리셋, `_last_processed_bar_time` 클리어. (5) `closed_symbols ∩ open_symbols` 겹치면 warning + open 우선(정상 경로 밖이지만 DB 오염 방어). (6) 그 외 `closed_symbols` 에 `mark_session_closed`. `start_session` 도 일관성을 위해 `_last_reconcile = None` 리셋을 추가 — 이전엔 리셋이 없어 전날 잔여 값이 남을 수 있었음.

7. **`RiskManager.restore_session` 입력 검증** — `starting_capital_krw ≤ 0` / `entries_today < 0` / `entries_today < len(open_positions)` / open_positions 심볼 중복·포맷 오류는 전부 `RuntimeError` 전파 (ADR-0003). `daily_realized_pnl_krw ≤ -starting_capital × daily_loss_limit_pct` 면 복원 시점에 즉시 `_halt_logged = True` 로 세팅 — 과거 세션에서 이미 halt 로그가 방출됐을 수 있어 중복 방출 방지.

8. **`OpenPositionInput` Protocol 로 순환 의존 회피** — `storage/db.py` 가 이미 `from stock_agent.execution import EntryEvent, ExitEvent` 하므로 execution → storage 역방향 import 는 순환. 대신 `execution/executor.py` 에 `OpenPositionInput` Protocol(5개 @property) 을 선언해 구조적 타입으로 수용. `storage.OpenPositionRow` 가 자연스럽게 만족.

9. **실패 정책 일관 — `load_*` 도 silent fail** — `SqliteTradingRecorder.load_open_positions` / `load_daily_pnl` 는 `sqlite3.Error`·`DecimalException`·`ValueError`·`TypeError` 에 대해 빈 결과를 반환 + `_consecutive_failures[op]` 증가. 메서드별 독립 카운터·`_critical_emitted` dedupe 는 기존 `record_*` 과 동일(ADR-0013 결정 6 연장). 복원 경로 전체가 예외로 죽지 않도록 — `main._on_session_start` 는 silent 빈 결과를 "신규 세션" 으로 해석해 정상 진행.

10. **`NullTradingRecorder` 대칭** — `load_open_positions` → `()`, `load_daily_pnl` → `DailyPnlSnapshot(session_date=입력, realized=0, entries=0, closed=())`. `_default_recorder_factory` 폴백 경로에서도 신규 세션 분기가 안정적으로 동작.

## 결과

**긍정**

- Phase 3 PASS 조건 "연속 10영업일 무중단" 이 **절전/재부팅에 의해 카운트 리셋되지 않는다**. 동일 `session_date` 로 재기동해도 active_positions·entries_today·realized_pnl·closed_symbols 가 DB 재생으로 정확히 복원.
- 중복 진입·서킷브레이커 우회·포지션 유실 3종 장애 경로 차단.
- 추가 스키마·테이블·마이그레이션 불필요 — 기존 `orders` 테이블 replay 만으로 구현.
- Protocol 기반 구조적 타입(`OpenPositionInput`) 덕분에 execution 가 storage 를 역방향으로 참조하지 않고 순환 의존 회피.
- `main._on_session_start` 한 곳만 분기를 알고 나머지 레이어는 "`restore_session` 이든 `start_session` 이든 진입점은 대칭" 으로 취급 — 테스트 주입 용이.

**부정**

- `restore_long_position` 이 `stop_price`/`take_price` 를 **현재 `StrategyConfig` 로 재계산**하므로, 재기동 전 실행과 config(손절·익절 비율) 가 달랐다면 청산 수준이 이동한다. 현재 프로젝트는 `config/strategy.yaml` 미도입으로 변경 빈도가 낮아 허용 범위지만, Phase 3 `main.py` YAML 로더 도입 시 config 버전 기록·일치 검증이 필요할 수 있음(후속 이슈로 이관).
- `last_close` 미복원이라 재기동 직후 force_close 시각까지 분봉이 한 번도 들어오지 않으면 `entry_price` 폴백 warning — 실운영 조건에서는 거의 발생하지 않지만 가시성 로그 증가.
- 재기동 시 `DailyPnlSnapshot.has_state=True` 진입 경로가 추가되어 `main._on_session_start` 의 테스트 복잡도가 증가. 기존 `MagicMock(spec=TradingRecorder)` 기반 테스트가 `load_daily_pnl` / `load_open_positions` 기본 반환값을 명시 configure 해야 하는 부담. `_make_runtime` 헬퍼에서 기본 stub 을 주입해 최소화.

**중립**

- 복원 경로는 `RiskManager` 가 PnL 계산을 **재실행하지 않는다** — `realized_pnl_krw` 합계는 storage 에 기록된 sell 행의 `net_pnl_krw` 합. ADR-0013 결정 3 의 `net_pnl_krw INTEGER NULL` 을 진실의 원천으로 신뢰.
- Phase 4 실전 전환 시에는 KIS 체결조회 API(`broker/` 확장, 별도 PR) 로 `net_pnl_krw` 정확도를 실체결가 기반으로 올리면 자동으로 복원 PnL 정확도도 향상.
- 멀티프로세스/스레드 safe 는 여전히 미제공(ADR-0008) — 단일 프로세스 재기동 시나리오만 대응.

## 추적

- 코드:
  - `src/stock_agent/storage/db.py` — `OpenPositionRow`, `DailyPnlSnapshot`, `TradingRecorder.load_*`, `SqliteTradingRecorder.load_*`, `NullTradingRecorder.load_*`, `_OP_LOAD_*` 카운터 키.
  - `src/stock_agent/risk/manager.py` — `RiskManager.restore_session`.
  - `src/stock_agent/strategy/orb.py` — `ORBStrategy.restore_long_position`, `ORBStrategy.mark_session_closed`.
  - `src/stock_agent/execution/executor.py` — `OpenPositionInput` Protocol, `Executor.restore_session`, `Executor.start_session` 의 `_last_reconcile` 리셋 추가.
  - `src/stock_agent/main.py` — `_on_session_start` 재기동 감지 분기.
- 테스트:
  - `tests/test_storage_db.py` — `TestLoadOpenPositions`, `TestLoadDailyPnl`, `TestOpenPositionRowDTO`, `TestDailyPnlSnapshotDTO`, `TestNullTradingRecorderLoadMethods`.
  - `tests/test_risk_manager.py` — `TestRestoreSession`.
  - `tests/test_strategy_orb.py` — `TestRestoreLongPosition`, `TestMarkSessionClosed`.
  - `tests/test_executor.py` — `TestExecutorRestoreSession`, `TestExecutorStartSessionResetsLastReconcile`.
  - `tests/test_main.py` — `TestOnSessionStartRestartDetection`.
- 문서: 각 모듈 `CLAUDE.md` (storage/risk/strategy/execution), root [CLAUDE.md](../../CLAUDE.md) "현재 상태" 섹션, [docs/architecture.md](../architecture.md).
- 도입 이슈: [#33](https://github.com/donggyushin/korean-stock-trading-system/issues/33).
- 관련 ADR: [0013](./0013-sqlite-trading-ledger.md) 원장 기반, [0011](./0011-apscheduler-adoption-single-process.md) `_on_session_start` 콜백 경로, [0008](./0008-single-process-only.md) 단일 프로세스 한계 재확인.
