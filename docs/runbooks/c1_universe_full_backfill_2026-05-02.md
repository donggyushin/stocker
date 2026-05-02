# C1 — universe 199 종목 전체 백필 + PR5 RSI MR 재평가 (2026-05-02)

> **작성**: 2026-05-02. ADR-0023 의 Phase 3 진입 조건 4종 중 **C1 (universe 199 전체 백필 + 재평가)** 결과 + ADR-0022 게이트 재판정.

## 컨텍스트

ADR-0023 (2026-05-02) 가 F5 RSI 평균회귀 (`RSIMRStrategy`) 를 Step F 1차 채택 후보로 선정하며 Phase 3 진입 조건으로 4 추가 검증 (C1~C4) 명시. 본 런북은 C1 검증 — `data/stock_agent.db` 일봉 캐시를 KOSPI 200 universe 199 종목 전체로 확장 후 PR5 재평가하여 ADR-0022 게이트 3종 재통과 확인.

원본 PR5 평가 (2026-05-02, `docs/runbooks/step_f_rsi_mr_2026-05-02.md`) 는 캐시된 101 종목 부분집합으로 실행 — 결과 PASS 였으나 universe 부분집합 편향 우려가 caveat 1 로 남아 있었다. 본 검증은 그 caveat 의 직접 해소.

## 백필 실행

### 명세

```bash
uv run python scripts/backfill_daily_bars.py \
  --from 2024-04-01 --to 2026-04-21 \
  --universe-yaml config/universe.yaml \
  --db-path data/stock_agent.db
```

- 시작일 `2024-04-01`: ADR-0023 본문 명시. 평가 구간 (2025-04-01 ~ 2026-04-21) 보다 1년 앞서 백필 — 후속 walk-forward (C2) 또는 SMA 기반 baseline 회귀 비교에 재사용 가능.
- 종료일 `2026-04-21`: PR1~PR5 평가 종료일 정렬.
- universe YAML: `config/universe.yaml` (KOSPI 200 199 종목).
- idempotent: `HistoricalDataStore` 캐시 hit 판정으로 기존 101 종목은 pykrx 재호출 생략. 신규 98 종목만 실제 네트워크 호출.

### 결과

- exit code: 0
- succeeded: 199 / failed: 0
- 실행 시간: ~9초 (대부분 캐시 hit, 신규 98 종목 백필).
- DB 검증: `SELECT COUNT(DISTINCT symbol) FROM daily_bars` → **200** (universe 199 + 069500 ETF, ETF 는 PR1 DCA baseline 캐시 잔존).
- 일자 범위: `2024-04-01` ~ `2026-04-21`.
- 로그: `data/c1_backfill_20260502_192921.log`.

trailing log 에 `KRX 로그인 실패: KRX_ID 또는 KRX_PW 환경 변수가 설정되지 않았습니다` 메시지 1줄 출력됐으나 백필 자체는 199/199 succeeded — pykrx 1.2.7 의 informational 메시지로 판단 (캐시 hit 다수 + 신규 98 종목도 KRX 로그인 없이 데이터 획득 성공). `~/.config/stocker/.env` 에 KRX_ID/KRX_PW 설정 확인 완료, repo `.env` 도 동일 — 환경 변수 자체는 정상 셋업.

## PR5 RSI MR 재평가 (universe 199)

### 명세

```bash
SYMBOLS=$(grep -oE '"[0-9]{6}"' config/universe.yaml | tr -d '"' | tr '\n' ',' | sed 's/,$//')
uv run python scripts/backtest.py \
  --loader=daily \
  --from 2025-04-01 --to 2026-04-21 \
  --symbols "$SYMBOLS" \
  --strategy-type rsi-mr \
  --starting-capital 2000000 \
  --rsi-period 14 \
  --oversold-threshold 30 \
  --overbought-threshold 70 \
  --stop-loss-pct 0.03 \
  --max-positions 10 \
  --output-markdown data/c1_step_f_rsi_mr_full199.md \
  --output-csv data/c1_step_f_rsi_mr_full199_metrics.csv \
  --output-trades-csv data/c1_step_f_rsi_mr_full199_trades.csv
```

