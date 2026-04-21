# stock-agent — 작업 가이드

이 프로젝트에서 작업할 때 반드시 읽어야 하는 파일입니다.

## 프로젝트 한 줄 요약

Python 기반 한국주식 **데이트레이딩** 자동매매 시스템. 한국투자증권 KIS Developers API + Opening Range Breakout(ORB) 전략 + 100~200만원 초기 자본. **paper 주문 + live 시세 하이브리드 키** 구조 (KIS paper 도메인에 시세 API 없음 — 시세는 별도 실전 APP_KEY 로 실전 도메인 호출). 현재 **Phase 1 PASS (코드·테스트 레벨). Phase 2 진행 중 (백테스트 엔진 코어·ORB 전략·리스크 매니저·CSV 어댑터·민감도 그리드·backtest.py CLI 완료). Phase 3 착수 전제 통과 (2026-04-21) + 첫 산출물 Executor 코드·테스트 레벨 완료 (2026-04-21) + 두 번째 산출물 main.py (APScheduler) 코드·테스트 레벨 완료 (2026-04-21) + 세 번째 산출물 monitor/notifier (텔레그램 알림) 코드·테스트 레벨 완료 (2026-04-21)**.

상세 설계는 `plan.md`를 참조한다. 외부 독자용 개요는 `README.md`.

## 소통 언어

한국어로 응답·작성한다. 기존 문서 톤(담담·구체·단정형)을 유지한다. 이모지는 쓰지 않는다.

## 확정된 결정 (임의 변경 금지, 변경 필요 시 사용자에게 먼저 확인)

- 증권사: 한국투자증권 KIS Developers (토스증권은 API 미제공)
- 전략: Opening Range Breakout (long-only, KOSPI 200 대형주)
- 초기 자본: 100~200만원
- 실행: 로컬 맥북, 장중(9:00~15:30 KST)
- 알림: 텔레그램 봇
- 스택: Python 3.12+, `uv`, `python-kis 2.x`, `pykrx`, `pyyaml`, `APScheduler`, `loguru`, `python-telegram-bot`, SQLite (백테스트 엔진은 자체 시뮬레이션 루프 — `backtesting.py` 폐기 결정 2026-04-20)
- 리스크 한도: 종목당 진입 자본의 20%, 동시 3종목, 손절 -1.5% / 익절 +3.0% / 15:00 강제청산, 일일 손실 -2% 서킷브레이커
- 키 정책: 주문/잔고(KisClient)는 paper APP_KEY → paper 도메인. 시세 조회/WebSocket(RealtimeDataStore)은 별도 실전 APP_KEY → 실전 도메인 (KIS paper 도메인에 `/quotations/*` 시세 API 미제공).

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
- 서브에이전트 규칙 추가·변경 (`.claude/agents/*.md`) — 에이전트 파일의 프로젝트 사실 진술(Python 버전·Phase 상태·도메인 요약) 도 동기화 대상

`markdown-writer` 호출 시 전달할 것: (a) 무엇이 바뀌었는지 (b) 어느 문서를 고쳐야 하는지 (c) 기존 승인 결정과 리스크 고지는 보존할 것. 추가로 markdown-writer 는 호출받은 범위 외에도 "rot 점검 체크리스트" (Python 버전·Phase 상태·리스크 한도·모듈 목록·ADR 번호) 를 매 호출 자동 실행한다 — 상세는 [.claude/agents/markdown-writer.md](./.claude/agents/markdown-writer.md).

### 동기화 필수 매트릭스

| 변경 유형 | CLAUDE.md | README.md | plan.md |
|---|:-:|:-:|:-:|
| Phase 상태 전환 | O | O | O |
| 리스크 한도 값 변경 | O | O | O |
| 기술 스택 교체 | O | O | O |
| 디렉토리 구조 추가 | — | O | O |
| 새 명령/스크립트 실행 가능 | — | O | O |
| 리스크 고지 수정 | — | O (완화 금지) | O |

매트릭스 밖 추가 대상: `.claude/agents/*.md` 는 프로젝트 사실 진술(버전·Phase·도메인) 이 바뀌면 동기화 — 단 본문 가이드라인·계약·산출물 형식은 건드리지 않음. `docs/adr/*.md` 의 결정·맥락·결과 4섹션은 역사 기록이라 사후 수정 금지(인덱스·추적 섹션의 PR 번호 확정만 허용).

### ADR (Architecture Decision Records)

새로운 **아키텍처 수준 결정** 이 생기면 같은 PR 에서 `docs/adr/NNNN-제목-kebab.md` 1건을 작성한다. 형식은 한국어 MADR 변형(상태·맥락·결정·결과 4섹션). 작성법과 인덱스는 [docs/adr/README.md](./docs/adr/README.md) 참조.

