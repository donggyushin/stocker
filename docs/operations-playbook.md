# stock-agent 모의투자 운영 플레이북

Phase 3 모의투자 연속 10영업일 운영을 위한 **1장짜리 런북**. 이상 상황이 터졌을 때 이 문서만 펴놓고 즉시 대응 가능하도록 설계한다. 상세 설계와 승인 결정은 [plan.md](../plan.md)·[CLAUDE.md](../CLAUDE.md)·모듈별 `CLAUDE.md` 가 정본.

> 본 문서는 **운영 절차** 만 다룬다. 전략 파라미터·리스크 한도 수치는 정본 문서가 바뀌면 본 문서도 함께 갱신한다. 민감정보(키·토큰·chat_id)는 절대 본 문서에 박지 않는다.
>
> 실전 전환은 본 플레이북이 10영업일간 실운영으로 검증된 이후에만 검토한다 — 코드 green ≠ 운영 green.

---

## 1. 장 시작 전 체크리스트 (08:50 KST)

개장 10분 전에 아래 순서로 점검한다. 한 항목이라도 실패하면 **당일 프로세스를 띄우지 않는다** — 모의투자 카운트는 하루 건너뛰기를 허용하므로 무리하지 않는다.

### 1.1 `.env` 키 존재 확인

최소 8종. 값은 표시하지 말 것 (shoulder surfing 방지).

```bash
grep -E '^(KIS_APP_KEY|KIS_APP_SECRET|KIS_ACCOUNT_NO|KIS_HTS_ID|KIS_LIVE_APP_KEY|KIS_LIVE_APP_SECRET|KIS_LIVE_ACCOUNT_NO|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=' .env \
  | awk -F= '{print $1}' | sort -u
```

정상 출력은 9줄 (`KIS_HTS_ID` 포함). 누락 항목이 있으면 `.env.example` 과 대조 후 보강.

### 1.2 KIS Developers IP 화이트리스트 유효성

맥북 공인 IP 가 마지막 등록 시점에서 바뀌었을 수 있다. 이사·카페 이동·VPN 활성화 모두 트리거.

```bash
curl -s https://api.ipify.org
```

KIS Developers 포털 → "앱 관리 → 허용 IP 목록" 과 대조. 불일치면 포털에서 현재 IP 추가 → 5분 정도 기다린 뒤 1.3 으로 진행.

### 1.3 `healthcheck.py` 4종 그린

```bash
uv run python scripts/healthcheck.py
```

- 1) 토큰 발급 · 2) 모의 잔고 조회 · 3) 텔레그램 "hello" 수신 — 시간대 무관 통과해야 함.
- 4) 삼성전자(005930) 현재가 조회 — **평일 장중 실행 시만 의미 있음**. 장외 실행은 WebSocket 연결은 되지만 체결 이벤트 미수신으로 2초 타임아웃 후 실패할 수 있다. 08:50 은 장중이 아니므로 여기서는 1~3 그린 + 4는 관찰(실패해도 장 시작 09:00~09:05 사이 재실행으로 확인).
- `EGW00123` 또는 "IP" / "접근이 허용되지 않" 포함 에러 → healthcheck 가 자동으로 IP 화이트리스트 힌트 로그를 남긴다 (scripts/healthcheck.py:101-107). 1.2 로 돌아가 갱신.

### 1.4 SQLite 원장 디스크 여유·백업

```bash
ls -lh data/trading.db*
df -h data/
```

`data/trading.db` 파일 크기가 비정상적으로 커졌거나 디스크 여유가 1GB 미만이면 정리. 원장은 append-only 이므로 운영 중 폭증은 없지만, `trading.db-wal` / `trading.db-shm` 가 남아 있다면 직전 세션이 비정상 종료됐을 수 있다 (SQLite 가 재기동 시 자동 체크포인트).

백업 권장 (세션 시작 전 1회):
```bash
cp data/trading.db "data/trading.db.$(date +%Y%m%d-bkp)"
```

### 1.5 `config/universe.yaml` 최신 여부

KOSPI 200 정기변경은 연 2회 (매년 6월·12월 선·옵 동시만기일 익영업일 기준, ADR-0004). 해당 주차에 운영 중이라면 갱신 필요. 갱신 절차는 [src/stock_agent/data/CLAUDE.md](../src/stock_agent/data/CLAUDE.md) 참조.

```bash
grep -c '^  - ' config/universe.yaml
```

현재 199줄(임시 가상 코드 1건 제외). 수가 다르거나 마지막 수정일이 직전 정기변경 전이면 점검.

---

## 2. 장중 이상 대응 매트릭스

