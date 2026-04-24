---
date: 2026-04-24
status: 승인됨
deciders: donggyu
related: [ADR-0008, ADR-0019]
---

# ADR-0020: Sensitivity 그리드 ProcessPool 병렬 실행 경로 도입

## 상태

승인됨 — 2026-04-24.

## 맥락

ADR-0019 의 Phase 2 복구 로드맵 Step A (민감도 그리드 실행) 가 실측에서
1 조합당 ≒18 분 (199 심볼 × 1 년 KIS 분봉 캐시 기준) 으로 측정되어,
`default_grid()` 의 32 조합 직렬 실행이 9~10 시간을 소요한다 (PID 17039,
17 분 경과 시점 1 조합 기준 — Issue #79 참조).

`run_sensitivity` (`src/stock_agent/backtest/sensitivity.py:228`) 는 단순
for-loop 로 동일 캐시(`data/minute_bars.db`) 를 조합마다 재스트리밍한다.
조합 간 상태 공유가 없고 각 조합은 CPU 바운드라 병렬화 여지가 크다.

호스트 자원: 11 CPU / 18 GB RAM. 1 프로세스 RSS ≒1.7 GB —
8 워커 시 ≒13.6 GB 로 18 GB 한도 여유가 좁다 → 기본 워커 상한 8.

ADR-0008 "단일 프로세스 전용" 은 운영 런타임(`main.py` + 데이터 어댑터)
범위 결정이다. 백테스트 분석 도구(scripts/sensitivity.py) 는 그 범위 밖이며
별도 정책이 필요하다.

대안 검토:

1. **threading + GIL** — `BacktestEngine` 은 순수 Python CPU 루프라 GIL
   하 병렬화 효과가 없다. 기각.
2. **asyncio** — 동일 사유로 CPU 병렬화 미해결. 기각.
3. **ProcessPoolExecutor + spawn** — Python stdlib, 의존성 추가 0,
   조합간 독립성에 정확히 매핑. 채택.
4. **기존 `run_sensitivity` 를 병렬화로 대체** — 단일 프로세스 호출 경로가
   사라져 회귀 위험·디버깅 복잡도 증가. 기각.

## 결정

`run_sensitivity_parallel(loader_factory, start, end, symbols, base_config,
grid, *, max_workers, mp_context)` 신규 함수를 도입한다. 기존
`run_sensitivity` 는 그대로 유지 — `scripts/sensitivity.py --workers 1` 분기
에서 직렬 경로로 보존한다 (회귀 안전망).

핵심 계약:

- `loader_factory: Callable[[], BarLoader]` 를 받아 워커 프로세스마다 새
  loader 인스턴스를 생성한다. `KisMinuteBarLoader` 의 PyKis 세션 ·
  `requests.Session` · `sqlite3.Connection` 이 pickle 불가능하므로
  loader 자체가 아닌 팩토리를 직렬화한다.
- 결과 순서는 `grid.iter_combinations()` 순서를 유지한다 — 워커는
  `as_completed` 로 수집하되 `combo_idx` 로 재정렬한다.
- Fail-fast: 한 워커 실패 시 `executor.shutdown(wait=False,
  cancel_futures=True)` 로 잔여 future 를 취소한 뒤 예외를 호출자로 전파한다
  (직렬 경로의 "조합 N 실패 → 이전 N-1 결과 폐기" 계약 유지).
- `max_workers <= 0` → `RuntimeError`.
- `mp_context` 는 옵션 — `None` 이면 PPE 기본값 (macOS/Linux Python 3.12 →
  `spawn`). 테스트에서 `multiprocessing.get_context("fork")` 를 주입해
  pytest 모듈 import 비용을 회피할 수 있다.

`scripts/sensitivity.py` CLI 확장:

- `--workers N` (int, default `min(os.cpu_count() - 1, 8)`).
- `1` → 기존 `run_sensitivity` 경로 (회귀 보존).
- `0` 또는 음수 → `RuntimeError` (exit code 2).
- 워커별 loader 생성을 위해 `functools.partial(_build_loader_primitive,
  args.loader, args.csv_dir)` 를 팩토리로 사용 — `argparse.Namespace`
  pickle 회피.

SQLite 동시 읽기 — `KisMinuteBarLoader` 는 `_init_db` 에서 `journal_mode=WAL`
을 설정하므로 다중 프로세스 readers 안전. 단 워커별로 별도 connection 을
생성해야 하며 (상속 connection 은 multi-process 에서 깨짐), 본 결정의
`loader_factory` 패턴이 이를 자동으로 강제한다.

KIS 백필이 완료되어 캐시 hit 만 발생하는 시나리오를 전제 — 캐시 miss 가
동시에 발생하면 워커 N 개가 각자 KIS API 를 호출해 EGW00201 rate limit 누적
위험이 있다. ADR-0019 복구 로드맵 Step A 는 백필 완료 후 진입이라 이 위험은
실효성 낮음. 캐시 miss 가 발생하면 운영자가 1 회 직렬 실행으로 캐시 채운 뒤
재실행하는 운용 패턴.

## 결과

**긍정**
- `default_grid()` 32 조합 실행 시간이 직렬 9~10h → 8 워커 ≒1~2h 로 단축
  (이론 5~8 배). ADR-0019 Step A 의 회전 속도 향상.
- 직렬 경로 (`run_sensitivity` + `--workers 1`) 보존으로 회귀 검증·소형
  그리드 디버깅 경로 유지.
- 의존성 추가 0 (`concurrent.futures` 는 stdlib).
- `BacktestEngine`·`ORBStrategy`·`RiskManager`·`backtest/costs.py` 비용
  산식 등 코어 로직은 어떤 것도 건드리지 않음 — 회귀 위험 최소화.

**부정**
- `max_workers × 1.7 GB` 메모리 점유 — 18 GB 호스트에서 8 워커가 사실상
  상한. CSV 어댑터 (`MinuteCsvBarLoader`) 는 더 큰 인스턴스를 만들 수
  있으니 `--workers` 를 조정해야 할 수 있음.
- ADR-0008 (단일 프로세스 전용) 의 명시적 예외 도입 — 분석 도구 범위로
  스코프 제한이 명확하지만 정책 문서를 함께 읽어야 의도 파악 가능.
- 워커별 loguru sink 가 OS 수준에서 stderr 로 interleave 됨. `{process}`
  토큰이 기본 포맷에 포함되어 PID 로 구분 가능하지만 라인 단위 race 는
  발생할 수 있다.

**중립**
- `mp_context` 주입 파라미터로 테스트 격리 유연성 확보. 운영 경로는 PPE
  기본값 (macOS spawn) 사용.
- `loader_factory` 패턴은 향후 walk-forward 검증 (Phase 5) 의 윈도 단위
  병렬 실행에도 그대로 재활용 가능.

## 추적

- 코드: `src/stock_agent/backtest/sensitivity.py`,
  `src/stock_agent/backtest/__init__.py`, `scripts/sensitivity.py`
- 문서: `src/stock_agent/backtest/CLAUDE.md`, `CLAUDE.md`, `plan.md`
- 도입 PR: #79
- 관련 ADR: ADR-0008 (단일 프로세스 전용 — 운영 런타임 범위), ADR-0019
  (Phase 2 복구 로드맵 — Step A 가속 동기)
