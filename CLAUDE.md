# stock-agent — 작업 가이드

이 프로젝트에서 작업할 때 반드시 읽어야 하는 파일입니다.

## 프로젝트 한 줄 요약

Python 기반 한국주식 **데이트레이딩** 자동매매 시스템. 한국투자증권 KIS Developers API + Opening Range Breakout(ORB) 전략 + 100~200만원 초기 자본. **paper 주문 + live 시세 하이브리드 키** 구조 (KIS paper 도메인에 시세 API 없음 — 시세는 별도 실전 APP_KEY 로 실전 도메인 호출). 현재 **Phase 1 PASS (코드·테스트 레벨). Phase 2 진행 중 — 백테스트 엔진·전략·리스크·CSV/KIS 분봉 어댑터·백필 CLI 까지 모든 코드 산출물 완료. 2026-04-24 1차 백테스트 FAIL + 복구 5단계 로드맵 (A 민감도 → B 비용 → C 유니버스 → D 파라미터 → E 전략 교체) 순차 게이팅. Step A FAIL (2026-04-25) · Step B FAIL (슬리피지 가정 유지) · Step C FAIL (2026-04-30, Top 50/100 두 서브셋 모두 ADR-0019 게이트 불통과) · Step D1 FAIL (2026-05-01) · Step D2 FAIL (2026-05-01, 96 런 전원 게이트 미통과) → D3/D4/E 결정 대기. Phase 3 코드 산출물 (Executor·main.py APScheduler·monitor/notifier·storage/db·세션 재기동·broker 체결조회) 모두 완료 상태로 보존 — 단 ADR-0019 에 따라 Phase 2 수익률 확인 전까지 Phase 3 진입 금지.**

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
- [src/stock_agent/data/CLAUDE.md](./src/stock_agent/data/CLAUDE.md) — 시장 데이터 모듈 (과거 일봉 `HistoricalDataStore`·`DailyBar` + 실시간 분봉 `RealtimeDataStore`·`TickQuote`·`MinuteBar` + KOSPI 200 유니버스 로더 + CSV 과거 분봉 어댑터 `MinuteCsvBarLoader` + KIS 과거 분봉 API 어댑터 `KisMinuteBarLoader`·`KisMinuteBarLoadError`, ADR-0016 + 공휴일 캘린더 `BusinessDayCalendar`·`YamlBusinessDayCalendar`·`HolidayCalendar`·`HolidayCalendarError`·`load_kospi_holidays`, ADR-0018 + 호가 스프레드 수집기 `SpreadSample`·`SpreadSampleCollector`·`SpreadSampleCollectorError`, Step B)
- [src/stock_agent/strategy/CLAUDE.md](./src/stock_agent/strategy/CLAUDE.md) — 전략 엔진 모듈 (Strategy Protocol, EntrySignal/ExitSignal DTO + ORBStrategy/StrategyConfig + VWAPMRStrategy/VWAPMRConfig — ADR-0019 Step E PR2 + GapReversalStrategy/GapReversalConfig — Step E PR3)
- [src/stock_agent/risk/CLAUDE.md](./src/stock_agent/risk/CLAUDE.md) — 리스크 매니저 모듈 (RiskManager, RiskConfig, RiskDecision, PositionRecord, RejectReason, RiskManagerError)
- [src/stock_agent/backtest/CLAUDE.md](./src/stock_agent/backtest/CLAUDE.md) — 백테스트 엔진 모듈 (BacktestEngine, BacktestConfig, BacktestResult, BacktestMetrics, TradeRecord, DailyEquity, BarLoader, InMemoryBarLoader; 자체 시뮬레이션 루프, 한국 시장 비용 반영) + 민감도 그리드 (ParameterAxis, SensitivityGrid, SensitivityRow, run_sensitivity, run_sensitivity_combos, run_sensitivity_combos_parallel, render_markdown_table, write_csv, default_grid, step_d1_grid, step_d2_grid, append_sensitivity_row, load_completed_combos)
- [src/stock_agent/execution/CLAUDE.md](./src/stock_agent/execution/CLAUDE.md) — Executor 오케스트레이션 모듈 (Executor, ExecutorConfig, OrderSubmitter/BalanceProvider/BarSource Protocol, LiveOrderSubmitter/LiveBalanceProvider/DryRunOrderSubmitter 어댑터, StepReport/ReconcileReport/EntryEvent/ExitEvent DTO, ExecutorError, last_reconcile 프로퍼티; 신호 → 주문 → 체결 추적 → 상태 동기화 루프, backtest/costs.py 비용 산식 재사용)
- [src/stock_agent/monitor/CLAUDE.md](./src/stock_agent/monitor/CLAUDE.md) — 텔레그램 알림 모듈 (Notifier Protocol, TelegramNotifier, NullNotifier, ErrorEvent, DailySummary; StepReport 이벤트 소비, silent fail + 연속 실패 dedupe 경보, ADR-0012)
- [src/stock_agent/storage/CLAUDE.md](./src/stock_agent/storage/CLAUDE.md) — SQLite 원장 (TradingRecorder Protocol, SqliteTradingRecorder, NullTradingRecorder, StorageError; 주문·체결·일일 PnL append-only 기록, ADR-0013)

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
- `.env` 로드 경로: `~/.config/stocker/.env` (worktree 무관 공용, 권장) → repo 루트 `.env` (선택 override). pydantic-settings 시퀀스 뒤가 앞을 덮음. 운영자 1회 셋업 절차는 `README.md` "최초 설정" 참조.
- `KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`, `KIS_LIVE_ACCOUNT_NO` (시세 전용 실전 키 3종) 도 동일하게 `.env`에만. 절대 커밋 금지. HTS_ID 는 paper/실전 공유(`KIS_HTS_ID`), 계좌번호는 paper/실전이 달라 별도 필드. 실전 앱은 KIS Developers 포털에서 별도 신청 후 **사용 IP를 화이트리스트에 등록**해야 한다(미등록 시 `EGW00123` 계열 오류).
- 커밋·PR 전 diff에 키 문자열이 섞여들어가지 않았는지 확인.
- 모의투자 키와 실전 키는 환경변수로만 구분, 코드에 하드코딩하지 않는다.