텔레그램 `[stock-agent] ERROR <stage>` 알림이 오면 **stage 별** 로 아래 매트릭스에 따라 대응한다. 모든 알림은 [src/stock_agent/monitor/CLAUDE.md](../src/stock_agent/monitor/CLAUDE.md) 의 `ErrorEvent` 포맷 (`stage`·`error_class`·`message`·`timestamp`·`severity`) 에 따라 본문에 구조화 정보가 담겨 있다.

### 2.1 stage 별 1차 조치

| stage | severity | 1차 조치 | 세션 계속? |
|---|---|---|---|
| `session_start` | `error` | logs/stock-agent-YYYY-MM-DD.log 에서 error_class 확인. `StartingCapitalError` → 모의 잔고 이슈, 잔고 조회 실패 → KIS 연결 문제. 이 경우 `on_step` 은 세션 미시작 감지로 첫 호출 1회 warning 후 전부 skip (main.py:_on_step). | 당일 매매 자동 중단 (다음 영업일 09:00 에 자연 복구) |
| `step` | `error` | `error_class` 에 따라 분기. `BrokerError` / `OSError` / 네트워크 예외가 대부분. 한 번 났다고 프로세스 죽이지 않음 — 다음 분 재시도. 5분 이상 연속되면 수동 중단 검토. | 예 (단일 sweep 실패는 루프 연속성 유지, ADR-0011 결정 5) |
| `reconcile` | `critical` | **잔고 ↔ RiskManager 포지션 불일치** — 운영자가 수동 정리 필요. 메시지에 `symbols=[...]` 목록이 포함됨. HTS 또는 KIS 앱에서 실제 보유 확인 → 상이하면 프로세스 중단 후 수동 청산. | 아니오 — 수동 개입 필요 |
| `force_close` | `critical` | **포지션 잔존 위험**. 15:00 강제청산이 실패했다는 뜻. logger.critical 와 이중 경보. 즉시 HTS 접속해 잔여 long 포지션 수동 시장가 매도. 동시호가(15:20~15:30) 넘어가면 다음 영업일 갭으로 손실 확대 가능. | 아니오 — 즉시 수동 청산 |
| `daily_report` | `error` | 일일 요약 집계 실패. 매매 자체는 이미 종료된 뒤라 운영 리스크는 낮음. `data/trading.db` 의 `daily_pnl` 테이블 직접 쿼리로 대체 (아래 4.2 참조). | 당일 운영 영향 없음 |

### 2.2 연속 실패 경보 (Persistent failure)

텔레그램·SQLite 원장 모두 **연속 5회 실패 시 `logger.critical` + stderr 2차 경보** dedupe 1회 방출 (monitor/notifier.py, storage/db.py).

```bash
# 텔레그램 sink 가 죽은 경우 stderr 가 유일한 단서
grep -E "(telegram\.notifier\.persistent_failure|storage\.db\.persistent_failure)" logs/stock-agent-*.log

# loguru sink 자체가 죽었을 가능성 — 콘솔(터미널) 출력 직접 확인
# (실행 중 터미널 스크롤업 또는 tmux capture-pane 사용)
```

경보가 뜨면 네트워크 / 디스크 여유 / 키 유효성 순으로 확인. 복구 후 다음 성공 호출에서 카운터·dedupe 플래그 자동 리셋.

### 2.3 서킷브레이커 발동

조건: `daily_realized_pnl_krw <= -(starting_capital * 0.02)` (RiskConfig 기본값). 발동 시:

- RiskManager 가 이후 모든 진입 시그널을 `rejected(reason="daily_loss_limit_reached")` 로 거부.
- `Executor.is_halted` true.
- 텔레그램 일일 요약의 `서킷브레이커=yes` 로 표기.

조치:
- 당일은 **추가 진입 중단**. 기존 포지션은 15:00 force_close 에 자연 청산되므로 수동 개입 불필요.
- 다음 영업일 개장 전에 원인 분석: `sqlite3 data/trading.db 'SELECT * FROM orders WHERE session_date = DATE("now","localtime","-0 day") ORDER BY filled_at;'` 로 진입·청산 체결 시계열 확인.
- 원인이 전략 파라미터 부적합으로 보이면 즉시 수정하지 말고 **리뷰 노트에 기록만** — plan.md Phase 3 PASS 기준은 "2주 무사고" 이며 튜닝은 회고 단계에서.

### 2.4 KIS `EGW00123` 계열 (IP 화이트리스트)

증상: 에러 메시지에 `EGW00123` / `IP` / `접근이 허용되지 않` 중 하나 이상 포함. 토큰 발급·조회·주문 경로 모두 해당.

