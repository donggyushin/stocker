# Step E 후속 작업 계획 (PR3 이후)

> **작성**: 2026-05-01 · 다른 세션 인계용 plan. **Stage 1·2 완료 (PR4) → 다음 세션은 Stage 3 부터 재개**. 진행 완료 후 결과는 별도 `docs/runbooks/` 에 기록하고 본 파일은 삭제.

## 다음 세션 빠른 재개 가이드 (READ FIRST)

**현재 위치**: Stage 1·2 완료. **Stage 3 (운영자 직접 백테스트 실행) 부터 재개**.

| Stage | 상태 | 비고 |
|---|---|---|
| 1 | ✅ 완료 (PR4) | `--strategy-type` CLI 인자 + `build_strategy_factory` 헬퍼 |
| 2 | ✅ 완료 (PR4) | `DailyBarPrevCloseProvider` + scripts 통합 + gap-reversal+workers≥2 거부 가드 |
| **3** | ⏳ **다음 세션 시작 지점** | 운영자 직접 백테스트 8 런 + ADR-0019 게이트 판정 |
| 4 | ⏸ Stage 3 PASS 조건부 | 민감도 그리드 + walk-forward |
| 5 | ⏸ Stage 3·4 결과 종합 | ADR (시나리오 A/B/C) |

**다음 세션에서 AI 가 수행할 일**:
1. 본 파일을 읽어 현재 상태 파악 (`Stage 3 재개`).
2. **운영자에게 다음 사항을 먼저 확인**:
   - 일봉 캐시 (`data/stock_agent.db`) 1 년치 백필 완료 여부 — 미백필 시 pykrx 네트워크 호출 발생.
   - KIS 분봉 캐시 (`data/minute_bars.db`) 1 년치 백필 완료 여부 — `scripts/backfill_minute_bars.py` 로 사전 백필 권장.
   - `config/universe_top50.yaml` · `config/universe_top100.yaml` 존재 확인 (Step C 에서 이미 git 추적, 커밋 781ec54).
3. 운영자가 직접 8 런 실행 → 결과 산출물 (`data/step_e_*_*.md/csv`) 회수.
4. AI 가 ADR-0019 세 게이트 (MDD>-15% · 승률×손익비>1.0 · 샤프>0) 판정 + `docs/runbooks/step_e_<후보>_<날짜>.md` 작성.
5. 결과에 따라 Stage 4 진입 (한 후보라도 PASS) 또는 Stage 5 폐기 ADR 직행 (둘 다 FAIL).

**Stage 3 사전 준비** (운영자, 1회):
```bash
# 일봉 캐시 백필 — gap-reversal 결정론 보장 선결 조건 (Stage 3 신규)
uv run python scripts/backfill_daily_bars.py \
  --from 2025-04-01 --to 2026-04-21 \
  --universe-yaml config/universe_top100.yaml
```

**Stage 3 첫 명령** (운영자 실행, 일봉 백필 완료 후):
```bash
uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml --strategy-type vwap-mr \
  --output-markdown data/step_e_vwap_mr_top50.md \
  --output-csv data/step_e_vwap_mr_top50.csv \
  --output-trades-csv data/step_e_vwap_mr_top50_trades.csv
```

나머지 7 런은 `## 단계 3 — 백테스트 실행 + ADR-0019 게이트 판정` 섹션 참조.

## 컨텍스트

ADR-0019 Phase 2 복구 로드맵 Step E (전략 교체 평가) 의 코드 산출물 4 PR 완료. 후속 작업 = **두 후보 (VWAPMRStrategy · GapReversalStrategy) 백테스트 실행 → ADR-0019 게이트 판정 → 채택/폐기 결정**.

### 완료된 PR

| PR | 산출물 | 상태 |
|---|---|---|
| #94 (PR1) | `BacktestConfig.strategy_factory` 추상화 + mutually exclusive 가드 + `_close_session` Strategy Protocol 타입 | 머지 |
| #95 (PR2) | `VWAPMRStrategy` + `VWAPMRConfig` + 35 단위 테스트 + 재노출 + CLAUDE.md 동기화 | 머지 |
| #96 (PR3) | `GapReversalStrategy` + `GapReversalConfig` + `PrevCloseProvider` + 34 단위 테스트 + 재노출 + CLAUDE.md 동기화 | 머지 |
| PR4 (이 세션) | Stage 1 + Stage 2 통합: `factory.py` + `prev_close.py` + `--strategy-type` CLI + `DailyBarPrevCloseProvider` + 64 신규 테스트 (33 factory + 18 prev_close + 13 CLI 통합 - 1 삭제) + 문서 동기화 | **PR 생성, 머지 대기** |

