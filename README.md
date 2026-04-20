# stock-agent

![CI](https://github.com/donggyushin/korean-stock-trading-system/actions/workflows/ci.yml/badge.svg?branch=main)

Python 기반 한국주식 **데이 트레이딩** 자동매매 시스템 (MVP 설계 단계).

## 개요

KOSPI 200 대형주를 대상으로 Opening Range Breakout(ORB) 전략을 자동 실행하는 시스템을 구축합니다. 초기에는 한국투자증권 **모의투자** 환경에서 검증하고, 2주 이상 무사고 운영이 확인된 뒤에만 소액(100~200만원) 실전으로 전환합니다.

**왜 한국투자증권인가**: 토스증권은 개인 개발자용 Open API를 제공하지 않습니다(2026-04 기준). 자동매매가 가능하면서 Mac/Python 환경에서 바로 사용할 수 있는 REST API + 모의투자 지원 증권사가 한국투자증권(KIS Developers)입니다.

## 핵심 전제

- 본 프로젝트의 목표는 **"수익 극대화"가 아니라 "손실을 최소화하며 데이터로 검증한 전략을 규율 있게 실행"** 하는 것입니다.
- 데이트레이딩은 통계적으로 개인 투자자의 **70~90%가 손실**을 봅니다. 거래세(0.18%) + 수수료 + 슬리피지가 마진을 잠식합니다.
- 실전 투입 전 **모의투자 → 백테스팅 → 페이퍼트레이딩** 단계를 반드시 거칩니다.

## 매매 전략

**Opening Range Breakout (ORB)** — long-only

- 9:00~9:30 동안 종목별 고가(OR-High)·저가(OR-Low) 기록
- 9:30 이후 OR-High 상향 돌파 시 시장가 매수
- 손절 -1.5%, 익절 +3.0%, 15:00 강제 청산
- KOSPI 200 중 9:00~9:30 거래대금 상위 N종목만 후보

## 리스크 관리 (기본값)

| 항목 | 값 |
|---|---|
| 종목당 진입 금액 | 자본의 20% |
| 동시 보유 종목 | 최대 3종목 |
| 종목당 손절 / 익절 | -1.5% / +3.0% |
| 일일 손실 한도 | 자본의 -2% 도달 시 당일 매매 중단 |
| 일일 최대 진입 | 10회 |

## 기술 스택

- **언어**: Python 3.12+
- **의존성 관리**: `uv`
- **브로커 API**: `python-kis 2.x` (KIS Developers REST/WebSocket)
- **시장 데이터**: `pykrx` (KRX 공식 과거 OHLCV), `pyyaml` (유니버스 설정 파일 로드)
- **백테스팅**: 자체 시뮬레이션 루프 (`backtest/engine.py`) — `backtesting.py` 라이브러리는 다중종목·포트폴리오 게이팅 표현 불가 + AGPL 라이센스 부담으로 폐기 (2026-04-20)
- **스케줄링**: `APScheduler`
- **알림**: `python-telegram-bot 22.x`
- **로깅**: `loguru`
- **설정 검증**: `pydantic-settings`
- **저장소**: SQLite (MVP)
- **테스트**: `pytest`, `pytest-mock`
- **포매터/린터**: `ruff`, `black` (`pre-commit` 훅 적용)
- **CI**: GitHub Actions — PR 및 main push 마다 ruff·black·pytest 자동 실행. main 머지는 CI job `Lint, format, test` 통과 필수.

## 로드맵 (총 약 4주)

| Phase | 기간 | 목표 |
|---|---|---|
| 0 | 2~3일 | 한투 계좌 개설, KIS 모의 키 발급, 텔레그램 봇 세팅, 레포 초기화 |
| 1 | 4~5일 | KIS 클라이언트(토큰/주문/조회) + 데이터 파이프라인(pykrx + 분봉 폴링) |
| 2 | 5~7일 | ORB 전략 구현, 리스크 매니저, 2~3년 분봉 백테스팅 리포트 |
| 3 | 5~7일 | 모의투자 자동 실행 루프, 텔레그램 알림, **2주 이상 무사고 운영** |
| 4 | 상시 | 소액 실전 전환 (초기 50만원 한도부터), 주간 회고 |
| 5 | 지속 | 2번째 전략 A/B, 필요 시 클라우드 VPS 이전, 대시보드 |

## 현재 상태

**Phase 1 PASS (코드·테스트 레벨)** (2026-04-19 선언). Phase 0 환경 준비 완료. broker(KisClient + rate_limiter) + data(historical + universe + realtime) 모두 완료. pytest **131건 green**. **paper 주문 + live 시세 하이브리드 키 정책 도입**: KIS paper 도메인에 시세 API가 없어 `RealtimeDataStore`는 별도 실전 APP_KEY로 실전 도메인을 호출하며, 실전 키 PyKis 인스턴스에는 `install_order_block_guard`를 설치해 주문 경로를 구조적으로 차단한다. **주의**: 장중 실시간 시세 수신 end-to-end 확인(실전 키 + IP 화이트리스트 + 평일 장중 틱 수신)은 Phase 3 착수 전제로 이관됨.

**Phase 2 진행 중 — ORB 전략 엔진 + 리스크 매니저 + 백테스트 엔진 코어 + CSV 분봉 어댑터 + 파라미터 민감도 그리드 + backtest.py CLI 완료** (2026-04-20). `strategy/` + `risk/` + `backtest/` 패키지 신설. pytest **542건 green**. `scripts/backtest.py` 실행 가능. **PASS 선언은 실데이터 수집 후 낙폭 절대값 15% 미만 확인 (MDD > -15%)까지 보류** — KIS 과거 분봉 API 어댑터(별도 PR) + 2~3년 분봉 CSV 확보(운영자 외부 작업) 필요. 상세 설계와 각 Phase의 PASS 기준, 비용·위험 분석은 [`plan.md`](./plan.md)에 있습니다.

**운영 주의**: KOSPI 200 구성종목은 `config/universe.yaml`에 수동 관리합니다. KRX KOSPI 200 정기변경(연 2회 — 매년 6월·12월의 선물·옵션 동시만기일 익영업일 기준)에 맞춰 운영자가 직접 갱신해야 합니다. 현재 KRX 정보데이터시스템 [11006] 기준 199/200 반영(2026-04-17 조회, 임시 가상 코드 1건 제외). 정식 티커 발급 후 다음 갱신에 추가 예정.

### Phase 0 체크리스트 (완료 2026-04-19)

- [x] 한국투자증권 비대면 계좌 개설
- [x] KIS Developers 가입 및 **모의투자** APP_KEY / APP_SECRET 발급
- [x] 텔레그램 봇 생성(@BotFather) 및 chat_id 확보
- [x] 레포 초기화(`uv init`), `.env.example` 작성
- [x] `scripts/healthcheck.py` 구현 — 모의 잔고 조회 + 텔레그램 알림 수신 확인

## 디렉토리 구조

현재 존재하는 파일 (Phase 2 여섯 번째 산출물 완료 기준):

```text
stock-agent/
├── .github/workflows/
│   └── ci.yml                 # PR·main push 시 ruff/black/pytest 자동 실행
├── .python-version            # 3.12
├── pyproject.toml             # uv 기반, ruff/black/pytest 설정 포함
├── uv.lock                    # 패키지 잠금 (pykrx 1.2.7, pyyaml 6.0.3 포함)
├── .pre-commit-config.yaml    # ruff, black, 기본 훅
├── .env.example               # KIS·텔레그램 키 placeholder (.env는 .gitignore)
├── .gitignore
├── README.md
├── plan.md
├── config/
│   └── universe.yaml          # KOSPI 200 종목코드 (수동 관리, 연 2회 정기변경)
├── src/stock_agent/
│   ├── __init__.py
│   ├── config.py              # pydantic-settings Settings + get_settings() 캐시
│   ├── broker/
│   │   ├── __init__.py
│   │   ├── kis_client.py      # KisClient — 토큰 관리, 잔고/주문/조회 DTO
│   │   ├── rate_limiter.py    # OrderRateLimiter — 주문 경로 전용 (2 req/s, 350ms)
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   └── data/
│       ├── __init__.py        # HistoricalDataStore, HistoricalDataError, DailyBar, KospiUniverse, UniverseLoadError, load_kospi200_universe, RealtimeDataStore, TickQuote, MinuteBar, RealtimeDataError export
│       ├── historical.py      # pykrx 일봉 SQLite 캐시 (fetch_daily_ohlcv 전용, 스키마 v3)
│       ├── universe.py        # KOSPI 200 유니버스 YAML 로더 (load_kospi200_universe)
│       ├── realtime.py        # 실시간 시세 (RealtimeDataStore — WebSocket 우선 + REST 폴링 fallback)
│       └── CLAUDE.md          # 모듈 세부 문서
│   ├── strategy/
│   │   ├── __init__.py        # EntrySignal, ExitReason, ExitSignal, ORBStrategy, Signal, Strategy, StrategyConfig, StrategyError export
│   │   ├── base.py            # Strategy Protocol, EntrySignal/ExitSignal DTO, ExitReason Literal, KST 상수
│   │   ├── orb.py             # ORBStrategy 상태 머신 + StrategyConfig (frozen dataclass)
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   ├── risk/
│   │   ├── __init__.py        # RiskManager, RiskConfig, RiskDecision, PositionRecord, RejectReason, RiskManagerError export
│   │   ├── manager.py         # RiskManager — 포지션 사이징·진입 게이팅·서킷브레이커
│   │   └── CLAUDE.md          # 모듈 세부 문서
│   └── backtest/
│       ├── __init__.py        # BacktestEngine, BacktestConfig, BacktestResult, BacktestMetrics, TradeRecord, DailyEquity, BarLoader, InMemoryBarLoader export
│       ├── engine.py          # BacktestEngine — 자체 시뮬레이션 루프
│       ├── costs.py           # 슬리피지·수수료·거래세 순수 함수
│       ├── metrics.py         # 총수익률·MDD·샤프·승률·평균손익비·일평균거래수
│       ├── loader.py          # BarLoader Protocol + InMemoryBarLoader
│       └── CLAUDE.md          # 모듈 세부 문서
├── tests/
│   ├── test_config.py
│   ├── test_kis_client.py
│   ├── test_safety.py
│   ├── test_rate_limiter.py
│   ├── test_historical.py     # 14 케이스
│   ├── test_universe.py       # 11 케이스
│   ├── test_realtime.py       # 28 케이스
│   ├── test_strategy_orb.py   # 36 케이스
│   ├── test_risk_manager.py   # 73 케이스
│   └── test_backtest_engine.py # 79 케이스 (pytest 324건 green)
└── scripts/
    └── healthcheck.py         # KIS 모의 잔고 조회 + 텔레그램 hello (실주문 없음)
```

`execution/`, `monitor/`, `storage/` 등 미구현 모듈의 청사진은 [`plan.md`](./plan.md)의 디렉토리 구조 섹션 참조.

## 설치 및 실행

### 사전 준비

1. KIS Developers 포털에서 **모의투자계좌 API 신청** 완료 및 모의투자용 APP_KEY / APP_SECRET 발급
   - MTS의 "상시 모의투자 참가신청"과 별개 절차임에 주의
2. 텔레그램 @BotFather로 봇 생성 → 토큰 및 chat_id 확보

### 환경 설정

```bash
# 가상환경 및 의존성 설치
uv sync

# git 훅 설치 (최초 1회)
uv run pre-commit install

# 환경변수 파일 작성 (.env는 절대 커밋하지 않습니다)
cp .env.example .env
# .env 에서 아래 값을 채웁니다:
#   KIS_ENV          = paper
#   KIS_HTS_ID       = 한투 HTS 아이디
#   KIS_APP_KEY      = 모의투자 APP_KEY (36자)
#   KIS_APP_SECRET   = 모의투자 APP_SECRET (180자)
#   KIS_ACCOUNT_NO   = 계좌번호 (XXXXXXXX-XX 형식)
#   TELEGRAM_BOT_TOKEN = 텔레그램 봇 토큰
#   TELEGRAM_CHAT_ID   = 텔레그램 chat_id
#
#   --- 시세 전용 실전 키 (Phase 3 착수 전 필수, 미설정 시 healthcheck 4번 SKIP) ---
#   # HTS_ID 는 paper/실전 공유 — 위 KIS_HTS_ID 재사용 (한 사람 당 하나)
#   KIS_LIVE_APP_KEY   = 실전 APP_KEY (36자)  ← KIS Developers 포털에서 실전 앱 별도 신청
#   KIS_LIVE_APP_SECRET = 실전 APP_SECRET (180자)
#   KIS_LIVE_ACCOUNT_NO = 실전 계좌번호 (XXXXXXXX-XX) — paper 계좌번호와 다름
#   # 실전 앱 발급 후 KIS Developers 포털 → 앱 관리 → 허용 IP 목록에 현재 공인 IP 등록 필수
```

### 환경 점검

```bash
uv run python scripts/healthcheck.py
# 통과 기준:
# 1) KIS 모의투자 토큰 발급 OK
# 2) 모의 계좌 잔고 조회 OK
# 3) 텔레그램 "hello" 메시지 수신 OK
# 4) 삼성전자(005930) 현재가 조회 OK — mode=websocket | polling
#    (실전 키 미설정 시 4번은 SKIP — Phase 3 착수 전 반드시 해결:
#     실전 키 3종 기입 + KIS Developers 포털 IP 화이트리스트 등록 필수)
```

## 참고 문서

- [`plan.md`](./plan.md) — 전체 설계서(Context, 전략 상세, 리스크 한도, 로드맵, Verification 기준, 비용·위험 분석)

## 책임 고지 (Disclaimer)

- 본 프로젝트는 **학습·연구 목적**이며 **수익을 보장하지 않습니다**. 데이트레이딩은 개인 투자자 대다수가 손실을 보는 고위험 영역입니다.
- 실전 투입 전 반드시 모의투자·백테스팅을 통해 검증하고, 본인 판단과 책임 하에 **잃어도 생활에 지장 없는 소액**으로 시작하십시오.
- 증권사 API 키 및 계좌 정보는 절대 리포지토리에 커밋하지 마십시오(`.env`는 `.gitignore` 대상).
- 본 코드가 원인이 된 금전적 손실에 대해 작성자는 책임을 지지 않습니다.
