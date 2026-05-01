# Step F PR2 — F2 Golden Cross 백테스트 결과 (2026-05-02)

> **작성**: 2026-05-02. ADR-0019 Step F PR2 (F2 Golden Cross) 결과 + ADR-0022 게이트 판정.

## 컨텍스트

ADR-0019 Step F (가설 풀 확장) 의 두 번째 후보 평가. ADR-0022 게이트 (MDD > -25%, DCA 대비 알파, Sharpe > 0.3) 적용.

DCA baseline 비교 기준: F1 PR1 (2026-05-02) 총수익률 +51.50% mark-to-market (KODEX 200 069500, 2025-04-22 ~ 2026-04-21, 시작 자본 2,000,000 KRW).

## 실행 명세

```bash
uv run python scripts/backtest.py \
  --loader=daily \
  --from 2024-06-01 --to 2026-04-21 \
  --symbols 069500 \
  --strategy-type golden-cross \
  --starting-capital 2000000 \
  --output-markdown data/step_f_golden_cross.md \
  --output-csv data/step_f_golden_cross_metrics.csv \
  --output-trades-csv data/step_f_golden_cross_trades.csv
```

### 설정

| 항목 | 값 |
|---|---|
| 전략 | GoldenCrossStrategy |
| 파라미터 | sma_period=200, position_pct=1.0 |
| 종목 | 069500 (KODEX 200 ETF) |
| 시작 자본 | 2,000,000 KRW |
| 데이터 소스 | pykrx 일봉 (data/stock_agent.db 캐시) |
| 평가 구간 | 2024-06-01 ~ 2026-04-21 (458 영업일) |
| 실 평가 시작 | 첫 200 영업일은 SMA(200) lookback — 시그널 없음. 실 평가 ≈ 2025-04 부터 (DCA baseline 1년 평가구간과 정렬) |

## 결과

| 항목 | 값 |
|---|---|
| 기간 | 2024-06-01 ~ 2026-04-21 (458 영업일) |
| 시작 자본 | 2,000,000 KRW |
| 거래 수 | 1 (cross-up 1회 + 가상청산 1회) |
| 진입 | 2025-05-07 @ 34,031.9980 KRW (slip 반영) × 58주 = 1,974,151 KRW |
| 가상청산 | 2026-04-21 @ 96,823.0800 KRW (slip 반영) × 58주 (스트림 종료 마지막 close) |
| 총수익률 (mark-to-market) | **+182.36%** |
| 최대 낙폭 (MDD) | **-20.52%** |
| 샤프 비율 (연환산) | **2.2753** |
| 승률 | 100% (1/1) |
| 평균 손익비 | 0.0 (1 trade — 분모 부족, 메트릭 계산 한계) |
| 일평균 거래 수 | 0.002 (1/458) |
| 순손익 | +3,647,209 KRW |

### MDD 해석

-20.52% 는 mark-to-market peak (2026-03~04 ≈ 5,800,000 KRW) 대비 낙폭. 실제 자본 최저점은 1,997,733 KRW (시작 2,000,000 대비 -0.11%) — 진입 직후 일시적 낙폭. peak-to-trough 정의상 MDD 가 크게 산출되는 것은 정상이나, "운영자 손실 한도" 관점에서는 최저점 절대금액도 병행 확인이 필요하다.

## ADR-0022 게이트 판정

| 게이트 | 기준 | 결과 | 판정 |
|---|---|---|---|
| 게이트 1 (MDD) | MDD > -25% | -20.52% | **PASS** |
| 게이트 2 (DCA 대비 알파) | (GC 총수익률) - (DCA 총수익률) > 0 | +182.36% - +51.50% = +130.86%p | **PASS** |
| 게이트 3 (Sharpe) | 연환산 Sharpe > 0.3 | 2.2753 | **PASS** |

**종합 판정: PASS (3 게이트 모두 충족)**

## DCA baseline 비교 (게이트 2)

| 지표 | DCA (PR1) | Golden Cross (PR2) | 차이 |
|---|---|---|---|
| 평가 구간 | 2025-04-22 ~ 2026-04-21 | 2024-06-01 ~ 2026-04-21 (실 평가 2025-04~) | — |
| 시작 자본 | 2,000,000 KRW | 2,000,000 KRW | — |
| 총수익률 | +51.50% | +182.36% | +130.86%p |
| MDD | -12.92% | -20.52% | -7.60%p |
| Sharpe (연환산) | 2.2683 | 2.2753 | +0.007 |

알파: +130.86%p (게이트 2 PASS). 단, 아래 caveat 2 (데이터 plausibility) 적용.

## 주요 caveat (운영자 검토 필수)

1. **단일 거래 한계**: trades=1 — sma_period=200 lookback 이후 cross-up 1회 발생. 평가 구간 동안 cross-down 미발생 (SMA 유지). hypothetical liquidation (마지막 close 기준 가상청산) 1건이 전부. 통계 신뢰도 낮음 — 승률 100%·평균 손익비 0 은 의미 있는 통계치가 아니다.

2. **데이터 plausibility**: 069500 가격이 2025-04 ≈ 33,000 → 2026-04 ≈ 96,920 (약 2.93×). KOSPI 200 ETF 1년 +180% 라는 의미 — 한국 시장 historical 대비 비현실적. pykrx 일봉 데이터의 수정주가 보정 여부 (액면분할·병합·배당) 검증 필요. PR1 DCA baseline 도 동일 소스이므로 알파 비교 자체는 유의하나, 절대 수익률 수치는 데이터 검증 후 재해석 권장.

3. **표본 부족**: 1 trade 로 통계적 결론 도출 불가. walk-forward 검증 (Phase 5) 시 모든 cross-up/cross-down 사이클을 포착해야 통계 신뢰도 확보.

4. **MDD 해석 주의**: peak-to-trough 기준 -20.52% 는 게이트 1 기준(-25%) 통과지만 실제 손실 경험(진입가 대비 -0.11%) 과 격차가 크다. 투자자 실제 손실 한도 관점 지표로는 별도 분석 필요.

## 다음 단계

운영자 결정 항목:

1. **069500 일봉 데이터 수정주가 보정 여부 검증** — pykrx 데이터 vs KRX 정보데이터시스템 [11003/11006] 직접 비교. 2025-04~2026-04 구간 KODEX 200 실제 가격 확인.
2. **SMA 50d / 100d 단축 평가 (선택)** — 더 많은 cross 사이클 표본 확보 (복수 trades → 통계 신뢰도 개선).
3. **단일 trade 한계 인정 (선택)** — 결과를 walk-forward 검증 (Phase 5) 의 단일 윈도로 취급.
4. **PR3 (F3 Cross-sectional 모멘텀) 진행** — Step F 가설 풀 다음 후보.

PR2 코드 산출물 (`strategy/golden_cross.py`, `backtest/golden_cross.py`, `scripts/backtest.py --strategy-type golden-cross`) 보존.

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵.
- ADR-0021 — Step E 폐기 + Step F 전환 결정.
- ADR-0022 — Step F 게이트 재정의 (MDD > -25%, DCA 대비 알파, Sharpe > 0.3).
- `docs/step_f_strategy_pool_plan.md` — Step F 가설 풀 plan.
- `docs/runbooks/step_f_dca_baseline_2026-05-02.md` — F1 DCA baseline (게이트 2 비교 기준).
- `data/step_f_golden_cross.md` — 자동 생성 리포트.
- `data/step_f_golden_cross_metrics.csv` / `data/step_f_golden_cross_trades.csv` — 자동 생성 메트릭/체결 CSV.
