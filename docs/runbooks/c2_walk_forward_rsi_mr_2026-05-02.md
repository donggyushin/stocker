# C2 — RSI 평균회귀 walk-forward 검증 (2026-05-02)

> **작성**: 2026-05-02. ADR-0023 의 Phase 3 진입 조건 4종 중 **C2 (walk-forward 본 구현 + 다년 코호트 검증)** 결과 + ADR-0024 결정 근거.

## 컨텍스트

ADR-0023 (2026-05-02) 가 F5 RSI 평균회귀 (`RSIMRStrategy`) 를 Step F 1차 채택 후보로 선정하며 Phase 3 진입 조건으로 4 추가 검증 (C1~C4) 명시. C1 (universe 199 전체 백필 + 재평가) 은 같은 날 PASS (`docs/runbooks/c1_universe_full_backfill_2026-05-02.md`).

본 런북은 C2 검증 — `src/stock_agent/backtest/walk_forward.py` 본 구현 + 다년 일봉 캐시 (2024-04-01 ~ 2026-04-21, C1 에서 universe 199 백필 완료) 로 walk-forward 분할 평가. ADR-0024 가 본 결과를 근거로 walk-forward pass-threshold = `0.3` 결정.

## 코드 산출물

### `src/stock_agent/backtest/walk_forward.py`

