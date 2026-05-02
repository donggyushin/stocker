# Step F 가설 풀 확장 plan

> **작성**: 2026-05-01 · 다른 세션 인계용 plan. 진행 완료 후 결과는 별도 `docs/runbooks/` 에 기록하고 본 파일은 삭제.

## 컨텍스트

ADR-0019 (Phase 2 복구 로드맵) Step A~E 전 230+ 백테스트 런 / 0 PASS 로 한국 KOSPI 200 일중 long-only 데이트레이딩의 retail alpha 부재 확인 (ADR-0021). 운영자 결정 (2026-05-01) 으로 일중 가정 폐기 + 일/월 단위 + 학술 검증 가설 풀 + DCA baseline 비교로 전환.

게이트 재정의: ADR-0022 (MDD>-25% · DCA baseline 대비 양의 알파 · 연환산 Sharpe>0.3).

## 평가 가설 풀

각 가설을 **별도 PR** 로 구현·백테스트. PR 순서는 의존성 (F1 DCA baseline 우선, 나머지는 평가 우선순위).

| # | 가설 | 학술 검증 | 인프라 재사용 | 우선순위 | PR 분량 |
|---|---|---|---|---|---|
| F1 | **Buy & Hold (DCA)** baseline | 1928~ S&P500 / KOSPI 200 30년 historical | 60% | 필수 (선행) | 1 PR |
| F2 | **Golden Cross (200d SMA cross)** | Faber 2007, Hurst 2017 | 85% | 높음 | 1 PR |
| F3 | **Cross-sectional 모멘텀 (12개월 ranking)** | Jegadeesh-Titman 1993 | 70% | 높음 | 1 PR |
| F4 | **저변동성 anomaly (변동성 하위 N개)** | Frazzini-Pedersen 2014 | 70% | 보통 | 1 PR |
| F5 | **RSI 평균회귀 (일봉 RSI 14)** | 약함 | 80% | 낮음 (보너스) | 1 PR |

### 의도적으로 평가 제외 (사용자 목록 중)

- **눌림목 (Pullback) 매매** — 정량화 어려움. "급등" 임계 · "조정" 깊이 등 변수 5+ 개로 과적합 위험 폭발. 알고리즘화 시 ORB/모멘텀 변형으로 수렴 — 별도 가설로 검증 가치 낮음.
- **차익 거래** — 한국 단일 KOSPI 종목 거래소 1개로 거래소 간 차익 부재. 가능 후보 (선물-현물 basis · ETF NAV vs 호가 · DR vs 본주) 모두 기관 영역 또는 HFT 가 ms 단위 차익화. retail 자본·인프라 진입 불가. 백테스트 의미 없음.

## 진행 순서 + PR 분할

