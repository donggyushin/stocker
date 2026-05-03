# Python 한국주식 자동매매 시스템 — 계획

## Context (왜 만드는가)

사용자는 Python 기반 한국주식 자동매매 시스템을 구축하고자 함. 도메인 지식이 제한적이므로, 본 계획은 **학습 → 검증 → 소액 실전**으로 단계적 리스크 노출을 원칙으로 한다.

### 핵심 전제 2가지 (반드시 동의 필요)
1. **토스증권은 개인용 Open API가 없음 (2026-04 기준)** → 자동매매 불가. 한투증권(KIS Developers) 계좌 신규 개설로 진행.
2. **"무조건 수익"은 어떤 시스템도 보장 불가.** 데이트레이딩은 통계적으로 개인 70~90%가 손실. 거래세(0.18%) + 수수료 + 슬리피지가 마진을 잠식. 본 시스템의 목표는 **"손실을 최소화하며 데이터로 검증한 전략을 규율 있게 실행"** 하는 것.

### 확정된 결정 사항
| 항목 | 결정 |
|---|---|
| 증권사 | 한국투자증권 KIS Developers (신규 개설) |
| 실행 환경 | 로컬 맥북, 장중(9:00~15:30) 상시 가동 (MVP) |
| 매매 빈도 | 데이 트레이딩 (당일 청산) |
| 종목 유니버스 | KOSPI 200 대형주 |
| 초기 자본 | 100~200만원 |
| 알림 | 텔레그램 봇 |
| MVP 스타일 | 빠른 시작형 — 심플 전략 1개 + 모의투자 |

---

## 전략 선택: Opening Range Breakout (ORB)

**선정 이유**: 데이트레이딩 교과서 전략 중 로직이 가장 명확·검증 가능·초보자가 실수하기 어려움.

**규칙**
- 9:00~9:30 (30분) 동안 각 종목의 고가(OR-High), 저가(OR-Low) 기록
- 9:30 이후 OR-High 상향 돌파 시 **시장가 매수**
- 진입 후: 손절 = 진입가 × (1 - 1.5%), 익절 = 진입가 × (1 + 3.0%), 또는 **15:00 강제 청산**
- KOSPI 200 중 9:00~9:30 거래대금 상위 N개 종목만 후보 (유동성 필터)
- 한국 공매도 제한으로 매수 방향(long-only)만 구현

**대안 전략 (Phase 5 이후 실험용, 지금은 구현 X)**: VWAP 반등, 5/20분봉 이동평균 크로스.

---

## 리스크 관리 기본값 (100~200만원 기준, config로 조정 가능)

**Phase 3 모의투자 운영 기준값 (ADR-0025, 2026-05-03 확정)** — ORB 시절 기본값을 RSI MR 일봉 전략 특성에 맞게 재정의.

| 항목 | 운영 기준값 (ADR-0025) | 적용 대상 | 이유 |
|---|---|---|---|
| 종목당 진입 금액 | 세션 자본의 10% | `RiskConfig.position_pct` | max_positions=10 과 결합해 전체 자본 균등 분배 |
| 동시 보유 종목 수 | 최대 10종목 | `RiskConfig.max_positions` | ADR-0023 C4 sensitivity grid 현행값 PASS |
| 종목당 손절 | -3% (stop_loss_pct) | `RSIMRConfig.stop_loss_pct` | ADR-0023 C4 96 조합 중 최고 통과율 |
| 종목당 익절 | RSI 과매수(70) 도달 시 청산 | `RSIMRConfig` | 고정 익절 미사용 — 평균회귀 전략 특성 |
| 강제청산 (force_close_at) | 운영 미사용 | main.py cron 비활성화 | 일봉 전략 — 다음 영업일 시초가 또는 분봉 stop_loss 가드로 청산 |
| 일일 손실 한도 | 자본의 -2% 도달 시 당일 매매 중단 | `RiskConfig.daily_loss_limit_pct` | 전략 무관 자본 보호 게이트 — 보존 |
| 일일 최대 진입 | 5회 | `RiskConfig.daily_max_entries` | RSI MR 백테스트 평균 ≈ 0.7건/일 기준 상한 |
| 최소 거래금액 | 10만원/종목 | `RiskConfig.min_notional_krw` | 수수료 비중 관리 — 보존 |

ORB 시절 기본값(position_pct 20%, max_positions 3, daily_max_entries 10, 손절 -1.5%, 익절 +3.0%, 15:00 강제청산)은 ADR-0025 로 대체됨. `RSIMRConfig.position_pct=1.0` 은 백테스트 내부 자금 배분 비율(백테스트 결과 재현용)이며 `RiskConfig.position_pct=0.10` 과 의미 차원이 다름 — 혼동 금지 (ADR-0025 맥락 섹션 참조).

---

## 아키텍처 & 기술 스택

**언어/런타임**: Python 3.12+

**CI**: `.github/workflows/ci.yml` — PR 및 main push 시 `uv sync --frozen` → ruff (lint + format) 정적 분석 → pytest 자동 실행. main 브랜치는 CI job `Lint, format, test` 통과 없이 머지 불가 (required status check, `strict=true`). black 폐기 (ADR-0026, 2026-05-03).

**주요 라이브러리**
- `python-kis 2.x`: KIS Developers REST/WebSocket 래퍼 (오픈소스 검증됨, mojito2 미사용)
- `pykrx`: KRX 공식 데이터(백테스트용 과거 OHLCV, KOSPI 200 구성종목)
- 백테스트 엔진: 자체 시뮬레이션 루프 (`src/stock_agent/backtest/engine.py`) — `backtesting.py` 라이브러리는 다중종목·포트폴리오 게이팅(동시 3종목 한도·서킷브레이커·일일 진입 횟수 한도) 표현 불가 + AGPL 라이센스 부담으로 폐기 결정 (2026-04-20). 외부 의존성 추가 없음.
- `pandas`, `numpy`: 데이터 처리
- `pydantic`, `pydantic-settings`: 설정 검증
- `loguru`: 구조화 로깅
- `python-telegram-bot`: 알림
- `APScheduler`: 스케줄링(장 시작/종료, 30분 OR 확정 타이밍 등)
- `pytest`, `pytest-asyncio`: 테스트
- `uv`: 의존성 관리 (빠르고 재현성 좋음)

**데이터 저장**: SQLite (MVP) → 체결/주문/일일 PnL 저장. 추후 PostgreSQL 전환 여지.

---

## 디렉토리 구조

```text
stock-agent/
├── pyproject.toml
├── .env.example                # KIS_APP_KEY, KIS_APP_SECRET, TELEGRAM_* 등
├── .gitignore                  # .env, data/, logs/
├── README.md
├── config/
│   ├── universe.yaml           # KOSPI 200 종목코드 (수동 관리, 연 2회 정기변경)
│   └── strategy.yaml           # ORB 파라미터, 리스크 한도
├── src/stock_agent/
│   ├── __init__.py
│   ├── config.py               # pydantic 설정 로더
│   ├── broker/
│   │   ├── kis_client.py       # 토큰 관리, 주문, 조회
│   │   └── rate_limiter.py     # KIS 초당 호출 제한 대응
│   ├── data/
│   │   ├── historical.py       # pykrx 일봉 + SQLite 캐시
│   │   ├── universe.py         # KOSPI 200 유니버스 YAML 로더
│   │   └── realtime.py         # WebSocket 우선 + REST 폴링 fallback 실시간 시세 (RealtimeDataStore)
│   ├── strategy/
│   │   ├── base.py             # Strategy 인터페이스
│   │   └── orb.py              # Opening Range Breakout
│   ├── risk/
│   │   └── manager.py          # 포지션 사이징, 손절/익절, 일일 한도
│   ├── execution/
│   │   ├── executor.py         # 주문 실행, 체결 추적, 동기화
│   │   └── state.py            # 보유 포지션 메모리 상태
│   ├── backtest/
│   │   ├── engine.py           # BacktestEngine + DTO (자체 시뮬레이션 루프)
│   │   ├── costs.py            # 슬리피지·수수료·거래세 순수 함수
│   │   ├── metrics.py          # 총수익률·MDD·샤프·승률·평균손익비·일평균거래수
│   │   └── loader.py           # BarLoader Protocol + InMemoryBarLoader
│   ├── monitor/
│   │   ├── notifier.py         # 텔레그램 알림
│   │   └── logger.py           # loguru 설정
│   ├── storage/
│   │   └── db.py               # SQLite (체결/PnL 로그)
│   └── main.py                 # 실행 진입점 (paper/live 모드)
├── tests/
│   ├── test_strategy_orb.py
│   ├── test_risk_manager.py
│   └── test_kis_client.py      # mocked
├── scripts/
│   ├── backtest.py             # CLI: 과거 구간 백테스트
│   └── healthcheck.py          # API 연결/토큰 점검
└── data/                       # gitignore (sqlite, 캐시)
```

---

## 단계별 로드맵 (총 약 4주)

### Phase 0 — 계좌·API·환경 준비 (2~3일) — 완료 2026-04-19
- 한투증권 비대면 계좌 개설 (모바일 MTS)
- KIS Developers 가입 → **모의투자용 APP_KEY/SECRET 먼저** 발급
- 레포 초기화 (`uv init`), `.env`/`.gitignore`, `pre-commit`(ruff lint + format) 세팅
- 텔레그램 봇 생성 (@BotFather), 채팅 ID 확보
- **산출물**: `scripts/healthcheck.py` 실행 시 모의 계좌 잔고 조회 성공 + "hello" 텔레그램 알림 수신 — **달성 확인** (KIS 토큰 발급 OK, 잔고 10,000,000원 조회 OK, 텔레그램 수신 OK)

### Phase 1 — 데이터 파이프라인 & 브로커 래퍼 (4~5일) — ✅ PASS 선언 (코드·테스트 레벨) — 2026-04-19
- `broker/kis_client.py`: 토큰 발급/갱신, 잔고 조회, 매수/매도 주문, 미체결 조회
- `data/historical.py`: pykrx로 KOSPI 200 구성종목 + 일봉 수집 & SQLite 캐시 (pykrx는 분봉 OHLCV 미지원. 분봉은 `data/realtime.py` 장중 폴링 누적 범위)
- `data/realtime.py`: 장중 분봉 폴링(우선) 또는 WebSocket 실시간 체결가 (후순위)
- 레이트 리미터 (KIS 초당 ~20회 제한 대응)
- **산출물**: 단위 테스트 + `healthcheck.py`에서 특정 종목 현재가 조회 성공

