# stock-agent — 작업 가이드

이 프로젝트에서 작업할 때 반드시 읽어야 하는 파일입니다.

## 프로젝트 한 줄 요약

Python 기반 한국주식 **데이트레이딩** 자동매매 시스템. 한국투자증권 KIS Developers API + Opening Range Breakout(ORB) 전략 + 100~200만원 초기 자본. 현재 **Phase 1 진행 중** (환경 준비 완료, 브로커 래퍼 + 데이터 파이프라인 구현 단계).

상세 설계는 `plan.md`를 참조한다. 외부 독자용 개요는 `README.md`.

## 소통 언어

한국어로 응답·작성한다. 기존 문서 톤(담담·구체·단정형)을 유지한다. 이모지는 쓰지 않는다.

## 확정된 결정 (임의 변경 금지, 변경 필요 시 사용자에게 먼저 확인)

- 증권사: 한국투자증권 KIS Developers (토스증권은 API 미제공)
- 전략: Opening Range Breakout (long-only, KOSPI 200 대형주)
- 초기 자본: 100~200만원
- 실행: 로컬 맥북, 장중(9:00~15:30 KST)
- 알림: 텔레그램 봇
- 스택: Python 3.11+, `uv`, `python-kis 2.x`, `pykrx`, `backtesting.py`, `APScheduler`, `loguru`, `python-telegram-bot`, SQLite
- 리스크 한도: 종목당 진입 자본의 20%, 동시 3종목, 손절 -1.5% / 익절 +3.0% / 15:00 강제청산, 일일 손실 -2% 서킷브레이커

자세한 수치와 근거는 `plan.md` 참조.

## 문서 동기화 정책 (중요)

프로젝트에는 세 개의 정본 문서가 있다:

| 문서 | 역할 |
|---|---|
| `CLAUDE.md` | Claude가 매 세션 시작 시 로드. 작업 지침과 현재 상태 요약. |
| `README.md` | 외부/신규 독자용 진입점. |
| `plan.md` | 승인된 상세 설계(로드맵·리스크·검증 기준). |

**작업 중 아래와 같은 사실관계 변경이 발생하면, 그 턴 안에 프로젝트의 `markdown-writer` 서브에이전트를 호출해 관련 문서를 동기화한다.**

동기화가 필요한 변경 예시:
- Phase 진입/완료, Phase 산출물 달성
- 전략 파라미터 또는 리스크 한도 변경 (예: 손절 -1.5% → -1.2%)
- 기술 스택 교체 (라이브러리 선택 확정, 저장소 변경 등)
- 디렉토리 구조 변경
- 새로운 결정사항 도입 또는 기존 결정 번복
- 실행 가능한 명령/스크립트의 신규 추가 ("예정" → 실제 실행 가능)

`markdown-writer` 호출 시 전달할 것: (a) 무엇이 바뀌었는지 (b) 어느 문서를 고쳐야 하는지 (c) 기존 승인 결정과 리스크 고지는 보존할 것.

### 동기화 필수 매트릭스

| 변경 유형 | CLAUDE.md | README.md | plan.md |
|---|:-:|:-:|:-:|
| Phase 상태 전환 | O | O | O |
| 리스크 한도 값 변경 | O | O | O |
| 기술 스택 교체 | O | O | O |
| 디렉토리 구조 추가 | — | O | O |
| 새 명령/스크립트 실행 가능 | — | O | O |
| 리스크 고지 수정 | — | O (완화 금지) | O |

### 계층 CLAUDE.md (모듈별 문서)

모듈별 세부 사실(공개 API, 설계 원칙, 테스트 정책, 주의 사항)은 해당 폴더의 `CLAUDE.md` 에 둔다.
root `CLAUDE.md` 는 프로젝트 전체 상태 요약과 하위 문서 링크만 유지한다.

현재 하위 CLAUDE.md:
- [src/stock_agent/broker/CLAUDE.md](./src/stock_agent/broker/CLAUDE.md) — KIS Developers API 래퍼 모듈 (KisClient, DTO, 에러 정책, 데이터 무결성 가드)

하위 CLAUDE.md 를 추가·갱신할 때도 root 의 동기화 가드레일(승인된 결정 보존·리스크 고지 보존·존재하지 않는 코드/명령 생성 금지)을 동일하게 적용한다.
신규 모듈(`src/stock_agent/<새 모듈>/`) 이 실제 코드와 함께 도입되면 같은 턴에 해당 폴더의 `CLAUDE.md` 도 작성하고 root 의 이 목록을 갱신한다.

## 리스크·고지 원칙

금융 자동매매 특성상 문서와 응답에서 다음 기조를 유지한다.