```
PR0 (본 PR — Step E close + Step F open)
  ├ ADR-0021 (Step E 폐기) 승인
  ├ ADR-0022 (Step F 게이트 재정의) 승인
  ├ docs/step_f_strategy_pool_plan.md 도입 (본 파일)
  ├ docs/runbooks/step_e_*.md 2건
  ├ docs/step_e_followup_plan.md 삭제
  ├ root CLAUDE.md / README.md / docs/adr/README.md 동기화
  ↓
PR1 — F1 DCA baseline (선행 의존성) ✓ 완료 (2026-05-02, PASS)
  ├ src/stock_agent/strategy/dca.py — DCAStrategy, DCAConfig
  ├ src/stock_agent/data/daily_bar_loader.py — DailyBarLoader, DailyBarSource
  ├ src/stock_agent/backtest/dca.py — DCABaselineConfig, compute_dca_baseline
  ├ tests/test_strategy_dca.py (31건), tests/test_daily_bar_loader.py (16건), tests/test_backtest_dca.py (32건)
  ├ scripts/backtest.py --strategy-type=dca, --loader=daily, --monthly-investment 라우팅
  ├ docs/runbooks/step_f_dca_baseline_2026-05-02.md
  ├ ADR-0022 게이트 판정: MDD -12.92% PASS / Sharpe 2.2683 PASS / DCA 알파 N/A → 종합 PASS
  └ baseline 수치: 총수익률 +51.50% mark-to-market (시작 자본 2,000,000 KRW, 069500, 13 lots)
  ↓
PR2 — F2 Golden Cross ✓ 완료 (2026-05-02, PASS — 3 게이트, 단 caveat 적용)
  ├ src/stock_agent/strategy/golden_cross.py — GoldenCrossStrategy (200d SMA)
  ├ src/stock_agent/backtest/golden_cross.py — GoldenCrossBaselineConfig, compute_golden_cross_baseline
  ├ tests/test_strategy_golden_cross.py (48건), tests/test_backtest_golden_cross.py (33건)
  ├ scripts/backtest.py --strategy-type golden-cross (BacktestEngine 우회, compute_golden_cross_baseline 경로)
  ├ docs/runbooks/step_f_golden_cross_2026-05-02.md
  ├ ADR-0022 게이트 판정: MDD -20.52% PASS / Sharpe 2.2753 PASS / DCA 대비 알파 +130.86%p PASS
  ├ 주요 caveat: trades=1 (통계 신뢰도 낮음), 데이터 plausibility 검증 필요 (+182.36% 절대 수치)
  └ baseline 비교: 총수익률 +182.36% mark-to-market (시작 자본 2,000,000 KRW, 069500, 58주)
  ↓
PR3 — F3 Cross-sectional 모멘텀 ✓ 완료 (2026-05-02, FAIL — 게이트 2)
  ├ src/stock_agent/strategy/momentum.py — MomentumStrategy, MomentumConfig
  ├ src/stock_agent/backtest/momentum.py — MomentumBaselineConfig, compute_momentum_baseline
  ├ tests/test_strategy_momentum.py (47건), tests/test_backtest_momentum.py (38건)
  ├ scripts/backtest.py --strategy-type momentum (--top-n / --lookback-months / --rebalance-day 신설)
  ├ docs/runbooks/step_f_momentum_2026-05-02.md
  ├ ADR-0022 게이트 판정: MDD -7.70% PASS / Sharpe 0.9910 PASS / DCA 대비 알파 -36.96%p FAIL → 종합 FAIL
  └ 결과: 총수익률 +11.22% mark-to-market (시작 자본 2,000,000 KRW, 101종목, 2025-04-01 ~ 2026-04-21)
  ↓
PR4 — F4 저변동성 ✓ 완료 (2026-05-02, FAIL — 게이트 2)
  ├ src/stock_agent/strategy/low_volatility.py — LowVolStrategy, LowVolConfig
  ├ src/stock_agent/backtest/low_volatility.py — LowVolBaselineConfig, compute_low_volatility_baseline
  ├ tests/test_strategy_low_volatility.py (47건), tests/test_backtest_low_volatility.py (38건)
  ├ scripts/backtest.py --strategy-type low-vol (--lookback-days / --rebalance-month-interval 신설)
  ├ docs/runbooks/step_f_low_volatility_2026-05-02.md
  ├ ADR-0022 게이트 판정: MDD -9.62% PASS / Sharpe 1.1713 PASS / DCA 대비 알파 -32.31%p FAIL → 종합 FAIL
  └ 결과: 총수익률 +15.87% mark-to-market (시작 자본 2,000,000 KRW, 101종목, 2025-04-01 ~ 2026-04-21)
  ↓
PR5 — F5 RSI 평균회귀 (옵션, 보너스)
  ├ src/stock_agent/strategy/rsi_mr.py — RSIMRStrategy
  ├ tests/test_strategy_rsi_mr.py
  ├ scripts/backtest.py --strategy-type rsi-mr
  ├ docs/runbooks/step_f_rsi_mr_2026-MM-DD.md
  ↓
PR6 — 종합 판정 + 최종 결정 ADR
  ├ docs/runbooks/step_f_summary_2026-MM-DD.md (4~5 후보 비교 표)
  ├ ADR-0023 — 시나리오별 (PASS 채택 / DCA 채택 / 자동매매 폐기)
  └ #78 또는 후속 이슈 close
```

## 각 PR 상세 명세

### PR1 — F1 Buy & Hold DCA baseline

**목표**: KOSPI 200 ETF (KODEX 200, 069500) 매월 첫 영업일 정액 매수 전략. ADR-0022 게이트 2 (DCA baseline 대비 알파) 의 비교 기준 산출.