### 현재 코드 상태 (PR4 머지 후 main 가정)

- `src/stock_agent/strategy/factory.py` — `STRATEGY_CHOICES`, `StrategyType`, `build_strategy_factory(strategy_type, *, strategy_config=None, vwap_mr_config=None, gap_reversal_config=None, prev_close_provider=None) -> Callable[[], Strategy]`.
- `src/stock_agent/backtest/prev_close.py` — `DailyBarPrevCloseProvider(daily_store, calendar, *, max_lookback_days=14)` (callable + close + context manager).
- `scripts/backtest.py` — `--strategy-type` 인자, `_build_backtest_config(args, *, prev_close_provider=None)`, `_build_prev_close_provider()` 헬퍼, `_run_pipeline` 의 try/finally provider close.
- `scripts/sensitivity.py` — 동일 인자, `_build_base_config(args, *, prev_close_provider=None)`, `_build_prev_close_provider()`, `gap-reversal + workers≥2` 거부 가드 (sqlite3 connection pickle 불가).
- pytest **1651 collected** (PR3 1562 + 33 factory + 18 prev_close + 13 CLI 통합 - 1 삭제 + 26 기타 신규 = 1651).
- 4 종 정적 검사 PASS (pytest · ruff check · ruff format / black --check · pyright `0 errors, 2 warnings` 무관).
- ADR-0019 게이트 판정 **미수행** (Stage 3 작업).

### 사용자 결정 (2026-05-01)

- D3·D4 패스 + Step E 직행.
- 우선 후보 2 개: VWAP mean-reversion · Opening gap reversal (서로 다른 가설).
- PR 분할: 인프라 1 + 후보별 1 + Stage 1+2 통합 = 4 PR (완료).
- **Stage 3 부터 별도 세션에서 본 plan 따라 진행**.

---

## 5 단계 후속 작업 명세

각 단계는 별도 PR. 의존성 순차 (단계 1 → 2 → 3 → 4 → 5).

### 단계 1 — CLI 확장 (scripts/backtest.py + scripts/sensitivity.py) — ✅ 완료 (PR4)

**실 산출물 (계획 대비 변경 사항)**:
- 헬퍼 모듈 위치: `scripts/_common.py` 가 아닌 `src/stock_agent/strategy/factory.py` (sys.path 안정성). 두 스크립트 모두 `from stock_agent.strategy.factory import STRATEGY_CHOICES, build_strategy_factory` 로 import.
- `build_strategy_factory(strategy_type, *, strategy_config=None, vwap_mr_config=None, gap_reversal_config=None, prev_close_provider=None) -> Callable[[], Strategy]`. 매 호출마다 새 인스턴스.
- 신규 헬퍼: `scripts/backtest.py::_build_backtest_config(args, *, prev_close_provider=None)`, `scripts/sensitivity.py::_build_base_config(args, *, prev_close_provider=None)`. orb 분기는 `BacktestConfig(starting_capital_krw=...)` 그대로 (회귀 0).
- 신규 테스트: `tests/test_strategy_factory.py` 33 케이스 + `tests/test_backtest_cli.py` `TestStrategyTypeFlag`(4) + `TestStrategyTypeRouting`(5) + `TestStrategyTypeMainExitCode`(2) = 11 + `tests/test_sensitivity_cli.py` `TestStrategyTypeFlag`(4) + `TestStrategyTypeBaseConfigRouting`(5) = 9.

**커밋 메시지** (실): `feat(scripts): --strategy-type {orb,vwap-mr,gap-reversal} 인자 + factory 헬퍼 (Step E PR4 Stage 1)`.

(아래는 원안 — 참고용)

**목표**: `--strategy-type {orb,vwap-mr,gap-reversal}` 인자 추가. 운영자가 단일 명령으로 신규 전략 백테스트 실행 가능.

**변경 파일**:
- `scripts/backtest.py` — `_parse_args` 에 `--strategy-type` 추가, `_build_config()` 헬퍼에서 strategy_type 별 `BacktestConfig.strategy_factory` 생성. 기본값 `orb` (회귀 0).
- `scripts/sensitivity.py` — 동일 `--strategy-type` 추가. 단계 4 의 그리드 분기와 별개.
- `scripts/_common.py` (없으면 신규) — `build_strategy_factory(strategy_type, **kwargs) -> Callable[[], Strategy]` 단일 진실원. 두 스크립트가 import.