ADR 작성 대상:
- 라이브러리 채택·교체·폐기 (예: `backtesting.py` 폐기)
- 모듈 경계·계층 변경
- 핵심 정책 도입·번복 (예: paper/live 키 분리, `RuntimeError` 전파 기조)
- 외부 의존성에 대한 운영 정책 (예: 수동 vs 자동 데이터 소스)

ADR **불필요** 한 변경:
- 단순 버그 수정·리팩터링·이름 변경
- 테스트 케이스 추가
- 기존 ADR 의 결정을 그대로 따르는 코드 변경

기존 ADR 가 번복되면 새 ADR 작성 + 기존 ADR 의 상태를 `폐기됨` 또는 `대체됨`(`Superseded by ADR-MMMM`) 으로 변경한다. ADR 자체의 결정·맥락은 역사 기록이므로 사후 수정하지 않고 새 ADR 로 덮는다.

### 계층 CLAUDE.md (모듈별 문서)

모듈별 세부 사실(공개 API, 설계 원칙, 테스트 정책, 주의 사항)은 해당 폴더의 `CLAUDE.md` 에 둔다.
root `CLAUDE.md` 는 프로젝트 전체 상태 요약과 하위 문서 링크만 유지한다.

현재 하위 CLAUDE.md:
- [src/stock_agent/broker/CLAUDE.md](./src/stock_agent/broker/CLAUDE.md) — KIS Developers API 래퍼 모듈 (KisClient, DTO, 에러 정책, 데이터 무결성 가드)
- [src/stock_agent/data/CLAUDE.md](./src/stock_agent/data/CLAUDE.md) — 시장 데이터 모듈 (과거 일봉 `HistoricalDataStore`·`DailyBar` + 실시간 분봉 `RealtimeDataStore`·`TickQuote`·`MinuteBar` + KOSPI 200 유니버스 로더 + CSV 과거 분봉 어댑터 `MinuteCsvBarLoader`)
- [src/stock_agent/strategy/CLAUDE.md](./src/stock_agent/strategy/CLAUDE.md) — ORB 전략 엔진 모듈 (ORBStrategy, StrategyConfig, Strategy Protocol, EntrySignal/ExitSignal DTO)
- [src/stock_agent/risk/CLAUDE.md](./src/stock_agent/risk/CLAUDE.md) — 리스크 매니저 모듈 (RiskManager, RiskConfig, RiskDecision, PositionRecord, RejectReason, RiskManagerError)
- [src/stock_agent/backtest/CLAUDE.md](./src/stock_agent/backtest/CLAUDE.md) — 백테스트 엔진 모듈 (BacktestEngine, BacktestConfig, BacktestResult, BacktestMetrics, TradeRecord, DailyEquity, BarLoader, InMemoryBarLoader; 자체 시뮬레이션 루프, 한국 시장 비용 반영) + 민감도 그리드 (ParameterAxis, SensitivityGrid, SensitivityRow, run_sensitivity, render_markdown_table, write_csv, default_grid)
- [src/stock_agent/execution/CLAUDE.md](./src/stock_agent/execution/CLAUDE.md) — Executor 오케스트레이션 모듈 (Executor, ExecutorConfig, OrderSubmitter/BalanceProvider/BarSource Protocol, LiveOrderSubmitter/LiveBalanceProvider/DryRunOrderSubmitter 어댑터, StepReport/ReconcileReport/EntryEvent/ExitEvent DTO, ExecutorError, last_reconcile 프로퍼티; 신호 → 주문 → 체결 추적 → 상태 동기화 루프, backtest/costs.py 비용 산식 재사용)
- [src/stock_agent/monitor/CLAUDE.md](./src/stock_agent/monitor/CLAUDE.md) — 텔레그램 알림 모듈 (Notifier Protocol, TelegramNotifier, NullNotifier, ErrorEvent, DailySummary; StepReport 이벤트 소비, silent fail + 연속 실패 dedupe 경보, ADR-0012)

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
- `KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`, `KIS_LIVE_ACCOUNT_NO` (시세 전용 실전 키 3종) 도 동일하게 `.env`에만. 절대 커밋 금지. HTS_ID 는 paper/실전 공유(`KIS_HTS_ID`), 계좌번호는 paper/실전이 달라 별도 필드. 실전 앱은 KIS Developers 포털에서 별도 신청 후 **사용 IP를 화이트리스트에 등록**해야 한다(미등록 시 `EGW00123` 계열 오류).
- 커밋·PR 전 diff에 키 문자열이 섞여들어가지 않았는지 확인.
- 모의투자 키와 실전 키는 환경변수로만 구분, 코드에 하드코딩하지 않는다.