- "수익 보장" 같은 표현 금지.
- 실전 전 **모의투자 → 백테스트 → 페이퍼트레이딩** 선행 원칙을 꺾지 않는다.
- README.md 하단 책임 고지(Disclaimer) 섹션은 항상 유지.
- 사용자가 "무조건 수익" 류의 기대를 표현하면, 데이트레이딩 현실(개인 70~90% 손실, 수수료·세금·슬리피지)을 간결히 상기시키고 계획된 검증 단계로 안내.

## 민감정보 취급

- `KIS_APP_KEY`, `KIS_APP_SECRET`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` 등은 `.env`에만. 절대 커밋 금지.
- 커밋·PR 전 diff에 키 문자열이 섞여들어가지 않았는지 확인.
- 모의투자 키와 실전 키는 환경변수로만 구분, 코드에 하드코딩하지 않는다.

## 코드 스타일 (Phase 0에서 도구 도입 완료, Phase 1부터 본격 적용)

- Python 3.11+, `uv`로 의존성 관리.
- 타입 힌트 필수. 설정·외부 경계는 `pydantic`으로 검증.
- 포매터·린터: `ruff` + `black`. 가능하면 `pre-commit`에 물릴 것.
- 로깅: `loguru` (구조화 로그 권장).
- 테스트: `pytest`, API 호출은 목킹.

## 현재 상태 (2026-04-19 기준)

- **Phase 0 완료** (2026-04-19)
  - `scripts/healthcheck.py` 3종 통과: KIS 모의투자 토큰 발급 OK, 모의 계좌 잔고 조회 OK (시드 10,000,000원), 텔레그램 "hello" 수신 OK
  - 신규 파일: `.python-version`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `.env.example`, `src/stock_agent/__init__.py`, `src/stock_agent/config.py`, `scripts/healthcheck.py`
  - 의존성 확정: `python-kis 2.1.6`, `python-telegram-bot 22.7`, `pydantic 2.13`, `pydantic-settings 2.13`, `loguru 0.7` / dev: `ruff 0.15`, `black 26.3`, `pytest 9.0`, `pytest-mock 3.15`, `pre-commit 4.5`
  - `python-kis` paper-only 초기화 우회: 모의 키를 실전 슬롯과 모의 슬롯 양쪽에 동일 입력 → `PyKis.virtual = True`로 모든 요청이 모의 도메인으로만 라우팅됨. Phase 4 실전 전환 시 실전 APP_KEY/SECRET 별도 발급 후 슬롯 분리.
  - 운영 메모: KIS Developers에서 "모의투자계좌 API 신청"을 MTS의 "상시 모의투자 참가신청"과 별도로 완료해야 모의 키 발급 가능 (미신청 시 `EGW2004` 에러). 토큰 첫 발급 시 레이트 리밋 경고 2회 후 자동 재시도 통과 — 정상 동작 범위.
  - GitHub Actions CI 도입 (`.github/workflows/ci.yml`): PR 및 main push 시 `uv sync --frozen` → ruff/black 정적 분석 → pytest 자동 실행. 첫 실행 12초, 10/10 통과 (PR #1 검증).
  - main 브랜치 보호 적용: required status check `Lint, format, test` (CI job), `strict=true`, force push/삭제 금지.

- **Phase 1 진행 중 — 브로커 래퍼 + 데이터 파이프라인** (첫 산출물 완료 2026-04-19)
  - `src/stock_agent/broker/` 패키지 신설 — `KisClient` + DTO 정규화. 모듈 세부(공개 API, 에러 정책, 데이터 무결성 가드, 테스트 정책)는 [src/stock_agent/broker/CLAUDE.md](./src/stock_agent/broker/CLAUDE.md) 참조.
  - `scripts/healthcheck.py` — `KisClient` 컨텍스트 매니저로 전환, 예수금 10,000,000원 조회 회귀 없음.
  - pytest 25건 green (test_config 5 + test_kis_client 15 + test_safety 5).

- **다음 작업 (Phase 1 잔여)**
  1. ~~`src/stock_agent/broker/kis_client.py`~~ — 완료
  2. `src/stock_agent/broker/rate_limiter.py` — KIS 초당 호출 제한 대응
  3. `src/stock_agent/data/historical.py` — pykrx로 KOSPI 200 분봉/일봉 수집 & SQLite 캐시
  4. `src/stock_agent/data/realtime.py` — 장중 분봉 폴링 또는 WebSocket 실시간 체결가

## 참고

- [plan.md](./plan.md) — 설계 상세
- [README.md](./README.md) — 외부 개요
- [.claude/agents/markdown-writer.md](./.claude/agents/markdown-writer.md) — 문서 동기화 에이전트