조치:
1. **즉시 프로세스 중단** (SIGINT). 포지션 잔존 여부 HTS 확인.
2. `curl -s https://api.ipify.org` 로 현재 공인 IP 재확인.
3. KIS Developers 포털 → 앱 관리 → 허용 IP 목록 갱신.
4. 5분 대기 후 `uv run python scripts/healthcheck.py` 재실행.
5. 1~3 그린이면 다음 영업일부터 재개. **오늘은 카운트 리셋** (4.3 참조).

### 2.5 네트워크 flake — WebSocket 끊김

`RealtimeDataStore` 는 WebSocket 우선 + REST 폴링 fallback ([src/stock_agent/data/CLAUDE.md](../src/stock_agent/data/CLAUDE.md)). 전환 시 `mode` 가 `"websocket"` → `"rest"` 로 바뀌고 `polling_consecutive_failures` 카운터가 관측 가능.

- 로그 `RealtimeDataStore 가동 — mode=rest` 가 뜨면 폴백 작동 중 — 세션은 계속.
- `polling_consecutive_failures` 가 누적 10회 이상 → 네트워크 근본 문제. 중단 검토.
- `on_step` 이 빈 분봉만 받게 되는 조용한 실패 경로가 가장 위험 — 15:00 전까지 진입 0건 + 청산 0건이면 네트워크 먼저 의심.

---

## 3. Kill switch 절차

세션을 즉시 멈춰야 할 때의 경로.

### 3.1 정상 종료 (SIGINT / SIGTERM)

```bash
# 실행 중 터미널에서
Ctrl+C

# 다른 터미널에서
pkill -INT -f "python -m stock_agent.main"
# 또는
pkill -TERM -f "python -m stock_agent.main"
```

내부 동작 (main.py:_graceful_shutdown):
1. SIGINT/SIGTERM 을 `SIG_DFL` 로 재설치 → 2차 시그널 도달 시 즉시 종료 (락 경합 회피).
2. `scheduler.shutdown(wait=False)` — 현재 실행 중인 job 만 끝나면 스케줄러 종료.
3. `realtime_store.close()` — WebSocket/폴링 스레드 정리.
4. `kis_client.close()` — 토큰 세션 정리.
5. `recorder.close()` — SQLite WAL 체크포인트 + connection 종료.
6. `finally` 블록에서 3~5 중복 호출 (멱등 보장).

exit code 0. 터미널에 `main.graceful_shutdown signum=2 — 리소스 정리 시작` 로그가 남으면 정상.

### 3.2 강제 종료 — 오픈 포지션 수동 청산

`kill -9` 또는 프로세스 크래시로 graceful shutdown 이 실행되지 않은 경우. 포지션이 남아 있을 수 있다.

1. HTS 또는 KIS 앱에서 **실제 보유 종목 확인**. 모의 계좌라도 반드시 직접 확인.
2. 남은 long 포지션을 수동 시장가 매도.
3. 15:20 이후 동시호가 구간은 시장가 지연 가능 — 지정가로 호가창 상단 제출.
4. 다음 영업일 개장 전에 `data/trading.db` 의 `orders` 테이블과 HTS 잔고를 재대조. 불일치가 있으면 다음 세션의 `reconcile` 단계가 다시 경보할 것이므로 지금 바로 수동 청산만 일치시키면 된다.

### 3.3 재시작 시 복원 경로

APScheduler job store 는 **메모리 전용** (ADR-0011 의도적 선택). 프로세스 재시작 = 모든 스케줄 재등록 + RiskManager 인메모리 상태 소실.

재시작 규칙:
- **09:00~15:00 사이 재시작**: `on_session_start` cron 이 이미 지나갔으므로 `session_status.started = False`. `on_step` 이 첫 호출 1회 warning 만 남기고 skip. **오늘은 매매 중단**. 자연 복구는 다음 영업일 09:00.
- **15:00 이후 재시작**: 다음 영업일까지 job 대기 — 안전.
- **09:00 직전 재시작**: 09:00 cron 이 정상 트리거. 단, 08:59 에 띄우는 건 비권장 (KIS 토큰 발급 + 유니버스 구독에 수십 초 소요 가능).

강제로 당일 매매를 재개하려면 코드 개입이 필요하며 현재 범위 밖. **해당 영업일은 포기**가 표준 대응.

---

## 4. 일일 마감 체크리스트 (15:40 KST)

장 종료 10분 후 아래를 순서대로 확인한다. `on_daily_report` cron 이 15:30 에 발사되므로 15:40 시점에는 로그·DB·텔레그램 세 경로 모두 결과가 들어와 있어야 한다.

### 4.1 텔레그램 일일 요약 수신 확인

수신 포맷 (monitor/notifier.py):