**`gap-reversal` 처리 주의**: `GapReversalStrategy` 는 `prev_close_provider` 의존. 단계 2 에서 백테스트 통합 완료 전까지는 stub provider (예: `lambda symbol, date: None`) 또는 단계 2 와 묶기.

**테스트** (`tests/test_scripts_backtest_cli.py` · `tests/test_sensitivity_cli.py`):
- `--strategy-type orb` 회귀 (기존 동작 동일).
- `--strategy-type vwap-mr` 라우팅 → `VWAPMRStrategy` 인스턴스 생성 검증.
- `--strategy-type gap-reversal` 라우팅 → `GapReversalStrategy` 인스턴스 생성 검증 (단계 2 전이면 dummy provider).
- 잘못된 값 (`--strategy-type unknown`) → argparse `choices` 위반 exit 2.

**검증**:
```bash
uv run pytest tests/test_scripts_backtest_cli.py tests/test_sensitivity_cli.py -q
uv run pyright src scripts tests
uv run ruff check src scripts tests
uv run black --check src scripts tests
```

**커밋 메시지**: `feat(scripts): --strategy-type {orb,vwap-mr,gap-reversal} 인자 추가 (Step E 후속)`.

---

### 단계 2 — 백테스트 통합 (`gap_reversal.prev_close_provider`) — ✅ 완료 (PR4)

**실 산출물 (계획 대비 변경 사항)**:
- 신규 모듈: `src/stock_agent/backtest/prev_close.py` — `DailyBarPrevCloseProvider(daily_store: HistoricalDataStore, calendar: BusinessDayCalendar, *, max_lookback_days: int = 14)`. `__call__(symbol, session_date) -> Decimal | None` (callable) + `close()` + 컨텍스트 매니저.
- 알고리즘: `session_date - 1` 부터 1 일씩 역행 (`max_lookback_days=14` 까지) → 첫 영업일을 캘린더로 판정 → `daily_store.fetch_daily_ohlcv(symbol, prev_day, prev_day)` → `bars[0].close` 또는 None.
- 입력 가드: symbol `^\d{6}$` (`RuntimeError`), `max_lookback_days > 0` (생성자, `RuntimeError`), 초과 시 None + `logger.warning`.
- CLI 통합: `scripts/backtest.py::_run_pipeline` 와 `scripts/sensitivity.py::_run_pipeline` 모두 `--strategy-type=gap-reversal` 시 `_build_prev_close_provider()` 호출 (기본 `data/stock_agent.db` + `config/holidays.yaml`) + `try/finally` 로 `provider.close()` 보장.
- **추가 제약**: `scripts/sensitivity.py` 에 `--strategy-type=gap-reversal` + `--workers >= 2` 거부 가드 신설 — `HistoricalDataStore` 의 sqlite3 connection 이 pickle 불가하여 ProcessPool 워커 전달 불가. `RuntimeError` (exit 2) 발생.
- 신규 테스트: `tests/test_backtest_prev_close_provider.py` 18 케이스 (정상 룩업·None 분기·입력 가드·라이프사이클·store 호출 검증). `tests/test_backtest_cli.py::TestGapReversalPrevCloseProviderInjection` 5 케이스. `tests/test_sensitivity_cli.py::TestGapReversalPrevCloseProviderInjection` 8 케이스. `TestStrategyTypeBaseConfigRouting::test_gap_reversal_parallel_strategy_factory_callable_GapReversalStrategy` 1 케이스 삭제 (Stage 2 제약과 충돌).

**dry-run 명령** (운영자, 1 일치):
```bash
uv run python scripts/backtest.py --loader=kis --from 2026-04-21 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml --strategy-type gap-reversal \
  --output-markdown /tmp/gap_dry.md \
  --output-csv /tmp/gap_dry.csv \
  --output-trades-csv /tmp/gap_dry_trades.csv
```

**커밋 메시지** (실): `feat(backtest): DailyBarPrevCloseProvider + scripts gap-reversal 통합 (Step E PR4 Stage 2)`.

(아래는 원안 — 참고용)

**목표**: 백테스트에서 `GapReversalStrategy` 가 실제 전일 종가를 룩업할 수 있도록 `prev_close_provider` 를 `HistoricalDataStore.DailyBar` + `BusinessDayCalendar` (ADR-0018) 조합으로 구성.

