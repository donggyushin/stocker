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

| 항목 | 기본값 | 이유 |
|---|---|---|
| 종목당 진입 금액 | 자본의 20% | 동시 최대 5종목 분산 |
| 동시 보유 종목 수 | 최대 3종목 (MVP) | 모니터링 부담 경감 |
| 종목당 손절 | -1.5% | 익절 2:1 손익비 확보 |
| 종목당 익절 | +3.0% or 15:00 청산 | 당일 청산 원칙 |
| 일일 손실 한도 | 자본의 -2% 도달 시 당일 매매 중단 | 서킷브레이커 |
| 일일 최대 진입 | 10회 | 오버트레이딩 방지 |
| 최소 거래금액 | 10만원/종목 | 수수료 비중 관리 |

---

## 아키텍처 & 기술 스택

**언어/런타임**: Python 3.12+

**CI**: `.github/workflows/ci.yml` — PR 및 main push 시 `uv sync --frozen` → ruff/black 정적 분석 → pytest 자동 실행. main 브랜치는 CI job `Lint, format, test` 통과 없이 머지 불가 (required status check, `strict=true`).

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
- 레포 초기화 (`uv init`), `.env`/`.gitignore`, `pre-commit`(ruff/black) 세팅
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
- walk-forward validation 본 구현 (`src/stock_agent/backtest/walk_forward.py` — 스켈레톤 선행 도입 완료 2026-04-23, Issue #67): `generate_windows`·`run_walk_forward` 본체 구현 + `pass_threshold` 기본값 결정 ADR 작성 예정. 민감도 그리드는 sanity check 이지 walk-forward 를 대체하지 않는다.
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

### Step C 인프라 완료 — 운영자 실행 대기 (2026-04-30, Issue #76)

코드 산출물 4건 완료. 운영자 실행(별도 PR)은 미완료.

**신규 스크립트**:

- `scripts/build_liquidity_ranking.py`: KOSPI 200 유동성 랭킹 산출.
  - `pykrx get_market_ohlcv_by_ticker(yyyymmdd, market="KOSPI")` 영업일 bulk 호출
  - `YamlBusinessDayCalendar` 영업일 필터 (주말·공휴일 skip)
  - 출력 CSV 컬럼: `symbol,avg_value_krw,daily_return_std,sample_days,rank_value`
  - 50% 이상 영업일 실패 시 `RuntimeError("excessive_failures: ...")` fail-fast
  - exit code: `0` 정상 / `2` 입력·설정 오류 / `3` I/O 오류
  - 사용 예: `uv run python scripts/build_liquidity_ranking.py --start 2024-04-22 --end 2025-04-21 --universe-yaml config/universe.yaml --output-csv data/liquidity_ranking.csv`

- `scripts/build_universe_subset.py`: 유동성 랭킹 CSV → KOSPI 200 서브셋 YAML 생성.
  - `rank_value <= top_n` 종목 추출. 출력 스키마: `as_of_date / source / tickers` (`config/universe.yaml` 정본과 동일)
  - 작성 직후 `load_kospi200_universe(output)` 자체 검증. `rank_value` 1..N 연속 검증 (음수·중복·결손 거부)
  - ADR-0004 수동 관리 정책 계승: 자동 git 커밋 아님 — 운영자 검토 후 `git add` 책임
  - 사용 예: `uv run python scripts/build_universe_subset.py --ranking-csv data/liquidity_ranking.csv --top-n 50 --output-yaml config/universe_top50.yaml --source "Step C — Top 50 by avg_value_krw, window=2024-04-22..2025-04-21" --as-of 2025-04-21`

**기존 CLI 확장** (`scripts/backtest.py`, `scripts/sensitivity.py`):
- `--universe-yaml PATH` 플래그 추가 (default `config/universe.yaml`, backward-compat)
- `_resolve_symbols(raw, universe_yaml=None)` 시그니처 확장 — path 주어지면 `load_kospi200_universe(path)` 호출
- 효과: `--loader=kis --universe-yaml config/universe_top50.yaml` 로 서브셋 백테스트 실행 가능

**테스트**: 신규 44건 (`test_build_liquidity_ranking.py` 16 + `test_build_universe_subset.py` 16 + `test_backtest_cli.py` +6 + `test_sensitivity_cli.py` +6). 4종 정적 검사 GREEN.

**다음 단계 (별도 PR — 운영자 실행)**:
1. `uv run python scripts/build_liquidity_ranking.py --start 2024-04-22 --end 2025-04-21 --universe-yaml config/universe.yaml --output-csv data/liquidity_ranking.csv`
2. `uv run python scripts/build_universe_subset.py --ranking-csv data/liquidity_ranking.csv --top-n 50 --output-yaml config/universe_top50.yaml ...` (top-100 도 동일)
3. `uv run python scripts/backtest.py --loader=kis --universe-yaml config/universe_top50.yaml --from 2025-04-22 --to 2026-04-21` (top-100 도 동일)
4. ADR-0019 세 게이트 판정 (MDD > -15%, 승률 × 손익비 > 1.0, 샤프 > 0)
5. `docs/runbooks/step_c_liquidity_filter_YYYY-MM-DD.md` 작성
6. 통과 서브셋 없으면 Step D 진행

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
