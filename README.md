# stock-agent

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

## 기술 스택 (예정)

- **언어**: Python 3.11+
- **의존성 관리**: `uv`
- **브로커 API**: `python-kis` 또는 `mojito2` (KIS Developers REST/WebSocket)
- **시장 데이터**: `pykrx` (KRX 공식 과거 OHLCV)
- **백테스팅**: `backtesting.py`
- **스케줄링**: `APScheduler`
- **알림**: `python-telegram-bot`
- **로깅**: `loguru`
- **저장소**: SQLite (MVP)
- **테스트**: `pytest`

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

**Phase 0 진입 예정** — 아직 코드는 작성되지 않았습니다. 상세 설계와 각 Phase의 PASS 기준, 비용·위험 분석은 [`plan.md`](./plan.md)에 있습니다.

### Phase 0 체크리스트

- [ ] 한국투자증권 비대면 계좌 개설
- [ ] KIS Developers 가입 및 **모의투자** APP_KEY / APP_SECRET 발급
- [ ] 텔레그램 봇 생성(@BotFather) 및 chat_id 확보
- [ ] 레포 초기화(`uv init`), `.env.example` 작성
- [ ] `scripts/healthcheck.py` 구현 — 모의 잔고 조회 + 텔레그램 알림 수신 확인

## 디렉토리 구조 (예정)

```text
stock-agent/
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── plan.md
├── config/          # universe.yaml, strategy.yaml
├── src/stock_agent/ # broker, data, strategy, risk, execution, backtest, monitor, storage
├── tests/
├── scripts/         # backtest.py, healthcheck.py
└── data/            # SQLite, 캐시 (gitignore)
```

## 설치 및 실행 (예정)

현재는 계획 단계이므로 실행 가능한 명령이 없습니다. Phase 1이 완료되면 다음과 같은 흐름으로 정리될 예정입니다.

```bash
# 예정 — 현 시점에서는 동작하지 않습니다
uv sync
cp .env.example .env   # KIS_APP_KEY, KIS_APP_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 입력
python scripts/healthcheck.py
```

## 참고 문서

- [`plan.md`](./plan.md) — 전체 설계서(Context, 전략 상세, 리스크 한도, 로드맵, Verification 기준, 비용·위험 분석)

## 책임 고지 (Disclaimer)

- 본 프로젝트는 **학습·연구 목적**이며 **수익을 보장하지 않습니다**. 데이트레이딩은 개인 투자자 대다수가 손실을 보는 고위험 영역입니다.
- 실전 투입 전 반드시 모의투자·백테스팅을 통해 검증하고, 본인 판단과 책임 하에 **잃어도 생활에 지장 없는 소액**으로 시작하십시오.
- 증권사 API 키 및 계좌 정보는 절대 리포지토리에 커밋하지 마십시오(`.env`는 `.gitignore` 대상).
- 본 코드가 원인이 된 금전적 손실에 대해 작성자는 책임을 지지 않습니다.