**변경 파일**:
- `src/stock_agent/backtest/engine.py` 또는 새 헬퍼 모듈 (`src/stock_agent/backtest/prev_close.py`):
  - `DailyBarPrevCloseProvider(daily_store, calendar)` 클래스 또는 `make_prev_close_provider(daily_store, calendar) -> PrevCloseProvider` 팩토리.
  - `(symbol, session_date) → Decimal | None`: 캘린더로 직전 영업일 산출 → `daily_store` 에서 일봉 조회 → `daily_bar.close` 반환. 미존재 시 None.
- `scripts/_common.py` 의 `build_strategy_factory` 가 `gap-reversal` 분기에서 위 provider 를 백테스트 컨텍스트로부터 주입 (CLI 인자에 `--daily-bars-csv` 등 추가 또는 KIS 어댑터 재사용 검토).

**의존성**:
- `HistoricalDataStore` 또는 `DailyBar` 의 일봉 데이터 소스. 기존 `data/CLAUDE.md` 의 `HistoricalDataStore` 가 있으면 그대로. 없거나 백필 안 된 경우 별도 백필 스크립트 필요.
- `BusinessDayCalendar` (`YamlBusinessDayCalendar` + `config/holidays_kospi.yaml`).

**탐색 필요** (단계 시작 시):
- `HistoricalDataStore` 인스턴스화 방법 (`scripts/backtest.py` 가 이미 사용하는지 확인).
- 백테스트 캐시 (`data/kis_cache/`) 가 일봉을 포함하는지 (분봉 캐시만 있으면 별도 일봉 백필 필요).
- KIS 일봉 API 어댑터 존재 여부.

**테스트**:
- `tests/test_backtest_prev_close_provider.py` (신규) — 캘린더·일봉 store stub 으로 단위 검증.
  - 정상 룩업 (직전 영업일 종가 반환).
  - 일봉 store 미존재 → None.
  - 캘린더 휴일 처리 (월요일 → 금요일 종가).
  - 첫 영업일 (전일 데이터 없음) → None.

**검증**:
```bash
uv run pytest tests/test_backtest_prev_close_provider.py -q
# Top 50 1 일치 dry-run (실제 데이터 필요)
uv run python scripts/backtest.py --loader=kis --from 2026-04-21 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml --strategy-type gap-reversal \
  --output-markdown /tmp/gap_dry.md
```

**커밋 메시지**: `feat(backtest): GapReversalStrategy prev_close_provider 통합 (Step E 후속)`.

---

### 단계 3 — 백테스트 실행 + ADR-0019 게이트 판정 — ⏳ **다음 세션 시작 지점**

**선결 조건 체크리스트 (AI 가 운영자에게 확인)**:

1. `data/stock_agent.db` 1 년치 일봉 캐시 백필 — **gap-reversal 의 prev_close lookup 에 필수**. 미백필 시 pykrx 네트워크 호출 반복 발생 + 결정론 미보장. `scripts/backfill_daily_bars.py` (Stage 3 신규) 로 사전 백필:
   ```bash
   uv run python scripts/backfill_daily_bars.py \
     --from 2025-04-01 --to 2026-04-21 \
     --universe-yaml config/universe_top100.yaml
   ```
   (Top 50 은 Top 100 의 부분집합 — 한 번에 처리됨. pykrx 1.2.7+ 는 `KRX_ID`/`KRX_PW` env 필수.)
2. `data/minute_bars.db` 1 년치 KIS 분봉 캐시 — `scripts/backfill_minute_bars.py` 로 사전 수집. 부재 시 백테스트 중 KIS API 호출 누적 + 레이트 리밋 위험. 권장 명령: `uv run python scripts/backfill_minute_bars.py --from 2025-04-22 --to 2026-04-21 --universe-yaml config/universe_top100.yaml`.
3. `config/universe_top50.yaml` · `config/universe_top100.yaml` 존재 — 이미 git 추적 (커밋 781ec54).
4. `config/holidays.yaml` 갱신 — 2025·2026 한국 휴장일 32 일 수록 (Step C 시점 기준). 추가 임시공휴일 발생 여부 운영자 확인.
5. KIS 실전 키 + IP 화이트리스트 — `KIS_LIVE_APP_KEY` / `KIS_LIVE_APP_SECRET` / `KIS_LIVE_ACCOUNT_NO` `.env` 주입 + Developers 포털 IP 등록 완료 가정 (Step B 시점 기준).

**목표**: 두 후보 모두 1 년치 KIS 캐시 (Top 50 + Top 100) 로 백테스트 실행. ADR-0019 세 게이트 판정.