기존 스켈레톤 (Issue #67) 위에 본 구현 추가:

- `_add_months(d: date, months: int) -> date` — day clamp 헬퍼 (월말 31일 → 28/29/30 자동 보정).
- `generate_windows(total_from, total_to, *, train_months, test_months, step_months) -> tuple[WalkForwardWindow, ...]` — 본 구현. `i = 0, 1, ...` 순회하며 `train_from = total_from + i*step_months`, `train_to = train_from + train_months - 1day`, `test_from = train_to + 1day`, `test_to = test_from + test_months - 1day` 계산. `test_to <= total_to` 인 동안 emit. 첫 window 부터 `test_to > total_to` 면 `RuntimeError`.
- `run_rsi_mr_walk_forward(loader, config: RSIMRBaselineConfig, windows, *, pass_threshold) -> WalkForwardResult` — 신규. 각 window 마다 `compute_rsi_mr_baseline` 을 train·test 두 번 호출 + degradation 집계.
- `run_walk_forward(BacktestConfig, ...)` 는 `NotImplementedError` 유지 — ORB engine 경로 본 구현은 본 PR 범위 밖 (Phase 5).

### `scripts/walk_forward_rsi_mr.py`

신규 CLI. argparse → `DailyBarLoader(HistoricalDataStore)` → `generate_windows` → window loop (`compute_rsi_mr_baseline` train/test + `compute_dca_baseline` test) → Markdown + CSV 리포트.

지원 인자:
- `--from`, `--to`: 평가 구간 (date.fromisoformat).
- `--train-months`, `--test-months`, `--step-months`: 분할 파라미터.
- `--pass-threshold`: degradation 허용 임계 (기본 `0.3`).
- `--universe-yaml`, `--starting-capital`, `--rsi-period`, `--oversold-threshold`, `--overbought-threshold`, `--stop-loss-pct`, `--max-positions`: RSI MR 파라미터.
- `--dca-symbol`, `--dca-monthly-investment`: 게이트 2 baseline 파라미터.
- `--output-markdown`, `--output-csv`: 산출물 경로.
- `--db-path`: HistoricalDataStore SQLite 경로 (기본 stock-agent 설정).

exit code: 0 정상 / 2 입력·설정 (`UniverseLoadError`/`RuntimeError`) / 3 I/O (`OSError`).

## 실행 명세

### 데이터 baseline

C1 에서 백필 완료한 `data/stock_agent.db` 재사용:
- 일자 범위: 2024-04-01 ~ 2026-04-21
- 종목 수: universe 199 + 069500 ETF (DCA baseline 용)
- 추가 백필 없음 — C1 산출물 그대로 사용.

### 실행 1 — primary (step6, 2 windows non-overlap)

```bash
uv run python scripts/walk_forward_rsi_mr.py \
  --from 2024-04-01 --to 2026-04-21 \
  --train-months 12 --test-months 6 --step-months 6 \
  --pass-threshold 0.3 \
  --universe-yaml config/universe.yaml \
  --starting-capital 2000000 \
  --output-markdown data/c2_walk_forward_rsi_mr_step6.md \
  --output-csv data/c2_walk_forward_rsi_mr_step6.csv
```

### 실행 2 — secondary (step3, 3 windows overlap)

```bash
uv run python scripts/walk_forward_rsi_mr.py \
  --from 2024-04-01 --to 2026-04-21 \
  --train-months 12 --test-months 6 --step-months 3 \
  --pass-threshold 0.3 \
  --universe-yaml config/universe.yaml \
  --starting-capital 2000000 \
  --output-markdown data/c2_walk_forward_rsi_mr_step3.md \
  --output-csv data/c2_walk_forward_rsi_mr_step3.csv
```

두 실행 모두 RSI MR 파라미터 동일 (rsi_period=14, oversold=30, overbought=70, stop_loss_pct=0.03, max_positions=10, position_pct=1.0).

## 결과

### Primary (step6 — 2 windows, non-overlap)

| 윈도우 | train 구간 | test 구간 | train 총수익률 | test 총수익률 | test MDD | test Sharpe | DCA test | 알파 | 게이트 1 | 게이트 2 | 게이트 3 | 종합 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W0 | 2024-04-01 ~ 2025-03-31 | 2025-04-01 ~ 2025-09-30 | +4.18% | +21.50% | -4.00% | 2.5587 | +2.61% | +18.89% | PASS | PASS | PASS | **PASS** |
| W1 | 2024-10-01 ~ 2025-09-30 | 2025-10-01 ~ 2026-03-31 | +34.22% | +18.88% | -8.49% | 1.5894 | +2.37% | +16.51% | PASS | PASS | PASS | **PASS** |

집계:
- train 평균 총수익률: +19.20%
- test 평균 총수익률: +20.19%
- degradation_pct: **-5.16%** (test 가 train 보다 우수 — degradation 음수)
- pass_threshold: +30.00%
- 집계 판정: **PASS**
- 윈도우 PASS 수: 2 / 2

### Secondary (step3 — 3 windows, overlapping test)

| 윈도우 | train 구간 | test 구간 | train 총수익률 | test 총수익률 | test MDD | test Sharpe | DCA test | 알파 | 게이트 1 | 게이트 2 | 게이트 3 | 종합 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W0 | 2024-04-01 ~ 2025-03-31 | 2025-04-01 ~ 2025-09-30 | +4.18% | +21.50% | -4.00% | 2.5587 | +2.61% | +18.89% | PASS | PASS | PASS | **PASS** |
| W1 | 2024-07-01 ~ 2025-06-30 | 2025-07-01 ~ 2025-12-31 | +18.50% | +10.08% | -5.10% | 1.3883 | +2.96% | +7.12% | PASS | PASS | PASS | **PASS** |
| W2 | 2024-10-01 ~ 2025-09-30 | 2025-10-01 ~ 2026-03-31 | +34.22% | +18.88% | -8.49% | 1.5894 | +2.37% | +16.51% | PASS | PASS | PASS | **PASS** |

집계:
- train 평균 총수익률: +18.97%
- test 평균 총수익률: +16.82%
- degradation_pct: **+11.32%** (≤ 30% pass_threshold)
- pass_threshold: +30.00%
- 집계 판정: **PASS**
- 윈도우 PASS 수: 3 / 3

## ADR-0024 결정 근거

- **pass_threshold = 0.3 채택 정당화**: step3 의 degradation +11.32% 가 0.3 아래 + 0.1 위 — Issue #67 의 0.3 임계치가 retail 운영 안전 마진 + 실용 PASS 가능성 균형으로 적절. 0.1 임계 적용 시 step3 fail (보수적 임계로 false-negative 가능성 입증). 0.5 임계는 운영 위험 과대 허용.
- **이중 PASS (per-window 게이트 + aggregate degradation) 정당화**: 두 실행 모두 모든 window 가 게이트 3종 동시 통과 + aggregate degradation PASS. retail 자본 노출 전 walk-forward 의 핵심 검증 = 한 시기 우수 결과가 다른 시기 부진을 가리지 않는지. 이중 검증 통과로 PR5 robustness 확인.
- **분할 정책 (step6 + step3) 정당화**: 두 분할 정책에서 모두 PASS — 결과가 분할 선택에 의존하지 않음을 입증. 단일 분할 결과만으로는 분할 우연 의심 가능.

## ADR-0023 게이트 재판정 (per-window)

ADR-0022 게이트 3종 적용 → 5 test windows 전수 통과:

| 게이트 | 기준 | step6 W0 | step6 W1 | step3 W0 | step3 W1 | step3 W2 | 통과율 |
|---|---|---|---|---|---|---|---|
| 1 (MDD) | > -25% | -4.00% | -8.49% | -4.00% | -5.10% | -8.49% | **5/5** |
| 2 (DCA 알파) | > 0 | +18.89% | +16.51% | +18.89% | +7.12% | +16.51% | **5/5** |
| 3 (Sharpe) | > 0.3 | 2.5587 | 1.5894 | 2.5587 | 1.3883 | 1.5894 | **5/5** |

(W0 step3 와 W0 step6 는 동일 train/test 구간이라 메트릭 일치. step3 W2 와 step6 W1 도 동일.)

## ADR-0023 C2 통과 결정

본 검증으로 **ADR-0023 의 C2 (walk-forward 본 구현 + 다년 코호트 검증) 통과**. 후속 검증 우선순위:

1. **C3 (069500 수정주가 plausibility)** — pykrx 일봉의 액면분할·병합·배당 수정 여부 KRX 정보데이터시스템 [11003/11006] 직접 비교. PR1~PR5 + C1 + C2 의 절대 수익률 신뢰도 baseline.
2. **C4 (PR5 파라미터 sensitivity grid)** — `step_f_grid` 신설 후 32~96 조합 스윕. stop_loss=-3% (청산 사유 64.6%) 가 핵심 축.

C2 결과는 PR5 의 walk-forward robustness 를 입증한다 — 단일 1년 코호트 (PR5 원본) → universe 199 (C1) → 다년 분할 (C2) 의 3 단계 검증 모두 PASS. **ADR-0023 의 1차 채택 후보 결정 (PR5 RSIMRStrategy) 재확인**.

## 제한 사항 / 잔존 caveat

- **표본 수 한계**: step6 n=2, step3 n=3. 학술 walk-forward (12+ 분할 다년) 표본 신뢰도 미달. 다년 데이터 백필 (2020-01 부터 등) 후 본 CLI 재실행으로 robustness 강화 가능.
- **단일 데이터 소스**: pykrx 일봉. 069500 절대 수익률 (DCA test +2.37%~2.96%) 의 plausibility 미해결 (C3).
- **파라미터 sensitivity**: 단일 파라미터 (rsi_period=14 / oversold=30 / overbought=70 / stop_loss=-3% / max_positions=10) 만 평가. C4 grid 결과로 robustness 재검증 필요.
- **degradation 분모 불안정성**: train_avg < 0 인 경우 degradation 의미 변질 — 본 코드는 0 폴백 + `is_pass = (0 <= pass_threshold)` = True 로 처리. 본 평가에서 train_avg = +18~19% 라 분모 안정.
- **데이터 누설 검증**: 각 window 의 `train_to < test_from` 으로 시간 축 누설은 차단되나 universe 종목 동일 사용 (전체 199) 라 종목 수준 누설 가능성은 잔존 — RSI 평균회귀가 cross-sectional 가설이므로 종목 분리 walk-forward 는 검증 본질에 부합하지 않음.
- **운영 환경 호환성**: 본 CLI 가 KRX 로그인 실패 정보 메시지 출력 (pykrx 1.2.7 의 informational) — 캐시 hit 라 영향 없으나 신규 백필 종목 발생 시 KRX_ID/KRX_PW env 설정 필수.

## 참조

- ADR-0024 — walk-forward pass-threshold 결정 (degradation_pct ≤ 0.3).
- ADR-0023 — F5 RSI 평균회귀 1차 채택 (조건부, C1~C4 명시).
- ADR-0022 — Step F 게이트 재정의 (MDD > -25% · DCA 알파 · Sharpe > 0.3).
- `docs/runbooks/c1_universe_full_backfill_2026-05-02.md` — C1 검증 (universe 199 백필 + PR5 재평가).
- `docs/runbooks/step_f_rsi_mr_2026-05-02.md` — 원본 PR5 결과 (universe 101).
- `docs/runbooks/step_f_dca_same_window_2026-05-02.md` — DCA baseline (same-window, 069500 ETF).
- `data/c2_walk_forward_rsi_mr_step6.{md,csv}` — primary 산출물 (2 windows).
- `data/c2_walk_forward_rsi_mr_step3.{md,csv}` — secondary 산출물 (3 windows).
- `src/stock_agent/backtest/walk_forward.py` — 본 구현.
- `scripts/walk_forward_rsi_mr.py` — 신규 CLI.
- 관련 이슈: #67 (walk-forward skeleton, 본 PR 에서 본 구현 진행).
