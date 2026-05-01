# Step F PR1 — F1 Buy & Hold DCA baseline (2026-05-02)

> ADR-0019 Step F (가설 풀 확장) 첫 번째 PR. ADR-0022 게이트 적용.

## 실행 명령

```bash
# 1. 일봉 캐시 백필 (idempotent)
uv run python scripts/backfill_daily_bars.py --symbols 069500 --from 2025-04-22 --to 2026-04-21

# 2. DCA 백테스트 실행
uv run python scripts/backtest.py \
  --strategy-type=dca \
  --loader=daily \
  --from=2025-04-22 \
  --to=2026-04-21 \
  --symbols=069500 \
  --starting-capital=2000000 \
  --monthly-investment=100000 \
  --output-markdown=data/step_f_dca_baseline.md \
  --output-csv=data/step_f_dca_baseline_metrics.csv \
  --output-trades-csv=data/step_f_dca_baseline_trades.csv
```

## 결과

| 항목 | 값 |
|---|---|
| 기간 | 2025-04-22 ~ 2026-04-21 (1년, 243 영업일) |
| target_symbol | `069500` (KODEX 200) |
| 시작 자본 | 2,000,000 KRW |
| 월 투자금 | 100,000 KRW |
| 매수 횟수 | 13 lots |
| 총수익률 (mark-to-market) | **+51.50%** |
| 최대 낙폭 (MDD) | **-12.92%** |
| 샤프 비율 (연환산) | **2.2683** |
| 승률 (lot 별 가상 청산) | 100.00% |
| 순손익 | +1,029,944 KRW |
| 종료 자본 | 3,029,944 KRW |

## ADR-0022 게이트별 판정

| 게이트 | 기준 | 결과 | 판정 |
|---|---|---|---|
| 게이트 1 (MDD) | MDD > -25% | -12.92% | **PASS** |
| 게이트 2 (DCA 대비 알파) | baseline 대비 양의 알파 | N/A (자기 자신 baseline) | **N/A** |
| 게이트 3 (Sharpe) | 연환산 Sharpe > 0.3 | 2.2683 | **PASS** |

**종합 판정: PASS** (적용 가능 게이트 전원 통과)

## 한계·주의

- **단일 종목 단일 구간 1년 표본** — ADR-0017 의 240 영업일 기준은 충족하지만, 1년치만으로는 generalization 한계. 후속 PR (F2~F5) 비교 baseline 으로만 사용 권장.
- **mark-to-market 기준 총수익률** — 슬리피지·세금 미반영. `TradeRecord.net_pnl_krw` 는 lot 별 가상 청산 비용 반영값.
- **qty=1 양자화 영향** — 월 투자금 100k 대비 ETF 단가 55k~86k 로 매수마다 1~2주 단위. 더 큰 자본 + 월 투자금이면 정밀도 개선.
- **2025-04~2026-04 KOSPI 200 강세 구간** 의 결과. 다른 시점 (음수 구간) 백테스트 시 PASS 보장 X.
- `BacktestEngine` 우회 설계 — `compute_dca_baseline` 은 다중 lot 누적·mark-to-market 을 직접 처리하므로 단일 lot 가정 + force_close 가정을 전제한 `BacktestEngine` 과 수치가 다를 수 있음. 설계 사유는 `src/stock_agent/backtest/CLAUDE.md` 참조.

## 후속 PR 인용 베이스

F2~F5 의 게이트 2 (DCA 대비 알파) 비교 기준:

- **+51.50% mark-to-market** (1년치, 시작 자본 2,000,000 KRW, 069500 KODEX 200, 2025-04-22 ~ 2026-04-21)
- 후속 전략 평가 시 **동일 데이터 소스 + 동일 시작 자본 + 동일 기간** 유지 필수.

## 코드 산출물

| 모듈 | 경로 | 공개 심볼 |
|---|---|---|
| DCA 전략 | `src/stock_agent/strategy/dca.py` | `DCAStrategy`, `DCAConfig` |
| 일봉 어댑터 | `src/stock_agent/data/daily_bar_loader.py` | `DailyBarLoader`, `DailyBarSource`, `KST` |
| DCA 평가 함수 | `src/stock_agent/backtest/dca.py` | `DCABaselineConfig`, `compute_dca_baseline` |
| CLI 라우팅 | `scripts/backtest.py` | `--strategy-type=dca`, `--loader=daily`, `--monthly-investment` |

테스트 (신규 79건):

| 파일 | 건수 |
|---|---|
| `tests/test_strategy_dca.py` | 31 |
| `tests/test_daily_bar_loader.py` | 16 |
| `tests/test_backtest_dca.py` | 32 |
| 합계 | **79** |

## 참조

- ADR-0022 — Step F 게이트 재정의 (MDD > -25% · DCA 대비 알파 · Sharpe > 0.3)
- ADR-0021 — Step E 폐기 + Step F 전환 결정
- `docs/step_f_strategy_pool_plan.md` — Step F 전체 진행 계획
- `src/stock_agent/strategy/CLAUDE.md` — DCAStrategy 상세
- `src/stock_agent/data/CLAUDE.md` — DailyBarLoader 상세
- `src/stock_agent/backtest/CLAUDE.md` — compute_dca_baseline 상세