**구현 모듈**: `src/stock_agent/strategy/dca.py`
- `DCAStrategy` (Strategy Protocol 구현)
- `DCAConfig` (frozen dataclass: `monthly_investment_krw: int`, `target_symbol: str = "069500"`, `purchase_day: int = 1` (영업일 기준 N))
- 룰: `on_time(t)` 에서 매월 첫 영업일이면 시장가 매수 시그널 1개. 청산 X (계속 보유).
- 일중 force_close 없음 (`force_close_at: None`).

**입력 데이터**: 월별 1 회 시장가 진입 시점 가격만 필요. 분봉 (KIS 1년 캐시) 또는 일봉 (pykrx 일봉 캐시) 둘 다 가능. **일봉 권장** — 의존성 단순.

**백테스트 인자**:
```bash
uv run python scripts/backtest.py --loader=daily --from 2025-04-22 --to 2026-04-21 \
  --symbols 069500 --strategy-type dca \
  --output-markdown data/step_f_dca_top1.md
```

`--loader=daily` 가 추가 인자 (기존 `csv`/`kis` 외 일봉 로더). `BarLoader` Protocol 의 일봉 어댑터 (`DailyBarLoader`) 신설 또는 `HistoricalDataStore` 의 stream 래퍼.

**테스트 (RED-first)**: `tests/test_strategy_dca.py`
- DTO 가드 (양수 투자금·심볼 정규식·purchase_day 1~28)
- 매월 첫 영업일 1회 진입 시그널
- 영업일 캘린더 적용 (휴일 시 다음 영업일)
- 일중 청산 시그널 0 검증
- StrategyConfig 회귀 0

**runbook**: `docs/runbooks/step_f_dca_baseline_2026-MM-DD.md`
- ADR-0022 게이트 3종 판정 (게이트 2 는 자기 자신이라 N/A — 게이트 1·3 만 적용)
- 12개월 누적 수익률 + MDD + Sharpe 기록
- 후속 PR 의 baseline 인용 데이터 source

**예상 PR 분량**: 코드 ≒200 LoC + 테스트 ≒400 LoC + runbook + ADR 갱신.

### PR2 — F2 Golden Cross (200d SMA)

**목표**: KOSPI 200 ETF 단일 종목에 대한 200일 단순이평선 cross 추세 추종.

**구현 모듈**: `src/stock_agent/strategy/golden_cross.py`
- `GoldenCrossStrategy(Strategy Protocol)`
- `GoldenCrossConfig` (frozen dataclass: `target_symbol: str = "069500"`, `sma_period: int = 200`, `position_pct: Decimal = Decimal("1.0")` (100% 투입))
- 룰:
  - 매일 종가 기준 SMA(200) 계산 (lookback 200일 일봉 필요)
  - 종가 > SMA 위 = LONG 보유
  - 종가 < SMA 아래 = 청산 (현금 보유)
  - 시그널 발생은 매일 1회 (장 마감 후 또는 다음 장 시초가)

**의존성**: pykrx 일봉 200일 lookback. `HistoricalDataStore` 직접 호출 또는 strategy 가 받는 lookback provider.

**백테스트**:
```bash
uv run python scripts/backtest.py --loader=daily --from 2024-01-01 --to 2026-04-21 \
  --symbols 069500 --strategy-type golden-cross \
  --output-markdown data/step_f_golden_cross.md
```

기간을 **2년 이상** 확보 — SMA(200) lookback 으로 인해 첫 200일은 시그널 없음. 1년 평가 + 200일 lookback = 18개월 이상 데이터 필요.

**테스트**: `tests/test_strategy_golden_cross.py`
- DTO 가드 (sma_period > 0, position_pct ∈ (0, 1])
- SMA 계산 정확성 (200 종가 평균)
- Cross-up 시 LONG 시그널
- Cross-down 시 청산 시그널
- Lookback 부족 시 시그널 보류
- 보유 중 SMA 변화에도 추가 진입 X (1회만)

**runbook**: `docs/runbooks/step_f_golden_cross_2026-MM-DD.md`
- F1 DCA baseline 결과와 비교 (게이트 2 판정 핵심)
- 매매 횟수 (예상 1~5회/년) + 보유 비율 + drawdown 패턴

### PR3 — F3 Cross-sectional 모멘텀

**목표**: KOSPI 200 종목 중 12개월 누적 수익 상위 N (예: 10) 종목 보유. 매월 리밸런싱.