## 코드 스타일 (Phase 0에서 도구 도입 완료, Phase 1부터 본격 적용)

- Python 3.12+, `uv`로 의존성 관리.
- 타입 힌트 필수. 설정·외부 경계는 `pydantic`으로 검증.
- 포매터·린터: `ruff` + `black`. 가능하면 `pre-commit`에 물릴 것.
- 로깅: `loguru` (구조화 로그 권장).
- 테스트: `pytest`, API 호출은 목킹.

## DTO 설계 체크리스트 (신규 `@dataclass(frozen=True, slots=True)` 추가 시)

PR #18 에서 `ExitEvent.reason: str` 이 프로젝트 내 기존 `ExitReason = Literal["stop_loss","take_profit","force_close"]` 재사용을 놓쳐 문서-코드 계약이 어긋난 사고를 반복하지 않도록, **신규 frozen dataclass 를 추가하거나 기존 DTO 필드를 수정할 때** 아래 4 항목을 리뷰 전에 스스로 확인한다.

1. **Literal 후보 필드는 Literal 로 선언한다.** 값 범위가 고정된 필드(예: `reason`, `severity`, `stage`, `side`) 는 `str` 대신 프로젝트 내 기존 `Literal` 또는 `Literal[...]` 직접 선언. 동일 개념이 이미 다른 모듈에 있으면 재사용 (예: `ExitReason` 는 `strategy/base.py` 가 정본).
2. **`timestamp` 필드는 `__post_init__` 에서 tz-aware 검증한다.** `Executor._require_aware` 와 동일 기조 — naive datetime 이 섞이면 포맷·시각 계산에서 silent 오독을 유도한다.
3. **값 제약이 있는 숫자 필드는 `__post_init__` 가드를 추가한다.** `qty > 0`, `price > 0`, `pct ∈ [0, 1)` 등. 기존 `ExecutorConfig` / `StrategyConfig` / `RiskConfig` 의 검증 패턴을 그대로 따른다 — 위반 시 `RuntimeError` (ADR-0003).
4. **관련 테스트 헬퍼 시그니처를 DTO 와 같은 타입 폭으로 유지한다.** `_make_*_event` / `_make_*_config` 의 매개변수 타입이 DTO 필드 타입과 같아야 한다 (예: `reason: ExitReason`). `str` 로 넓히면 Pyright 가 호출부에서 Literal 좁힘을 잃어버려 정적 타입 계약이 깨진다.

자동화된 안전장치로 `pyright` 가 CI 게이트에 포함되어 있다 (`src/` + `scripts/` 범위, tests 는 baseline 정리 후 점진 확대). `[tool.pyright]` 설정은 `pyproject.toml` 에 있고 에디터 LSP 와 동일 규칙을 공유한다.

## 테스트 작성 정책 (하드 규칙)

`tests/` 하위 Python 파일의 생성·수정은 **반드시 `unit-test-writer` 서브에이전트를 경유**한다. 메인 assistant 의 직접 `Write`/`Edit`/`NotebookEdit` 은 `.claude/hooks/tests-writer-guard.sh` 가 `PreToolUse` 단계에서 exit 2 로 차단한다.

- 목적: 실주문·실네트워크 접촉을 원천 차단하고, 외부 의존(KIS API·텔레그램·시계·파일·DB) 목킹 규율을 전담 에이전트가 일관되게 적용하도록 한다.
- 예외: 임포트 경로·네이밍 단순 리팩터처럼 테스트 로직 자체가 바뀌지 않는 수정은 사용자에게 **명시적으로 확인**을 받고 우회할 수 있다.
- 관련 자산: [.claude/hooks/tests-writer-guard.sh](.claude/hooks/tests-writer-guard.sh), [.claude/agents/unit-test-writer.md](.claude/agents/unit-test-writer.md). 서브에이전트 식별은 PreToolUse payload 의 `agent_id` 필드 존재 여부로 한다.

## TDD 순서 강제 (하드 규칙)

기능 추가·동작 변경은 반드시 **Red 선행** 플로우를 따른다. 즉 `src/stock_agent/` 에 새 공개 동작을 도입하기 전에 그 동작을 검증하는 **실패하는 pytest 케이스가 먼저 존재**해야 한다.

정식 플로우:

1. 요구사항 정리 (메인 assistant).
2. `Agent subagent_type="unit-test-writer"` 호출 — 기본 모드는 RED. 실패 테스트 작성 후 `uv run pytest -x tests/<target> -q` 실행해 FAIL 을 리포트.
3. 메인 assistant 가 `src/stock_agent/` 구현.
4. `uv run pytest -x tests/<target>` 로 GREEN 확인.
5. (선택) 리팩터 — `mode=refactor-invariant` 로 불변성 테스트 보강해 회귀 방지.

