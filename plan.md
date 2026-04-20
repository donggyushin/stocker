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

**언어/런타임**: Python 3.11+

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
- [x] `data/minute_csv.py`: CSV 분봉 어댑터 — 완료 2026-04-20. 레이아웃 `{csv_dir}/{symbol}.csv`, 헤더 `bar_time,open,high,low,close,volume`. 누락 파일 fail-fast, 여러 심볼 `heapq.merge` 정렬 스트리밍, stdlib 전용 추가 의존성 0. KIS 과거 분봉 API 어댑터는 별도 PR 로 분리.
- [ ] 파라미터 튜닝: OR 구간(15/30분), 손절/익절 레벨 비교 — 미착수
- **산출물**: 백테스트 리포트 HTML/노트북 + 파라미터 민감도 테이블 (실데이터 어댑터 도입 후)

### Phase 3 — 모의투자 자동 실행 (5~7일)
- **착수 전제**: 실전 APP_KEY (시세 전용) 발급 완료 + KIS Developers 포털에서 IP 화이트리스트 등록 + `healthcheck.py` 4종 통과.
- `execution/executor.py`: 신호 → 주문 → 체결 추적 → 상태 동기화 루프
- `main.py`: APScheduler로 9:00 시작, 9:30 OR 확정, 장중 루프, 15:00 청산, 15:30 리포트
- `monitor/notifier.py`: 진입/청산/에러/일일 요약 텔레그램 알림
- SQLite에 모든 주문/체결/PnL 기록
- **드라이런 모드**: `--dry-run` 플래그로 주문 API 호출 없이 로그만 (최종 검증용)
- **산출물**: **모의투자 환경에서 최소 2주 무사고 운영** (에러 0건 · 알림 정상 · PnL 기록 정확)

### Phase 4 — 소액 실전 전환 (운영 상시)
- 모의 2주 통과 시 실전 APP_KEY로 전환, 환경변수만 교체
- **초기 1주는 자본 50만원 한도** (`config/strategy.yaml`로 제한), 무사고 확인 후 점진 증액
- 매일 장 마감 후 PnL · 승률 · 최대 손실 종목 텔레그램 리포트
- 주간 회고: 백테스트 대비 실전 괴리(슬리피지, 누락 체결) 측정

### Phase 5 — 개선 사이클 (지속)
- 2번째 전략 추가 (예: VWAP 반등) 후 A/B
- 장시간 안정성 요구되면 네이버클라우드/AWS Seoul VPS 이전 (월 5천~2만원)
- 종목 선정 로직 고도화 (거래대금 상위, 변동성 필터)
- `scripts/update_universe.py` — CSV 기반 `config/universe.yaml` 갱신 자동화. 연 2회 정기변경 수동 갱신 워크플로우의 휴먼 에러 제거. `data/universe_imports/*.csv` 최신 파일 자동 선택(또는 `--csv <path>`), 인코딩 자동 감지, 로더 정규식과 동일한 티커 검증, 임시 가상 코드(`NNNNZ0` 등) 자동 제외 + 주석 기록, 티커 수 ±20% 편차 가드, 기본 드라이런(diff 출력) + 명시적 `--apply` 플래그로만 YAML 덮어쓰기. 부팅 시 자동 스캔은 지양(결정론성 보호 — 장중에 파일이 떨어져 유니버스가 바뀌는 사고 방지). git add/commit 은 운영자 책임.
- 성능 모니터링 대시보드 (Streamlit 또는 Grafana)

---

## Verification — 어떻게 검증할 것인가

**각 Phase의 PASS 기준**
- **Phase 0**: `python scripts/healthcheck.py` → 잔고 조회 OK, 텔레그램 알림 수신 OK
- **Phase 1**: `pytest tests/test_kis_client.py` 통과 — **충족** (pytest 131건 green). 삼성전자(005930) 현재가 조회 OK — **코드 경로 완성 + WebSocket 구독 등록 성공**. 틱 수신 end-to-end 확인(장중 + 실전 키)은 Phase 3 착수 전제로 이관.
- **Phase 2**:
  - `pytest tests/test_strategy_orb.py` — 고정 시나리오 OHLCV 입력에 정확한 진입/청산 시그널
  - `python scripts/backtest.py --from 2023-01-01 --to 2025-12-31` → 리포트 생성, 수수료·세금 반영, MDD < -15%
- **Phase 3**: 모의투자 **연속 10영업일 무중단 · 0 unhandled error · 모든 주문이 SQLite에 기록 · 텔레그램 알림 100% 수신**
- **Phase 4**: 실전 1주차 실거래 결과가 백테스트 범위 ±50% 이내 (슬리피지 과대 여부 체크)

**End-to-End 스모크 테스트 (매일 장전 1회)**