**실행 명령** (운영자 직접):
```bash
# VWAPMR Top 50
uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml --strategy-type vwap-mr \
  --output-markdown data/step_e_vwap_mr_top50.md \
  --output-csv data/step_e_vwap_mr_top50.csv \
  --output-trades-csv data/step_e_vwap_mr_top50_trades.csv

# VWAPMR Top 100
uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --universe-yaml config/universe_top100.yaml --strategy-type vwap-mr \
  --output-markdown data/step_e_vwap_mr_top100.md \
  ...

# GapReversal Top 50 / Top 100 — 동일 패턴
```

**판정 (ADR-0019 게이트, `Decimal` 비교)**:
| 게이트 | 임계값 |
|---|---|
| MDD | > -0.15 (낙폭 절대값 < 15%) |
| 승률 × 평균 손익비 | > 1.0 |
| 연환산 샤프 | > 0 |

세 게이트 전부 충족해야 PASS. 한 후보·한 서브셋이라도 PASS 하면 단계 4 (민감도 그리드) 로 진행. 전 후보 FAIL 시 단계 5 의 폐기 ADR.

**산출물**:
- `docs/runbooks/step_e_vwap_mr_2026-MM-DD.md` (4 런 결과 표 + 판정).
- `docs/runbooks/step_e_gap_reversal_2026-MM-DD.md` (4 런 결과 표 + 판정).
- 이슈 #78 댓글 (Markdown 표 첨부).

**커밋 메시지**: `docs(step-e): VWAPMR/GapReversal 백테스트 결과 (Step E 후속, Closes #78 if PASS)`.

---

### 단계 4 — 민감도 그리드 + walk-forward (단계 3 PASS 후만)

**목표**: 단계 3 에서 한 후보라도 게이트 통과 시 파라미터 민감도 + walk-forward 검증.

**변경 파일**:
- `src/stock_agent/backtest/sensitivity.py`:
  - `_AXIS_PARSERS` 에 신규 prefix 등록 — `vwap_mr.<field>`, `gap_reversal.<field>`. ORB 처럼 Decimal·int·time 파서 분기.
  - 신규 그리드 함수 — `step_e_vwap_mr_grid()` (예: threshold_pct 3 × take 3 × stop 4 = 36 조합), `step_e_gap_reversal_grid()` (gap_threshold_pct × take × stop).
  - `_apply_axis_value` 가 `strategy_factory` 와 호환되도록 — partial 갱신 헬퍼.
- `scripts/sensitivity.py` — `--grid {default,step-d1,step-d2,step-e-vwap-mr,step-e-gap-reversal}` 분기 확장. CLAUDE.md 의 grid_func dict 갱신.

**테스트**:
- `tests/test_sensitivity.py` — 신규 axis prefix 정상·이상 케이스, 신규 그리드 함수, CLI 라우팅 (기존 `step-d2` 패턴 그대로).

**실행 (운영자)**:
```bash
uv run python scripts/sensitivity.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml --strategy-type vwap-mr \
  --grid step-e-vwap-mr --workers 8 \
  --output-markdown data/sensitivity_step_e_vwap_mr_top50.md \
  --output-csv data/sensitivity_step_e_vwap_mr_top50.csv \
  --resume data/sensitivity_step_e_vwap_mr_top50.csv
```

**walk-forward**:
- 1 년치 구간을 6+6 개월로 분할 (in-sample / out-of-sample).
- in-sample 에서 최적 조합 선정 → out-of-sample 에서 검증.
- 산출물: `docs/runbooks/step_e_<후보>_walk_forward_2026-MM-DD.md`.

**커밋 메시지** (분리 가능):
- `feat(sensitivity): step_e_vwap_mr_grid + step_e_gap_reversal_grid + --grid 분기 (Step E 후속)`.
- `docs(step-e): walk-forward 검증 결과`.

---

### 단계 5 — ADR + 결정

**목표**: 단계 3·4 결과 종합 → 채택 또는 폐기 ADR 작성.

**시나리오 분기**:

#### A) 한 후보 PASS + walk-forward 통과
- `docs/adr/00NN-<후보>-strategy-adoption.md` 신규 (예: `0021-vwap-mr-strategy-adoption.md`):
  - 결정: `<후보>Strategy` 를 Phase 2 PASS 전략으로 채택. ORBStrategy 폐기.
  - 맥락: ADR-0019 게이트 + walk-forward 결과 인용.
  - 결과: Phase 2 PASS 재시도 → Phase 3 진입 가능.