훅 3 종 역할:

| 훅 | 시점 | 차단 조건 |
| --- | --- | --- |
| [`tests-writer-guard.sh`](./.claude/hooks/tests-writer-guard.sh) | PreToolUse / `Write`·`Edit`·`NotebookEdit` | 메인 assistant 의 `tests/*.py` 직접 쓰기 |
| [`src-first-requires-tests.sh`](./.claude/hooks/src-first-requires-tests.sh) | PreToolUse / `Write` | `src/stock_agent/` 신규 파일 + 대응 `tests/test_*.py` 부재 |
| [`test-coverage-check.sh`](./.claude/hooks/test-coverage-check.sh) | Stop | `src/` 변경 O + `tests/` 변경 X (세션당 1회 리마인더) |

훅 없이도 통과하는 예외 (이 훅 스코프 밖):

- 문서·주석·타입힌트만 변경 (동작 불변).
- 기존 동작 불변 리팩터 (기존 테스트가 커버).
- `__init__.py` 얇은 패키지 마커.
- 기존 `src/` 파일에 `Edit` — `src-first-requires-tests.sh` 통과. Stop 훅(`test-coverage-check.sh`) 이 사후 리마인더.

명시적 우회가 필요한 예외:

- **긴급 핫픽스**: `STOCK_AGENT_TDD_BYPASS=1` 환경변수로 해당 세션 한정 우회. 24 시간 내 회귀 테스트 작성 필수.
- **순수 리팩터·명명 변경**: 사용자에게 명시적으로 확인받고 우회.

관련 자산: [.claude/hooks/src-first-requires-tests.sh](.claude/hooks/src-first-requires-tests.sh), [.claude/agents/unit-test-writer.md](.claude/agents/unit-test-writer.md) 의 "TDD 모드 계약" 섹션, [docs/adr/0010-tdd-order-enforcement.md](./docs/adr/0010-tdd-order-enforcement.md).

## 현재 상태 (2026-04-21 기준)