원본 PR5 와 동일한 파라미터 (rsi_period=14, oversold=30, overbought=70, stop_loss_pct=0.03, max_positions=10, position_pct=1.0). 차이는 `--symbols` 입력 — 캐시된 101 → universe 199.

### 설정

| 항목 | 값 |
|---|---|
| 전략 | RSIMRStrategy (multi-symbol, per-bar 시그널) |
| 파라미터 | rsi_period=14, oversold=30, overbought=70, stop_loss_pct=0.03, max_positions=10, position_pct=1.0 |
| RSI 계산 | simple average gain/loss (Wilder smoothing 미사용) |
| universe | KOSPI 200 universe 199 종목 (`config/universe.yaml` 전체) |
| 시작 자본 | 2,000,000 KRW |
| 데이터 소스 | pykrx 일봉 (`data/stock_agent.db` 캐시) |
| 평가 구간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |

### 결과

| 항목 | 값 |
|---|---|
| 거래 수 | **177** (entry+exit pair 기준) |
| 총수익률 | **+63.44%** |
| 최대 낙폭 (MDD) | **-8.17%** |
| 샤프 비율 (연환산) | **2.2966** |
| 승률 | 36.72% (65/177) |
| 평균 손익비 | 4.2118 |
| 일평균 거래 수 | 0.686 |
| 순손익 | +1,268,702 KRW |
| 종료 시점 자본 | 3,268,702 KRW |
| 최저점 자본 (2025-05-23) | 1,957,979 KRW |
| 최고점 자본 | 3,277,464 KRW |

### 청산 사유 분포

| 사유 | 카운트 | 비율 |
|---|---|---|
| `stop_loss` | 109 | 61.6% |
| `take_profit` | 59 | 33.3% |
| `force_close` | 9 | 5.1% |

## 원본 PR5 (universe 101) 대비 변동

| 항목 | PR5 (101 종목) | C1 (199 종목) | 변동 |
|---|---|---|---|
| 거래 수 | 175 | 177 | +2 |
| 총수익률 | +56.31% | +63.44% | **+7.13%p** |
| MDD | -6.40% | -8.17% | -1.77%p (악화 — 여전히 게이트 1 통과) |
| Sharpe | 2.4723 | 2.2966 | -0.1757 (소폭 악화) |
| 승률 | 34.29% | 36.72% | +2.43%p |
| 평균 손익비 | 4.3799 | 4.2118 | -0.1681 |
| 순손익 (KRW) | 1,126,256 | 1,268,702 | +142,446 |
| stop_loss 비율 | 64.6% | 61.6% | -3.0%p |
| take_profit 비율 | 33.1% | 33.3% | +0.2%p |
| force_close 비율 | 2.3% | 5.1% | +2.8%p |

해석:
- **총수익률 +7.13%p 개선**: universe 확장으로 추가 평균회귀 기회 포착 — 98 신규 종목에서 entry 시그널 추가 발생.
- **MDD -1.77%p 악화**: 동시 보유 종목 다양화로 noise 손실 lot 누적 — 단 -8.17% 는 게이트 1 (-25%) 대비 16.83%p 여유.
- **Sharpe 소폭 악화**: 변동성 증가가 평균 수익 증가를 부분 상쇄. 여전히 게이트 3 (>0.3) 8배 여유.
- **승률·손익비**: 큰 변화 없음 — 평균회귀 본질 (작은 손실 다수 + 큰 익절 소수) 패턴 유지.
- **force_close 비율 +2.8%p**: 평가 종료일 (2026-04-21) 잔존 lot 가상 청산 카운트 증가 — 종목 풀 확대 효과로 평가 만기 시점 보유 lot 다수.

표본 크기 변화 (175 → 177) 가 작은 점에 주의: cross-sectional 평균회귀라 max_positions=10 한도가 binding constraint — 추가 종목은 신호 다양성을 늘리나 동시 보유는 10 으로 cap. 결과적으로 entry 시그널 풀이 커도 실제 진입은 cap 으로 제한된다. 이는 stop_loss 비율 하락 (-3%p) 으로도 확인 — universe 확대로 시그널 품질이 약간 향상됐다는 해석 가능.