## 코드 스타일 (Phase 0에서 도구 도입 완료, Phase 1부터 본격 적용)

- Python 3.12+, `uv`로 의존성 관리.
- 타입 힌트 필수. 설정·외부 경계는 `pydantic`으로 검증.
- 포매터·린터: `ruff` + `black`. 가능하면 `pre-commit`에 물릴 것.
- 로깅: `loguru` (구조화 로그 권장).
- 테스트: `pytest`, API 호출은 목킹.

## 작업 실행 스타일 — 독립 작업은 병렬 (하드 규칙)

Claude 가 이 저장소에서 작업할 때, **독립적이고 서로 영향을 끼칠 확률이 없는 작업은 반드시 병렬로 실행**한다. 순차 실행은 의존성이 있을 때만.

### 병렬 실행이 기본인 경우

- **정적 검사 4종** (`pytest` · `ruff check` · `black --check` · `pyright`) — 읽기 전용, 서로 영향 0. 항상 `run_in_background: true` 로 동시 기동.
- **독립적인 코드 탐색** — 여러 `Grep` / `Read` / `Glob` / `Explore` agent 호출이 서로의 결과를 입력으로 쓰지 않으면 단일 메시지 안에서 병렬 tool call.
- **파일 범위가 안 겹치는 서브에이전트** — 예: `markdown-writer` (`.md` 만 수정) 와 `pytest` / `ruff` (`.py` 만 읽음) 는 동시 진행 가능.
- **여러 독립 파일에 대한 Read/Edit** — 서로 다른 파일을 수정하는 `Edit` 들은 한 메시지에 묶어 호출.

### 순차 실행이 필수인 경우

- 같은 파일을 수정하는 작업 ↔ 같은 파일을 읽는 검사 (예: `markdown-writer` 가 `CLAUDE.md` 수정 중 · root `CLAUDE.md` 를 읽는 다른 agent).
- `git stash` / `git merge` / `git reset` 같이 **working tree 전체를 잡는 조작** — 진행 중인 파일 편집 agent 가 있으면 경합 위험. agent 완료 후 실행.
- 출력을 입력으로 쓰는 의존성 체인 — 예: unit-test-writer RED → src 구현 → pytest. 이건 순서가 본질.
- 디렉터리 구조를 바꾸는 작업(`mkdir` / `mv` / `git rm`) 직후의 탐색 — 캐시·LSP 재로드를 위해 짧은 순차 권장.

### 판단 프로토콜

새 작업 세트를 시작할 때 30초 내에 다음을 자문:

1. 이 N개 작업이 **같은 파일 또는 같은 디렉터리 트리**를 동시에 쓰는가? → 순차.
2. 한 작업의 출력이 다른 작업의 입력인가? → 순차.
3. 어느 쪽도 아닌가? → **병렬** (단일 메시지 + 복수 tool call / `run_in_background`).

