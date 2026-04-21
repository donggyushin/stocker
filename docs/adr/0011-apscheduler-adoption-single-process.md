---
date: 2026-04-21
status: 승인됨
deciders: donggyu
related: [0008-single-process-only.md]
---

# ADR-0011: APScheduler 채택 및 BlockingScheduler + 공휴일 수동 판정 정책

## 상태

승인됨 — 2026-04-21.

## 맥락

Phase 3 `main.py` 구현 시점에 스케줄러 선택과 공휴일 처리 방식을 결정해야 했다.

plan.md Phase 3 요구사항: "APScheduler로 9:00 시작, 9:30 OR 확정, 장중 루프, 15:00 청산, 15:30 리포트". 이 요구를 구체화하면서 다음 세 가지 결정이 필요했다.

1. **스케줄러 종류**: `BlockingScheduler` vs `BackgroundScheduler` vs `AsyncIOScheduler`
2. **공휴일 자동 판정 도입 여부**: pykrx 기반 KRX 공휴일 자동 판정 vs cron `day_of_week='mon-fri'` 단순 제한
3. **APScheduler 버전 대역**: 3.x 유지보수 모드 대역 vs 4.x API 리팩터 중인 대역

ADR-0008(단일 프로세스 전용)이 이미 결정되어 있어, 스케줄러 역시 단일 프로세스 원칙과 가장 자연스럽게 결합하는 방식이 요구됐다.

검토한 대안:
- `BackgroundScheduler`: 메인 스레드가 별도 루프를 돌 필요가 있을 때 적합. 이 프로젝트에서는 메인 스레드가 할 일이 없어 blocking 방식이 더 단순.
- `AsyncIOScheduler`: `asyncio` 이벤트 루프 필요. 현재 코드베이스가 동기 중심이라 도입 비용 대비 이점 없음.
- pykrx 공휴일 자동 판정: `pykrx 1.2.7` 지수 API 가 이미 KRX 서버 호환성 문제로 깨져 있음(ADR-0004 맥락). KRX 공휴일 파일 형식 변동 리스크가 추가 의존성을 정당화하지 않는다고 판단.
- APScheduler 4.x: 2026-04 기준 API 리팩터 진행 중. 안정 대역이 아니라 제외.

## 결정

1. `apscheduler 3.x` (3.10~3.11 대역) 채택.
2. `BlockingScheduler(timezone='Asia/Seoul')` 선택 — 단일 프로세스 원칙(ADR-0008)과 가장 자연스럽게 결합. 전경 점유로 SIGINT/SIGTERM 이 메인 스레드에 자연스럽게 전달된다.
3. 공휴일 자동 판정 도입 **안 함** — cron `day_of_week='mon-fri'` 만. pykrx 추가 의존성 회피 + KRX 공휴일 파일 형식 변동 리스크 회피. KRX 임시공휴일 및 대체공휴일은 운영자가 해당 일에 프로세스를 띄우지 않는 방식으로 처리한다.
4. APScheduler job store 영속화 하지 않음 — 단일 프로세스 인메모리 스케줄. 재시작 시 스케줄 재등록은 `main()` 시작 시 수행.
5. 콜백 4종(`on_session_start`, `on_step`, `on_force_close`, `on_daily_report`) 모두 예외 re-raise 금지 — 단일 sweep 실패가 세션 전체를 죽이지 않게. 단, `on_force_close` 실패는 `logger.critical` 로 경보 레벨을 올린다(포지션 잔존 운영 리스크).
6. cron job 4종 스케줄: 09:00 session_start, 매분 00s(hour='9-14') step, 15:00 force_close, 15:30 daily_report. 모두 `day_of_week='mon-fri'`, `timezone='Asia/Seoul'`.

## 결과

**긍정**
- 외부 의존성 1개(`apscheduler 3.11.2` + transitive `tzlocal 5.3.1`) 추가만으로 cron 의미론 확보.
- 단위 테스트에서 `BlockingScheduler` / `CronTrigger` 를 팩토리 주입으로 mock 대체해 KIS·실시간 시세 접촉 0 상태로 wiring 검증 가능 (`tests/test_main.py` 47건).
- 드라이런 모드가 `DryRunOrderSubmitter` 주입으로 표현되어 `main.py` 내 분기 로직 최소화 (`_build_order_submitter` 한 곳만).
- SIGINT/SIGTERM → `_graceful_shutdown` → `scheduler.shutdown(wait=False)` → `realtime_store.close()` → `kis_client.close()` 의 정리 순서가 직관적.

**부정**
- 공휴일 운영자 수동 판단 — KRX 임시공휴일 누락 시 장 없는 날 스케줄이 울린다. 단, 매매는 KIS API 호출 실패·빈 분봉으로 자연스럽게 무해 종료.
- APScheduler 3.x 는 유지보수 모드 — 장기적으로 4.x 또는 다른 스케줄러로 재평가 여지.

**중립**
- `tzlocal 5.3.1` transitive 의존성 추가. 기능 범위는 타임존 감지에 한정.

## 추적

- 코드: `src/stock_agent/main.py` (`_install_jobs`, `_graceful_shutdown`, `build_runtime`)
- 테스트: `tests/test_main.py` (47건)
- 관련 ADR: [ADR-0008](./0008-single-process-only.md) (단일 프로세스 전용)
- 문서: [CLAUDE.md](../../CLAUDE.md), [plan.md](../../plan.md), [architecture.md](../architecture.md)
- 도입 PR: #17