## ADR-0022 게이트 재판정

| 게이트 | 기준 | C1 결과 (universe 199) | 판정 |
|---|---|---|---|
| 게이트 1 (MDD) | MDD > -25% | -8.17% | **PASS** |
| 게이트 2 (DCA 대비 알파) | (RSI MR 총수익률) - (DCA 총수익률) > 0 | 63.44% - 48.18% = **+15.26%p** | **PASS** |
| 게이트 3 (Sharpe) | 연환산 Sharpe > 0.3 | 2.2966 | **PASS** |

DCA baseline 비교 (`data/step_f_dca_same_window.md`, +48.18%) 는 069500 ETF 단일 종목 기준이라 universe 변경 무관 — same baseline 재사용. PR5 원본 알파 +8.13%p 에서 **+15.26%p 로 개선** — universe 확대 효과 = 알파 +7.13%p 증분.

**종합 판정: PASS (게이트 3종 전원 통과)**

## ADR-0023 C1 통과 결정

본 검증으로 **ADR-0023 의 C1 (universe 199 종목 전체 백필 + 재평가) 통과**. 후속 검증 우선순위:

1. **C2 (walk-forward 검증)** — `src/stock_agent/backtest/walk_forward.py` 본 구현 (PR #70 스켈레톤). 다년 일봉 캐시 (이미 2024-04-01 부터 백필됨) 로 2~4 분할 walk-forward.
2. **C3 (069500 수정주가 plausibility 검증)** — pykrx 일봉의 액면분할·병합·배당 수정 여부 KRX 정보데이터시스템 [11003/11006] 직접 비교.
3. **C4 (PR5 sensitivity grid)** — `step_f_grid` 신설 후 32~96 조합 스윕. stop_loss=-3% (청산 사유 61.6%) 가 핵심 축.

C1 결과는 PR5 의 통계 robustness 를 증가시킨다 — universe 부분집합 편향 caveat 가 해소됐고, trades=177 + MDD -8.17% + Sharpe 2.30 + 알파 +15.26%p 라는 모든 지표가 게이트 한도 대비 큰 여유를 유지한다. **ADR-0023 의 1차 채택 후보 결정 (PR5 RSIMRStrategy) 재확인**.

## 제한 사항 / 잔존 caveat

- **단일 1년 코호트**: 본 검증도 2025-04-01 ~ 2026-04-21 단일 구간. C2 walk-forward 통과 전까지 robustness 미보장.
- **데이터 plausibility**: pykrx 일봉 수정주가 검증 미해결 (C3). 모든 절대 수익률 (DCA +48% / RSI MR +63%) 의 신뢰도 baseline 미확정.
- **파라미터 sensitivity**: stop_loss=-3% / max_positions=10 등이 한 점만 평가. C4 grid 결과로 robustness 재검증 필요.
- **승률 36.72% (소폭 상승)**: 여전히 retail 운영 심리 부담 큼 (10 trade 중 6~7 손절). 자동매매로 감정 개입 차단되나 운영자 strategy 이탈 위험.
- **force_close 비율 5.1% (+2.8%p)**: 평가 만기 시점 잔존 lot 가상 청산. 실제 운영에서는 무중단 운영 가정으로 force_close 미발생 — 본 메트릭은 백테스트 산출물에만 의미.

## 참조

- ADR-0023 — F5 RSI 평균회귀 1차 채택 (조건부, C1~C4 명시).
- ADR-0022 — Step F 게이트 재정의 (MDD > -25% · DCA 알파 · Sharpe > 0.3).
- `docs/runbooks/step_f_rsi_mr_2026-05-02.md` — 원본 PR5 결과 (universe 101).
- `docs/runbooks/step_f_dca_same_window_2026-05-02.md` — DCA baseline (same-window, 069500 ETF).
- `data/c1_step_f_rsi_mr_full199.md` — 자동 생성 RSI 평균회귀 리포트 (universe 199).
- `data/c1_step_f_rsi_mr_full199_metrics.csv` / `data/c1_step_f_rsi_mr_full199_trades.csv` — 자동 생성 메트릭/체결 CSV.
- `data/c1_backfill_20260502_192921.log` — 백필 실행 로그.