애매하면 기본값은 **순차 + 사용자에게 "병렬로 묶어도 되나요?" 한 줄 질문**. 시간이 급할수록 병렬 기본값을 선호.

### 백그라운드 작업 위생

- `run_in_background: true` 로 띄운 명령은 **완료 알림을 기다린다** — 폴링·`sleep` 금지.
- 백그라운드 명령 결과를 사용하기 전에는 exit code 와 요약 라인(예: pytest 의 "X passed, Y failed") 을 반드시 확인. 탭 간 race 방지.
- 사용자에게 장시간 작업을 띄울 때는 어떤 작업이 돌고 있는지 한 줄로 알린다 — "pytest + ruff + black + pyright 4종 병렬 실행 중".

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

훅 7 종 역할:

| 훅 | 시점 | 차단 조건 | 우회 환경변수 |
| --- | --- | --- | --- |
| [`tests-writer-guard.sh`](./.claude/hooks/tests-writer-guard.sh) | PreToolUse / `Write`·`Edit`·`NotebookEdit` | 메인 assistant(`agent_id` 부재) 의 `tests/*.py` 직접 쓰기 | 없음 (예외는 사용자 명시 확인) |
| [`src-first-requires-tests.sh`](./.claude/hooks/src-first-requires-tests.sh) | PreToolUse / `Write`·`Edit`·`NotebookEdit` (실동작은 `Write` 만) | `src/stock_agent/` 신규 `.py` 생성 + 대응 `tests/test_*.py` 3 후보 전부 부재. `__init__.py`·기존 파일 overwrite 는 통과 | `STOCK_AGENT_TDD_BYPASS=1` (24시간 내 회귀 테스트 필수) |
| [`test-coverage-check.sh`](./.claude/hooks/test-coverage-check.sh) | Stop | `src/stock_agent/**/*.py`(`__init__.py` 제외) 변경 O + `tests/**/*.py` 변경 X (세션당 1회, `/tmp/stock-agent-testcov-${SESSION_ID}` marker) | 없음 |
| [`pyright-full-scope.sh`](./.claude/hooks/pyright-full-scope.sh) | PreToolUse / `Bash` | `git push`(—dry-run 제외) 직전 `uv run pyright src scripts tests` 실패. CI pyright job 과 로컬 검사 범위 일치 강제 | `STOCK_AGENT_PYRIGHT_BYPASS=1 git push ...` (24시간 내 회귀 테스트 + 원인 제거 필수) |
| [`ci-lint-full-scope.sh`](./.claude/hooks/ci-lint-full-scope.sh) | PreToolUse / `Bash` | `git push`(—dry-run 제외) 직전 CI 3 종 lint (`uv run ruff check` + `uv run ruff format --check` + `uv run black --check`, 모두 `src scripts tests`) 중 하나라도 실패. CI "Lint, format, test" job 과 동일 범위 강제 (PR #43 UP037 재발 방지) | `STOCK_AGENT_LINT_BYPASS=1 git push ...` (24시간 내 원인 제거 필수) |
| [`doc-sync-check.sh`](./.claude/hooks/doc-sync-check.sh) | Stop | 워킹 트리에 비독스 파일 변경 O + `CLAUDE.md`/`README.md`/`plan.md` 미갱신 (`.claude/*` 무시, 세션당 1회, `/tmp/stock-agent-docsync-${SESSION_ID}` marker). exit 2 로 리마인더, `*/stock-agent` suffix 한정 | 없음 |
| [`notify-waiting.sh`](./.claude/hooks/notify-waiting.sh) | PreToolUse / `AskUserQuestion`·`ExitPlanMode` + Notification | **차단 안 함** (항상 exit 0, stdin passthrough). macOS `osascript display notification` + Glass 사운드로 운영자에게 응답 대기 알림 발송 | 없음 (off 필요 시 `.claude/settings.json` 에서 제거) |

`pyright-full-scope.sh` · `ci-lint-full-scope.sh` 두 훅은 저장소 시그니처 (`.github/workflows/ci.yml` 의 대응 명령 + `pyproject.toml` 의 `[tool.ruff]` / `[tool.pyright]`) 로 프로젝트를 판정하므로 claude-squad worktree (`*/.claude-squad/worktrees/**`) 에서도 동일하게 작동한다.

`doc-sync-check.sh` · `test-coverage-check.sh` 는 Stop 이벤트에서 함께 발화하며 둘 다 세션당 1회 리마인더를 출력한다 — 각각 `markdown-writer` 에이전트와 `unit-test-writer` 에이전트 호출을 유도한다. `notify-waiting.sh` 는 차단 없이 macOS 알림만 발송한다 (항상 exit 0).

훅 없이도 통과하는 예외 (이 훅 스코프 밖):

- 문서·주석·타입힌트만 변경 (동작 불변).
- 기존 동작 불변 리팩터 (기존 테스트가 커버).
- `__init__.py` 얇은 패키지 마커.
- 기존 `src/` 파일에 `Edit` — `src-first-requires-tests.sh` 통과. Stop 훅(`test-coverage-check.sh`) 이 사후 리마인더.

명시적 우회가 필요한 예외:

- **긴급 핫픽스**: `STOCK_AGENT_TDD_BYPASS=1` 환경변수로 해당 세션 한정 우회. 24 시간 내 회귀 테스트 작성 필수.
- **순수 리팩터·명명 변경**: 사용자에게 명시적으로 확인받고 우회.

관련 자산: [.claude/hooks/src-first-requires-tests.sh](.claude/hooks/src-first-requires-tests.sh), [.claude/hooks/doc-sync-check.sh](.claude/hooks/doc-sync-check.sh), [.claude/agents/unit-test-writer.md](.claude/agents/unit-test-writer.md) 의 "TDD 모드 계약" 섹션, [docs/adr/0010-tdd-order-enforcement.md](./docs/adr/0010-tdd-order-enforcement.md).

## 현재 상태 (2026-05-01 기준)

**한 줄 진행도**: Phase 1 PASS · Phase 2 진행 중 (1차 백테스트 FAIL → ADR-0019 복구 로드맵). Phase 3 코드 산출물 완료 상태로 보존, 진입 금지.

- **Phase 2 1차 백테스트 결과 (2026-04-24, 1년치 KIS 백필 + `--loader=kis`)**: MDD **-51.36%**, 총수익률 -50.05%, 샤프 -6.81, 승률 31.35%, 손익비 1.28, 기대값 ≈ -0.28R. Phase 2 PASS 기준 3.4 배 초과 미달.
- **신규 Phase 2 PASS 게이트 (ADR-0019)**: (1) MDD > -15%, (2) 승률 × 손익비 > 1.0, (3) 연환산 샤프 > 0 — 세 조건 전부 충족 + walk-forward 통과 후에만 Phase 3 착수.
- **복구 로드맵 진행 상황**:
  - **Step A — 민감도 그리드** (2026-04-25): **FAIL**. 28/32 조합 게이트 0 통과, 최저 MDD -42.08%. 상세는 `docs/runbooks/step_a_result_2026-04-25.md`.
  - **Step B — 비용 가정 재검정** (Issue #75, 2026-04-29 완료): 3 거래일 (04-27·04-29·04-30) 장중 실 호가 331,530 샘플 수집 → 전체 중앙값 스프레드 0.1305% (현행 가정 0.1% 대비 1.3×, 사전 기준 0.05~0.2% 내). **ADR-0006 슬리피지 0.1% 유지 결정**. `backtest/costs.py` 변경 없음. 새 ADR 없음. Step A 재실행 불필요. 분석: `docs/runbooks/step_b_spread_analysis.md`.
  - **Step C — 유니버스 유동성 필터 실행 완료 / FAIL (2026-04-30, Issue #76)**: 인프라 완료 후 운영자가 Top 50 / Top 100 두 서브셋 백테스트 실행 (`--loader=kis`, 2025-04-22 ~ 2026-04-21). 두 서브셋 모두 ADR-0019 세 게이트 전원 FAIL. Top 50: MDD -44.70%, 총수익률 -44.97%, 샤프 -6.68, 승률×손익비 0.377. Top 100: MDD -50.13%, 총수익률 -50.01%, 샤프 -7.74, 승률×손익비 0.383. ADR-0020 작성 안 함 (채택 결정 부재). 상세: `docs/runbooks/step_c_liquidity_filter_2026-04-30.md`. `config/universe_top50.yaml`·`config/universe_top100.yaml` git 추적 (커밋 781ec54). pykrx 1.2.7 부터 KRX_ID/KRX_PW env 필수 — `~/.config/stocker/.env` 및 `.env.example` 갱신 (커밋 36bfc65).
  - **Step D** — 전략 파라미터 구조 변경 (OR 윈도·force_close_at·재진입·일 N 진입). **Step C FAIL 확인 → 진입. D1·D2 모두 FAIL → D3/D4/E 결정 대기.**
    - **D1 — OR 윈도 스터디 (2026-04-30 ~ 2026-05-01): FAIL.** `step_d1_grid` 48 조합 × Top 50 / Top 100 = 96 런 전원 ADR-0019 게이트 미통과. 최선 조합: Top 50 `or_end=10:00, stop=2.5%, take=5.0%` MDD -37.18% / Top 100 `or_end=09:15, stop=2.5%, take=5.0%` MDD -35.98%. Step C 대비 MDD 개선(+7.5~14.2%p)이나 게이트 한도 -15% 까지 여전히 21~23%p 격차. 상세: `docs/runbooks/step_d1_or_window_2026-05-01.md`.
    - **D2 — force_close_at 스터디 (2026-05-01): FAIL.** `step_d2_grid` 48 조합 × Top 50 / Top 100 = 96 런 전원 ADR-0019 게이트 미통과. 최선 조합: Top 50 `force_close_at=15:20, stop=2.5%, take=5.0%` MDD **-35.02%** / Top 100 동일 파라미터 MDD **-37.56%**. `force_close_at=15:20` 이 두 서브셋 모두 가장 얕은 MDD. D1 vs D2 거의 동급 — `stop=2.5%/take=5.0%` 가 본질 개선 벡터. 상세: `docs/runbooks/step_d2_force_close_2026-05-01.md`. → D3/D4/E 결정 대기.
  - **Step E** — 전략 교체 (VWAP mean-reversion / opening gap reversal / pre-market pullback). A~D 전원 실패 전제. **진입 중 — PR2: `VWAPMRStrategy` 코드·테스트(35건) 완료, 백테스트 결과 대기. PR3: `GapReversalStrategy` 코드·테스트(34건) 완료, 백테스트 결과 대기. PR4 (Stage 1 CLI 확장) 완료: `strategy/factory.py` 신설(`STRATEGY_CHOICES`·`StrategyType`·`build_strategy_factory`) + `scripts/backtest.py`·`scripts/sensitivity.py` 에 `--strategy-type {orb,vwap-mr,gap-reversal}` 인자 추가. 테스트 54건 신규. PR4 Stage 2 완료: `backtest/prev_close.py` 신설 — `DailyBarPrevCloseProvider` (`HistoricalDataStore` + `BusinessDayCalendar` 조합으로 `GapReversalStrategy.PrevCloseProvider` 실주입). `scripts/backtest.py`·`scripts/sensitivity.py` `_run_pipeline` 갱신 (gap-reversal 시 provider 생성·try/finally close 보장). `scripts/sensitivity.py` 에 gap-reversal + workers≥2 거부 가드 신설. 테스트 31건 신규 (prev_close 18 + backtest CLI 5 + sensitivity CLI 8), 삭제 1건.**
- **Phase 3 진입 금지 (ADR-0019)**: 게이트 통과 전까지 `main.py` 모의투자 무중단 운영 계획 전면 보류. `execution/`·`main.py`·`monitor/`·`storage/` 코드 산출물은 보존 — 복구 후 그대로 재사용.
- **테스트 카운트**: pytest **1651 collected** (Step E PR4 Stage 2 기준 — PR4 Stage 1 1621 + Stage 2 신규 31건: `test_backtest_prev_close_provider.py` 18 + `test_backtest_cli.py` `TestGapReversalPrevCloseProviderInjection` 5 + `test_sensitivity_cli.py` `TestGapReversalPrevCloseProviderInjection` 8, 삭제 1건: `TestStrategyTypeBaseConfigRouting::test_gap_reversal_parallel_strategy_factory_callable_GapReversalStrategy`).
- **운영자 close 대기 Issue**: #51 (Phase 2 PASS 판정 FAIL → 복구 로드맵으로 대체) · #52 (`KisMinuteBarLoader` 파싱 실패 대응, 운영자 `scripts/debug_kis_minute.py` 실행 후 댓글) · #63 (공휴일 캘린더 가드, 백필 재실행으로 `date_mismatch` 0 확인 후 댓글) · #71 (장시간 hang 방지, 2026-04-24 백필 완주 확인 — 운영자 댓글만 잔여).

상세한 Phase 별 산출물·결정·테스트 카운트 변화·Issue 대응 이력은 [docs/phase-history.md](./docs/phase-history.md) 참조.

## 참고

- [docs/phase-history.md](./docs/phase-history.md) — Phase 진행 이력 (사후 수정 금지)
- [plan.md](./plan.md) — 설계 상세
- [README.md](./README.md) — 외부 개요
- [docs/architecture.md](./docs/architecture.md) — 기술 아키텍처 한눈 조망 (모듈 의존 그래프·DTO 계약·실행 시나리오·외부 I/O 경계)
- [.claude/agents/markdown-writer.md](./.claude/agents/markdown-writer.md) — 문서 동기화 에이전트
