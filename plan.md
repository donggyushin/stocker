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
- `backtesting.py`: 백테스팅 엔진 (단순·직관적)
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
│   ├── universe.yaml           # KOSPI 200 종목코드 (수동 관리, 분기 갱신)
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
│   │   └── realtime.py         # WebSocket/폴링 실시간 시세 (구현 예정)
│   ├── strategy/
│   │   ├── base.py             # Strategy 인터페이스
│   │   └── orb.py              # Opening Range Breakout
│   ├── risk/
│   │   └── manager.py          # 포지션 사이징, 손절/익절, 일일 한도
│   ├── execution/
│   │   ├── executor.py         # 주문 실행, 체결 추적, 동기화
│   │   └── state.py            # 보유 포지션 메모리 상태
│   ├── backtest/
│   │   └── engine.py           # backtesting.py 래퍼
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

### Phase 1 — 데이터 파이프라인 & 브로커 래퍼 (4~5일)
- `broker/kis_client.py`: 토큰 발급/갱신, 잔고 조회, 매수/매도 주문, 미체결 조회
- `data/historical.py`: pykrx로 KOSPI 200 구성종목 + 일봉 수집 & SQLite 캐시 (pykrx는 분봉 OHLCV 미지원. 분봉은 `data/realtime.py` 장중 폴링 누적 범위)
- `data/realtime.py`: 장중 분봉 폴링(우선) 또는 WebSocket 실시간 체결가 (후순위)
- 레이트 리미터 (KIS 초당 ~20회 제한 대응)
- **산출물**: 단위 테스트 + `healthcheck.py`에서 특정 종목 현재가 조회 성공

### Phase 2 — 전략 + 백테스팅 + 리스크 모듈 (5~7일)
- `strategy/orb.py`: 규칙 구현 (진입/청산 시그널 생성)
- `risk/manager.py`: 포지션 사이징, 손절/익절, 일일 손실 한도
- `backtest/engine.py`: 최근 2~3년 KOSPI 200 분봉 데이터로 ORB 백테스트
  - 리포트 항목: 총수익률, MDD, 샤프, 승률, 평균 손익비, 일평균 거래수, 수수료·세금 반영 후 순수익
  - **현실적 슬리피지 가정**: 시장가 0.1% 불리하게
- 파라미터 튜닝: OR 구간(15/30분), 손절/익절 레벨 비교
- **산출물**: 백테스트 리포트 HTML/노트북 + 파라미터 민감도 테이블

### Phase 3 — 모의투자 자동 실행 (5~7일)
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
- 성능 모니터링 대시보드 (Streamlit 또는 Grafana)

---

## Verification — 어떻게 검증할 것인가

**각 Phase의 PASS 기준**
- **Phase 0**: `python scripts/healthcheck.py` → 잔고 조회 OK, 텔레그램 알림 수신 OK
- **Phase 1**: `pytest tests/test_kis_client.py` 통과, 삼성전자(005930) 현재가 조회 OK
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
| pykrx 분봉 미지원 | 백테스트용 과거 분봉 데이터 확보 경로 미정 | Phase 2 착수 시점에 KIS 과거 분봉 API 추가 or realtime.py 누적본 재활용 중 선택. 현재는 `data/realtime.py` 가 장중 분봉을 폴링으로 수집·누적하는 경로 유지. |
| pykrx 1.2.7 지수 API(`get_index_portfolio_deposit_file` 등) KRX 서버 호환성 깨짐 + KIS Developers 인덱스 구성종목 API 미제공 | 자동 유니버스 갱신 불가 | `config/universe.yaml` 로 수동 관리. 분기 리밸런싱 때마다 운영자 갱신. Phase 5 에서 자동화 경로(pykrx 수정 릴리스 대기 또는 KRX 정보데이터시스템 스크래핑) 재도입. |

---

## 다음 액션 (Phase 1)

Phase 0 완료 (2026-04-19). Phase 1 진행 중 — 브로커 래퍼 + 데이터 파이프라인 구현. 첫 산출물 완료 (2026-04-19).

1. [x] `src/stock_agent/broker/kis_client.py` — 완료. DTO 정규화, pykis_factory 주입, paper 전용, live는 defer.
2. [x] `src/stock_agent/broker/rate_limiter.py` — 완료. 주문 경로 전용 `OrderRateLimiter`(기본 2 req/s + 최소 간격 350 ms). 조회 경로는 python-kis 내장 리미터에 그대로 위임.
3. [x] `src/stock_agent/data/historical.py` — 완료. pykrx 일봉 + SQLite 캐시 (KOSPI 200 구성종목 조회는 분리). `HistoricalDataStore`는 `fetch_daily_ohlcv` 전용으로 축소. SQLite 스키마 v3 (v2→v3 자동 마이그레이션).
3a. [x] `src/stock_agent/data/universe.py` + `config/universe.yaml` — 완료. KOSPI 200 유니버스 YAML 하드코딩. pykrx 지수 API·KIS Developers 모두 미제공으로 수동 관리. 분기 리밸런싱 때 운영자 갱신. 의존성 추가: `pyyaml 6.0.3`.
4. [ ] `src/stock_agent/data/realtime.py` — 장중 분봉 폴링(우선) 또는 WebSocket 실시간 체결가(후순위)
5. 단위 테스트 작성 + `healthcheck.py`에서 특정 종목(예: 삼성전자 005930) 현재가 조회 성공 확인

**Phase 1 PASS 기준**: `pytest tests/test_kis_client.py` 통과, 삼성전자(005930) 현재가 조회 OK.

현재 진척: broker(kis_client + rate_limiter) + data(historical + universe) 완료(pytest 68건 green, 1.43s). 다음은 data/realtime.
