# stock-agent — 작업 가이드

이 프로젝트에서 작업할 때 반드시 읽어야 하는 파일입니다.

## 프로젝트 한 줄 요약

Python 기반 한국주식 **데이트레이딩** 자동매매 시스템. 한국투자증권 KIS Developers API + Opening Range Breakout(ORB) 전략 + 100~200만원 초기 자본. 현재 **계획 단계(Phase 0 진입 예정)**, 코드 미작성.

상세 설계는 `plan.md`를 참조한다. 외부 독자용 개요는 `README.md`.

## 소통 언어

한국어로 응답·작성한다. 기존 문서 톤(담담·구체·단정형)을 유지한다. 이모지는 쓰지 않는다.

## 확정된 결정 (임의 변경 금지, 변경 필요 시 사용자에게 먼저 확인)

- 증권사: 한국투자증권 KIS Developers (토스증권은 API 미제공)
- 전략: Opening Range Breakout (long-only, KOSPI 200 대형주)
- 초기 자본: 100~200만원
- 실행: 로컬 맥북, 장중(9:00~15:30 KST)
- 알림: 텔레그램 봇
- 스택: Python 3.11+, `uv`, `python-kis`/`mojito2`, `pykrx`, `backtesting.py`, `APScheduler`, `loguru`, `python-telegram-bot`, SQLite
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

## 코드 스타일 (Phase 1부터 적용 예정)

- Python 3.11+, `uv`로 의존성 관리.
- 타입 힌트 필수. 설정·외부 경계는 `pydantic`으로 검증.
- 포매터·린터: `ruff` + `black`. 가능하면 `pre-commit`에 물릴 것.
- 로깅: `loguru` (구조화 로그 권장).
- 테스트: `pytest`, API 호출은 목킹.

## 현재 상태 (2026-04-19 기준)

- 계획 승인 완료. `plan.md`, `README.md`, `CLAUDE.md`, `.claude/agents/markdown-writer.md` 존재.
- Phase 0 다음 액션:
  1. 한국투자증권 비대면 계좌 개설
  2. KIS Developers 가입 · **모의투자** APP_KEY/SECRET 발급
  3. 텔레그램 봇 토큰 · chat_id 확보
  4. `uv init` · `.env.example` 작성
  5. `scripts/healthcheck.py` 구현 (모의 잔고 조회 + 텔레그램 hello)

## 참고

- [plan.md](./plan.md) — 설계 상세
- [README.md](./README.md) — 외부 개요
- [.claude/agents/markdown-writer.md](./.claude/agents/markdown-writer.md) — 문서 동기화 에이전트
