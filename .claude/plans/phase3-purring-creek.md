# Phase 3 착수 — RSIMRStrategy 모의투자 무중단 운영

## Context

2026-05-03 기준 Phase 2 PASS 공식 선언, Phase 3 진입 게이팅 해제 (ADR-0023 C1~C4 전원 통과). `main.py`/`Executor`/`monitor`/`storage` 코드 산출물은 Phase 3 첫 PR 시점부터 모두 보존 상태. 단, 두 가지 갭으로 즉시 가동 불가:

- **전략 미연결**: `main.py:83,293` 가 `ORBStrategy(StrategyConfig())` 주입. 채택 후보 `RSIMRStrategy` 미import.
- **시간 해상도 충돌**: `RSIMRStrategy` 는 **일봉 RSI(14) 평균회귀** (`backtest/rsi_mr.py` 가 매일 1건의 MinuteBar 컨테이너로 일봉을 래핑해 흘림). `main.py` cron 은 매분 `executor.step` (분봉 가정). 매분 strategy.on_bar 호출 시 RSI 가 분봉 close 로 재계산돼 백테스트 의미 무효화.
- **리스크 한도 충돌**: `RiskConfig` 기본 `max_positions=3 / position_pct=0.20 / daily_loss=-2%` vs `RSIMRConfig` `max_positions=10 / position_pct=1.0 / stop_loss=3%`. ADR-0023 결과 섹션의 "운영 한도 재정의 ADR 작성 예정" 약속 미해결.

사용자 결정 (이 세션):
1. **운영 모델 = EOD 1회 일봉 + 분봉 fill 추적**. 장 마감 후 일봉 1건/종목을 strategy 에 흘려 entry/exit signal 산출. entry 는 다음 영업일 시초가 시장가, 분봉 step 은 미체결 fill 추적 + 일중 stop_loss 모니터링 전용.
2. **ADR-0025 (RSI MR 운영 한도 재정의) Phase 3 진입 전 작성**.

목표: 10영업일 모의투자 무중단 운영 + 0 unhandled error + 모든 주문 SQLite 기록 + 텔레그램 알림 100% 수신.

## 실행 시퀀스 (PR 단위 순서)

### PR1 — ADR-0025 작성 (코드 변경 0)

`docs/adr/0025-rsi-mr-operational-risk-limits.md` 신설. 4섹션 (상태·맥락·결정·결과).

결정 항목 (ADR-0023 C4 sensitivity 기준값 채택):
- `max_positions = 10`, `position_pct = Decimal("0.1")` (종목당 10%, RiskManager 측 — RSIMRConfig 의 `position_pct=1.0` 은 잔액 전부 분배 의미라 의미 차원 다름. ADR 에 명시).
- `stop_loss_pct = Decimal("0.03")` (Strategy 측 stop_price 산출 + RiskManager 측 후처리 검증 일치).
- `daily_loss_limit_pct = Decimal("-0.02")` 유지.
- `daily_max_entries`: 신규 도입 (5 후보, ADR 에서 근거 제시).
- `force_close_at`: 일봉 전략이므로 일중 강제청산 미사용. RiskConfig 에서 옵셔널화 또는 `time(15, 35)` (EOD 트리거 직전) 마커.
- 동일 세션 재진입 차단: Strategy 가 이미 `_last_exit_date` 로 처리. RiskConfig 추가 가드 불필요.

문서 동기화 (`markdown-writer` 호출): `CLAUDE.md` "확정된 결정" 의 리스크 한도 줄 갱신, `plan.md` Phase 3 리스크 한도 섹션, `README.md` 리스크 한도 표.

### PR2 — main.py 전략 교체 (TDD RED 선행)

수정 파일:
- `src/stock_agent/main.py:83` — `from stock_agent.strategy import ORBStrategy, StrategyConfig` → `RSIMRStrategy, RSIMRConfig` 추가 (직접 import: `from stock_agent.strategy.rsi_mr import RSIMRConfig, RSIMRStrategy`).
- `src/stock_agent/main.py:293` — `strategy = ORBStrategy(StrategyConfig())` → `strategy = RSIMRStrategy(RSIMRConfig(universe=universe.symbols, ...))`. universe 는 `load_kospi200_universe(...)` 결과 활용.
- `src/stock_agent/main.py` RiskConfig 생성 (`risk_manager` 팩토리) — ADR-0025 값 주입.

TDD RED: `unit-test-writer` 서브에이전트 호출. 대상 `tests/test_main.py` (또는 전략 wiring 테스트). 실패 케이스 — `_assemble_runtime` 호출 시 strategy 인스턴스가 `RSIMRStrategy` 임을 주장.

### PR3 — EOD 일봉 트리거 + 분봉 step 분기

신규 파일:
- `src/stock_agent/execution/eod_runner.py` (가칭) — 책임:
  - `wrap_daily_as_minute_bar(daily: DailyBar) -> MinuteBar` 헬퍼. `bar_time=15:30 KST aware` 등.
  - `run_eod_signals(now, *, universe, strategy, fetch_daily) -> list[Signal]` — 일봉 fetch + strategy.on_bar loop.
- KIS 일봉 fetch 어댑터 — `KisClient` 에 메서드 신규 또는 `data/historical.py` 의 `HistoricalDataStore` 재사용 검토 (이미 일봉 캐시 존재).