- **Phase 0 완료** (2026-04-19)
  - `scripts/healthcheck.py` 3종 통과: KIS 모의투자 토큰 발급 OK, 모의 계좌 잔고 조회 OK (시드 10,000,000원), 텔레그램 "hello" 수신 OK
  - 신규 파일: `.python-version`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `.env.example`, `src/stock_agent/__init__.py`, `src/stock_agent/config.py`, `scripts/healthcheck.py`
  - 의존성 확정: `python-kis 2.1.6`, `python-telegram-bot 22.7`, `pydantic 2.13`, `pydantic-settings 2.13`, `loguru 0.7` / dev: `ruff 0.15`, `black 26.3`, `pytest 9.0`, `pytest-mock 3.15`, `pre-commit 4.5`
  - `python-kis` paper-only 초기화 우회: 모의 키를 실전 슬롯과 모의 슬롯 양쪽에 동일 입력 → `PyKis.virtual = True`로 모든 요청이 모의 도메인으로만 라우팅됨. Phase 4 실전 전환 시 실전 APP_KEY/SECRET 별도 발급 후 슬롯 분리.
  - 운영 메모: KIS Developers에서 "모의투자계좌 API 신청"을 MTS의 "상시 모의투자 참가신청"과 별도로 완료해야 모의 키 발급 가능 (미신청 시 `EGW2004` 에러). 토큰 첫 발급 시 레이트 리밋 경고 2회 후 자동 재시도 통과 — 정상 동작 범위.
  - GitHub Actions CI 도입 (`.github/workflows/ci.yml`): PR 및 main push 시 `uv sync --frozen` → ruff/black 정적 분석 → pytest 자동 실행. 첫 실행 12초, 10/10 통과 (PR #1 검증).
  - main 브랜치 보호 적용: required status check `Lint, format, test` (CI job), `strict=true`, force push/삭제 금지.

- **Phase 1 PASS (코드·테스트 레벨) — 브로커 래퍼 + 데이터 파이프라인** (2026-04-19 완료 선언)
  - `src/stock_agent/broker/` 패키지 신설 — `KisClient` + DTO 정규화. 모듈 세부(공개 API, 에러 정책, 데이터 무결성 가드, 테스트 정책)는 [src/stock_agent/broker/CLAUDE.md](./src/stock_agent/broker/CLAUDE.md) 참조.
  - `scripts/healthcheck.py` — `KisClient` 컨텍스트 매니저로 전환, 예수금 10,000,000원 조회 회귀 없음.
  - `src/stock_agent/broker/rate_limiter.py` — 완료. 주문 경로 전용 `OrderRateLimiter`(기본 2 req/s + 최소 간격 350 ms, 단일 프로세스). 조회 경로는 python-kis 내장에 위임.
  - `src/stock_agent/data/` 패키지 신설 — `HistoricalDataStore` + SQLite 캐시(일봉). 모듈 세부(공개 API, 캐시 정책, 에러 정책, 테스트 정책)는 [src/stock_agent/data/CLAUDE.md](./src/stock_agent/data/CLAUDE.md) 참조.
  - `src/stock_agent/data/universe.py` + `config/universe.yaml` — 완료. KOSPI 200 유니버스 YAML 하드코딩. pykrx 지수 API(`get_index_portfolio_deposit_file` 등) 와 KIS Developers 모두 인덱스 구성종목 API 미제공으로 수동 관리. KOSPI 200 정기변경(연 2회 — 6월·12월 선·옵 동시만기일 익영업일 기준) 때 운영자 갱신. 현재 KRX 정보데이터시스템 [11006] 기준 199/200 반영 (임시 가상 코드 1건 제외). 정식 6자리 티커 발급 시 다음 갱신에 추가.
  - `HistoricalDataStore`는 `get_kospi200_constituents` 제거로 `fetch_daily_ohlcv` 전용으로 축소. SQLite 스키마 v3 (v2→v3 자동 마이그레이션, `daily_bars` 보존).
  - `src/stock_agent/data/realtime.py` — 완료. `RealtimeDataStore` + `TickQuote`·`MinuteBar`·`RealtimeDataError` DTO. WebSocket 우선 + REST 폴링 fallback. **실전(live) 키 전용** — `settings.has_live_keys=False` 이면 `RealtimeDataError` fail-fast. 실전 키 PyKis 인스턴스에 `install_order_block_guard` 설치(`/trading/order*` 도메인 무관 차단). 분봉 집계(분 경계 OHLC 누적)·스레드 안전(`threading.Lock`), volume Phase 1 에서 0 고정(Phase 3 실사). `scripts/healthcheck.py` 4번째 체크(`check_realtime_price`, 삼성전자 005930) — 실전 키 미설정 시 SKIP. 모듈 세부는 [src/stock_agent/data/CLAUDE.md](./src/stock_agent/data/CLAUDE.md) 참조.
  - `src/stock_agent/config.py` — `Settings` 에 `kis_live_app_key`, `kis_live_app_secret`, `kis_live_account_no` 선택 필드 추가 (HTS_ID 는 paper/실전 공유라 별도 필드 없음, 계좌번호는 paper/실전이 달라 별도 필드 필수). 3종 all-or-none + 길이·패턴 검증(model_validator). `has_live_keys` 프로퍼티 신규.
  - `src/stock_agent/safety.py` — `install_order_block_guard(kis)` 신규. `/trading/order` 부분 문자열 매칭 시 도메인 무관 차단. `install_paper_mode_guard` 는 기존 역할(paper 키 KisClient 보호) 유지.
  - PR #7 Critical 피드백 반영: 가드 중복 설치 방어(`GUARD_MARKER_ATTR` 재설치 거부), 폴링 연속 실패 경보(`polling_consecutive_failures` 공개 프로퍼티), docstring 정정 (3건).
  - pytest **131건 green** (test_config 11 + test_kis_client 15 + test_safety 23 + test_rate_limiter 18 + test_historical 14 + test_universe 11 + test_realtime 28 + 기타 회귀 없음).
  - 의존성 추가: `pykrx 1.2.7` (+ transitive: pandas, numpy, matplotlib 등), `pyyaml 6.0.3`.
  - **미완료 조건**: 장중 실시간 시세 수신 end-to-end 확인(실전 키 + IP 화이트리스트 + 평일 장중 틱 수신)은 **Phase 3 착수 전제**로 이관. PASS 선언은 코드·테스트 레벨 기준.

- **Phase 2 진행 중 — ORB 전략 엔진 + 리스크 매니저 + 백테스트 엔진 코어 + CSV 분봉 어댑터 + 파라미터 민감도 그리드 + backtest.py CLI 완료** (2026-04-20)
  - `src/stock_agent/strategy/` 패키지 신설 — `ORBStrategy` + `StrategyConfig` + `Strategy` Protocol + `EntrySignal`/`ExitSignal` DTO. 모듈 세부는 [src/stock_agent/strategy/CLAUDE.md](./src/stock_agent/strategy/CLAUDE.md) 참조.
  - 설계 결정: 진입은 분봉 close 기준 OR-High strict 상향 돌파, 동일 분봉 손절·익절 동시 성립 시 손절 우선, 1일 1회 진입, `StrategyConfig` 생성자 주입(YAML 미도입), `Strategy` Protocol 최소, 세션 경계는 `bar.bar_time.date()` 기반 자동 리셋.
  - `src/stock_agent/risk/` 패키지 신설 — `RiskManager` + `RiskConfig` + `RiskDecision` + `PositionRecord` + `RejectReason` + `RiskManagerError`. 모듈 세부는 [src/stock_agent/risk/CLAUDE.md](./src/stock_agent/risk/CLAUDE.md) 참조.
  - 리스크 매니저 설계 결정: 포지션 사이징(세션 자본 × 20% / 참고가 floor), 진입 게이팅 6단계 판정 순서 고정, 서킷브레이커(`daily_realized_pnl_krw ≤ -starting_capital × 2%`), 세션 단위 인메모리 상태, 외부 I/O 없음, `datetime.now()` 미사용.
  - `src/stock_agent/backtest/` 패키지 신설 — `BacktestEngine` + `BacktestConfig` + `BacktestResult` + `BacktestMetrics` + `TradeRecord` + `DailyEquity` + `BarLoader` Protocol + `InMemoryBarLoader`. 모듈 세부는 [src/stock_agent/backtest/CLAUDE.md](./src/stock_agent/backtest/CLAUDE.md) 참조.
  - 백테스트 엔진 핵심 결정: `backtesting.py` 라이브러리 폐기 — 다중종목·RiskManager 게이팅(동시 3종목 한도·서킷브레이커·일일 진입 횟수 한도) 표현 불가, AGPL 라이센스 부담. 자체 시뮬레이션 루프로 `ORBStrategy.on_bar/on_time` + `RiskManager` 호출 — 실전 코드와 동일 인터페이스 공유. 비용 계약: 슬리피지 0.1% 시장가 불리, 수수료 0.015%(매수·매도 대칭), 거래세 0.18%(매도만). phantom_long 처리: `ORBStrategy._enter_long` 이 EntrySignal 반환 전 자체 상태 전이 → RiskManager 거부 시 후속 ExitSignal 을 `phantom_longs: set[str]` 으로 흡수(debug 로그). 외부 I/O 0, 의존성 추가 0.
  - `src/stock_agent/data/minute_csv.py` 패키지 편입 — `MinuteCsvBarLoader` + `MinuteCsvLoadError` 공개. 레이아웃 `{csv_dir}/{symbol}.csv`, 헤더 `bar_time,open,high,low,close,volume`. bar_time naive KST 파싱·오프셋 포함 거부, Decimal 가격 파싱, OHLC 일관성 검증, 분 경계 강제, 단조증가+중복 금지, 누락 파일 fail-fast. 여러 심볼은 `heapq.merge` 로 `(bar_time, symbol)` 정렬 스트리밍. stdlib 전용, 추가 의존성 0. 모듈 세부는 [src/stock_agent/data/CLAUDE.md](./src/stock_agent/data/CLAUDE.md) 참조.
  - `src/stock_agent/backtest/sensitivity.py` + `scripts/sensitivity.py` — 완료 2026-04-20. `ParameterAxis`·`SensitivityGrid`·`SensitivityRow`·`run_sensitivity`·`render_markdown_table`·`write_csv`·`default_grid` 공개 (backtest `__init__` 재노출). 기본 그리드 `or_end` 2종 × `stop_loss_pct` 4종 × `take_profit_pct` 4종 = 32 조합, 현재 운영 기본값 포함. 파라미터 이름 공간 `strategy.*`·`risk.*`·`engine.*`. CLI: `uv run python scripts/sensitivity.py --csv-dir ... --from ... --to ...`. 외부 네트워크·KIS 접촉 없음, 의존성 추가 0. 민감도 리포트는 sanity check 용도이며 walk-forward 검증을 대체하지 않는다. PR #12 리뷰 반영: `SensitivityRow.params` 를 `tuple[tuple[str, Any], ...]` 로 변경해 frozen 계약 회복, `metrics: BacktestMetrics` 중첩으로 엔진 진화 자동 추종, `params_dict()` 편의 메서드 추가. `scripts/sensitivity.py` exit code 규약: 2 입력·설정 오류, 3 I/O 오류. `BarLoader` Protocol 재호출 안전성 계약 명시. 모듈 세부는 [src/stock_agent/backtest/CLAUDE.md](./src/stock_agent/backtest/CLAUDE.md) 참조.
  - `scripts/backtest.py` — 완료 2026-04-20. `MinuteCsvBarLoader` + `BacktestEngine` 1회 실행 → Markdown·메트릭 CSV·체결 CSV 3종 산출. `--csv-dir` (required), `--from`/`--to` (required, `date.fromisoformat`), `--symbols`(default 유니버스 전체), `--starting-capital`(default 1,000,000), `--output-markdown`/`--output-csv`/`--output-trades-csv`. PASS 판정: 낙폭 절대값 15% 미만일 때 PASS (`mdd > Decimal("-0.15")` 이면 PASS — 경계 -15% 정확값은 FAIL). **exit code 에는 반영 안 함** — 운영자 수동 검토 보존, CI 자동 pass/fail 금지. exit code 규약: `0` 정상, `2` `MinuteCsvLoadError`/`UniverseLoadError`/`RuntimeError`, `3` `OSError` (`scripts/sensitivity.py`는 `UniverseLoadError` 분기 미포함 — 별도 PR 이슈). 외부 네트워크·KIS 접촉 0, 의존성 추가 0. 테스트: `tests/test_backtest_cli.py` 65건.
  - pytest **245 → 324 → 384 → 464 → 477 → 539 → 542건 green** (기존 539 + verdict 경계값 보강 2건 + UniverseLoadError 회귀 1건). 회귀 없음. 의존성 추가 없음.
  - 미완료: KIS 과거 분봉 API 어댑터(별도 PR), 2~3년 실데이터 수집(운영자 외부 작업), 낙폭 절대값 15% 미만 확인 (MDD > -15%). Phase 2 전체 PASS 선언은 이후.

- **Phase 3 착수 전제 통과 (2026-04-21)** — 실전 시세 전용 APP_KEY 3종 발급·IP 화이트리스트 등록·평일 장중 healthcheck.py 4종 그린(WebSocket 체결 수신 OK).

- **Phase 3 첫 산출물 — Executor 단독 (코드·테스트 레벨) 완료 (2026-04-21)**
  - `src/stock_agent/execution/` 패키지 신설 — `Executor` + `ExecutorConfig` + Protocol 3종 (`OrderSubmitter`/`BalanceProvider`/`BarSource`) + 어댑터 3종 (`LiveOrderSubmitter`/`LiveBalanceProvider`/`DryRunOrderSubmitter`) + `StepReport`/`ReconcileReport` DTO + `ExecutorError`. 모듈 세부는 [src/stock_agent/execution/CLAUDE.md](./src/stock_agent/execution/CLAUDE.md) 참조.
  - 핵심 결정: `KisClient` 직접 의존 금지 → Protocol 의존성 역전. 드라이런 모드는 `DryRunOrderSubmitter` 주입만으로 KIS 접촉 0. `backtest/costs.py` 비용 산식 그대로 재사용해 실전·시뮬레이션 비용 가정 단일 소스.
  - pytest **542 → 605건 green** (`tests/test_executor.py` 63건 신규). 회귀 0건. 의존성 추가 없음.

- **Phase 3 두 번째 산출물 — main.py + APScheduler 통합 (코드·테스트 레벨) 완료 (2026-04-21)**
  - `src/stock_agent/main.py` 신설 — `BlockingScheduler(timezone='Asia/Seoul')` + 4종 cron job + `Runtime` 조립 컨테이너 + `build_runtime` + CLI 인자(`--dry-run`, `--starting-capital`, `--universe-path`, `--log-dir`). 공개 심볼: `EXIT_OK/EXIT_UNEXPECTED/EXIT_INPUT_ERROR/EXIT_IO_ERROR`, `Runtime`, `SessionStatus`, `build_runtime`, `_parse_args`, `_install_jobs`, `_on_session_start`, `_on_step`, `_on_force_close`, `_on_daily_report`, `_graceful_shutdown`, `main`, `KST`.
  - 스케줄: 09:00 session_start, 매분 00s(9~14시) step, 15:00 force_close, 15:30 daily_report. 모두 `day_of_week='mon-fri'`, `timezone='Asia/Seoul'`.
  - 드라이런: `--dry-run` 플래그 → `DryRunOrderSubmitter` 주입, KIS 주문 접촉 0. 분기 로직은 `_build_order_submitter` 한 곳만.
  - 예외 정책: 콜백 4종 모두 예외 re-raise 안 함(스케줄러 연속성 보장). `on_force_close` 실패만 `logger.critical`. exit code: 0 정상 / 1 예기치 않은 예외 / 2 입력·설정 오류 / 3 I/O 오류.
  - 리소스 정리: SIGINT/SIGTERM → `_graceful_shutdown` → `signal.signal(SIG_DFL)` 재진입 가드 → `scheduler.shutdown(wait=False)` → `realtime_store.close()` → `kis_client.close()`. `finally` 블록에서도 중복 호출(멱등).
  - **PR #17 리뷰 반영 패치 (2026-04-21)** — silent failure 루프 차단(`SessionStatus` 공개, `_on_step` 에서 세션 미시작 감지 → warning 1회만 남기고 skip, dedupe 플래그) · 세션 자본 기준 `balance.total` → `balance.withdrawable` 교정(RiskManager withdrawable 사이징과 계약 정합) · `Runtime.risk_manager` 공개 경로화(`_on_daily_report` 가 Executor private `_risk_manager` 의존 제거) · `_graceful_shutdown` SIGINT/SIGTERM SIG_DFL 재진입 가드 · `main()` `get_settings` except 좁힘(`ValidationError`+`OSError`+`RuntimeError` 만 catch, `ImportError` 등 프로그래밍 오류는 전파) · 모듈 docstring 스케줄 표 정리 + `build_runtime` Raises `OSError` 추가 + 콜백 docstring ADR-0011 참조.
  - pytest **605 → 681건 green** (`tests/test_main.py` 47건 신규 + 리뷰 반영 29건 추가·보강). 회귀 0건. 의존성 추가: `apscheduler 3.11.2` + transitive `tzlocal 5.3.1`.
  - 미완료: `storage/db.py` (SQLite 영속화) · `broker/` 체결조회 API 통합 — 후속 PR. **Phase 3 PASS 선언은 모의투자 연속 10영업일 무중단 운영 후.**

- **Phase 3 세 번째 산출물 — monitor/notifier.py (텔레그램 알림) 코드·테스트 레벨 완료 (2026-04-21)**
  - `src/stock_agent/monitor/` 패키지 신설 — `Notifier` Protocol + `TelegramNotifier` + `NullNotifier` + `ErrorEvent`/`DailySummary` DTO. 모듈 세부는 [src/stock_agent/monitor/CLAUDE.md](./src/stock_agent/monitor/CLAUDE.md) 참조.
  - 핵심 결정 (ADR-0012): Protocol 의존성 역전 유지(Executor 는 notifier 모름), `StepReport` 확장(`entry_events`/`exit_events` 기본값 `()` backward compat), 전송 실패 silent fail + 연속 실패 dedupe 경보, 드라이런도 실전송 + `[DRY-RUN]` 프리픽스, plain text 한국어 포맷.
  - `execution/executor.py` 확장: `EntryEvent`/`ExitEvent` DTO 신설, `StepReport.entry_events`/`exit_events` 추가, `Executor.last_reconcile` 프로퍼티 추가.
  - `main.py` 확장: `Runtime.notifier: Notifier` 필드, `_default_notifier_factory`, `build_runtime(..., notifier_factory=...)`, 콜백 4종에 `notify_*` 호출 삽입.
  - pytest **681 → 778건 green** (notifier 71건 신규 + executor/main 확장분 포함; Issue #13 대응 중복 검출 O(n) 단순화·빈 axes 가드 추가, 허용 테스트 3건 삭제 + 가드 테스트 1건 추가로 순감 2). 회귀 0건. 의존성 추가 없음.
  - 미완료: `storage/db.py` (SQLite 체결 기록, 미착수). **Phase 3 PASS 선언은 모의투자 연속 10영업일 무중단 운영 후.**

- **다음 작업**
  - **Phase 2 잔여 (후속 PR)**: KIS 과거 분봉 API 어댑터(별도 PR) · 2~3년 실데이터 수집(운영자 외부 작업, 리포지토리 미포함) · `uv run python scripts/backtest.py --csv-dir ... --from 2023-01-01 --to 2025-12-31` 실행 후 낙폭 절대값 15% 미만 확인 (MDD > -15%). PASS 라벨이 출력돼도 즉시 실전 전환 아님 — Phase 3 모의투자 2주 무사고 운영이 전제.
  - **Phase 3 착수 전 필수 — 통과 완료 (2026-04-21)**: 운영자 `.env` 실전 키 3종(`KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`, `KIS_LIVE_ACCOUNT_NO`) 기입 + KIS Developers 포털 IP 화이트리스트 등록 + `uv run python scripts/healthcheck.py` 4종(삼성전자 현재가 포함) 통과 확인 (**평일 장중 09:00~15:30 KST 실행 필수** — 4번 `check_realtime_price`는 장외 실행 시 2초 타임아웃 후 실패 가능; 나머지 3종은 시간대 무관). **2026-04-21 평일 장중 4종 그린, WebSocket 체결 수신 OK.**
  - **Phase 3 후속 PR**: `execution/executor.py` 완료 (2026-04-21) · `main.py` 완료 (2026-04-21) · `monitor/notifier.py` 완료 (2026-04-21) · `storage/db.py` (SQLite 체결 기록, 미착수) 남음.

## 참고

- [plan.md](./plan.md) — 설계 상세
- [README.md](./README.md) — 외부 개요
- [docs/architecture.md](./docs/architecture.md) — 기술 아키텍처 한눈 조망 (모듈 의존 그래프·DTO 계약·실행 시나리오·외부 I/O 경계)
- [.claude/agents/markdown-writer.md](./.claude/agents/markdown-writer.md) — 문서 동기화 에이전트