- `docs/adr/00MM-orb-strategy-deprecated.md` 신규 (예: `0022-orb-strategy-deprecated.md`):
  - ORBStrategy 코드 보존 (회귀 비교용) + ADR 인덱스 상태 `폐기됨 (Superseded by ADR-00NN)`.
- 코드 변경:
  - `main.py` 의 strategy 인스턴스화를 채택 후보로 교체.
  - `BacktestConfig.strategy_factory` 디폴트 변경 검토 (or ORBStrategy 디폴트 유지 + 명시 주입).
  - ADR-0019 의 결과 섹션에 PASS 기록 추가 (사후 수정 금지 규칙 검토 — ADR-0019 자체는 로드맵이라 결과 추가는 OK).
- 이슈 #78 close + 새 이슈 — Phase 3 진입 준비.

#### B) 두 후보 모두 PASS
- 두 ADR 작성 후 단일 후보 선택 (walk-forward 결과·robust 정도·과적합 위험 비교) — A) 와 동일 처리.

#### C) 두 후보 모두 FAIL
- `docs/adr/00NN-step-e-vwap-gap-failed.md` 신규:
  - 결정: VWAP MR + Gap Reversal 두 후보 폐기. ORBStrategy 도 비채택.
  - 결과: 별도 이슈로 다음 옵션 검토 — (a) Pre-market pullback / Intraday momentum (SMA) 추가 평가, (b) 일중 데이트레이딩 자체 폐기 + 더 근본적 변경 (스윙 트레이딩·다른 자산 클래스).
- 이슈 #78 close + 새 이슈 발의.

**검증** (단계 5 PR):
```bash
uv run pytest -q
uv run pyright src scripts tests
uv run ruff check src scripts tests
uv run black --check src scripts tests
```

**커밋 메시지**:
- `docs(adr): VWAPMRStrategy 채택 + ORBStrategy 폐기 (ADR-0021/0022, Closes #78)` (시나리오 A/B).
- `docs(adr): Step E VWAP/Gap 두 후보 폐기 — 다음 단계 별도 이슈 (ADR-00NN, Closes #78)` (시나리오 C).

---

## 의존성 + 진행 순서

```
PR3 (#96) 머지 → main
  ↓
단계 1 — CLI 확장
  ↓
단계 2 — prev_close_provider 통합
  ↓
단계 3 — 백테스트 실행 (Top 50 + Top 100, 후보 2 × 서브셋 2 = 4 런 × 후보 = 8 런 + 운영자 직접)
  ↓
단계 4 — 민감도 그리드 + walk-forward (단계 3 PASS 후만)
  ↓
단계 5 — ADR (3 가지 시나리오 분기)
```

## 위험 요소

1. **두 후보 모두 FAIL 가능성 큼**: ORB 가 D1·D2 로 92 런 전원 FAIL 했음. 후보 전략도 한국 시장 일중 변동성 특성에 부합하지 않으면 동일 결과. 단계 5 시나리오 C 대비.
2. **prev_close_provider 데이터 의존**: 일봉 백필 누락 시 GapReversalStrategy 평가 자체 불가. 단계 2 시작 시 데이터 확보 우선 확인.
3. **과적합 위험**: 단계 4 민감도 그리드에서 best 조합이 walk-forward out-of-sample 에서 무너지면 채택 금지 — robust 결과만 채택.
4. **PR 분할 비대 가능성**: 단계 1 + 2 가 함께 들어갈 수도 (CLI 가 provider 의존). 진행 중 분할 재조정.

## 참조

- 이슈 #78 — Step E 평가 본 이슈.
- ADR-0019 (`docs/adr/0019-phase2-backtest-fail-remediation.md`) — 복구 로드맵.
- ADR-0006 (`docs/adr/0006-*`) — 비용 가정 (수수료 0.015% / 거래세 0.18% / 슬리피지 0.1%).
- ADR-0018 (`docs/adr/0018-*`) — BusinessDayCalendar.
- `docs/runbooks/step_d2_force_close_2026-05-01.md` — D2 결과 + E 권장.
- `src/stock_agent/strategy/CLAUDE.md` — VWAPMRStrategy / GapReversalStrategy 공개 API + 알고리즘.
- `src/stock_agent/backtest/CLAUDE.md` — BacktestEngine + 민감도 그리드 패턴.
- `tests/test_strategy_vwap_mr.py` · `tests/test_strategy_gap_reversal.py` — 35 + 34 케이스 패턴.
