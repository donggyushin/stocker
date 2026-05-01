# Step E 후속 작업 계획 (PR3 이후)

> **작성**: 2026-05-01 · 다른 세션 인계용 plan. 진행 완료 후 결과는 별도 `docs/runbooks/` 에 기록하고 본 파일은 삭제.

## 컨텍스트

ADR-0019 Phase 2 복구 로드맵 Step E (전략 교체 평가) 의 코드 산출물 3 PR 완료. 후속 작업 = **두 후보 (VWAPMRStrategy · GapReversalStrategy) 백테스트 실행 → ADR-0019 게이트 판정 → 채택/폐기 결정**.

### 완료된 PR

| PR | 산출물 | 상태 |
|---|---|---|
| #94 (PR1) | `BacktestConfig.strategy_factory` 추상화 + mutually exclusive 가드 + `_close_session` Strategy Protocol 타입 | 머지 |
| #95 (PR2) | `VWAPMRStrategy` + `VWAPMRConfig` + 35 단위 테스트 + 재노출 + CLAUDE.md 동기화 | 머지 |
| #96 (PR3) | `GapReversalStrategy` + `GapReversalConfig` + `PrevCloseProvider` + 34 단위 테스트 + 재노출 + CLAUDE.md 동기화 | 진행 중 (머지 대기) |

### 현재 코드 상태 (PR3 머지 후 main 가정)

- `src/stock_agent/strategy/__init__.py` 공개 심볼: `EntrySignal`, `ExitReason`, `ExitSignal`, `GapReversalConfig`, `GapReversalStrategy`, `ORBStrategy`, `Signal`, `Strategy`, `StrategyConfig`, `StrategyError`, `VWAPMRConfig`, `VWAPMRStrategy`.
- `src/stock_agent/backtest/engine.py:276` — `BacktestConfig.strategy_factory` 분기 활성. None 이면 ORBStrategy 디폴트.
- pytest **1562 passed, 4 skipped**.
- 4 종 정적 검사 PASS.
- ADR-0019 게이트 (MDD > -15% · 승률×손익비 > 1.0 · 샤프 > 0) 판정 미수행.

### 사용자 결정 (2026-05-01)

- D3·D4 패스 + Step E 직행.
- 우선 후보 2 개: VWAP mean-reversion · Opening gap reversal (서로 다른 가설).
- PR 분할: 인프라 1 + 후보별 1 = 3 PR (완료).
- **후속 작업 = 별도 세션에서 본 plan 따라 진행**.

---

## 5 단계 후속 작업 명세

각 단계는 별도 PR. 의존성 순차 (단계 1 → 2 → 3 → 4 → 5).

### 단계 1 — CLI 확장 (scripts/backtest.py + scripts/sensitivity.py)

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

### 단계 2 — 백테스트 통합 (`gap_reversal.prev_close_provider`)

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

### 단계 3 — 백테스트 실행 + ADR-0019 게이트 판정

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