```bash
python scripts/healthcheck.py
# 1) KIS 토큰 발급 OK
# 2) 잔고 조회 OK
# 3) 삼성전자 현재가 조회 OK
# 4) 텔레그램 "장전 점검 통과" 수신
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
| pykrx 분봉 미지원 | **백테스트용 과거 분봉** 데이터 확보 경로 미정 (장중 실시간 분봉은 `data/realtime.py` 로 해소) | `minute_csv.py` 로 CSV 임포트 경로 해소 (2026-04-20). KIS 과거 분봉 API 어댑터는 별도 PR. |
| pykrx 1.2.7 지수 API(`get_index_portfolio_deposit_file` 등) KRX 서버 호환성 깨짐 + KIS Developers 인덱스 구성종목 API 미제공 | 자동 유니버스 갱신 불가 | `config/universe.yaml` 로 수동 관리. 연 2회 정기변경(6월·12월)마다 운영자 갱신. Phase 5 에서 자동화 경로(pykrx 수정 릴리스 대기 또는 KRX 정보데이터시스템 스크래핑) 재도입. |
| KIS paper 도메인(`openapivts`) 시세 API(`/quotations/*`) 미제공 → python-kis 고레벨 시세 API paper 환경에서 사용 불가 | 모의투자 자동 실행(Phase 3) 에서 실시간 체결가 수신 불가 | 시세 전용 실전 APP_KEY 발급, 실전 도메인(`openapi`) 직접 호출 (`RealtimeDataStore`). Phase 3 착수 전 실전 앱 발급·IP 화이트리스트 등록 필수. |
| 실전 키 IP 화이트리스트 이탈 (공인 IP 변경, ISP 동적 IP 할당 등) | 시세 단절 → `RealtimeDataStore` 전체 장애 | `healthcheck.py` 에서 `EGW00123` 계열 오류 감지 시 힌트 로그("KIS Developers 포털 → 앱 관리 → 허용 IP 갱신") 출력. 장기적으로 VPS 이전 시 고정 IP 확보 (Phase 5). |
| 자체 백테스트 루프의 시뮬레이션 정확도 검증 부재 | 비용 계산 오류가 백테스트 PnL 을 왜곡 → 실전 괴리 | 후속 PR 에서 KIS 실데이터로 회귀 비교. 현 PR 은 단위 테스트(costs 18 + metrics 22)로 슬리피지·수수료·거래세 적용 정확도를 명시 assert. |

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

Phase 2 네 번째 산출물(CSV 분봉 어댑터) 완료. 전체 PASS 선언은 파라미터 민감도 리포트 + 실데이터 PASS 검증 이후.

1. [x] `src/stock_agent/strategy/orb.py` + `base.py` + `__init__.py` — 완료. `ORBStrategy` 상태 머신(IDLE→FLAT→LONG→CLOSED), `StrategyConfig`(frozen dataclass, 생성자 주입), `Strategy` Protocol(최소 — `on_bar`/`on_time`), `EntrySignal`/`ExitSignal`/`ExitReason` DTO. 설계 결정: 분봉 close 기준 strict 돌파, 동일 분봉 손절·익절 동시 성립 시 손절 우선, 1일 1회 진입, `force_close_at` 이후 신규 진입 금지, 세션 경계 자동 리셋. 의존성 추가 없음.
2. [x] `src/stock_agent/risk/manager.py` — 완료 2026-04-20. `RiskConfig` 기본값 고정(position_pct 20%, max_positions 3, daily_loss_limit_pct 2%, daily_max_entries 10, min_notional 10만원). `realized_pnl_krw` 부호 계약(손실 음수·수익 양수)은 호출자 책임. 공개 심볼 6종(`RiskManager`, `RiskConfig`, `RiskDecision`, `PositionRecord`, `RejectReason`, `RiskManagerError`) `risk/__init__` 재노출.
3. [x] `src/stock_agent/backtest/{__init__.py, engine.py, costs.py, metrics.py, loader.py}` — 완료 2026-04-20. 자체 시뮬레이션 루프(`backtesting.py` 폐기). `ORBStrategy` + `RiskManager` 호출, 슬리피지(0.1%) + 수수료(0.015%) + 거래세(0.18% 매도만) 반영, 세션 마감 force_close 훅, 복리 자본 갱신, phantom_long 처리(rejected entry 의 후속 ExitSignal 흡수), 시간 단조증가 검증. 외부 I/O 0, 의존성 추가 0. 공개 심볼 8종(`BacktestEngine`, `BacktestConfig`, `BacktestResult`, `BacktestMetrics`, `TradeRecord`, `DailyEquity`, `BarLoader`, `InMemoryBarLoader`).
4. [x] `src/stock_agent/data/minute_csv.py` — 완료 2026-04-20. `MinuteCsvBarLoader` + `MinuteCsvLoadError` 공개. 레이아웃 `{csv_dir}/{symbol}.csv`, 헤더 `bar_time,open,high,low,close,volume`. bar_time naive KST 파싱·오프셋 포함 거부, Decimal 가격 파싱, OHLC 일관성 검증, 분 경계 강제, 단조증가+중복 금지, 누락 파일 fail-fast. 여러 심볼 `heapq.merge` 정렬 스트리밍. stdlib 전용, 추가 의존성 0. KIS 과거 분봉 API 어댑터는 별도 PR.
5. [ ] 파라미터 민감도 리포트 — 미착수

pytest **245 → 324 → 384건 green** (기존 324 + test_minute_csv 56). ruff check/format + black --check 모두 green. 의존성 추가 없음.