### Phase 2 — 전략 + 백테스팅 + 리스크 모듈 (5~7일) — 진행 중
- [x] `strategy/orb.py`: 규칙 구현 (진입/청산 시그널 생성) — 완료 2026-04-20
- [x] `risk/manager.py`: 포지션 사이징, 손절/익절, 일일 손실 한도 — 완료 2026-04-20
- [x] `backtest/engine.py`: 자체 시뮬레이션 루프 엔진 코어 — 완료 2026-04-20 (코드·테스트 레벨). 슬리피지 0.1% 시장가 불리, 수수료 0.015%(매수·매도 대칭), 거래세 0.18%(매도만) 반영.
  - 리포트 항목: 총수익률, MDD, 샤프, 승률, 평균 손익비, 일평균 거래수, 수수료·세금 반영 후 순수익
- [x] `data/minute_csv.py`: CSV 분봉 어댑터 — 완료 2026-04-20. 레이아웃 `{csv_dir}/{symbol}.csv`, 헤더 `bar_time,open,high,low,close,volume`. 누락 파일 fail-fast, 여러 심볼 `heapq.merge` 정렬 스트리밍, stdlib 전용 추가 의존성 0.
- [x] `data/kis_minute_bars.py`: KIS 과거 분봉 API 어댑터 — 완료 2026-04-22 (ADR-0016). `KisMinuteBarLoader` + `KisMinuteBarLoadError`. KIS API `FHKST03010230` (`kis.fetch()` 로우레벨 직접 호출). 120건 역방향 페이지네이션, `EGW00201` 레이트 리밋 재시도(최대 3회), SQLite 캐시 `data/minute_bars.db` (별도 파일). 실전(live) 키 전용, `install_order_block_guard` 설치. `scripts/backtest.py`·`scripts/sensitivity.py` 에 `--loader={csv,kis}` 옵션 추가. **중요 제한**: KIS 서버 최대 1년 분봉 보관 — 2~3년 PASS 기준은 충족 불가. Phase 2 PASS 검증은 CSV 경로로 수행. 2~3년 데이터는 Issue #5 후속으로 별도 데이터 소스 분리 평가. 테스트 39건.
- [x] `scripts/backfill_minute_bars.py`: KIS 과거 분봉 캐시 일괄 적재 CLI — 완료 2026-04-22 (Issue #47). `KisMinuteBarLoader.stream(start, end, (symbol,))` 호출 → `data/minute_bars.db` 적층. 인자: `--from`/`--to` (required, ISO date), `--symbols` (default 유니버스 전체), `--throttle-s` (float, default 0.0), `--cache-db-path` (Path | None). exit code: 0 정상 / 1 일부 심볼 `KisMinuteBarLoadError` / 2 입력·설정 오류 / 3 I/O 오류. 실 KIS API 접촉 0 테스트 37건. Phase 2 PASS 검증 전 선행 실행 필수.
- [x] 파라미터 민감도 그리드: OR 구간(15/30분), 손절/익절 레벨 비교 — 완료 2026-04-20. `src/stock_agent/backtest/sensitivity.py` + `scripts/sensitivity.py` + `tests/test_sensitivity.py` (80건). 기본 그리드 32 조합, 현재 운영 기본값 포함. 민감도 리포트는 sanity check 용도이며 walk-forward 검증을 대체하지 않는다.
- [x] `scripts/backtest.py`: 단일 런 백테스트 CLI — 완료 2026-04-20. `MinuteCsvBarLoader` + `BacktestEngine` 1회 실행 → Markdown·메트릭 CSV·체결 CSV 3종 산출. PASS 판정 리포트 기록(낙폭 절대값 15% 미만일 때 PASS, 즉 `mdd > Decimal("-0.15")` 이면 PASS — 경계 -15% 정확값은 FAIL). exit code 규약: `0` 정상 / `2` `MinuteCsvLoadError`·`UniverseLoadError`·`RuntimeError` / `3` `OSError`. 테스트 65건.
- **산출물**: 파라미터 민감도 테이블 (`scripts/sensitivity.py` CLI, CSV/Markdown 출력) — 완료. 단일 런 백테스트 CLI (`scripts/backtest.py`) — 완료. KIS 과거 분봉 API 어댑터 (`data/kis_minute_bars.py`, ADR-0016) — 완료 2026-04-22. 백필 전용 CLI (`scripts/backfill_minute_bars.py`, Issue #47) — 완료 2026-04-22. 백테스트 리포트 HTML/노트북은 Phase 5 후보 (의도적 defer). **Phase 2 PASS 선언 잔여 조건**: `scripts/backfill_minute_bars.py` 로 1년치 KIS 분봉 백필 후 `uv run python scripts/backtest.py --loader=kis --from <1년 전> --to <오늘>` 실행 → 낙폭 절대값 15% 미만 (MDD > -15%) 확인. 표본 기간 240 영업일 이상·다중 종목 필수 (ADR-0017). Issue #36 close 예정.

### Phase 3 — 모의투자 자동 실행 (5~7일)
- **착수 전제**: 실전 APP_KEY (시세 전용) 발급 완료 + KIS Developers 포털에서 IP 화이트리스트 등록 + `healthcheck.py` 4종 통과 (**평일 장중 09:00~15:30 KST에 실행해야 안정적으로 통과** — 4번 체크 `check_realtime_price`는 WebSocket 모드에서 장중 체결 이벤트가 있어야 2초 내 `TickQuote` 수신 가능; 나머지 3종은 시간대 무관).
  - [x] 통과 확인 — 2026-04-21 평일 장중 healthcheck 4종 그린, WebSocket 체결 수신 OK.
- [x] `execution/executor.py`: 신호 → 주문 → 체결 추적 → 상태 동기화 루프 — 완료 2026-04-21. Protocol 분리(`OrderSubmitter`/`BalanceProvider`/`BarSource`) + `DryRunOrderSubmitter` 주입으로 KIS 접촉 0 드라이런 + 재동기화 halt + `KisClientError` 지수 백오프 + `backtest/costs.py` 비용 산식 재사용. 단위 테스트 63건 green (총 605건). 의존성 추가 없음.
- [x] `main.py`: APScheduler로 9:00 시작, 장중 루프, 15:00 청산, 15:30 리포트 — 완료 2026-04-21. `BlockingScheduler(timezone='Asia/Seoul')` + 4종 cron job(09:00 session_start·매분 step·15:00 force_close·15:30 daily_report, 평일 한정). `--dry-run` CLI 플래그로 `DryRunOrderSubmitter` 주입 → KIS 주문 접촉 0. SIGINT/SIGTERM graceful shutdown. 단위 테스트 47건 green (총 652건). `apscheduler 3.11.2` 의존성 추가. 9:30 OR 확정 별도 훅 불필요 — `ORBStrategy.on_bar` 가 분봉 경계에서 자동 처리. 공휴일 자동 판정 미도입 — 운영자 수동 처리 (ADR-0011).
- [x] `monitor/notifier.py`: 진입/청산/에러/일일 요약 텔레그램 알림 — 완료 2026-04-21. `Notifier` Protocol 분리(`Executor` 는 notifier 모름) + `StepReport` 이벤트 확장(`entry_events`/`exit_events`) + 전송 실패 silent fail + 연속 실패 dedupe 경보 + 드라이런 실전송 `[DRY-RUN]` 프리픽스. ADR-0012. I1/I2 후속 정리 반영 (2026-04-22) — 연속 실패 stderr 2차 경보 + `_fmt_time` naive/non-KST 가드.
- [x] `storage/db.py`: SQLite에 모든 주문/체결/PnL 기록 — 완료 2026-04-22. `TradingRecorder` Protocol + `SqliteTradingRecorder` + `NullTradingRecorder` + `StorageError`. 스키마 v1(orders/daily_pnl/schema_version). silent fail + 연속 실패 dedupe 경보. `EntryEvent`·`ExitEvent` 에 `order_number: str` 추가. 의존성 추가 없음.
- **드라이런 모드**: `--dry-run` 플래그로 주문 API 호출 없이 로그만 (최종 검증용) — `main.py` 에서 구현 완료.
- **산출물**: **모의투자 환경에서 최소 2주 무사고 운영** (에러 0건 · 알림 정상 · PnL 기록 정확)

#### Phase 3 후속 정리 작업 (PR #18 코드 리뷰 피드백 — 모의투자 10영업일 운영 전·중 해소)

PR #18 에서 Critical/Important 중 즉시 수정(C1, I4)만 반영했다. 나머지 2건은 **운영 안정성** 영역으로 모의투자 운영 중 실제로 만날 가능성이 높은 이슈다. 후속 PR 1건으로 묶어 처리한다. 각 항목은 파일/라인과 실패 시나리오를 명시했으므로 착수 시 원본 리뷰로 되돌아갈 필요 없다.

- [x] **I1 — 연속 실패 경보 자체의 blackout 경로 보강** (`src/stock_agent/monitor/notifier.py:250-258`). `TelegramNotifier._record_failure` 의 `logger.critical` 1회 경보는 텔레그램 채널 자체가 죽은 시나리오에서 loguru sink(단말/파일)에만 남는다 — 야간/주말 운영에서 운영자가 놓칠 가능성. 최소한 (a) 운영 절차서에 `grep "telegram.notifier.persistent_failure" logs/*.log` 매 세션 종료 후 확인 명시, (b) 임계값 도달 시 `sys.stderr` 직접 write 로 2차 경보, (c) 일정 횟수 초과 시 `NullNotifier` 자기 강등 + 다음 호출에서 `RuntimeError` 1회 raise 중 하나 이상 도입. **(2026-04-22 완료 — 옵션 (a)+(b) 채택. (c) NullNotifier 자기 강등·RuntimeError raise 는 silent-fail 계약 부작용 우려로 defer)**
- [x] **I2 — `_fmt_time` tz-naive 조용 포맷 가드** (`src/stock_agent/monitor/notifier.py:260-262`). 현재 docstring 에 "naive 도 그대로 포맷" 이라고 허용. Executor `_require_aware` 기조와 어긋나 naive datetime 혼입 시 UTC 시각을 KST 로 오독할 위험. `if ts.tzinfo is None` 에서 `logger.warning` + "(tz?)" 꼬리표 부착 또는 `astimezone(KST)` 강제. **(2026-04-22 완료 — naive: warning 1회 dedupe + "(tz?)" 꼬리표. non-KST aware: astimezone(KST) 정규화. RuntimeError 미사용 — silent-fail 원칙 준수)**

합류 기준: 2건 모두 해소 후 Phase 3 PASS 조건(모의투자 10영업일 무중단) 에 재진입. C1/I4 는 PR #18 내 수정으로 반영 완료(ADR-0012 "후속 정리" 섹션 참조). I3/I5/I6 은 이슈 #25/#26/#27 로 이관(2026-04-22) — plan.md 추적 스코프 밖, GitHub 이슈 트래커에서 관리. Suggestion(S1~S9)는 우선순위 더 낮아 Phase 3 PASS 선언 이후 정리.

### Phase 4 — 소액 실전 전환 (운영 상시)
- 모의 2주 통과 시 실전 APP_KEY로 전환, 환경변수만 교체
- **초기 1주는 자본 50만원 한도** (`config/strategy.yaml`로 제한), 무사고 확인 후 점진 증액
- 매일 장 마감 후 PnL · 승률 · 최대 손실 종목 텔레그램 리포트
- 주간 회고: 백테스트 대비 실전 괴리(슬리피지, 누락 체결) 측정

### Phase 5 — 개선 사이클 (지속)
- 2번째 전략 추가 (예: VWAP 반등) 후 A/B
- 장시간 안정성 요구되면 네이버클라우드/AWS Seoul VPS 이전 (월 5천~2만원)
- 종목 선정 로직 고도화 (거래대금 상위, 변동성 필터)
- walk-forward validation 범용 구현 (`src/stock_agent/backtest/walk_forward.py` — `generate_windows`·`run_rsi_mr_walk_forward` C2 에서 본 구현 완료 2026-05-02, ADR-0024): `run_walk_forward(BacktestConfig, ...)` 범용 구현은 Phase 5 잔여. 민감도 그리드는 sanity check 이지 walk-forward 를 대체하지 않는다.
- `scripts/update_universe.py` — CSV 기반 `config/universe.yaml` 갱신 자동화. 연 2회 정기변경 수동 갱신 워크플로우의 휴먼 에러 제거. `data/universe_imports/*.csv` 최신 파일 자동 선택(또는 `--csv <path>`), 인코딩 자동 감지, 로더 정규식과 동일한 티커 검증, 임시 가상 코드(`NNNNZ0` 등) 자동 제외 + 주석 기록, 티커 수 ±20% 편차 가드, 기본 드라이런(diff 출력) + 명시적 `--apply` 플래그로만 YAML 덮어쓰기. 부팅 시 자동 스캔은 지양(결정론성 보호 — 장중에 파일이 떨어져 유니버스가 바뀌는 사고 방지). git add/commit 은 운영자 책임.
- 성능 모니터링 대시보드 (Streamlit 또는 Grafana)

---

## Verification — 어떻게 검증할 것인가

**각 Phase의 PASS 기준**
- **Phase 0**: `python scripts/healthcheck.py` → 잔고 조회 OK, 텔레그램 알림 수신 OK
- **Phase 1**: `pytest tests/test_kis_client.py` 통과 — **충족** (pytest 131건 green). 삼성전자(005930) 현재가 조회 OK — **코드 경로 완성 + WebSocket 구독 등록 성공**. 틱 수신 end-to-end 확인(장중 + 실전 키)은 Phase 3 착수 전제로 이관.
- **Phase 2**:
  - `pytest tests/test_strategy_orb.py` — 고정 시나리오 OHLCV 입력에 정확한 진입/청산 시그널
  - `python scripts/backtest.py --from <1년 전 날짜> --to <오늘>` → 리포트 생성, 수수료·세금 반영. 아래 세 조건 **전부** 충족 시 Phase 2 PASS (ADR-0019 게이트):
    1. `max_drawdown_pct > -15%` (ADR-0017 계승)
    2. 승률 × 평균 손익비 > 1.0 (트레이드당 기대값 양수)
    3. 연환산 샤프 비율 > 0
  - 표본 기간: 연속 최소 240 영업일 (약 1년, KIS 서버 1년 보관 한도, ADR-0017). 단일 종목 단독 PASS 근거 불충분 — 다중 종목 실행이 공식 선언 조건.
  - 추가 게이트 (PASS 후): `backtest/walk_forward.py` (PR #70) 로 2~4 분할 walk-forward 검증에서도 세 조건 전부 통과.
- **Phase 3**: Phase 2 PASS + walk-forward 통과 확인 후에만 착수. 모의투자 **연속 10영업일 무중단 · 0 unhandled error · 모든 주문이 SQLite에 기록 · 텔레그램 알림 100% 수신**. **수익률이 확인되기 전에는 Phase 3 진입 금지** (ADR-0019).
- **Phase 4**: 실전 1주차 실거래 결과가 백테스트 범위 ±50% 이내 (슬리피지 과대 여부 체크)

**End-to-End 스모크 테스트 (매일 장전 1회)**

```bash
python scripts/healthcheck.py
# 1) KIS 토큰 발급 OK          — 시간대 무관
# 2) 잔고 조회 OK              — 시간대 무관
# 3) 텔레그램 "장전 점검 통과" 수신 — 시간대 무관
# 4) 삼성전자(005930) 현재가 조회 OK — 평일 장중(09:00~15:30 KST) 실행 필수
#    장외에서는 WebSocket 연결은 성공하지만 체결 이벤트 미수신 → 2초 타임아웃 후 실패 가능
#    (실전 키 미설정 시 4번은 SKIP)
```

---

## 비용 추정

| 항목 | MVP(모의) | 실전 |
|---|---|---|
| 증권사 API | 0원 | 0원 (KIS 무료) |
| 과거 데이터 | 0원 (pykrx) | 0원 |
| 텔레그램 | 0원 | 0원 |
| 로컬 맥북 전기 | ≈0원 | ≈0원 |
| 거래 수수료 | — | 약 0.015~0.025%/건 |
| 거래세 (매도만) | — | 0.18% |
| (Phase 5) 클라우드 VPS | — | 월 5천~2만원 |

---

## 주요 위험 & 완화책

| 위험 | 영향 | 완화책 |
|---|---|---|
| 맥북 종료/절전/네트워크 끊김 | 포지션 방치 → 손실 확대 | `caffeinate` 사용, 중요 포지션 시 수동 감시, Phase 5에 VPS 이전 |
| KIS API 레이트 제한·토큰 만료 | 주문 누락 | `broker/rate_limiter.py`, 토큰 선제 갱신 |
| 메모리 포지션 상태 ↔ 실계좌 불일치 | 이중 주문/미청산 | **매 루프마다 계좌 조회 후 상태 재동기화** |
| 백테스트 과적합 | 실전에서 무너짐 | Walk-forward 검증, 파라미터 적게, 슬리피지 비관적 가정 |
| 데이트레이딩의 구조적 불리함 | 기대수익 잠식 | 최소 거래금액·승률 조건으로 저품질 신호 필터 |
| 악성 코드/키 유출 | 계좌 탈취 | `.env`는 `.gitignore`, **실전 키는 권한 최소화 · IP 화이트리스트** |
| 감정적 개입 (수동 매매 섞임) | 시스템 검증 불가 | 실전 전환 후 **최소 1개월 수동 개입 금지**, 개선은 코드 반영으로만 |
| `python-kis` paper-only 초기화 우회 | 설계가 라이브러리 내부 구현에 의존 | Phase 4 실전 전환 시 실전 APP_KEY/SECRET 별도 발급 및 슬롯 분리 (`PyKis.virtual` 프로퍼티로 라우팅 확인) |
| 회귀 코드 머지 | 실거래 자금 시스템에 결함 유입 | GitHub Actions CI 자동 실행 + main 브랜치 보호로 CI 통과 필수 |
| pykrx 분봉 미지원 | **백테스트용 과거 분봉** 데이터 확보 경로 미정 (장중 실시간 분봉은 `data/realtime.py` 로 해소) | `minute_csv.py` 로 CSV 임포트 경로 해소 (2026-04-20). `kis_minute_bars.py` 로 KIS API 어댑터 해소 (2026-04-22, ADR-0016). **KIS 서버 최대 1년 보관 제약** 있으나, Phase 2 PASS 기준이 1년 표본으로 완화됨 (ADR-0017) — `--loader=kis` 경로로 즉시 실행 가능. walk-forward·다년 표본은 Phase 5 유예. |
| pykrx 1.2.7 지수 API(`get_index_portfolio_deposit_file` 등) KRX 서버 호환성 깨짐 + KIS Developers 인덱스 구성종목 API 미제공 | 자동 유니버스 갱신 불가 | `config/universe.yaml` 로 수동 관리. 연 2회 정기변경(6월·12월)마다 운영자 갱신. Phase 5 에서 자동화 경로(pykrx 수정 릴리스 대기 또는 KRX 정보데이터시스템 스크래핑) 재도입. |
| KIS paper 도메인(`openapivts`) 시세 API(`/quotations/*`) 미제공 → python-kis 고레벨 시세 API paper 환경에서 사용 불가 | 모의투자 자동 실행(Phase 3) 에서 실시간 체결가 수신 불가 | 시세 전용 실전 APP_KEY 발급, 실전 도메인(`openapi`) 직접 호출 (`RealtimeDataStore`). Phase 3 착수 전 실전 앱 발급·IP 화이트리스트 등록 필수. |
| 실전 키 IP 화이트리스트 이탈 (공인 IP 변경, ISP 동적 IP 할당 등) | 시세 단절 → `RealtimeDataStore` 전체 장애 | `healthcheck.py` 에서 `EGW00123` 계열 오류 감지 시 힌트 로그("KIS Developers 포털 → 앱 관리 → 허용 IP 갱신") 출력. 장기적으로 VPS 이전 시 고정 IP 확보 (Phase 5). |
| 자체 백테스트 루프의 시뮬레이션 정확도 검증 부재 | 비용 계산 오류가 백테스트 PnL 을 왜곡 → 실전 괴리 | 후속 PR 에서 KIS 실데이터로 회귀 비교. 현 PR 은 단위 테스트(costs 18 + metrics 22)로 슬리피지·수수료·거래세 적용 정확도를 명시 assert. |
| `data/trading.db` 미백업 | 디스크 장애·실수 삭제 시 체결 원장 소실 | `data/` 는 `.gitignore` 로 제외. 운영자가 주기적으로 외부 스토리지에 백업 필요. Phase 5 클라우드 이전 시 관리형 DB 또는 자동 백업 도입 검토. |

---

## Phase 1 완료 요약 (2026-04-19 PASS 선언)

Phase 0 완료 (2026-04-19). Phase 1 코드·테스트 레벨 PASS 선언 (2026-04-19).

1. [x] `src/stock_agent/broker/kis_client.py` — 완료. DTO 정규화, pykis_factory 주입, paper 전용, live는 defer.
2. [x] `src/stock_agent/broker/rate_limiter.py` — 완료. 주문 경로 전용 `OrderRateLimiter`(기본 2 req/s + 최소 간격 350 ms). 조회 경로는 python-kis 내장 리미터에 그대로 위임.
3. [x] `src/stock_agent/data/historical.py` + `data/universe.py` + `config/universe.yaml` — 완료. pykrx 일봉 + SQLite 캐시 v3. KOSPI 200 유니버스 YAML 하드코딩(수동 관리, 연 2회 정기변경). 의존성 추가: `pykrx 1.2.7`, `pyyaml 6.0.3`.
4. [x] `src/stock_agent/data/realtime.py` — 완료. WebSocket 우선 + REST 폴링 fallback. **실전(live) 키 전용** (`has_live_keys=False` 시 `RealtimeDataError` fail-fast). 실전 키 PyKis 에 `install_order_block_guard` 설치(`/trading/order*` 도메인 무관 차단). 분봉 집계(분 경계 OHLC 누적)·스레드 안전(`threading.Lock`), volume Phase 1 에서 0 고정(Phase 3 실사 후 확정).
5. [x] 단위 테스트 작성 — pytest **131건 green** (test_config 11 + test_kis_client 15 + test_safety 23 + test_rate_limiter 18 + test_historical 14 + test_universe 11 + test_realtime 28). PR #7 Critical 피드백 반영: 가드 중복 설치 방어(`GUARD_MARKER_ATTR` 재설치 거부), 폴링 연속 실패 경보(`polling_consecutive_failures` 공개 프로퍼티), docstring 정정.

**미완료 조건**: 장중 실시간 시세 수신 end-to-end 확인(실전 키 + IP 화이트리스트 + 평일 장중 틱 수신)은 **Phase 3 착수 전제**로 이관 (plan.md Phase 3 섹션 착수 전제 항목 참조). 코드 경로 완성 + WebSocket 구독 등록 성공까지는 달성.

**Phase 1 PASS 선언. Phase 2 착수.**

---

## Phase 2 진행 요약 (2026-04-20 기준)

Phase 2 여섯 번째 산출물 — `scripts/backtest.py` CLI 완료 (2026-04-20). `scripts/backtest.py` CLI 는 완료. 전체 PASS 선언은 1년 실데이터 PASS 검증(낙폭 절대값 15% 미만, MDD > -15%, 240 영업일 이상, 다중 종목) 이후 (ADR-0017). 이 PR 은 Phase 2 전체 PASS 를 선언하지 않는다.

1. [x] `src/stock_agent/strategy/orb.py` + `base.py` + `__init__.py` — 완료. `ORBStrategy` 상태 머신(IDLE→FLAT→LONG→CLOSED), `StrategyConfig`(frozen dataclass, 생성자 주입), `Strategy` Protocol(최소 — `on_bar`/`on_time`), `EntrySignal`/`ExitSignal`/`ExitReason` DTO. 설계 결정: 분봉 close 기준 strict 돌파, 동일 분봉 손절·익절 동시 성립 시 손절 우선, 1일 1회 진입, `force_close_at` 이후 신규 진입 금지, 세션 경계 자동 리셋. 의존성 추가 없음.
2. [x] `src/stock_agent/risk/manager.py` — 완료 2026-04-20. `RiskConfig` 기본값 고정(position_pct 20%, max_positions 3, daily_loss_limit_pct 2%, daily_max_entries 10, min_notional 10만원). `realized_pnl_krw` 부호 계약(손실 음수·수익 양수)은 호출자 책임. 공개 심볼 6종(`RiskManager`, `RiskConfig`, `RiskDecision`, `PositionRecord`, `RejectReason`, `RiskManagerError`) `risk/__init__` 재노출.
3. [x] `src/stock_agent/backtest/{__init__.py, engine.py, costs.py, metrics.py, loader.py}` — 완료 2026-04-20. 자체 시뮬레이션 루프(`backtesting.py` 폐기). `ORBStrategy` + `RiskManager` 호출, 슬리피지(0.1%) + 수수료(0.015%) + 거래세(0.18% 매도만) 반영, 세션 마감 force_close 훅, 복리 자본 갱신, phantom_long 처리(rejected entry 의 후속 ExitSignal 흡수), 시간 단조증가 검증. 외부 I/O 0, 의존성 추가 0. 공개 심볼 8종(`BacktestEngine`, `BacktestConfig`, `BacktestResult`, `BacktestMetrics`, `TradeRecord`, `DailyEquity`, `BarLoader`, `InMemoryBarLoader`).
4. [x] `src/stock_agent/data/minute_csv.py` — 완료 2026-04-20. `MinuteCsvBarLoader` + `MinuteCsvLoadError` 공개. 레이아웃 `{csv_dir}/{symbol}.csv`, 헤더 `bar_time,open,high,low,close,volume`. bar_time naive KST 파싱·오프셋 포함 거부, Decimal 가격 파싱, OHLC 일관성 검증, 분 경계 강제, 단조증가+중복 금지, 누락 파일 fail-fast. 여러 심볼 `heapq.merge` 정렬 스트리밍. stdlib 전용, 추가 의존성 0. KIS 과거 분봉 API 어댑터는 별도 PR.
5. [x] `src/stock_agent/backtest/sensitivity.py` + `scripts/sensitivity.py` — 완료 2026-04-20. `ParameterAxis`·`SensitivityGrid`·`SensitivityRow`·`run_sensitivity`·`render_markdown_table`·`write_csv`·`default_grid` 공개 (backtest `__init__` 재노출, 7종 추가). 기본 그리드 `or_end` 2종 × `stop_loss_pct` 4종 × `take_profit_pct` 4종 = 32 조합. 파라미터 이름 공간 `strategy.*`·`risk.*`·`engine.*`. 외부 의존성 추가 0. 민감도 리포트는 sanity check 용도이며 walk-forward 검증을 대체하지 않는다 (백테스트 과적합 위험 보존). PR #12 리뷰 반영 — `SensitivityRow` frozen 계약 회복 (params tuple 화 + `BacktestMetrics` 중첩), `scripts/sensitivity.py` 예외 좁힘 (`MinuteCsvLoadError`/`RuntimeError` → exit 2, `OSError` → exit 3), `BarLoader` Protocol 재호출 안전성 계약 명시, 회귀 테스트 3건 보강 (`post_slippage_rejections` end-to-end + `engine.commission_rate`/`engine.sell_tax_rate` 라우팅).
6. [x] `scripts/backtest.py` — 완료 2026-04-20. `MinuteCsvBarLoader` + `BacktestEngine` 1회 실행 → 3종 산출물(Markdown 리포트·메트릭 CSV·체결 CSV). 공개 인자: `--csv-dir` (required), `--from`/`--to` (required, `date.fromisoformat`), `--symbols` (default 유니버스 전체), `--starting-capital` (default 1,000,000), `--output-markdown`/`--output-csv`/`--output-trades-csv`. PASS 판정: 낙폭 절대값 15% 미만일 때 PASS (`mdd > Decimal("-0.15")` 이면 PASS — 경계 정확값 -15%는 FAIL). exit code 에는 반영 안 함 — 운영자 수동 검토 보존, CI 자동 pass/fail 금지. exit code 규약: `0` 정상 / `2` `MinuteCsvLoadError`·`KisMinuteBarLoadError`·`UniverseLoadError`·`RuntimeError` / `3` `OSError` (sensitivity.py 도 동일 계약 — Issue #65 로 `UniverseLoadError` 분기 추가). 외부 네트워크·KIS 접촉 0, 의존성 추가 0. 테스트: `tests/test_backtest_cli.py` 65건 + `tests/test_sensitivity_cli.py` 7건. **PASS 라벨이 출력돼도 즉시 실전 전환 금지 — Phase 3 모의투자 2주 무사고 운영이 전제.**

pytest **245 → 324 → 384 → 464 → 477 → 539 → 542건 green** (기존 539 + verdict 경계값 보강 2건 + UniverseLoadError 회귀 1건). ruff check/format + black --check 모두 green. 의존성 추가 없음.

7. [x] `src/stock_agent/data/kis_minute_bars.py` — 완료 2026-04-22 (ADR-0016). `KisMinuteBarLoader` + `KisMinuteBarLoadError`. KIS API `FHKST03010230` (`kis.fetch()` 로우레벨 직접 호출). 120건 역방향 페이지네이션 + `EGW00201` 레이트 리밋 재시도(최대 3회) + SQLite 캐시 `data/minute_bars.db` (별도 파일). 실전(live) 키 전용, `install_order_block_guard` 설치. `scripts/backtest.py`·`scripts/sensitivity.py` 에 `--loader={csv,kis}` 추가. **중요 제한**: KIS 서버 최대 1년 보관 — Phase 2 PASS 검증은 CSV 로 수행. 테스트 39건 신규. 의존성 추가 없음.

8. [x] `scripts/backfill_minute_bars.py` — 완료 2026-04-22 (Issue #47). ADR-0016 결정 10 의 대량 백필 전용 CLI 이행. `KisMinuteBarLoader.stream` 호출로 심볼별 `data/minute_bars.db` 적층. 인자: `--from`/`--to`(required), `--symbols`(default 유니버스 전체), `--throttle-s`, `--cache-db-path`. exit code: 0 / 1 부분 실패 / 2 입력 오류 / 3 I/O 오류. 실 KIS API 접촉 0, 테스트 37건. 의존성 추가 없음.

---

## Phase 2 1차 백테스트 FAIL + 복구 로드맵 (2026-04-24, ADR-0019)

2026-04-24 02:06 KIS 1년치 분봉 백필 완료 (199 심볼, `data/minute_bars.db` 2.78 GB, 약 11시간 러닝). 같은 날 10:25 에 `uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21` 1회 실행 완료.

결과 (`data/backtest_report.md`):

| 항목 | 값 | PASS 기준 (ADR-0019) |
|---|---|---|
| 기간 | 2025-04-22 ~ 2026-04-21 (243 영업일) | 240 영업일 이상 — 충족 |
| 종목 수 | 199 (KOSPI 200) | 다중 종목 — 충족 |
| **MDD** | **-51.36%** | `> -15%` — **FAIL** |
| 총수익률 | -50.05% | — |
| 샤프 (연환산) | -6.81 | `> 0` — **FAIL** |
| 승률 × 손익비 | 0.3135 × 1.28 ≈ 0.40 | `> 1.0` — **FAIL** |
| 거래 수 | 1027 (일평균 4.226) | — |
| 종료 자본 | 499,489 KRW (시작 1,000,000) | — |

거부 상위 (RiskManager 게이팅): `max_positions_reached` 14,568 · `below_min_notional` 5,370 · `halted_daily_loss` 7 · `daily_entry_cap` 3.

**트레이드당 기대값 ≈ -0.28R** (비용 차감 전). 구조적 손실 전략 판정.

**사용자 결정** (2026-04-24): *"수익률이 생길때까지 절대로 다음 Phase 로 넘어가면 안될 것 같아"* → ADR-0019 로 성문화.

### 복구 5단계 로드맵 (저비용 → 고비용 순, 순차 게이팅)

| 단계 | 작업 | 비용·리스크 | 산출 조건 |
|---|---|---|---|
| A | 민감도 그리드 실행 (`scripts/sensitivity.py`, 32 조합) — `--resume <csv>` + `--workers N` 으로 freeze 내성·병렬 가속 가능 (Issue #82·ADR-0020) | 캐시 재사용, KIS 호출 0, 수 분 | 세 게이트(MDD·샤프·기대값) 전부 통과 조합 존재 여부 |
| B | 비용 가정 재검정 — 실제 호가 스프레드 측정 → 슬리피지 재보정 (ADR-0006 갱신) | KIS 실전 키 1주 샘플, 코드 수정 소량 | 새 비용 모델로 A 재실행 |
| C | 유니버스 유동성 필터 — 거래대금·변동성 상위 N (50·100) 서브셋 | `pykrx` 일봉 활용, 백테스트 캐시 재사용 | 필터된 유니버스에서 A 재실행 |
| D | 전략 파라미터 구조 변경 — OR 윈도·force_close_at·재진입·일 N 진입 | ADR/Issue 단위 관리, 백테스트 재실행 | 새 구조로 A 재실행 |
| E | 전략 교체 — ORB 폐기 → VWAP mean-reversion / gap reversal / pullback 후보 평가 | 신규 Strategy 구현, ADR 필요 | 새 전략으로 A~D 재실행 |

**각 단계 결과**:
- 세 게이트 전부 통과 → walk-forward 검증 (`backtest/walk_forward.py`, PR #70) 추가 게이트 → Phase 3 착수 허가.
- 미달 → 다음 단계 진행.
- E 까지 실패 → 프로젝트 접근법 원점 재검토 (별도 ADR).

### Step A 실행 결과 (2026-04-25) — FAIL

실행: 2026-04-25 17:09~20:00 KST (회사 PC 3 조합 + 집 PC 25 조합). 데이터 범위: 2025-04-22 ~ 2026-04-21 (1년치 KIS 분봉 캐시, 199 심볼). 시작 자본 1,000,000 KRW.

완료 28/32 조합. 미실행 4 조합: `or_end=09:30, stop=2.5%, take=2~5%` — 207940 (셀트리온헬스케어, 합병 폐지 추정) 2025-11 캐시 0건 + `EGW00201` rate limit 누적으로 `KisMinuteBarLoadError`. 28 조합 일관 결과 상 4 조합이 게이트를 통과할 가능성 0% — 즉시 종결 판단.

| 게이트 | 기준 | 통과 조합 |
|---|---|---|
| MDD > -15% | 낙폭 절대값 15% 미만 | **0 / 28** |
| 승률 × 손익비 > 1.0 | 기대값 양수 | **0 / 28** |
| 샤프 > 0 | 위험조정 수익 양수 | **0 / 28** |

- 최고 수익률: -40.91% (`or_end=09:15, stop=2.5%, take=4%`)
- 최저 MDD: -42.08% (한도 -15% 의 2.8배)
- 샤프: 전 조합 음수 (-3.99 ~ -7.x)
- 승률 35% × 손익비 1.30 ≈ 0.45 (게이트 기준 1.0 의 절반)

**Step A 결론: FAIL.** 세 게이트 동시 통과 조합 없음. 상세 메트릭은 `docs/runbooks/step_a_result_2026-04-25.md` 참조.

**→ Step B (비용 가정 재검정, Issue #75)** 로 이행.

### 부수적 개선 (본 섹션 도입 PR 포함)

- `config/holidays.yaml` 에 `2025-05-01`·`2026-05-01` 근로자의날 2 건 보강. 1차 백테스트 시 해당 날짜 캐시 miss 로 199 심볼 × 4 페이지 KIS 허탕 호출 + `EGW00201` 캐스케이드 → 프로세스 비정상 종료 원인이었음. ADR-0018 YAML 관리 정책 그대로 계승 (신규 결정 아님).

### Step B 완료 — ADR-0006 슬리피지 가정 유지 결정 (2026-04-29, Issue #75)

`src/stock_agent/data/spread_samples.py` + `scripts/collect_spread_samples.py` 신설 (코드·테스트 레벨 완료 2026-04-26). 이후 운영자가 3 거래일 장중 실 호가 수집을 완료하여 Step B 종결.

**구현 요약**:
- `SpreadSample` (frozen dataclass): symbol, ts (KST aware), bid1, ask1, bid_qty1, ask_qty1, spread_pct. `__post_init__` 가드 7종 (symbol 정규식·naive ts·음수/0 가격·역전 스프레드·음수 잔량 → `RuntimeError`).
- `SpreadSampleCollector`: 실전 키 전용. `kis.fetch()` 로우레벨 직접 호출. `EGW00201` rate limit 자동 재시도 (최대 3회). 거래정지(0)·빈문자열·역전 스프레드 → `None` 반환 (정상 흡수). `install_order_block_guard` 설치.
- `scripts/collect_spread_samples.py`: `--symbols`/`--interval-s`(min 1.0)/`--duration-h`/`--output-dir`/`--http-timeout-s`/`--no-skip-outside-market`. JSONL 세션 날짜 단위 파일 (`Decimal` str 직렬화, ts isoformat). 심볼 단위 실패 격리. exit code: 0 정상 / 1 부분 실패 / 2 입력·설정 오류 / 3 I/O 오류.
- pytest 신규 58건 (`test_spread_samples.py` 38 + `test_collect_spread_samples_cli.py` 20). 회귀 0건. 의존성 추가 0.

**실측 결과 (2026-04-27, 04-29, 04-30 — 3 거래일)**:

| 항목 | 값 |
|---|---|
| 수집 샘플 수 | 331,530 |
| 전체 중앙값 스프레드 | 0.1305% |
| 현행 가정 (ADR-0006) | 0.1% |
| 비율 | 1.3× |
| 사전 기준 범위 | 0.05~0.2% |
| 기준 내 수렴 | 예 |

상세 분석: `docs/runbooks/step_b_spread_analysis.md` 참조.

**결정 (2026-04-29)**:
- 현행 슬리피지 가정 **0.1% 유지**. ADR-0006 계승 — 새 ADR 불필요.
- `src/stock_agent/backtest/costs.py` 변경 없음.
- Step A 민감도 그리드 재실행 불필요.

**→ Step C (유니버스 유동성 필터)** 로 이행.

### Step C 완료 — 유니버스 유동성 필터 실행 / FAIL (2026-04-30, Issue #76)

코드 산출물 4건 완료 후 운영자가 Top 50 / Top 100 두 서브셋 백테스트를 실행하여 결과 확인.

**실행 조건**: `--loader=kis`, 기간 2025-04-22 ~ 2026-04-21, 시작 자본 1,000,000 KRW.

**결과**:

| 서브셋 | MDD | 총수익률 | 샤프 | 승률×손익비 | PASS 기준 |
|---|---|---|---|---|---|
| Top 50 (config/universe_top50.yaml) | -44.70% | -44.97% | -6.68 | 0.377 | 전원 FAIL |
| Top 100 (config/universe_top100.yaml) | -50.13% | -50.01% | -7.74 | 0.383 | 전원 FAIL |
| 베이스라인 199종목 (Step A) | -51.36% | -50.05% | -6.81 | 0.401 | — |

비고:
- Top 50 이 베이스라인 대비 MDD 소폭 개선 (-51.36% → -44.70%) 이나 PASS 기준 -15% 와 여전히 3배 격차.
- ADR-0020 작성 안 함 (채택 결정 부재).
- 신규 추적 파일: `config/universe_top50.yaml`, `config/universe_top100.yaml` (커밋 781ec54).
- pykrx 1.2.7 부터 KRX_ID/KRX_PW env 필수 — `~/.config/stocker/.env` 및 `.env.example` 갱신 (커밋 36bfc65).
- 상세 runbook: `docs/runbooks/step_c_liquidity_filter_2026-04-30.md`.

**Step C 결론: FAIL.** → Step D 진입.

### Step D 진행 — 전략 파라미터 구조 변경 (Issue #77)

#### Step D1 — OR 윈도 스터디 (2026-04-30 ~ 2026-05-01) — FAIL

`src/stock_agent/backtest/sensitivity.py` 에 `step_d1_grid()` 함수 추가 + `scripts/sensitivity.py` 에 `--grid {default,step-d1}` 플래그 도입.

- **`step_d1_grid()`** (`backtest/__init__.py` `__all__` 노출): `strategy.or_end` 3종 × `strategy.stop_loss_pct` 4종 × `strategy.take_profit_pct` 4종 = **48 조합**.
  - `or_end`: `time(9, 15)` / `time(9, 30)` / `time(10, 0)` — 15분·30분·60분 윈도.
  - `stop_loss_pct` / `take_profit_pct`: `default_grid()` 와 동일.
  - `default_grid()` 동작 변경 없음 (회귀 0).
- pytest **1408 → 1478 passed, 4 skipped** (신규 11건: `TestStepD1Grid` 8 + `TestGridFlag` 3 + 기타 추가분).

**운영자 실행 결과 (2026-04-30 ~ 2026-05-01)**:

- 48 조합 × Top 50 / Top 100 = 96 런 완료 (8 워커, KIS 캐시 hit 율 높음). 데이터 범위: 2025-04-22 ~ 2026-04-21, 시작 자본 1,000,000 KRW.
- 최선 조합: Top 50 `or_end=10:00, stop=2.5%, take=5.0%` MDD **-37.18%** / Top 100 `or_end=09:15, stop=2.5%, take=5.0%` MDD **-35.98%**.
- Step C 대비 MDD 개선 (Top 50 -44.70% → -37.18% / Top 100 -50.13% → -35.98%) 이나 게이트 한도 -15% 까지 21~23%p 격차.
- 96/96 런 ADR-0019 세 게이트 전원 미통과.
- 산출물: `data/sensitivity_step_d1_top50.{md,csv}`, `data/sensitivity_step_d1_top100.{md,csv}` (모두 `.gitignore`).
- 상세: `docs/runbooks/step_d1_or_window_2026-05-01.md`.

**Step D1 결론: FAIL.** ADR 작성 안 함 (채택 결정 부재). `step_d1_grid()` 코드 보존. → D2 진행.

#### Step D2 — force_close_at 스터디 (2026-05-01) — FAIL

`src/stock_agent/backtest/sensitivity.py` 에 `step_d2_grid()` 함수 추가 + `scripts/sensitivity.py` 에 `--grid step-d2` 추가.

- **`step_d2_grid()`** (`backtest/__init__.py` `__all__` 노출): `strategy.force_close_at` 3종 × `strategy.stop_loss_pct` 4종 × `strategy.take_profit_pct` 4종 = **48 조합**.
  - `force_close_at`: `time(14, 50)` / `time(15, 0)` / `time(15, 20)` — 동시호가 회피 / 현재 기본값 / 동시호가 시작 직전.
  - `stop_loss_pct` / `take_profit_pct`: `default_grid()` 와 동일.
  - `default_grid()` · `step_d1_grid()` 동작 변경 없음 (회귀 0).
- **CLI 플래그**: `--grid {default,step-d1,step-d2}`. 기본값 `default`. 기존 인자 전부 호환.
- pytest **1478 → 1487 passed, 4 skipped** (신규 9건: `test_sensitivity.py` `TestStepD2Grid` 9건 + `test_sensitivity_cli.py` `TestGridFlag` `step-d2` 분기 1건 포함). ruff/black/pyright 4종 PASS.

**운영자 실행 결과 (2026-05-01)**:

- 48 조합 × Top 50 / Top 100 = 96 런 완료 (Top 50 ~33분, Top 100 ~55분). 데이터 범위: 2025-04-22 ~ 2026-04-21, 시작 자본 1,000,000 KRW.
- 최선 조합: Top 50 `force_close_at=15:20, stop=2.5%, take=5.0%` MDD **-35.02%**, 샤프 -3.89, 승률×손익비 0.441 / Top 100 동일 파라미터 MDD **-37.56%**, 샤프 -3.94, 승률×손익비 0.435.
- `force_close_at=15:20` 이 두 서브셋 모두 가장 얕은 MDD (Top 50 평균 -42.93% / Top 100 평균 -47.19%). 14:50 vs 15:00 거의 동등 (~1bp 차이).
- D1 vs D2 거의 동급 — `stop=2.5%/take=5.0%` 가 본질 개선 벡터. OR 윈도·force_close 시각은 ~1~3%p 부차적 효과.
- 96/96 런 ADR-0019 세 게이트 전원 미통과.
- 산출물: `data/sensitivity_step_d2_top50.{md,csv}`, `data/sensitivity_step_d2_top100.{md,csv}` (모두 `.gitignore`).
- 상세: `docs/runbooks/step_d2_force_close_2026-05-01.md`.

**Step D2 결론: FAIL.** ADR 작성 안 함 (채택 결정 부재). `step_d2_grid()` 코드 보존. → D3/D4/E 결정 대기.

### Step E 진입 — 전략 교체 (Issue #78)

A~D 전원 실패 전제. VWAP mean-reversion / gap reversal / pullback 후보 전략을 순차 평가한다.

#### Step E PR1 — `BacktestConfig.strategy_factory` 주입 추상화 (2026-05-01)

`BacktestConfig` 에 `strategy_factory: Callable[[], Strategy] | None` 필드 추가. `strategy_config` 와 mutually exclusive (`__post_init__` 검증 → `RuntimeError`). 엔진·sensitivity 루프가 팩토리 경로를 통해 ORB 이외 전략을 주입받을 수 있도록 추상화. 테스트 6건 신규.

#### Step E PR2 — `VWAPMRStrategy` 구현 (2026-05-01)

`src/stock_agent/strategy/vwap_mr.py` 신설. `VWAPMRConfig` + `VWAPMRStrategy` (VWAP mean-reversion, per-symbol 상태 머신). 백테스트 결과 대기. 테스트 35건 신규.

#### Step E PR3 — `GapReversalStrategy` 구현 (2026-05-01)

`src/stock_agent/strategy/gap_reversal.py` 신설. `GapReversalConfig` + `GapReversalStrategy` (갭 반작용 long-only). `PrevCloseProvider` 의존 주입. 백테스트 결과 대기. 테스트 34건 신규.

#### Step E PR4 — Stage 1 CLI 확장 (2026-05-01)

`src/stock_agent/strategy/factory.py` 신설: `STRATEGY_CHOICES` · `StrategyType` · `build_strategy_factory`. `scripts/backtest.py` · `scripts/sensitivity.py` 에 `--strategy-type {orb,vwap-mr,gap-reversal}` 인자 추가. `orb` 분기는 기존 경로 그대로(회귀 0). 테스트 54건 신규 (factory 33 + backtest CLI 11 + sensitivity CLI 10). 4종 정적 검사 PASS (pytest exit 0 · ruff · black · pyright `0 errors`).

#### Step E PR4 — Stage 2 `prev_close_provider` 백테스트 통합 (2026-05-01)

`src/stock_agent/backtest/prev_close.py` 신설. `DailyBarPrevCloseProvider(daily_store: HistoricalDataStore, calendar: BusinessDayCalendar, *, max_lookback_days: int = 14)` — `GapReversalStrategy.PrevCloseProvider` 시그니처를 만족하는 Callable. `session_date` 직전 영업일 일봉을 `daily_store.fetch_daily_ohlcv` 로 조회해 `close` 반환, 없으면 `None`. `close()` + 컨텍스트 매니저 지원. 입력 가드: symbol `^\d{6}$`, `max_lookback_days > 0`, `max_lookback_days` 초과 시 `None` + `logger.warning`.

`scripts/backtest.py` `_run_pipeline` 갱신: `--strategy-type=gap-reversal` 시 `DailyBarPrevCloseProvider` 인스턴스 생성 + `try/finally` 로 `provider.close()` 보장.

`scripts/sensitivity.py` `_run_pipeline` 갱신: 동일 provider 라이프사이클 + **gap-reversal + workers≥2 거부 가드** 신설 (`RuntimeError` exit 2 — `HistoricalDataStore(sqlite3 connection)` 는 pickle 불가하여 ProcessPool 워커에 전달 불가).

테스트 신규 31건: `test_backtest_prev_close_provider.py` 18건 + `test_backtest_cli.py` `TestGapReversalPrevCloseProviderInjection` 5건 + `test_sensitivity_cli.py` `TestGapReversalPrevCloseProviderInjection` 8건. 삭제 1건: `TestStrategyTypeBaseConfigRouting::test_gap_reversal_parallel_strategy_factory_callable_GapReversalStrategy` (Stage 2 제약으로 불가능 조합). 4종 정적 검사 PASS (pytest 1651 collected · ruff · black · pyright `0 errors, 2 warnings`).

#### Step E Stage 3 — 운영자 백테스트 실행 + Step E FAIL 종료 (2026-05-01, ADR-0021)

`scripts/backfill_daily_bars.py` 로 일봉 캐시 결정론 확보 후 운영자가 4 런 백테스트를 실행하였다.

**결과**:

| 후보 × 서브셋 | MDD | 승률 × 손익비 | 샤프 | ADR-0019 |
|---|---|---|---|---|
| VWAP-MR Top 50 | -49.09% | 0.046 | -11.02 | 전원 FAIL |
| VWAP-MR Top 100 | -50.11% | 0.045 | -10.35 | 전원 FAIL |
| Gap-Reversal Top 50 | -10.19% (게이트 1만) | 0.339 | -3.23 | 전원 FAIL |
| Gap-Reversal Top 100 | -19.99% | 0.289 | -6.27 | 전원 FAIL |

ADR-0019 세 게이트(MDD > -15% · 승×손익비 > 1.0 · 샤프 > 0) 동시 통과 0.

**Step E 결론: FAIL.** VWAP-MR · Gap-Reversal 두 후보 폐기. 코드 산출물 보존 (회귀·재현용). 상세 런북: `docs/runbooks/step_e_vwap_mr_2026-05-01.md` · `docs/runbooks/step_e_gap_reversal_2026-05-01.md`.

**ADR-0021** 작성 — Step E 두 후보 폐기 + Step F 전환 결정 기록. → Step F 진입.

### Step F 진입 — 가설 풀 확장 (ADR-0021·ADR-0022, 2026-05-01)

A~E 전원 실패 (230+ 런 / 0 PASS) 확인 후 운영자 결정으로 일중 데이트레이딩 가정을 폐기하고 일/월 단위 전략 + DCA baseline 비교 경로로 전환.

**게이트 재정의 (ADR-0022)**: ADR-0019 의 일중 가정 게이트는 Step F 평가에 사용하지 않는다. 신규 세 게이트 동시 통과 필요:
1. MDD > -25% (strict greater)
2. (전략 총수익률) - (F1 DCA baseline 동일 기간 총수익률) > 0
3. 연환산 샤프 > 0.3

ADR-0019 자체는 폐기 X — 일중 가정 평가 사이클 사실 기록 보존.

상세 진행 계획 (PR0~PR6 분할): `docs/step_f_strategy_pool_plan.md` — **PR6 진입 직후 폐기 (2026-05-02, ADR-0023 결정)**. PR6 후속 정본은 `docs/runbooks/step_f_summary_2026-05-02.md` + `docs/adr/0023-rsi-mr-strategy-adoption-conditional.md` + 5 PR 별 런북.

#### Step F PR1 — F1 DCA baseline 완료 — PASS (2026-05-02)

`src/stock_agent/strategy/dca.py` (`DCAStrategy`, `DCAConfig`) + `src/stock_agent/backtest/dca.py` (`DCABaselineConfig`, `compute_dca_baseline`) + `scripts/backtest.py --strategy-type=dca --loader=daily` 라우팅. 테스트 79건 신규 (pytest 1670 → 1749 collected).

결과: MDD -12.92% · Sharpe 2.2683 · 총수익률 +51.50% mark-to-market (시작 자본 2,000,000 KRW, KODEX 200 069500, 1년). ADR-0022 게이트 1·3 PASS (게이트 2 N/A). 런북: `docs/runbooks/step_f_dca_baseline_2026-05-02.md`.

#### Step F PR2 — F2 Golden Cross 완료 — PASS (2026-05-02)

`src/stock_agent/strategy/golden_cross.py` (`GoldenCrossStrategy`, `GoldenCrossConfig`) + `src/stock_agent/backtest/golden_cross.py` (`GoldenCrossBaselineConfig`, `compute_golden_cross_baseline`) + `scripts/backtest.py --strategy-type=golden-cross` 라우팅 (BacktestEngine 우회). 테스트 81건 신규 (pytest 1749 → 1830 collected).

결과: MDD -20.52% · Sharpe 2.2753 · 총수익률 +182.36% mark-to-market (시작 자본 2,000,000 KRW, KODEX 200 069500, 2024-06-01 ~ 2026-04-21). DCA 대비 알파 +130.86%p. ADR-0022 게이트 3종 PASS. 런북: `docs/runbooks/step_f_golden_cross_2026-05-02.md`.

주요 caveat: (1) trades=1 — 통계 신뢰도 낮음. (2) 069500 가격 2.93× 급등 — pykrx 수정주가 보정 여부 검증 필요. 절대 수익률 수치는 데이터 검증 후 재해석 권장.

#### Step F PR3 — F3 Cross-sectional 모멘텀 완료 — FAIL (2026-05-02)

`src/stock_agent/strategy/momentum.py` (`MomentumStrategy`, `MomentumConfig`) + `src/stock_agent/backtest/momentum.py` (`MomentumBaselineConfig`, `compute_momentum_baseline`) + `scripts/backtest.py --strategy-type=momentum` 라우팅 (`--top-n` / `--lookback-months` / `--rebalance-day` CLI 인자 신설). 테스트 85 함수 신규 (parametrize 확장 후 pytest 1830 → 1941 collected).

결과: MDD -7.70% · Sharpe 0.9910 · 총수익률 +11.22% mark-to-market (시작 자본 2,000,000 KRW, KOSPI 200 캐시 101종목, 2025-04-01 ~ 2026-04-21, lookback 6개월, top-N 10). ADR-0022 게이트 1(MDD > -25%) PASS · 게이트 3(Sharpe > 0.3) PASS · 게이트 2(DCA 대비 알파 +11.22% - +48.18% = **-36.96%p**) **FAIL** → 종합 FAIL. 런북: `docs/runbooks/step_f_momentum_2026-05-02.md`.

주요 caveat: (1) 유니버스 부분집합 (199 종목 중 101 — 캐시 부족). (2) lookback 단축 (12개월 학술 표준 → 6개월). (3) 2025-04 ~ 2026-04 KOSPI 200 강세장 구간 — 인덱스 베타(+48.18%)가 cross-sectional 알파를 압도. (4) Strategy-backtest drift: entry skip 시 MomentumStrategy holdings 와 실 lot 불일치 — 후속 보강 필요.

#### Step F PR4 — F4 Low Volatility 완료 — FAIL (2026-05-02)

`src/stock_agent/strategy/low_volatility.py` (`LowVolStrategy`, `LowVolConfig`) + `src/stock_agent/backtest/low_volatility.py` (`LowVolBaselineConfig`, `compute_low_volatility_baseline`) + `scripts/backtest.py --strategy-type=low-vol` 라우팅 (`--lookback-days` (default 60) · `--rebalance-month-interval` (default 3) CLI 인자 신설). 테스트 114건 신규 (pytest 1941 → 2055 collected).

결과: MDD -9.62% · Sharpe 1.1713 · 총수익률 +15.87% mark-to-market (시작 자본 2,000,000 KRW, KOSPI 200 캐시 101종목, 2025-04-01 ~ 2026-04-21, lookback_days=60, top-N 10, rebalance_month_interval=3). ADR-0022 게이트 1(MDD > -25%) PASS · 게이트 3(Sharpe > 0.3) PASS · 게이트 2(DCA 대비 알파 +15.87% - +48.18% = **-32.31%p**) **FAIL** → 종합 FAIL. 런북: `docs/runbooks/step_f_low_volatility_2026-05-02.md`.

#### Step F PR5 — F5 RSI 평균회귀 완료 — PASS (2026-05-02)

`src/stock_agent/strategy/rsi_mr.py` (`RSIMRStrategy`, `RSIMRConfig`) + `src/stock_agent/backtest/rsi_mr.py` (`RSIMRBaselineConfig`, `compute_rsi_mr_baseline`) + `scripts/backtest.py --strategy-type=rsi-mr` 라우팅 (`--rsi-period` · `--oversold-threshold` · `--overbought-threshold` · `--stop-loss-pct` · `--max-positions` CLI 인자 신설). 테스트 85건 신규 (`test_strategy_rsi_mr.py` 45건 + `test_backtest_rsi_mr.py` 40건, pytest 2055 → 2140 collected).

설계 특이점: RSI 계산은 simple average gain/loss 방식 (Wilder smoothing 미사용). 동일 세션 내 청산 후 재진입 차단 (RSI 즉시 회복 시 무한 루프 방지). `EntrySignal.take_price=Decimal("0")` 마커 — 고정 익절 미사용, RSI 회귀로만 take_profit 청산. multi-symbol per-bar 시그널 (LowVol/Momentum 의 시점 트리거와 다름 — 매일 신호 산출).

결과: MDD -6.40% · Sharpe 2.4723 · 총수익률 +56.31% mark-to-market (시작 자본 2,000,000 KRW → 종료 3,126,256 KRW, KOSPI 200 캐시 101종목, 2025-04-01 ~ 2026-04-21, RSI 14, 과매도 30, 과매수 70). ADR-0022 게이트 1(MDD -6.40% > -25%) **PASS** · 게이트 3(Sharpe 2.4723 > 0.3) **PASS** · 게이트 2(DCA 대비 알파 +56.31% - +48.18% = **+8.13%p**) **PASS** → 종합 **PASS**. trades=175 (entry+exit pair) — Step F 전체에서 통계적으로 가장 신뢰도 높은 알파 확인. 청산 사유: stop_loss 113 (64.6%) / take_profit 58 (33.1%) / force_close 4 (2.3%). 승률 34.29% + 평균 손익비 4.3799 — 평균회귀 전형 패턴. 런북: `docs/runbooks/step_f_rsi_mr_2026-05-02.md`.

#### Step F PR6 — 종합 판정 + ADR-0023 (2026-05-02)

PR6 본 PR. 코드 변경 없음 — 종합 판정 런북 (`docs/runbooks/step_f_summary_2026-05-02.md`) + ADR-0023 (`docs/adr/0023-rsi-mr-strategy-adoption-conditional.md`) 작성 + `docs/step_f_strategy_pool_plan.md` 폐기.

**시나리오 판정**: ADR-0022 의 시나리오 표 적용 결과 시나리오 A (F2~F5 중 1+ 가 게이트 3종 동시 통과 + DCA 도 PASS) 충족. PASS 후보 2종 (PR2 Golden Cross · PR5 RSI MR) 중 PR5 우위 — 통계 신뢰도 (trades=175 vs trades=1) · MDD (-6.40% vs -20.52%) · Sharpe (2.4723 vs 2.2753) · 데이터 plausibility 영향 (cross-sectional vs 단일 종목) 4 차원에서 PR5 우위.

**ADR-0023 결정**: F5 RSI 평균회귀 (`RSIMRStrategy`) 를 Step F 1차 채택 후보로 선정. Phase 3 (모의투자 무중단 운영) 진입은 다음 4 추가 검증 전부 통과 후로 게이팅:

- **C1**: universe 199 종목 전체 백필 + PR5 재평가 (현재 캐시 101 종목 부분집합). — **PASS (2026-05-02)**. MDD -8.17% · Sharpe 2.2966 · 총수익률 +63.44% · DCA 알파 +15.26%p · trades=177. ADR-0022 게이트 3종 전원 통과. 런북: `docs/runbooks/c1_universe_full_backfill_2026-05-02.md`.
- **C2**: walk-forward 검증 본 구현 + 다년 코호트 검증 (현재 단일 1년 코호트만 평가). — **PASS (2026-05-02)**. `scripts/walk_forward_rsi_mr.py` 신규 CLI + `backtest/walk_forward.py` `generate_windows`·`run_rsi_mr_walk_forward` 본 구현. step6 (2 windows): train_avg +19.20% · test_avg +20.19% · degradation -5.16% · 2/2 PASS. step3 (3 windows): train_avg +18.97% · test_avg +16.82% · degradation +11.32% · 3/3 PASS. pass_threshold 0.3 (ADR-0024). 런북: `docs/runbooks/c2_walk_forward_rsi_mr_2026-05-02.md`.
- **C3**: 069500 일봉 수정주가 보정 검증. — **PASS (2026-05-03)**. `scripts/verify_069500_adjusted.py` + `data/c3_verify_069500.json`. `data/stock_agent.db` 캐시 458 행 = pykrx `adjusted=True` 458 행 close 완전 일치 (Stage 3 diff 0). ETF/KOSPI 200 (1028) 비율 점프 0건 (Stage 2). Google Finance · Wikipedia KOSPI 200 absolute level cross-check 정합 (Stage 4). pykrx 일봉 캐시는 수정주가 데이터로 확정 — PR1~PR5 절대 수익률은 한국 KOSPI 200 강세장 macro 의 결과. 런북: `docs/runbooks/c3_069500_adjusted_plausibility_2026-05-03.md`.
- **C4**: PR5 파라미터 sensitivity grid (`rsi_period` · `oversold/overbought` · `stop_loss_pct` · `max_positions`) 96 조합 스윕. — **PASS (2026-05-03)**. `scripts/c4_rsi_mr_sensitivity.py` + `backtest/rsi_mr_sensitivity.py`. 5축 3×2×2×4×2 = 96 조합. DCA baseline +48.18% 대비 64/96 (66.67%) all_gates_pass. 현행 14/30/70/0.03/10 PASS, 현행 인접 7/8 (87.5%) PASS. Phase 3 진입 게이트 (전체 ≥50% + 인접 ≥70%) 판정 PASS. 런북: `docs/runbooks/c4_rsi_mr_sensitivity_2026-05-03.md`.

**C1~C4 전원 통과 → Phase 2 PASS 공식 선언 (2026-05-03) + Phase 3 착수 재허가.** ADR-0023 결정 3항 조건 충족. `main.py` 모의투자 무중단 운영 착수 가능.

**부결과**:

- PR2 Golden Cross 는 단일 trade caveat 로 1차 채택 보류 (코드 보존). 후속 옵션: sma_period 단축 평가 / 다년 백테스트 / RSI MR 와 ensemble.
- PR3 모멘텀 · PR4 저변동성은 본 평가 환경 한계 (한국 1년 KOSPI 200 부분집합 단일 코호트) 인정으로 채택 후보 제외 (코드 보존, Phase 5 다년 walk-forward 시 baseline 으로 재사용).

**문서 폐기**: `docs/step_f_strategy_pool_plan.md` (헤더 line 3 명시 — PR6 진입 후 삭제). 본 ADR + 종합 런북 + 5 PR 별 런북 + ADR-0021/0022 가 후속 정본.

**Phase 2 PASS 단계**: PR5 가 ADR-0022 게이트 3종 통과 = Phase 2 백테스트 단계 PASS 조건 충족. 단 Phase 3 진입은 ADR-0023 C1~C4 추가 검증 통과 후로 게이팅 — ADR-0019 의 "수익률 확보 전 Phase 3 금지" 정책 본질 (수익률 확보 후 진입) 과 정합.

---

## Phase 3 진행 요약 (2026-04-21 기준)

### Phase 3 착수 전제 통과 (2026-04-21)

실전 시세 전용 APP_KEY 3종 발급·IP 화이트리스트 등록·평일 장중 `healthcheck.py` 4종 그린(WebSocket 체결 수신 OK). 완료.

### Phase 3 첫 산출물 — execution/executor.py (2026-04-21)

[x] `src/stock_agent/execution/` 패키지 신설. Protocol 분리(`OrderSubmitter`/`BalanceProvider`/`BarSource`) + `DryRunOrderSubmitter` 주입으로 KIS 접촉 0 드라이런 + 재동기화 halt + `KisClientError` 지수 백오프 + `backtest/costs.py` 비용 산식 재사용. 단위 테스트 63건 green (총 605건). 의존성 추가 없음.

### Phase 3 두 번째 산출물 — main.py + APScheduler (2026-04-21)

[x] `src/stock_agent/main.py` 신설. `BlockingScheduler(timezone='Asia/Seoul')` + 4종 cron job(09:00 session_start·매분 step·15:00 force_close·15:30 daily_report, 평일 한정). `--dry-run` CLI 플래그 → `DryRunOrderSubmitter` 주입 → KIS 주문 접촉 0. SIGINT/SIGTERM graceful shutdown. PR #17 리뷰 반영: `SessionStatus` 공개·세션 자본 기준 `balance.withdrawable` 교정·`Runtime.risk_manager` 공개 경로화·재진입 가드. 단위 테스트 47건 + 29건 추가·보강 (총 681건). 의존성 추가: `apscheduler 3.11.2`.

### Phase 3 세 번째 산출물 — monitor/notifier.py (2026-04-21)

[x] `src/stock_agent/monitor/` 패키지 신설. `Notifier` Protocol + `TelegramNotifier` + `NullNotifier` + `ErrorEvent`/`DailySummary` DTO. 핵심 결정(ADR-0012): Protocol 의존성 역전 유지(Executor 는 notifier 모름), `StepReport.entry_events`/`exit_events` 확장(기본값 `()` backward compat), 전송 실패 silent fail + 연속 실패 dedupe 경보(`consecutive_failure_threshold` 기본 5), 드라이런도 실전송 + `[DRY-RUN]` 프리픽스, plain text 한국어 포맷. `Executor.last_reconcile: ReconcileReport | None` 프로퍼티 신설. pytest **681 → 780건 green** (notifier 71건 신규 + executor/main 확장분). 의존성 추가 없음.

### Phase 3 네 번째 산출물 — storage/db.py (2026-04-22)

[x] `src/stock_agent/storage/` 패키지 신설. `TradingRecorder` Protocol(`@runtime_checkable`) + `SqliteTradingRecorder` + `NullTradingRecorder` + `StorageError`. 단일 파일 DB `data/trading.db`(historical `data/stock_agent.db` 와 별개). 스키마 v1: `orders`/`daily_pnl`/`schema_version` 3 테이블 + 2 인덱스. PRAGMA: WAL(파일 전용)/NORMAL/foreign_keys ON. autocommit + 스키마 init 한정 `BEGIN IMMEDIATE`. 실패 정책: `record_*` silent fail + 연속 실패 dedupe 경보(`monitor/notifier.py` 패턴 재사용), 생성자 실패만 `StorageError` raise → `NullTradingRecorder` 폴백(`_default_recorder_factory`). `EntryEvent`·`ExitEvent` 에 `order_number: str` 필드 추가. `main.py` 확장: `Runtime.recorder`, `_default_recorder_factory`, 콜백 4종에 `recorder.record_*` 삽입, `_graceful_shutdown`/`finally` 멱등 `close()`. 의존성 추가 없음(stdlib `sqlite3`). ADR-0013.

### Phase 3 다섯 번째 산출물 — broker 체결조회 + 부분체결 정책 (2026-04-22)

[x] ADR-0015 적용. `KisClient.cancel_order(order_number) -> None` 신설 + `PendingOrder.qty_filled: int` 필드 추가. `_to_pending_order` 가 PyKis 정식 필드 우선 매핑 → `qty_remaining` fallback. `execution/executor.py`: `OrderSubmitter.cancel_order` Protocol 확장 + `_resolve_fill(ticket) -> _FillOutcome` 교체 (타임아웃 시 `cancel_order` + 부분/0 체결 수습) + `_handle_entry` 부분체결 → `filled_qty` 만 기록, 0 체결 → skip + `_handle_exit` 부분/0 체결 → `ExecutorError`. 의존성 추가 없음.

**Phase 3 코드 산출물 전부 완료. PASS 선언은 모의투자 환경 연속 10영업일 무중단 + 0 unhandled error + 모든 주문이 SQLite 기록 + 텔레그램 알림 100% 수신 후.**

### Phase 3 PR2 — main.py 전략 wiring 교체 + RiskConfig 명시 주입 (2026-05-03, ADR-0025)

[x] `src/stock_agent/main.py`: `ORBStrategy`/`StrategyConfig` import 제거 → `RSIMRStrategy`/`RSIMRConfig` import 추가. `strategy = RSIMRStrategy(RSIMRConfig(universe=tuple(universe.tickers)))` + `risk_manager = RiskManager(RiskConfig(position_pct=Decimal("0.10"), max_positions=10, daily_loss_limit_pct=Decimal("0.02"), daily_max_entries=5))` 명시 주입. docstring 갱신 — "RSIMRStrategy (ADR-0023 채택, ADR-0025 한도 적용). EOD 트리거 + 분봉 fill 추적 하이브리드 운영은 PR3 에서 도입."

[x] `src/stock_agent/execution/executor.py`: `strategy` 매개변수 타입 `ORBStrategy` → `Strategy` Protocol 로 확장. `restore_session` 의 ORB 상태 복원 루프(`restore_long_position`/`mark_session_closed`/`reset_session`)를 `isinstance(strategy, ORBStrategy)` 가드로 감싸고, else 분기에서 RSIMRStrategy 등 일봉 전략은 EOD 일봉 재흐름으로 자연 복원 가정 + warning 로그.

pytest **2221 passed, 4 skipped** (PR2 기준).
