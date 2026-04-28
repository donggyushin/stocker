# Phase 진행 이력

stock-agent 프로젝트의 Phase 별 산출물·결정·테스트 카운트 변화 이력. root `CLAUDE.md` 의 "현재 상태" 섹션이 비대해지는 것을 막기 위해 분리한 역사 기록 문서이다.

- 본 문서는 **사후 수정 금지** (ADR 와 동일 원칙). 새 사실은 항상 추가만 한다.
- 실행 가능한 명령·현재 결정·리스크 한도는 root `CLAUDE.md` / `README.md` / `plan.md` 의 정본을 참조한다.
- 모듈별 세부 (공개 API·테스트 정책·에러 정책) 는 해당 폴더의 `CLAUDE.md` 에 둔다.

## Phase 0 완료 (2026-04-19)

- `scripts/healthcheck.py` 3종 통과: KIS 모의투자 토큰 발급 OK, 모의 계좌 잔고 조회 OK (시드 10,000,000원), 텔레그램 "hello" 수신 OK
- 신규 파일: `.python-version`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `.env.example`, `src/stock_agent/__init__.py`, `src/stock_agent/config.py`, `scripts/healthcheck.py`
- 의존성 확정: `python-kis 2.1.6`, `python-telegram-bot 22.7`, `pydantic 2.13`, `pydantic-settings 2.13`, `loguru 0.7` / dev: `ruff 0.15`, `black 26.3`, `pytest 9.0`, `pytest-mock 3.15`, `pre-commit 4.5`
- `python-kis` paper-only 초기화 우회: 모의 키를 실전 슬롯과 모의 슬롯 양쪽에 동일 입력 → `PyKis.virtual = True`로 모든 요청이 모의 도메인으로만 라우팅됨. Phase 4 실전 전환 시 실전 APP_KEY/SECRET 별도 발급 후 슬롯 분리.
- 운영 메모: KIS Developers에서 "모의투자계좌 API 신청"을 MTS의 "상시 모의투자 참가신청"과 별도로 완료해야 모의 키 발급 가능 (미신청 시 `EGW2004` 에러). 토큰 첫 발급 시 레이트 리밋 경고 2회 후 자동 재시도 통과 — 정상 동작 범위.
- GitHub Actions CI 도입 (`.github/workflows/ci.yml`): PR 및 main push 시 `uv sync --frozen` → ruff/black 정적 분석 → pytest 자동 실행. 첫 실행 12초, 10/10 통과 (PR #1 검증).
- main 브랜치 보호 적용: required status check `Lint, format, test` (CI job), `strict=true`, force push/삭제 금지.

## Phase 1 PASS (코드·테스트 레벨) — 브로커 래퍼 + 데이터 파이프라인 (2026-04-19 완료 선언)

- `src/stock_agent/broker/` 패키지 신설 — `KisClient` + DTO 정규화. 모듈 세부(공개 API, 에러 정책, 데이터 무결성 가드, 테스트 정책)는 [src/stock_agent/broker/CLAUDE.md](../src/stock_agent/broker/CLAUDE.md) 참조.
- `scripts/healthcheck.py` — `KisClient` 컨텍스트 매니저로 전환, 예수금 10,000,000원 조회 회귀 없음.
- `src/stock_agent/broker/rate_limiter.py` — 완료. 주문 경로 전용 `OrderRateLimiter`(기본 2 req/s + 최소 간격 350 ms, 단일 프로세스). 조회 경로는 python-kis 내장에 위임.
- `src/stock_agent/data/` 패키지 신설 — `HistoricalDataStore` + SQLite 캐시(일봉). 모듈 세부(공개 API, 캐시 정책, 에러 정책, 테스트 정책)는 [src/stock_agent/data/CLAUDE.md](../src/stock_agent/data/CLAUDE.md) 참조.
- `src/stock_agent/data/universe.py` + `config/universe.yaml` — 완료. KOSPI 200 유니버스 YAML 하드코딩. pykrx 지수 API(`get_index_portfolio_deposit_file` 등) 와 KIS Developers 모두 인덱스 구성종목 API 미제공으로 수동 관리. KOSPI 200 정기변경(연 2회 — 6월·12월 선·옵 동시만기일 익영업일 기준) 때 운영자 갱신. 현재 KRX 정보데이터시스템 [11006] 기준 199/200 반영 (임시 가상 코드 1건 제외). 정식 6자리 티커 발급 시 다음 갱신에 추가.
- `HistoricalDataStore`는 `get_kospi200_constituents` 제거로 `fetch_daily_ohlcv` 전용으로 축소. SQLite 스키마 v3 (v2→v3 자동 마이그레이션, `daily_bars` 보존).
- `src/stock_agent/data/realtime.py` — 완료. `RealtimeDataStore` + `TickQuote`·`MinuteBar`·`RealtimeDataError` DTO. WebSocket 우선 + REST 폴링 fallback. **실전(live) 키 전용** — `settings.has_live_keys=False` 이면 `RealtimeDataError` fail-fast. 실전 키 PyKis 인스턴스에 `install_order_block_guard` 설치(`/trading/order*` 도메인 무관 차단). 분봉 집계(분 경계 OHLC 누적)·스레드 안전(`threading.Lock`), volume Phase 1 에서 0 고정(Phase 3 실사). `scripts/healthcheck.py` 4번째 체크(`check_realtime_price`, 삼성전자 005930) — 실전 키 미설정 시 SKIP. 모듈 세부는 [src/stock_agent/data/CLAUDE.md](../src/stock_agent/data/CLAUDE.md) 참조.
- `src/stock_agent/config.py` — `Settings` 에 `kis_live_app_key`, `kis_live_app_secret`, `kis_live_account_no` 선택 필드 추가 (HTS_ID 는 paper/실전 공유라 별도 필드 없음, 계좌번호는 paper/실전이 달라 별도 필드 필수). 3종 all-or-none + 길이·패턴 검증(model_validator). `has_live_keys` 프로퍼티 신규.
- `src/stock_agent/safety.py` — `install_order_block_guard(kis)` 신규. `/trading/order` 부분 문자열 매칭 시 도메인 무관 차단. `install_paper_mode_guard` 는 기존 역할(paper 키 KisClient 보호) 유지.
- PR #7 Critical 피드백 반영: 가드 중복 설치 방어(`GUARD_MARKER_ATTR` 재설치 거부), 폴링 연속 실패 경보(`polling_consecutive_failures` 공개 프로퍼티), docstring 정정 (3건).
- pytest **131건 green** (test_config 11 + test_kis_client 15 + test_safety 23 + test_rate_limiter 18 + test_historical 14 + test_universe 11 + test_realtime 28 + 기타 회귀 없음).
- 의존성 추가: `pykrx 1.2.7` (+ transitive: pandas, numpy, matplotlib 등), `pyyaml 6.0.3`.
- **미완료 조건**: 장중 실시간 시세 수신 end-to-end 확인(실전 키 + IP 화이트리스트 + 평일 장중 틱 수신)은 **Phase 3 착수 전제**로 이관. PASS 선언은 코드·테스트 레벨 기준.

## Phase 2 진행 — ORB 전략 + 리스크 매니저 + 백테스트 엔진 + CSV 분봉 어댑터 + 민감도 그리드 + backtest CLI + KIS 분봉 어댑터 (2026-04-20~22)

- `src/stock_agent/strategy/` 패키지 신설 — `ORBStrategy` + `StrategyConfig` + `Strategy` Protocol + `EntrySignal`/`ExitSignal` DTO. 모듈 세부는 [src/stock_agent/strategy/CLAUDE.md](../src/stock_agent/strategy/CLAUDE.md) 참조.
- 설계 결정: 진입은 분봉 close 기준 OR-High strict 상향 돌파, 동일 분봉 손절·익절 동시 성립 시 손절 우선, 1일 1회 진입, `StrategyConfig` 생성자 주입(YAML 미도입), `Strategy` Protocol 최소, 세션 경계는 `bar.bar_time.date()` 기반 자동 리셋.
- `src/stock_agent/risk/` 패키지 신설 — `RiskManager` + `RiskConfig` + `RiskDecision` + `PositionRecord` + `RejectReason` + `RiskManagerError`. 모듈 세부는 [src/stock_agent/risk/CLAUDE.md](../src/stock_agent/risk/CLAUDE.md) 참조.
- 리스크 매니저 설계 결정: 포지션 사이징(세션 자본 × 20% / 참고가 floor), 진입 게이팅 6단계 판정 순서 고정, 서킷브레이커(`daily_realized_pnl_krw ≤ -starting_capital × 2%`), 세션 단위 인메모리 상태, 외부 I/O 없음, `datetime.now()` 미사용.
- `src/stock_agent/backtest/` 패키지 신설 — `BacktestEngine` + `BacktestConfig` + `BacktestResult` + `BacktestMetrics` + `TradeRecord` + `DailyEquity` + `BarLoader` Protocol + `InMemoryBarLoader`. 모듈 세부는 [src/stock_agent/backtest/CLAUDE.md](../src/stock_agent/backtest/CLAUDE.md) 참조.
- 백테스트 엔진 핵심 결정: `backtesting.py` 라이브러리 폐기 — 다중종목·RiskManager 게이팅(동시 3종목 한도·서킷브레이커·일일 진입 횟수 한도) 표현 불가, AGPL 라이센스 부담. 자체 시뮬레이션 루프로 `ORBStrategy.on_bar/on_time` + `RiskManager` 호출 — 실전 코드와 동일 인터페이스 공유. 비용 계약: 슬리피지 0.1% 시장가 불리, 수수료 0.015%(매수·매도 대칭), 거래세 0.18%(매도만). phantom_long 처리: `ORBStrategy._enter_long` 이 EntrySignal 반환 전 자체 상태 전이 → RiskManager 거부 시 후속 ExitSignal 을 `phantom_longs: set[str]` 으로 흡수(debug 로그). 외부 I/O 0, 의존성 추가 0.
- `src/stock_agent/data/minute_csv.py` 패키지 편입 — `MinuteCsvBarLoader` + `MinuteCsvLoadError` 공개. 레이아웃 `{csv_dir}/{symbol}.csv`, 헤더 `bar_time,open,high,low,close,volume`. bar_time naive KST 파싱·오프셋 포함 거부, Decimal 가격 파싱, OHLC 일관성 검증, 분 경계 강제, 단조증가+중복 금지, 누락 파일 fail-fast. 여러 심볼은 `heapq.merge` 로 `(bar_time, symbol)` 정렬 스트리밍. stdlib 전용, 추가 의존성 0. 모듈 세부는 [src/stock_agent/data/CLAUDE.md](../src/stock_agent/data/CLAUDE.md) 참조.
- `src/stock_agent/backtest/sensitivity.py` + `scripts/sensitivity.py` — 완료 2026-04-20. `ParameterAxis`·`SensitivityGrid`·`SensitivityRow`·`run_sensitivity`·`render_markdown_table`·`write_csv`·`default_grid` 공개 (backtest `__init__` 재노출). 기본 그리드 `or_end` 2종 × `stop_loss_pct` 4종 × `take_profit_pct` 4종 = 32 조합, 현재 운영 기본값 포함. 파라미터 이름 공간 `strategy.*`·`risk.*`·`engine.*`. CLI: `uv run python scripts/sensitivity.py --csv-dir ... --from ... --to ...`. 외부 네트워크·KIS 접촉 없음, 의존성 추가 0. 민감도 리포트는 sanity check 용도이며 walk-forward 검증을 대체하지 않는다. PR #12 리뷰 반영: `SensitivityRow.params` 를 `tuple[tuple[str, Any], ...]` 로 변경해 frozen 계약 회복, `metrics: BacktestMetrics` 중첩으로 엔진 진화 자동 추종, `params_dict()` 편의 메서드 추가. `scripts/sensitivity.py` exit code 규약: 2 입력·설정 오류, 3 I/O 오류. `BarLoader` Protocol 재호출 안전성 계약 명시. 모듈 세부는 [src/stock_agent/backtest/CLAUDE.md](../src/stock_agent/backtest/CLAUDE.md) 참조.
- `scripts/backtest.py` — 완료 2026-04-20. `MinuteCsvBarLoader` + `BacktestEngine` 1회 실행 → Markdown·메트릭 CSV·체결 CSV 3종 산출. `--csv-dir` (required), `--from`/`--to` (required, `date.fromisoformat`), `--symbols`(default 유니버스 전체), `--starting-capital`(default 1,000,000), `--output-markdown`/`--output-csv`/`--output-trades-csv`. PASS 판정: 낙폭 절대값 15% 미만일 때 PASS (`mdd > Decimal("-0.15")` 이면 PASS — 경계 -15% 정확값은 FAIL). **exit code 에는 반영 안 함** — 운영자 수동 검토 보존, CI 자동 pass/fail 금지. exit code 규약: `0` 정상, `2` `MinuteCsvLoadError`/`KisMinuteBarLoadError`/`UniverseLoadError`/`RuntimeError`, `3` `OSError` (`scripts/sensitivity.py` 도 동일 계약 — Issue #65 로 `UniverseLoadError` 분기 추가). 외부 네트워크·KIS 접촉 0, 의존성 추가 0. 테스트: `tests/test_backtest_cli.py` 65건 + `tests/test_sensitivity_cli.py` 7건.
- pytest **245 → 324 → 384 → 464 → 477 → 539 → 542건 green** (기존 539 + verdict 경계값 보강 2건 + UniverseLoadError 회귀 1건). 회귀 없음. 의존성 추가 없음.

## Phase 2 일곱 번째 산출물 — KIS 과거 분봉 API 어댑터 (ADR-0016) (2026-04-22)

- `src/stock_agent/data/kis_minute_bars.py` 신설 — `KisMinuteBarLoader` + `KisMinuteBarLoadError`. `BarLoader` Protocol 준수. `data/__init__.py` 에 두 심볼 공개.
- KIS API: `/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` (국내주식-213), `api="FHKST03010230"`. python-kis 2.1.6 미래핑 → `kis.fetch()` 로우레벨 직접 호출. 실전(live) 키 전용 — `has_live_keys=False` 이면 생성자에서 `KisMinuteBarLoadError`. PyKis 생성 직후 `install_order_block_guard` 설치.
- 캐시: `data/minute_bars.db` 별도 파일. 스키마 v1 — `minute_bars(symbol, bar_time, open, high, low, close, volume, PRIMARY KEY(symbol, bar_time))` + `schema_version`. `data/stock_agent.db`·`data/trading.db` 와 생명주기 독립.
- 페이지네이션: 120건 역방향 커서 + 1분 감소, 종료 조건 `len(rows) < 120` 또는 `min_time <= "090000"`.
- 레이트 리밋 재시도: `EGW00201` → `sleep(61.0)` 후 최대 3회 재시도.
- **제약 및 완화**: KIS 서버 최대 1년 분봉 보관. Phase 2 PASS 기준이 1년 표본으로 완화됨(ADR-0017) — `--loader=kis` 경로로 즉시 실행 가능. walk-forward·다년 표본은 Phase 5 유예.
- CLI: `scripts/backtest.py` + `scripts/sensitivity.py` 에 `--loader={csv,kis}` 옵션 추가 (default `"csv"`). `--csv-dir` 는 `--loader=csv` 시만 필수.
- 결정 (ADR-0016): 캐시 별도 파일 분리·`kis.fetch()` 로우레벨 직접 호출·후속 PR 에서 백필 스크립트.
- pytest `tests/test_kis_minute_bar_loader.py` 39건 신규. 회귀 0건. 의존성 추가 없음.

## Phase 3 착수 전제 통과 (2026-04-21)

실전 시세 전용 APP_KEY 3종 발급·IP 화이트리스트 등록·평일 장중 healthcheck.py 4종 그린(WebSocket 체결 수신 OK).

## Phase 3 첫 산출물 — Executor 단독 (2026-04-21)

- `src/stock_agent/execution/` 패키지 신설 — `Executor` + `ExecutorConfig` + Protocol 3종 (`OrderSubmitter`/`BalanceProvider`/`BarSource`) + 어댑터 3종 (`LiveOrderSubmitter`/`LiveBalanceProvider`/`DryRunOrderSubmitter`) + `StepReport`/`ReconcileReport` DTO + `ExecutorError`. 모듈 세부는 [src/stock_agent/execution/CLAUDE.md](../src/stock_agent/execution/CLAUDE.md) 참조.
- 핵심 결정: `KisClient` 직접 의존 금지 → Protocol 의존성 역전. 드라이런 모드는 `DryRunOrderSubmitter` 주입만으로 KIS 접촉 0. `backtest/costs.py` 비용 산식 그대로 재사용해 실전·시뮬레이션 비용 가정 단일 소스.
- pytest **542 → 605건 green** (`tests/test_executor.py` 63건 신규). 회귀 0건. 의존성 추가 없음.

## Phase 3 두 번째 산출물 — main.py + APScheduler 통합 (2026-04-21)

- `src/stock_agent/main.py` 신설 — `BlockingScheduler(timezone='Asia/Seoul')` + 4종 cron job + `Runtime` 조립 컨테이너 + `build_runtime` + CLI 인자(`--dry-run`, `--starting-capital`, `--universe-path`, `--log-dir`). 공개 심볼: `EXIT_OK/EXIT_UNEXPECTED/EXIT_INPUT_ERROR/EXIT_IO_ERROR`, `Runtime`, `SessionStatus`, `build_runtime`, `_parse_args`, `_install_jobs`, `_on_session_start`, `_on_step`, `_on_force_close`, `_on_daily_report`, `_graceful_shutdown`, `main`, `KST`.
- 스케줄: 09:00 session_start, 매분 00s(9~14시) step, 15:00 force_close, 15:30 daily_report. 모두 `day_of_week='mon-fri'`, `timezone='Asia/Seoul'`.
- 드라이런: `--dry-run` 플래그 → `DryRunOrderSubmitter` 주입, KIS 주문 접촉 0. 분기 로직은 `_build_order_submitter` 한 곳만.
- 예외 정책: 콜백 4종 모두 예외 re-raise 안 함(스케줄러 연속성 보장). `on_force_close` 실패만 `logger.critical`. exit code: 0 정상 / 1 예기치 않은 예외 / 2 입력·설정 오류 / 3 I/O 오류.
- 리소스 정리: SIGINT/SIGTERM → `_graceful_shutdown` → `signal.signal(SIG_DFL)` 재진입 가드 → `scheduler.shutdown(wait=False)` → `realtime_store.close()` → `kis_client.close()`. `finally` 블록에서도 중복 호출(멱등).
- **PR #17 리뷰 반영 패치 (2026-04-21)** — silent failure 루프 차단(`SessionStatus` 공개, `_on_step` 에서 세션 미시작 감지 → warning 1회만 남기고 skip, dedupe 플래그) · 세션 자본 기준 `balance.total` → `balance.withdrawable` 교정(RiskManager withdrawable 사이징과 계약 정합) · `Runtime.risk_manager` 공개 경로화(`_on_daily_report` 가 Executor private `_risk_manager` 의존 제거) · `_graceful_shutdown` SIGINT/SIGTERM SIG_DFL 재진입 가드 · `main()` `get_settings` except 좁힘(`ValidationError`+`OSError`+`RuntimeError` 만 catch, `ImportError` 등 프로그래밍 오류는 전파) · 모듈 docstring 스케줄 표 정리 + `build_runtime` Raises `OSError` 추가 + 콜백 docstring ADR-0011 참조.
- pytest **605 → 681건 green** (`tests/test_main.py` 47건 신규 + 리뷰 반영 29건 추가·보강). 회귀 0건. 의존성 추가: `apscheduler 3.11.2` + transitive `tzlocal 5.3.1`.

## Phase 3 세 번째 산출물 — monitor/notifier.py (텔레그램 알림) (2026-04-21)

- `src/stock_agent/monitor/` 패키지 신설 — `Notifier` Protocol + `TelegramNotifier` + `NullNotifier` + `ErrorEvent`/`DailySummary` DTO. 모듈 세부는 [src/stock_agent/monitor/CLAUDE.md](../src/stock_agent/monitor/CLAUDE.md) 참조.
- 핵심 결정 (ADR-0012): Protocol 의존성 역전 유지(Executor 는 notifier 모름), `StepReport` 확장(`entry_events`/`exit_events` 기본값 `()` backward compat), 전송 실패 silent fail + 연속 실패 dedupe 경보, 드라이런도 실전송 + `[DRY-RUN]` 프리픽스, plain text 한국어 포맷.
- `execution/executor.py` 확장: `EntryEvent`/`ExitEvent` DTO 신설, `StepReport.entry_events`/`exit_events` 추가, `Executor.last_reconcile` 프로퍼티 추가.
- `main.py` 확장: `Runtime.notifier: Notifier` 필드, `_default_notifier_factory`, `build_runtime(..., notifier_factory=...)`, 콜백 4종에 `notify_*` 호출 삽입.
- pytest **681 → 778건 green** (notifier 71건 신규 + executor/main 확장분 포함; Issue #13 대응 중복 검출 O(n) 단순화·빈 axes 가드 추가, 허용 테스트 3건 삭제 + 가드 테스트 1건 추가로 순감 2). 회귀 0건. 의존성 추가 없음.
- (2026-04-22) I1/I2 후속 정리 반영 — 연속 실패 stderr 2차 경보 + `_fmt_time` naive/non-KST 가드. pytest 778 → 788건 green.

## Phase 3 네 번째 산출물 — storage/db.py (SQLite 원장) (2026-04-22)

- `src/stock_agent/storage/` 패키지 신설 — `TradingRecorder` Protocol (`@runtime_checkable`) + `SqliteTradingRecorder` + `NullTradingRecorder` + `StorageError`. 모듈 세부는 [src/stock_agent/storage/CLAUDE.md](../src/stock_agent/storage/CLAUDE.md) 참조.
- DB 파일: `data/trading.db` (`data/stock_agent.db` 와 별개 파일 — 생명주기·스키마 버전 공간 독립).
- 스키마 v1: `orders` + `daily_pnl` + `schema_version` 3 테이블 + 2 인덱스(`idx_orders_session`, `idx_orders_symbol`). PRAGMA: WAL(파일 전용)/NORMAL/foreign_keys ON. `isolation_level=None` autocommit + 스키마 init 한정 `BEGIN IMMEDIATE`.
- 실패 정책: `record_*` 내부 `sqlite3.Error` silent fail + 연속 실패 dedupe 경보 (`monitor/notifier.py` `_record_failure` 패턴 재사용). 생성자 실패만 `StorageError` raise → `NullTradingRecorder` 폴백은 `main.py` `_default_recorder_factory` 담당.
- `EntryEvent`·`ExitEvent` DTO 에 `order_number: str` 필드 추가 (`__post_init__` 가드: 빈 문자열·naive timestamp·qty≤0·price≤0 → `RuntimeError`). `_handle_entry`/`_handle_exit` 가 `ticket.order_number` 주입.
- `main.py` 확장: `Runtime.recorder: TradingRecorder` 필드, `_default_recorder_factory`, `build_runtime(..., recorder_factory=...)`, 콜백 4종에 `recorder.record_*` 호출 삽입, `_graceful_shutdown` / `finally` 에 멱등 `close()` 추가.
- 의존성 추가 없음 — stdlib `sqlite3` 전용.
- pytest 카운트: `test_storage_db.py` 49건 green + 3건 skip, `test_executor.py` +10건, `test_main.py` +14건. 회귀 0건.

## Phase 3 다섯 번째 산출물 — 세션 재기동 상태 복원 경로 (Issue #33, 2026-04-22)

- `storage/db.py` 확장 — `OpenPositionRow` DTO, `DailyPnlSnapshot` DTO (`has_state` 프로퍼티). `TradingRecorder` Protocol 에 `load_open_positions(session_date) -> tuple[OpenPositionRow, ...]` · `load_daily_pnl(session_date) -> DailyPnlSnapshot` 2 메서드 추가. `SqliteTradingRecorder` 는 `orders` 테이블을 `filled_at ASC, rowid ASC` 순으로 재생해 buy/sell 페어를 상쇄한 결과를 반환. `NullTradingRecorder` 는 빈 결과.
- `risk/manager.py` — `RiskManager.restore_session(session_date, starting_capital_krw, *, open_positions, entries_today, daily_realized_pnl_krw)` 신설. `start_session` 이 카운터를 0 으로 리셋하는 것과 달리 외부 값으로 직접 주입. `entries_today < len(open_positions)` 이면 `RuntimeError`. 복원 시 halt 임계치를 넘으면 `_halt_logged=True` 로 세팅해 중복 halt 로그 방출 방지.
- `strategy/orb.py` — `ORBStrategy.restore_long_position(symbol, entry_price, entry_ts)` + `mark_session_closed(symbol, session_date)` 2 메서드 신설. 전자는 `_SymbolState.position_state='long'` + stop/take 재계산, `or_confirmed=True`. 후자는 `position_state='closed'` + `or_confirmed=True`. `or_high`/`or_low`/`last_close` 는 복원 안 함.
- `execution/executor.py` — `OpenPositionInput` Protocol 신설 (storage 와 순환 import 회피용 구조적 타입). `Executor.restore_session(session_date, starting_capital_krw, *, open_positions, closed_symbols=(), entries_today, daily_realized_pnl_krw)` 신설. 기존 `start_session` 에 `self._last_reconcile = None` 리셋 추가. `execution/__init__` 에 `OpenPositionInput` 재노출.
- `main.py` — `_on_session_start` 가 `recorder.load_open_positions(today)` + `load_daily_pnl(today)` 를 호출해 재기동 여부 감지. `True` 이면 `executor.restore_session(...)`, `False` 이면 기존 `executor.start_session(...)`. `logger.info` 에 `restart={r}` 필드 추가.
- ADR-0014 신설 (`docs/adr/0014-runtime-state-recovery.md`) + 인덱스 갱신.
- pytest **989건 green** (신규 152건). 회귀 0건. 의존성 추가 없음.

## Phase 3 여섯 번째 산출물 — broker 체결조회 + 부분체결 정책 (ADR-0015, 2026-04-22)

- `src/stock_agent/broker/kis_client.py` 확장 — `PendingOrder.qty_filled: int` 필드 추가. `_to_pending_order` 가 PyKis 정식 필드(`executed_quantity`/`pending_quantity`) 우선 매핑 → `qty_remaining` fallback. `KisClient.cancel_order(order_number: str) -> None` 신설 (멱등, `OrderRateLimiter` 경유, `_call` 에러 래핑). 모듈 세부는 [src/stock_agent/broker/CLAUDE.md](../src/stock_agent/broker/CLAUDE.md) 참조.
- `src/stock_agent/execution/executor.py` 확장 — `OrderSubmitter` Protocol 에 `cancel_order(order_number: str) -> None` 추가. `LiveOrderSubmitter.cancel_order` (KisClient 위임) + `DryRunOrderSubmitter.cancel_order` (info 로그 + no-op). 내부 `_FillOutcome` DTO 신설 (`filled_qty: int`, `status: Literal["full","partial","none"]`). `_wait_fill` → `_resolve_fill(ticket) -> _FillOutcome` 교체 — 타임아웃 시 `cancel_order` 호출 + 부분/0 체결 수습. `_handle_entry`: partial → `filled_qty` 만 기록·warning 로그, zero → skip·info 로그. `_handle_exit`: `status != "full"` → `ExecutorError` 승격 (운영자 개입 유도). 모듈 세부는 [src/stock_agent/execution/CLAUDE.md](../src/stock_agent/execution/CLAUDE.md) 참조.
- pytest **963건 green** (`tests/test_kis_client.py` + `tests/test_executor.py` 확장, 기존 대비 +183). 회귀 0건. 의존성 추가 없음.
- Phase 3 코드 산출물 전부 완료 (broker 체결조회까지). **Phase 3 PASS 선언은 모의투자 연속 10영업일 무중단 운영 후.**

## 후속 정리 및 Issue 대응 (2026-04-22~23)

- (2026-04-22) `storage/db.py` `load_*` 행 단위 예외 격리 (Issue #40) 완료: 쿼리 자체 실패 → 빈 결과 + 카운터 +1, 개별 행 파싱 실패 → 행 skip + `logger.error`, 1건 이상 파싱 실패 시 메서드 카운터 +1 경로 합류.
- (2026-04-22) Issue #41 — `_on_session_start` NullTradingRecorder 폴백 가시성 보강: 콜백 진입부에서 `isinstance(runtime.recorder, NullTradingRecorder)` 검사 → `logger.critical` + `notify_error(stage="session_start.recorder_null", severity="critical")` 1회 방출. ADR-0013 결정 7의 가시성 보강이며 결정 번복·스택 교체 아님 — 별도 ADR 없음. pytest **1068 passed, 4 skipped**.
- (2026-04-23) Issue #52 — `KisMinuteBarLoader` 파싱 실패·rate limit 대응: `_parse_row` 원인 카테고리별 dedupe 로깅(`missing_date_or_time`/`date_mismatch`/`invalid_price`/`invalid_volume`/`malformed_bar_time`), M2 경보에 첫 행 `sorted(keys)` 동봉, `scripts/debug_kis_minute.py` 신규, `scripts/backfill_minute_bars.py` `--throttle-s` 기본값 0.0 → 0.2 상향. 테스트 54건(`test_kis_minute_bar_loader.py`) green. 실 응답 키 최종 확정은 장중 운영자 실행 후속.
- (2026-04-23) Issue #57 — `KisMinuteBarLoader` 파싱 실패 진단력 강화: `_ParseSkipError(kind, keys, detail=None)` + `from_row` 팩토리 · M2 error 에 `kinds_observed=`/`cursor=` 필드 · `malformed_pages_count=N` 요약 warning 신설 · `_parse_decimal`/`_parse_int` 에 `field=<...> reason=<...>` 라벨(raw 원값 미포함). pytest **70 건** green.
- (2026-04-23) Issue #61 — `KisMinuteBarLoader` 주말 영업일 가드 추가: `_collect_symbol_bars` 에서 `current.weekday() >= 5` (토=5, 일=6) 이면 `_fetch_day` 호출 없이 skip — KIS 주말 요청 시 직전 영업일 데이터 반환 → `date_mismatch` 전원 skip 허탕 제거. `_fetch_day` docstring 정정 (빈 응답은 보관 경계 밖에만 해당). `scripts/backfill_minute_bars.py` `--throttle-s` 하한 `< 0` → `< 0.1` 강화 (EGW00201 누적 방지). pytest `TestCollectSymbolBarsWeekendSkip` 3건 + `TestThrottleLowerBoundGuard` 2건 신규, **75 건** green. 공휴일은 여전히 KIS 호출 후 `date_mismatch` skip 경로.
- (2026-04-23) Issue #63 — `KisMinuteBarLoader` 공휴일 캘린더 가드 추가 (ADR-0018): `BusinessDayCalendar` Protocol + `YamlBusinessDayCalendar` + `HolidayCalendar` + `HolidayCalendarError` + `load_kospi_holidays` 신설 (`src/stock_agent/data/calendar.py`). `config/holidays.yaml` 신설 (KRX 2025·2026 휴장일 32일). `KisMinuteBarLoader.__init__` 에 `calendar: BusinessDayCalendar | None = None` 파라미터 추가 (기본 `YamlBusinessDayCalendar()` lazy 인스턴스화). `_collect_symbol_bars` 루프에서 주말 가드 다음 위치에 `not self._calendar.is_business_day(current)` 가드 추가. 효과: 평일 공휴일 허탕 페이지 ≒12,000건 → 0건. `EGW00201` rate limit 누적 제거. pytest `test_calendar.py` 23건 + `TestCollectSymbolBarsHolidaySkip` 4건 신규, **79 건** green. 의존성 추가 없음.
- (2026-04-23) Issue #71 — `KisMinuteBarLoader` 장시간 hang 방지: `__init__` 에 `http_timeout_s: float = 30.0` + `http_max_retries_per_day: int = 3` kwarg 신설 (음수 → `RuntimeError`, `http_timeout_s=0` 이면 설치 skip). `_install_http_timeout` 이 `kis._sessions` dict 의 각 `requests.Session.request` 에 wrapper 를 설치해 `timeout=http_timeout_s` 기본 주입 — python-kis 2.1.6 미지원 무한 대기 해결. `_fetch_once_with_timeout_retry` 가 `requests.exceptions.Timeout` 재시도 후 한도 초과 시 내부 `_DayHttpTimeoutError` raise → `_fetch_day` catch 후 해당 날짜만 skip (빈 리스트 반환, 다음 날짜 계속 진행). `requests.exceptions.ConnectionError` 등 기타 예외는 기존 `KisMinuteBarLoadError` 래핑 유지 — 외부 계약 변경 없음. `scripts/backfill_minute_bars.py` 에 `--per-page-timeout-s` (float, default `30.0`) + `--max-retries-per-day` (int, default `3`) CLI 옵션 추가. pytest **1277 → 1293 passed, 4 skipped** (신규 16건: loader 10 + backfill CLI 6). 의존성 추가 없음. 잔여: 운영자 실 백필 1회 hang 없이 완주 검증 → Issue #71 close.

## Phase 2 1차 백테스트 FAIL (ADR-0019, 2026-04-24)

1년치 KIS 백필 완료 (199 심볼, 2.78 GB, 러닝 11 시간) + `uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21` 1회 실행. 결과:

- **MDD -51.36%**, 총수익률 -50.05%, 샤프 -6.81, 승률 31.35%, 손익비 1.28, 트레이드당 기대값 ≈ -0.28R (비용 차감 전)
- 종료 자본 499,489 KRW (시작 1,000,000). 거부 상위 `max_positions_reached` 14,568.
- Phase 2 PASS 기준 3.4배 초과 미달.

**사용자 정책 결정**: *"수익률이 생길때까지 절대로 다음 Phase 로 넘어가면 안될 것 같아"* → ADR-0019 성문화. 신규 Phase 2 PASS 게이트:

1. MDD > -15% (ADR-0017 계승)
2. 승률 × 손익비 > 1.0
3. 연환산 샤프 > 0

세 조건 전부 충족 + walk-forward 검증 통과 후에만 Phase 3 착수. 복구 5단계 로드맵 A(민감도) → B(비용) → C(유니버스 필터) → D(전략 파라미터) → E(전략 교체) 순차 게이팅.

부수적 개선: `config/holidays.yaml` 에 근로자의날 2건 (`2025-05-01`, `2026-05-01`) 보강 — ADR-0018 YAML 관리 정책 계승.

## Phase 2 복구 로드맵 Step A — 민감도 그리드 실행 / FAIL (2026-04-25)

- 2026-04-25 17:09~20:00 KST 28/32 조합 완료 (4 조합 미실행 — 207940 셀트리온헬스케어 2025-11 캐시 0건 + rate limit 누적으로 `KisMinuteBarLoadError`; 28 조합 일관 결과 상 4 조합 결과가 뒤집힐 가능성 0% — 즉시 종결).
- 데이터 범위: 2025-04-22 ~ 2026-04-21, 1,000,000 KRW 시작.
- 게이트 판정: MDD > -15% 통과 조합 **0 / 28**, 승률×손익비 > 1.0 통과 **0 / 28**, 샤프 > 0 통과 **0 / 28**.
- 최고 수익률 -40.91% (`or_end=09:15, stop=2.5%, take=4%`), 최저 MDD -42.08% (한도 -15% 의 2.8배).
- **Step A 결론: FAIL.** 상세 결과는 `docs/runbooks/step_a_result_2026-04-25.md` 참조.

## Phase 2 복구 로드맵 Step B 첫 산출물 — SpreadSampleCollector + collect_spread_samples.py CLI (Issue #75, 2026-04-26)

- `src/stock_agent/data/spread_samples.py` 신설 — `SpreadSample` (frozen dataclass) · `SpreadSampleCollector` · `SpreadSampleCollectorError`. KIS 주식현재가 호가/예상체결 조회 (`/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`, TR `FHKST01010200`) `kis.fetch()` 로우레벨 직접 호출. 실전 키 전용 + `install_order_block_guard` 설치. EGW00201 rate limit 자동 재시도. 거래정지(0)·빈문자열·역전 스프레드는 `None` 반환으로 흡수. (모듈 세부는 [src/stock_agent/data/CLAUDE.md](../src/stock_agent/data/CLAUDE.md) 참조.)
- `scripts/collect_spread_samples.py` 신설 — `--symbols`/`--interval-s`/`--duration-h`/`--output-dir`/`--http-timeout-s`/`--no-skip-outside-market`. JSONL 세션 날짜 단위 파일 (`Decimal` str 직렬화, ts isoformat). 심볼 단위 실패 격리. exit code 4종 (`backfill_minute_bars.py` 와 정합).
- pytest 신규 58건 (`test_spread_samples.py` 38 + `test_collect_spread_samples_cli.py` 20). 회귀 0건. 의존성 추가 0.

## 보조 산출물

- **Issue #67 완료 (2026-04-23)**: `src/stock_agent/backtest/walk_forward.py` 신설 — Phase 5 본 구현 대비 walk-forward validation 스켈레톤 선행 도입. `WalkForwardWindow`·`WalkForwardMetrics`·`WalkForwardResult` DTO + `generate_windows`·`run_walk_forward` 스텁(`NotImplementedError`). `backtest/__init__.py` 5 심볼 재노출. `tests/test_walk_forward.py` 18건. `pass_threshold` 기본값 결정은 Phase 5 본 구현 PR 에서 ADR 로 기록 예정.
- **Issue #82 완료 (2026-04-25)**: incremental CSV flush — `append_sensitivity_row(row, path, grid)` 신설 + `run_sensitivity_combos`·`run_sensitivity_combos_parallel` 에 `on_row` keyword-only 콜백 추가. `--resume <csv>` 지정 시 `scripts/sensitivity.py` 가 `append_sensitivity_row` 를 콜백으로 주입 — 조합 1개 완료마다 atomic flush (tmp 파일 → `os.replace`). 재부팅·freeze 후 재실행 시 완료 조합 자동 skip. `on_row=None` 기본값으로 기존 동작 회귀 0. pytest **1364 passed, 4 skipped** (신규 23건). 의존성 추가 없음. ADR-0020 운영 정책 보강.
