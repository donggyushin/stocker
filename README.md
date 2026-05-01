# stock-agent

![CI](https://github.com/donggyushin/korean-stock-trading-system/actions/workflows/ci.yml/badge.svg?branch=main)

Python 기반 한국주식 **데이 트레이딩** 자동매매 시스템 (MVP 설계 단계).

## 개요

KOSPI 200 대형주를 대상으로 Opening Range Breakout(ORB) 전략을 자동 실행하는 시스템을 구축합니다. 초기에는 한국투자증권 **모의투자** 환경에서 검증하고, 2주 이상 무사고 운영이 확인된 뒤에만 소액(100~200만원) 실전으로 전환합니다.

**왜 한국투자증권인가**: 토스증권은 개인 개발자용 Open API를 제공하지 않습니다(2026-04 기준). 자동매매가 가능하면서 Mac/Python 환경에서 바로 사용할 수 있는 REST API + 모의투자 지원 증권사가 한국투자증권(KIS Developers)입니다.

## 핵심 전제

- 본 프로젝트의 목표는 **"수익 극대화"가 아니라 "손실을 최소화하며 데이터로 검증한 전략을 규율 있게 실행"** 하는 것입니다.
- 데이트레이딩은 통계적으로 개인 투자자의 **70~90%가 손실**을 봅니다. 거래세(0.18%) + 수수료 + 슬리피지가 마진을 잠식합니다.
- 실전 투입 전 **모의투자 → 백테스팅 → 페이퍼트레이딩** 단계를 반드시 거칩니다.

## 매매 전략

**Opening Range Breakout (ORB)** — long-only

- 9:00~9:30 동안 종목별 고가(OR-High)·저가(OR-Low) 기록
- 9:30 이후 OR-High 상향 돌파 시 시장가 매수
- 손절 -1.5%, 익절 +3.0%, 15:00 강제 청산
- KOSPI 200 중 9:00~9:30 거래대금 상위 N종목만 후보

## 리스크 관리 (기본값)

| 항목 | 값 |
|---|---|
| 종목당 진입 금액 | 자본의 20% |
| 동시 보유 종목 | 최대 3종목 |
| 종목당 손절 / 익절 | -1.5% / +3.0% |
| 일일 손실 한도 | 자본의 -2% 도달 시 당일 매매 중단 |
| 일일 최대 진입 | 10회 |

## 기술 스택

- **언어**: Python 3.12+
- **의존성 관리**: `uv`
- **브로커 API**: `python-kis 2.x` (KIS Developers REST/WebSocket)
- **시장 데이터**: `pykrx` (KRX 공식 과거 OHLCV), `pyyaml` (유니버스 설정 파일 로드)
- **백테스팅**: 자체 시뮬레이션 루프 (`backtest/engine.py`) — `backtesting.py` 라이브러리는 다중종목·포트폴리오 게이팅 표현 불가 + AGPL 라이센스 부담으로 폐기 (2026-04-20)
- **스케줄링**: `APScheduler`
- **알림**: `python-telegram-bot 22.x`
- **로깅**: `loguru`
- **설정 검증**: `pydantic-settings`
- **저장소**: SQLite (MVP)
- **테스트**: `pytest`, `pytest-mock`
- **포매터/린터**: `ruff`, `black` (`pre-commit` 훅 적용)
- **CI**: GitHub Actions — PR 및 main push 마다 ruff·black·pytest 자동 실행. main 머지는 CI job `Lint, format, test` 통과 필수.

## 로드맵 (총 약 4주)

| Phase | 기간 | 목표 |
|---|---|---|
| 0 | 2~3일 | 한투 계좌 개설, KIS 모의 키 발급, 텔레그램 봇 세팅, 레포 초기화 |
| 1 | 4~5일 | KIS 클라이언트(토큰/주문/조회) + 데이터 파이프라인(pykrx + 분봉 폴링) |
| 2 | 5~7일 | ORB 전략 구현, 리스크 매니저, 1년 분봉 백테스팅 리포트 (ADR-0017) |
| 3 | 5~7일 | 모의투자 자동 실행 루프, 텔레그램 알림, **2주 이상 무사고 운영** |
| 4 | 상시 | 소액 실전 전환 (초기 50만원 한도부터), 주간 회고 |
| 5 | 지속 | 2번째 전략 A/B, 필요 시 클라우드 VPS 이전, 대시보드 |

## 현재 상태

**Phase 1 PASS (코드·테스트 레벨)** (2026-04-19 선언). Phase 0 환경 준비 완료. broker(KisClient + rate_limiter) + data(historical + universe + realtime) 모두 완료. pytest **131건 green**. **paper 주문 + live 시세 하이브리드 키 정책 도입**: KIS paper 도메인에 시세 API가 없어 `RealtimeDataStore`는 별도 실전 APP_KEY로 실전 도메인을 호출하며, 실전 키 PyKis 인스턴스에는 `install_order_block_guard`를 설치해 주문 경로를 구조적으로 차단한다.

**Phase 2 진행 중 — ORB 전략 엔진 + 리스크 매니저 + 백테스트 엔진 코어 + CSV 분봉 어댑터 + 파라미터 민감도 그리드 + backtest.py CLI + KIS 과거 분봉 API 어댑터 + 백필 CLI 완료** (2026-04-20~22). `strategy/` + `risk/` + `backtest/` + `data/kis_minute_bars.py` 완료. `scripts/backtest.py`·`scripts/sensitivity.py` 에 `--loader={csv,kis}` 옵션 추가. PASS 판정은 ADR-0019 세 게이트(MDD > -15% · 승률 × 손익비 > 1.0 · 연환산 샤프 > 0) 전부 충족 + walk-forward 검증 통과 조건.

**Phase 2 1차 백테스트 FAIL (2026-04-24, ADR-0019)**. 2026-04-24 에 1년치 KIS 분봉 백필 완료 (199 심볼, 2.78 GB) + `uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21` 1회 실행. 결과: **MDD -51.36%**, 총수익률 -50.05%, 샤프 -6.81, 승률 31.35%, 손익비 1.28, 트레이드당 기대값 ≈ -0.28R. Phase 2 PASS 기준 3.4배 초과 미달. **사용자 정책**: 수익률 확인 전까지 Phase 3 진입 금지 (ADR-0019). 복구 5단계 로드맵 A(민감도 그리드) → B(비용 가정 재검정) → C(유니버스 필터) → D(전략 파라미터 구조 변경) → E(전략 교체) 순차 게이팅.

**ADR-0019 복구 로드맵 Step A FAIL (2026-04-25)**. 32 조합 민감도 그리드 실행 (28/32 완료 — 4 조합 미실행, 결과 뒤집힐 가능성 0% 로 즉시 종결). 세 게이트(MDD > -15% · 승률×손익비 > 1.0 · 샤프 > 0) 동시 통과 조합 **0 / 28**. 최고 수익률 -40.91%, 전 조합 샤프 음수.

**Step B 완료 (2026-04-29)** — 3 거래일 장중 실 호가 수집 (331,530 샘플). 전체 중앙값 스프레드 0.1305% — 현행 가정 0.1% 대비 1.3× 이지만 사전 기준(0.05~0.2%) 내. **ADR-0006 슬리피지 가정 0.1% 유지 결정. `costs.py` 변경 없음.**

**Step C FAIL (2026-04-30, Issue #76)** — Top 50 / Top 100 유동성 서브셋 백테스트 실행 (`--loader=kis`, 2025-04-22 ~ 2026-04-21). 두 서브셋 모두 ADR-0019 세 게이트 전원 FAIL (Top 50: MDD -44.70%, 샤프 -6.68, 승률×손익비 0.377 / Top 100: MDD -50.13%, 샤프 -7.74, 승률×손익비 0.383). **Step D (전략 파라미터 구조 변경) 진입.**

**Step D1 FAIL (2026-05-01)** — OR 윈도 스터디. `step_d1_grid` 48 조합 × Top 50 / Top 100 = 96 런 전원 ADR-0019 게이트 미통과. 최선 조합: Top 50 MDD -37.18% / Top 100 MDD -35.98%. Step C 대비 MDD 소폭 개선이나 PASS 기준 -15% 까지 여전히 21~23%p 격차. 상세: `docs/runbooks/step_d1_or_window_2026-05-01.md`.

**Step D2 FAIL (2026-05-01)** — force_close_at 스터디. `step_d2_grid` 48 조합 × Top 50 / Top 100 = 96 런 전원 ADR-0019 게이트 미통과. 최선 조합 (`force_close_at=15:20, stop=2.5%, take=5.0%`): Top 50 MDD -35.02% / Top 100 MDD -37.56%. D1 vs D2 거의 동급 — `stop=2.5%/take=5.0%` 가 본질 개선 벡터.

**Step E 진입 (2026-05-01)** — 전략 교체. PR1~PR4 코드 산출물 완료: `VWAPMRStrategy`(PR2) · `GapReversalStrategy`(PR3) · `strategy/factory.py` + `--strategy-type {orb,vwap-mr,gap-reversal}` CLI 옵션(PR4 Stage 1). **PR4 Stage 2 완료**: `backtest/prev_close.py` 신설 — `DailyBarPrevCloseProvider` 로 `--strategy-type gap-reversal` 이 이제 실 동작 가능 (일봉 캐시 `data/stock_agent.db` 미백필 시 pykrx 네트워크 호출 발생). **PR4 Stage 3 완료**: `scripts/backfill_daily_bars.py` 신설 — pykrx 일봉 캐시 일괄 백필 CLI. gap-reversal 백테스트 결정론 보장을 위한 사전 백필 도구 (Stage 3 선결 조건). `scripts/sensitivity.py` 에서 `--strategy-type gap-reversal + --workers >= 2` 조합은 pickle 제약으로 거부됨 (`--workers 1` 사용). 운영자 백테스트 실행(Stage 3) 및 결과 기반 ADR 작성(Stage 5)은 후속 단계. 상세 설계와 각 Phase의 PASS 기준, 비용·위험 분석은 [`plan.md`](./plan.md)에 있습니다.

**Phase 3 착수 전제 통과** (2026-04-21). 실전 시세 전용 APP_KEY 3종 발급·IP 화이트리스트 등록·평일 장중 `healthcheck.py` 4종 그린(WebSocket 체결 수신 OK) 완료.

**Phase 3 첫 산출물 — Executor (코드·테스트 레벨) 완료** (2026-04-21). `execution/` 패키지 신설 — `Executor` + Protocol 3종(`OrderSubmitter`/`BalanceProvider`/`BarSource`) + 어댑터 3종(`LiveOrderSubmitter`/`LiveBalanceProvider`/`DryRunOrderSubmitter`) + `StepReport`/`ReconcileReport` DTO. pytest **605건 green**.

**Phase 3 두 번째 산출물 — main.py + APScheduler 통합 (코드·테스트 레벨) 완료** (2026-04-21). `src/stock_agent/main.py` 신설 — `BlockingScheduler` + 4종 cron job(09:00 session_start·매분 step·15:00 force_close·15:30 daily_report, 평일 한정) + `--dry-run` CLI 플래그(KIS 주문 접촉 0). pytest **681건 green**.

**Phase 3 세 번째 산출물 — monitor/notifier.py (텔레그램 알림, 코드·테스트 레벨) 완료** (2026-04-21). `src/stock_agent/monitor/` 패키지 신설 — `Notifier` Protocol + `TelegramNotifier` + `NullNotifier` + `ErrorEvent`/`DailySummary` DTO. 진입·청산·에러·일일 요약 4종 텔레그램 알림. 전송 실패 silent fail + 연속 실패 경보. pytest **778건 green**. 의존성 추가 없음.

**Phase 3 네 번째 산출물 — storage/db.py (SQLite 원장, 코드·테스트 레벨) 완료** (2026-04-22). `src/stock_agent/storage/` 패키지 신설 — `TradingRecorder` Protocol + `SqliteTradingRecorder` + `NullTradingRecorder` + `StorageError`. 주문·체결·일일 PnL을 `data/trading.db`에 append-only 기록. 기록 실패 silent fail + 연속 실패 경보. 의존성 추가 없음(stdlib `sqlite3` 전용).

**Phase 3 다섯 번째 산출물 — broker 체결조회 + 부분체결 정책 (코드·테스트 레벨) 완료** (2026-04-22, ADR-0015). `KisClient.cancel_order` 신설 + `PendingOrder.qty_filled` 추가. `Executor._resolve_fill` 이 타임아웃 시 `cancel_order` 호출 + 부분/0 체결 수습. 진입 부분체결 → 실체결 수량만 원장 기록. 청산 부분/0 체결 → `ExecutorError` 승격. 의존성 추가 없음. **Phase 3 코드 산출물 전부 완료. Phase 3 PASS 선언은 모의투자 환경 연속 10영업일 무중단 운영 후.**

**운영 주의**: KOSPI 200 구성종목은 `config/universe.yaml`에 수동 관리합니다. KRX KOSPI 200 정기변경(연 2회 — 매년 6월·12월의 선물·옵션 동시만기일 익영업일 기준)에 맞춰 운영자가 직접 갱신해야 합니다. 현재 KRX 정보데이터시스템 [11006] 기준 199/200 반영(2026-04-17 조회, 임시 가상 코드 1건 제외). 정식 티커 발급 후 다음 갱신에 추가 예정.

### Phase 0 체크리스트 (완료 2026-04-19)

- [x] 한국투자증권 비대면 계좌 개설
- [x] KIS Developers 가입 및 **모의투자** APP_KEY / APP_SECRET 발급
- [x] 텔레그램 봇 생성(@BotFather) 및 chat_id 확보
- [x] 레포 초기화(`uv init`), `.env.example` 작성
- [x] `scripts/healthcheck.py` 구현 — 모의 잔고 조회 + 텔레그램 알림 수신 확인

## 디렉토리 구조

현재 존재하는 파일 (Phase 2 Step E PR4 Stage 2 완료 기준):

```text
stock-agent/
├── .github/workflows/
│   └── ci.yml                 # PR·main push 시 ruff/black/pytest 자동 실행
├── .python-version            # 3.12
├── pyproject.toml             # uv 기반, ruff/black/pytest 설정 포함
├── uv.lock                    # 패키지 잠금 (pykrx 1.2.7, pyyaml 6.0.3 포함)
├── .pre-commit-config.yaml    # ruff, black, 기본 훅
├── .env.example               # KIS·텔레그램 키 placeholder (.env는 .gitignore)
├── .gitignore
├── README.md
├── plan.md
├── config/
│   └── universe.yaml          # KOSPI 200 종목코드 (수동 관리, 연 2회 정기변경)
├── src/stock_agent/
│   ├── __init__.py
│   ├── config.py              # pydantic-settings Settings + get_settings() 캐시
│   ├── broker/
│   │   ├── __init__.py
│   │   ├── kis_client.py      # KisClient — 토큰 관리, 잔고/주문/조회 DTO
│   │   ├── rate_limiter.py    # OrderRateLimiter — 주문 경로 전용 (2 req/s, 350ms)
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   └── data/
│       ├── __init__.py        # HistoricalDataStore, HistoricalDataError, DailyBar, KospiUniverse, UniverseLoadError, load_kospi200_universe, RealtimeDataStore, TickQuote, MinuteBar, RealtimeDataError, MinuteCsvBarLoader, MinuteCsvLoadError, KisMinuteBarLoader, KisMinuteBarLoadError, SpreadSample, SpreadSampleCollector, SpreadSampleCollectorError export
│       ├── historical.py      # pykrx 일봉 SQLite 캐시 (fetch_daily_ohlcv 전용, 스키마 v3)
│       ├── universe.py        # KOSPI 200 유니버스 YAML 로더 (load_kospi200_universe)
│       ├── realtime.py        # 실시간 시세 (RealtimeDataStore — WebSocket 우선 + REST 폴링 fallback)
│       ├── minute_csv.py      # CSV 과거 분봉 어댑터 (MinuteCsvBarLoader)
│       ├── kis_minute_bars.py # KIS API 과거 분봉 어댑터 (KisMinuteBarLoader, SQLite 캐시 data/minute_bars.db)
│       ├── spread_samples.py  # KIS 호가 스프레드 스냅샷 수집기 (SpreadSampleCollector, Step B 인프라)
│       └── CLAUDE.md          # 모듈 세부 문서
│   ├── strategy/
│   │   ├── __init__.py        # EntrySignal, ExitReason, ExitSignal, GapReversalConfig, GapReversalStrategy, ORBStrategy, Signal, Strategy, StrategyConfig, StrategyError, VWAPMRConfig, VWAPMRStrategy export
│   │   ├── base.py            # Strategy Protocol, EntrySignal/ExitSignal DTO, ExitReason Literal, KST 상수
│   │   ├── orb.py             # ORBStrategy 상태 머신 + StrategyConfig (frozen dataclass)
│   │   ├── vwap_mr.py         # VWAPMRStrategy + VWAPMRConfig (Step E PR2)
│   │   ├── gap_reversal.py    # GapReversalStrategy + GapReversalConfig (Step E PR3)
│   │   ├── factory.py         # build_strategy_factory + STRATEGY_CHOICES (Step E PR4)
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   ├── risk/
│   │   ├── __init__.py        # RiskManager, RiskConfig, RiskDecision, PositionRecord, RejectReason, RiskManagerError export
│   │   ├── manager.py         # RiskManager — 포지션 사이징·진입 게이팅·서킷브레이커
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   ├── backtest/
│   │   ├── __init__.py        # BacktestEngine, BacktestConfig, BacktestResult, BacktestMetrics, TradeRecord, DailyEquity, BarLoader, InMemoryBarLoader export
│   │   ├── engine.py          # BacktestEngine — 자체 시뮬레이션 루프
│   │   ├── costs.py           # 슬리피지·수수료·거래세 순수 함수
│   │   ├── metrics.py         # 총수익률·MDD·샤프·승률·평균손익비·일평균거래수
│   │   ├── loader.py          # BarLoader Protocol + InMemoryBarLoader
│   │   ├── sensitivity.py     # 파라미터 민감도 그리드
│   │   ├── prev_close.py      # DailyBarPrevCloseProvider (Step E Stage 2 — GapReversalStrategy 전일 종가 주입)
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   ├── execution/
│   │   ├── __init__.py        # Executor, ExecutorConfig, OrderSubmitter, BalanceProvider, BarSource, LiveOrderSubmitter, LiveBalanceProvider, DryRunOrderSubmitter, StepReport, ReconcileReport, EntryEvent, ExitEvent, ExecutorError export (13종)
│   │   ├── executor.py        # Executor — 신호 → 주문 → 체결 추적 → 상태 동기화 루프
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   ├── monitor/
│   │   ├── __init__.py        # Notifier, TelegramNotifier, NullNotifier, ErrorEvent, DailySummary export
│   │   ├── notifier.py        # 텔레그램 알림 — Notifier Protocol + 구현체
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   ├── storage/
│   │   ├── __init__.py        # TradingRecorder, SqliteTradingRecorder, NullTradingRecorder, StorageError export
│   │   ├── db.py              # SQLite 원장 — 주문·체결·일일 PnL append-only 기록
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   └── main.py                # 장중 실행 진입점 (BlockingScheduler + Executor 오케스트레이터)
├── tests/
│   ├── test_config.py
│   ├── test_kis_client.py
│   ├── test_safety.py
│   ├── test_rate_limiter.py
│   ├── test_historical.py     # 14 케이스
│   ├── test_universe.py       # 11 케이스
│   ├── test_realtime.py       # 28 케이스
│   ├── test_strategy_orb.py   # 36 케이스
│   ├── test_risk_manager.py   # 73 케이스
│   ├── test_backtest_engine.py
│   ├── test_kis_minute_bar_loader.py  # 39 케이스
│   ├── test_executor.py       # 63 케이스
│   ├── test_main.py           # 47 케이스 (+ 확장분 포함)
│   ├── test_notifier.py       # 71 케이스
│   └── test_storage_db.py     # 49 케이스 (+ 3 skip)
└── scripts/
    ├── healthcheck.py              # KIS 모의 잔고 조회 + 텔레그램 hello (실주문 없음)
    ├── backtest.py                 # 단일 런 백테스트 CLI
    ├── sensitivity.py              # 파라미터 민감도 그리드 CLI (--grid {default,step-d1,step-d2}, --resume 지정 시 조합 단위 incremental flush, freeze 내성)
    ├── backfill_minute_bars.py     # KIS 과거 분봉 캐시 일괄 적재 CLI
    ├── backfill_daily_bars.py      # pykrx 일봉 캐시 일괄 백필 CLI (Step E Stage 3 선결 — gap-reversal 결정론 보장)
    ├── collect_spread_samples.py   # KIS 호가 스프레드 스냅샷 수집 CLI (Step B 인프라, JSONL 출력)
    ├── build_liquidity_ranking.py  # KOSPI 200 유동성 랭킹 산출 CLI (Step C 인프라, CSV 출력)
    └── build_universe_subset.py    # 유동성 랭킹 CSV → KOSPI 200 서브셋 YAML 생성 (Step C 보조)
```

미착수 모듈의 청사진은 [`plan.md`](./plan.md)의 디렉토리 구조 섹션 참조.

## 설치 및 실행

### 사전 준비

1. KIS Developers 포털에서 **모의투자계좌 API 신청** 완료 및 모의투자용 APP_KEY / APP_SECRET 발급
   - MTS의 "상시 모의투자 참가신청"과 별개 절차임에 주의
2. 텔레그램 @BotFather로 봇 생성 → 토큰 및 chat_id 확보

### 최초 설정 — `.env` 준비

`.env` 는 두 경로를 순서대로 로드하고 뒤 파일이 앞을 덮는다.

| 경로 | 역할 |
|---|---|
| `~/.config/stocker/.env` | worktree 무관 공용 master (권장) |
| repo 루트 `.env` | worktree-local override (선택) |

운영자 1회 셋업:

```bash
mkdir -p ~/.config/stocker
cp .env.example ~/.config/stocker/.env
chmod 600 ~/.config/stocker/.env
# 편집기로 ~/.config/stocker/.env 를 열어 필수 값 기입:
#   KIS paper 3종(APP_KEY·APP_SECRET·ACCOUNT_NO) + 텔레그램 2종(BOT_TOKEN·CHAT_ID)
#   KIS 실전 3종은 Phase 3 착수 시 필요 (지금은 선택)
uv run python scripts/healthcheck.py   # 통과 확인
```

이후 `claude-squad` 로 새 worktree 를 만들 때 `.env` 를 다시 복사할 필요 없다.
worktree 단위로 다른 값이 필요하면(예: paper/live 전환) repo 루트에 `.env` 를 따로 작성한다.

민감정보 취급 원칙: `.env` 파일(두 경로 모두)은 절대 커밋하지 않는다. `~/.config/stocker/.env` 는 저장소 밖 경로라 구조적으로 커밋 대상이 아니고, repo 루트 `.env` 는 `.gitignore` 의 `.env` 라인으로 차단된다(이 라인을 실수로 지우지 않아야 한다). 커밋·PR 전 diff 에 키 문자열이 섞여들지 않았는지 확인한다.

### 환경 설정

```bash
# 가상환경 및 의존성 설치
uv sync

# git 훅 설치 (최초 1회)
uv run pre-commit install

# worktree-local override 가 필요한 경우에만 (통상은 ~/.config/stocker/.env 로 충분)
cp .env.example .env
# .env 에서 아래 값을 채웁니다:
#   KIS_ENV          = paper
#   KIS_HTS_ID       = 한투 HTS 아이디
#   KIS_APP_KEY      = 모의투자 APP_KEY (36자)
#   KIS_APP_SECRET   = 모의투자 APP_SECRET (180자)
#   KIS_ACCOUNT_NO   = 계좌번호 (XXXXXXXX-XX 형식)
#   TELEGRAM_BOT_TOKEN = 텔레그램 봇 토큰
#   TELEGRAM_CHAT_ID   = 텔레그램 chat_id
#
#   --- 시세 전용 실전 키 (Phase 3 착수 전 필수, 미설정 시 healthcheck 4번 SKIP) ---
#   # HTS_ID 는 paper/실전 공유 — 위 KIS_HTS_ID 재사용 (한 사람 당 하나)
#   KIS_LIVE_APP_KEY   = 실전 APP_KEY (36자)  ← KIS Developers 포털에서 실전 앱 별도 신청
#   KIS_LIVE_APP_SECRET = 실전 APP_SECRET (180자)
#   KIS_LIVE_ACCOUNT_NO = 실전 계좌번호 (XXXXXXXX-XX) — paper 계좌번호와 다름
#   # 실전 앱 발급 후 KIS Developers 포털 → 앱 관리 → 허용 IP 목록에 현재 공인 IP 등록 필수
```

### 백테스트용 일봉 백필 (Step E Stage 3 선결)

`--strategy-type gap-reversal` 백테스트는 `DailyBarPrevCloseProvider` 가 `data/stock_agent.db` 일봉 캐시를 조회한다. 미백필 상태이면 백테스트 중 pykrx 네트워크 호출이 반복되어 결정론이 깨진다. **운영자가 1회 선행 실행**해야 한다.

```bash
# Top 100 유니버스 전체 1년치 일봉 백필 (Top 50 은 부분집합이라 한 번에 처리됨)
uv run python scripts/backfill_daily_bars.py \
    --from 2025-04-01 --to 2026-04-21 \
    --universe-yaml config/universe_top100.yaml

# 특정 심볼만 백필
uv run python scripts/backfill_daily_bars.py \
    --from 2025-04-01 --to 2026-04-21 \
    --symbols 005930 000660

# exit code: 0 정상 / 1 일부 심볼 HistoricalDataError / 2 입력·설정 오류 / 3 I/O 오류
```

pykrx 1.2.7 이상은 `KRX_ID` / `KRX_PW` 환경변수가 필요하다 (`~/.config/stocker/.env` 에 기입).

### 백테스트용 분봉 백필

Phase 2 PASS 검증 전 `data/minute_bars.db` 에 1년치 KIS 분봉을 미리 적재한다.

```bash
# 유니버스 전체 심볼에 대해 최근 1년치 분봉 백필
uv run python scripts/backfill_minute_bars.py \
    --from 2024-04-22 --to 2025-04-22

# 특정 심볼만 백필
uv run python scripts/backfill_minute_bars.py \
    --from 2024-04-22 --to 2025-04-22 --symbols 005930 000660

# exit code: 0 정상 / 1 일부 심볼 KisMinuteBarLoadError / 2 입력·설정 오류 / 3 I/O 오류
```

백필 완료 후 `scripts/backtest.py --loader=kis --from ... --to ...` 로 MDD > -15% 확인 시 Phase 2 PASS 판정.

### 장중 실행 (Phase 3)

실전 전 반드시 모의투자 2주 무사고 운영을 먼저 거친다. 드라이런 모드로 동작을 먼저 검증하는 것을 권장한다.

```bash
# 드라이런 — KIS 주문 API 접촉 0, 시그널·로그만 남긴다
uv run python -m stock_agent.main --dry-run

# 실 주문 실행 (paper 또는 실전 — .env 의 KIS_ENV 에 따름)
uv run python -m stock_agent.main

# 시작 자본 명시 (기본 1,000,000원)
uv run python -m stock_agent.main --dry-run --starting-capital 2000000
```

- 스케줄: 평일(`mon-fri`) 09:00 세션 시작 → 매분 신호 처리 → 15:00 강제청산 → 15:30 일일 리포트 (모두 KST)
- 공휴일 자동 판정 미지원 — KRX 임시공휴일은 운영자가 프로세스를 띄우지 않는 방식으로 처리
- SIGINT(Ctrl+C) / SIGTERM 모두 graceful shutdown 처리 (exit 0)

### 환경 점검

```bash
uv run python scripts/healthcheck.py
# 통과 기준:
# 1) KIS 모의투자 토큰 발급 OK  — 시간대 무관
# 2) 모의 계좌 잔고 조회 OK    — 시간대 무관
# 3) 텔레그램 "hello" 메시지 수신 OK — 시간대 무관
# 4) 삼성전자(005930) 현재가 조회 OK — 평일 장중(09:00~15:30 KST) 실행 필수
#    장외에서는 WebSocket 연결은 성공하지만 체결 이벤트 미수신 → 2초 타임아웃 후 실패 가능
#    (실전 키 미설정 시 4번은 SKIP — Phase 3 착수 전 반드시 해결:
#     실전 키 3종 기입 + KIS Developers 포털 IP 화이트리스트 등록 필수)
```

## 참고 문서

- [`plan.md`](./plan.md) — 전체 설계서(Context, 전략 상세, 리스크 한도, 로드맵, Verification 기준, 비용·위험 분석)
- [`docs/operations-playbook.md`](./docs/operations-playbook.md) — 모의투자 운영 플레이북 (장 전 체크리스트·장중 이상 대응·Kill switch·일일 마감 체크)

## 책임 고지 (Disclaimer)

- 본 프로젝트는 **학습·연구 목적**이며 **수익을 보장하지 않습니다**. 데이트레이딩은 개인 투자자 대다수가 손실을 보는 고위험 영역입니다.
- 실전 투입 전 반드시 모의투자·백테스팅을 통해 검증하고, 본인 판단과 책임 하에 **잃어도 생활에 지장 없는 소액**으로 시작하십시오.
- 증권사 API 키 및 계좌 정보는 절대 리포지토리에 커밋하지 마십시오(`.env`는 `.gitignore` 대상).
- 본 코드가 원인이 된 금전적 손실에 대해 작성자는 책임을 지지 않습니다.