```text
[stock-agent] 일일 요약 2026-04-21
실현 PnL=45000원 (1.50%)
진입 횟수=2
서킷브레이커=no
Executor halt=no
Reconcile mismatch=없음
```

미수신 시:
- loguru 로그에서 `main.daily_report date=... realized_pnl=...` 라인 존재 확인. 있으면 텔레그램 sink 만 실패.
- 2.2 의 연속 실패 경보 확인.
- `[DRY-RUN]` 프리픽스가 붙어 있으면 드라이런 모드 — 실거래 카운트 제외 (4.3).

### 4.2 `data/trading.db` 당일 레코드 확인

```bash
sqlite3 data/trading.db <<'SQL'
SELECT session_date, realized_pnl_krw, entries_today, halted, mismatch_symbols
FROM daily_pnl
ORDER BY session_date DESC LIMIT 5;

SELECT order_number, session_date, symbol, side, qty, fill_price, exit_reason, net_pnl_krw, filled_at
FROM orders
WHERE session_date = DATE('now','localtime')
ORDER BY filled_at;
SQL
```

확인 포인트:
- `daily_pnl` 최상단 행의 `session_date` = 오늘 = 텔레그램 요약 날짜.
- `orders` 의 buy/sell 수가 일치 (홀수면 오픈 포지션 존재 가능 — HTS 재확인).
- `net_pnl_krw` 합계 ≈ `daily_pnl.realized_pnl_krw` (비용 반영 차이로 수 원 오차는 허용).
- `mismatch_symbols` 가 빈 배열(`[]`)이 아니면 2.1 `reconcile` 섹션으로.

### 4.3 영업일 카운트 관리

모의투자 연속 10영업일 무중단 운영이 Phase 3 PASS 조건 ([plan.md](../plan.md)). 아래 기준으로 카운트:

| 상황 | 카운트 |
|---|---|
| 정상 운영 (진입 0~10회, unhandled error 0, reconcile mismatch 0, 텔레그램 100% 수신) | +1 |
| 시장 휴장 (공휴일·임시휴장) | 변동 없음 (영업일 아님) |
| 1.1~1.5 중 실패로 프로세스 미기동 | 변동 없음 (0 도 아니지만 +1 도 아님 — 건너뛰기) |
| 장중 `EGW00123` / `reconcile` / `force_close` critical 발생 | **카운트 리셋 (0 으로)** |
| 텔레그램 연속 실패 경보 + stderr 도 막힘 | **카운트 리셋** |
| `step` error 1회 후 자연 복구 | +1 (카운트 유지) |

간이 기록 양식 (로컬 노트):
```text
[YYYY-MM-DD] day N/10  pnl=±XXX원 (±X.XX%) entries=X halted=no mismatch=none  notes=...
```

10일 달성 후: plan.md Phase 3 PASS 선언 여부 판정 → 실전 전환 검토 착수 (ADR-0002 paper/live 키 분리 구조 그대로 유지).

---

## 5. 참고 자료

- [CLAUDE.md](../CLAUDE.md) — Phase 상태·현재 테스트 카운트·승인된 결정 정본
- [plan.md](../plan.md) — 전략 파라미터·리스크 한도·PASS 기준
- [docs/architecture.md](./architecture.md) — 모듈 의존 그래프·외부 I/O 경계
- [docs/adr/README.md](./adr/README.md) — ADR 인덱스 (특히 0011 APScheduler, 0012 notifier, 0013 storage)
- [src/stock_agent/monitor/CLAUDE.md](../src/stock_agent/monitor/CLAUDE.md) — 텔레그램 실패 정책
- [src/stock_agent/storage/CLAUDE.md](../src/stock_agent/storage/CLAUDE.md) — SQLite 원장 스키마
- [src/stock_agent/execution/CLAUDE.md](../src/stock_agent/execution/CLAUDE.md) — Executor reconcile 계약

## 이 문서 갱신 정책

본 문서는 **실제 운영 중 누락 항목을 발견하면 즉시 반영** 한다. 10영업일 운영은 실사용 필드 테스트이기도 하다. 다음 조건에서 갱신:

- 새 stage 에서 에러가 발생했으나 2.1 매트릭스에 없음
- 1.1~1.5 체크 중 놓친 항목 발견
- 대응 절차의 실제 소요 시간·성공률이 본 문서 기술과 다름
- Phase 상태 전환 (Phase 3 PASS → Phase 4 실전 전환 시 본 문서 전면 재검토)

갱신 시 root CLAUDE.md "문서 동기화 정책" 기조를 따른다 — 승인된 결정·리스크 고지 보존, 존재하지 않는 명령 금지.