**구현 모듈**: `src/stock_agent/strategy/momentum.py`
- `MomentumStrategy(Strategy Protocol)`
- `MomentumConfig` (frozen dataclass: `lookback_months: int = 12`, `top_n: int = 10`, `rebalance_day: int = 1`)
- 룰:
  - 매월 첫 영업일 리밸런싱
  - 직전 12개월 (lookback) 종목별 수익률 계산
  - 상위 N 개 보유, 동일 가중 (자본 / N)
  - 직전 보유 종목 중 N 위 밖이면 청산

**의존성**: `load_kospi200_universe()` + 종목별 12개월 일봉.

**구현 주의**: `Strategy Protocol` 의 시그널 흐름이 단일 종목 진입·청산 전제. 다중종목 동시 리밸런싱 (10 청산 + 10 진입 한 시점) 패턴은 `Executor` / 백테스트 엔진 처리 가능 — 이미 다중종목 ORB 가 검증함.

**백테스트** (1년):
```bash
uv run python scripts/backtest.py --loader=daily --from 2024-04-22 --to 2026-04-21 \
  --strategy-type momentum --output-markdown data/step_f_momentum.md
```

**테스트**: `tests/test_strategy_momentum.py`
- DTO 가드 (lookback > 0, top_n ∈ [1, 200])
- 12개월 수익률 계산 정확성
- 리밸런싱 시그널 (청산 + 진입 분리)
- 보유 종목 변경 없으면 시그널 0 (회전율 0%)

### PR4 — F4 저변동성 (보너스)

**목표**: KOSPI 200 종목 중 직전 60일 일별 수익률 표준편차 하위 N (예: 20) 보유. 분기 리밸런싱.

**구현 모듈**: `src/stock_agent/strategy/low_volatility.py`
- F3 와 거의 동일 패턴 — ranking metric 만 변경 (수익률 → 변동성 역순).

### PR5 — F5 RSI 평균회귀 (보너스)

**목표**: 다중종목 일봉 RSI(14) < 30 매수, RSI > 70 매도.

**구현 모듈**: `src/stock_agent/strategy/rsi_mr.py`
- `RSIMRStrategy(Strategy Protocol)`
- 룰: 종목별 RSI(14) 계산. < 30 진입. > 70 청산. stop_loss 보조.

**위험**: VWAP-MR FAIL 패턴 재발 가능. 작은 이익 + 큰 손실 비대칭. 학술 검증 약함. **시도해볼 가치는 보통**.

### PR6 — 종합 판정 + ADR-0023

**목표**: F1~F5 결과 종합 표 + 시나리오별 ADR.

**시나리오**:

| # | 결과 패턴 | 의사결정 |
|---|---|---|
| A | F2~F5 중 1+ 가 게이트 3종 동시 통과 (DCA 도 PASS) | 채택 ADR — 해당 전략으로 Phase 3 모의투자 진입 검토. retail 자동매매 alpha 확보. |
| B | F1 DCA 만 PASS, F2~F5 모두 FAIL | 자동매매 가설 폐기 ADR — DCA 단순 cron 으로 정리. 본 프로젝트의 자동매매 영역 close. retail 영역 정직 결과. |
| C | 전부 FAIL (DCA 도 FAIL) | KOSPI 200 1년치 음수 구간 — Step F 폐기 + 다른 자산 (해외 ETF 등) 평가 신규 ADR 또는 프로젝트 close. |

`docs/runbooks/step_f_summary_2026-MM-DD.md`:
- F1~F5 전 결과 표 (총수익률 · MDD · Sharpe · 게이트 판정)
- 베스트 후보 + DCA 대비 알파
- 시나리오 A/B/C 판정 근거

`docs/adr/0023-*.md`:
- 시나리오 A: `0023-<후보>-strategy-adoption.md`
- 시나리오 B: `0023-automation-deprecated-dca-only.md`
- 시나리오 C: `0023-step-f-failed-next-asset-evaluation.md`

## 의존성 + 게이트