`main.py` 변경:
- cron 추가: `15:35 KST` `on_eod_step` (RSI 신호 산출 + pending entry queue 적재).
- 매분 `on_step` 콜백에서 `strategy.on_bar` 호출 경로를 분기 — RSI MR 모드일 때 분봉으로 strategy 호출 안 함. 분봉은 `executor` 의 fill 추적 + 일중 stop_loss 가드만.
- `15:00 force_close_all` 정책 변경: RSI MR 은 일봉 강제청산 없으므로 cron 비활성 또는 noop.

`Executor` 변경:
- `submit_pending_entries(now)` — 다음 영업일 09:01 cron 에서 호출, 전일 EOD 신호를 시장가 발송.
- 분봉 step 에서 일중 stop_loss 발동 정책 (선택): bar.low ≤ holding.stop_price 도달 시 즉시 시장가. ADR-0025 에 정책 결정 반영.

TDD RED: `unit-test-writer` 호출. 신규 테스트 — `tests/test_eod_runner.py` (KIS 일봉 fetch 목킹), `tests/test_main_eod.py` (15:35 cron 발화 + pending entry queue 검증).

### PR4 — 모의투자 1일 dry-run + 운영 시작

검증 절차:
1. `uv run pytest -x` + `uv run ruff check src scripts tests` + `uv run black --check src scripts tests` + `uv run pyright src scripts tests` 4종 PASS.
2. `uv run python -m stock_agent.main --dry-run --once` (CLI 옵션 신설 또는 기존 활용) — 1회 cron 발화 시뮬레이션, 실주문 0.
3. paper APP_KEY + paper 도메인으로 실 1영업일 가동. SQLite `data/trading.db` append 확인, 텔레그램 4종 알림 (session_start/entry/exit/daily_summary) 수신 확인.
4. 10영업일 누적 모의투자. 일일 결과 운영자 검토.

## 핵심 파일 (수정 대상)

| 파일 | 역할 |
|---|---|
| `docs/adr/0025-rsi-mr-operational-risk-limits.md` | 신설. 결정·맥락·결과 4섹션 |
| `src/stock_agent/main.py` | 전략 교체 (line 83, 293), 15:35 cron 추가, 매분 step 분기 |
| `src/stock_agent/execution/eod_runner.py` | 신설. 일봉 → MinuteBar wrap + strategy 호출 |
| `src/stock_agent/execution/executor.py` | pending entry queue + 일중 stop_loss 가드 정책 반영 |
| `src/stock_agent/risk/manager.py` | RiskConfig 의 force_close_at 옵셔널화 (ADR-0025) |
| `CLAUDE.md` / `README.md` / `plan.md` | Phase 3 상태 + 리스크 한도 + 전략 갱신 (markdown-writer) |
| `src/stock_agent/strategy/CLAUDE.md` | Phase 3 진입 사실 갱신 (markdown-writer) |

## 재사용할 기존 자산

- `RSIMRStrategy` / `RSIMRConfig` (`src/stock_agent/strategy/rsi_mr.py`) — Strategy Protocol 준수, 별도 변경 불필요.
- `backtest/rsi_mr.py:203` `loader.stream` 의 일봉 → MinuteBar wrap 패턴 — `wrap_daily_as_minute_bar` 의 reference 구현.
- `HistoricalDataStore.fetch_daily_ohlcv` (`data/historical.py`) — 일봉 캐시 + pykrx 폴백. EOD 시점 fetch 에 재사용.
- `Executor` (`execution/executor.py`) — Protocol 기반이라 RSI MR 직접 주입 가능. pending entry queue 만 신규.
- `TelegramNotifier` / `SqliteTradingRecorder` — 변경 0.
- `BusinessDayCalendar` (`data/calendar.py`, ADR-0018) — 다음 영업일 계산 (EOD entry 대기).

## 검증 (end-to-end)

1. **단위 테스트**: `uv run pytest -x tests/test_eod_runner.py tests/test_main_eod.py tests/test_main.py` 신규 케이스 GREEN.
2. **정적 검사 4종 병렬**: `pytest` + `ruff check` + `black --check` + `pyright` (`src scripts tests` 범위) 모두 PASS. CI 게이트와 동일.
3. **dry-run 1회**: `python -m stock_agent.main --dry-run --once` — KIS API 호출 0, 텔레그램 발송 0, SQLite append 0. 로그에서 신호 산출만 확인.
4. **모의투자 1일**: paper 키로 1영업일 가동. 운영자가 확인:
   - SQLite `data/trading.db` 에 주문·체결·일일 PnL append.
   - 텔레그램 알림 4종 (`session_start` 1, `entry` n, `exit` n, `daily_summary` 1).
   - 0 unhandled exception (loguru ERROR 0).
5. **모의투자 10영업일 누적**: Phase 3 PASS 조건 (plan.md). 운영자가 일일 회고 후 Phase 3 PASS 라벨 결정.

## 위험 / 고지

- 운영 EOD 로직은 백테스트(전 종목 일봉 1건 동시 진입) 와 다름 — 다음 영업일 시초가 lag 1일 발생. 백테스트 결과 (MDD -8.17% C1 기준) 와 운영 결과는 직접 등가 아님. ADR-0025 에 명시 + 10영업일 운영 결과로 검증.
- KIS paper 도메인은 시세 API 미제공 → 시세는 실전 키, 주문은 paper 키 (이미 운영 중인 하이브리드). EOD 일봉 fetch 도 실전 키 경로 통과.
- "수익 보장" 표현 금지. 모의투자 → 페이퍼 → 실전 단계 보존.