```
PR0 (Step E close + Step F open)
  ↓ 머지 후
PR1 (F1 DCA — baseline 산출, 후속 PR 의 비교 기준)
  ↓ 머지 후 (DCA baseline 데이터 인용 가능)
PR2 (F2 Golden Cross) ──┐
PR3 (F3 모멘텀) ─────────┤  병렬 가능 (서로 의존 X)
PR4 (F4 저변동성) ───────┤
PR5 (F5 RSI MR) ─────────┘
  ↓ 모두 머지 후
PR6 (종합 + ADR-0023)
```

## 일정·자원 추정

| PR | 코드 | 테스트 | 백테스트 | 문서 | 합계 |
|---|---|---|---|---|---|
| PR0 (본) | 0 | 0 | 0 | 8h | **8h** |
| PR1 (DCA) | 4h | 4h | 1h | 2h | **11h** |
| PR2 (Golden Cross) | 6h | 6h | 1h | 2h | **15h** |
| PR3 (모멘텀) | 8h | 8h | 1h | 3h | **20h** |
| PR4 (저변동성) | 6h | 6h | 1h | 2h | **15h** |
| PR5 (RSI MR) | 6h | 6h | 1h | 2h | **15h** |
| PR6 (종합 + ADR) | 0 | 0 | 0 | 6h | **6h** |
| **총** | | | | | **90h** |

운영자 평일 1~2h/일 + AI 위임 가속 = **2~4주** 예상.

## 위험 요소

1. **F1 DCA 도 FAIL 가능**: 1년치 (2025-04~2026-04) KOSPI 200 historical 이 음수 구간이면 게이트 1·3 (절대 임계) FAIL. 시나리오 C 발생.
2. **F2~F5 모두 DCA 이김 못함**: 가장 흔한 retail 결과. 시나리오 B → 자동매매 폐기.
3. **데이터 부족**: F2 Golden Cross 는 2년 일봉 필요. pykrx + `data/stock_agent.db` 캐시 충분. 이미 `scripts/backfill_daily_bars.py` 도입 완료.
4. **인프라 전환 비용**: `BarLoader` 의 일봉 어댑터 신설 (`DailyBarLoader`?) 또는 기존 `HistoricalDataStore` 의 stream 래퍼. PR1 첫 작업.
5. **다중종목 리밸런싱 패턴**: F3·F4 가 한 시점 다중 청산+진입. 백테스트 엔진은 이미 검증됐으나 동일 시각 다중 시그널 처리 회귀 테스트 필요.

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵 (Step A~E 일중 가정 평가, 0 PASS).
- ADR-0021 — Step E VWAP-MR · Gap-Reversal 폐기 + Step F 전환 결정.
- ADR-0022 — Step F 게이트 재정의 (MDD>-25% · DCA 대비 알파 · Sharpe>0.3).
- `docs/runbooks/step_e_vwap_mr_2026-05-01.md` · `docs/runbooks/step_e_gap_reversal_2026-05-01.md` — Step E 결과.
- `src/stock_agent/strategy/CLAUDE.md` — Strategy Protocol + 기존 ORB/VWAP-MR/Gap-Reversal 회귀 기준 (보존).
- `src/stock_agent/backtest/CLAUDE.md` — BacktestEngine + 민감도 그리드.
- `scripts/backfill_daily_bars.py` — 일봉 캐시 백필 CLI (Step E Stage 3 신규).

## 다음 세션 시작 가이드

1. 본 파일 읽기 → 현재 위치 파악 (`PR4 완료, FAIL` 상태에서 시작).
2. PR5 (F5 RSI 평균회귀) RED-first TDD 사이클 시작 (옵션, 보너스):
   - `tests/test_strategy_rsi_mr.py` 작성 (unit-test-writer 위임)
   - FAIL 확인
   - `src/stock_agent/strategy/rsi_mr.py` 구현
   - GREEN 확인
   - `scripts/backtest.py --strategy-type rsi-mr` 라우팅 추가
   - 백테스트 실행 + runbook 작성 + ADR-0022 게이트 판정 (DCA baseline +51.50% 대비 알파 포함)
3. PR5 완료 후 또는 PR5 건너뛰고 PR6 (종합 판정 + ADR-0023) 진입 가능. DCA baseline 비교 기준: **+51.50% mark-to-market** (2025-04-22 ~ 2026-04-21, 시작 자본 2,000,000 KRW, 069500). 현재 Step F 결과: PR1 PASS · PR2 PASS (caveat) · PR3 FAIL · PR4 FAIL.
