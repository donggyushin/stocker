# Step A 민감도 그리드 — 집 PC 이어받기 (resume 플로우)

Issue #74 (ADR-0019 복구 로드맵 Step A) 민감도 32 조합 실행을 회사 PC 에서 시작했다가 freeze 로 중단된 상황을 집 PC 에서 이어 완료하기 위한 절차.

## 전제

- 회사 PC 에서 `data/minute_bars.db` (2.78 GB, KIS 백필 1 년치 캐시) 는 확보됐지만 실 실행은 freeze 로 미완료.
- Google Drive 브라우저 업로드로 캐시 DB 를 집 PC 로 이전 예정 (Drive CLI·rclone 미설치).
- 집 PC RAM 32 GB — 워커 2 개까지 안전. 워커 1 로 시작해 여유 확인 후 필요시 증설.
- `scripts/sensitivity.py --resume <csv 경로>` 플래그가 같은 CSV 경로를 재지정하면 이미 완료된 조합을 skip 하고 미완료 조합만 이어 실행. freeze·재부팅·세션 종료 내성 확보됨.
- **freeze 내성 단위**: 조합 1개 완료 시점마다 `append_sensitivity_row` (atomic `os.replace`) 가 CSV 에 flush — 이전 PR #81 의 "재실행 시 skip" 보다 한 조합 단위로 세분화됨 (Issue #82). 최악의 경우 마지막 flush 이후 실행 중이던 조합 1개만 재실행.

## 회사 PC 에서 (퇴근 전)

1. 현재 러닝 중인 nohup 프로세스가 남아있다면 kill (`ps aux | grep sensitivity.py`).
2. `data/minute_bars.db` 브라우저로 https://drive.google.com 업로드. 2.78 GB, 회사 WiFi 1~2 h 예상. 무료 15 GB 한도 여유.
3. 본 PR(`ops_step_a` 브랜치) 머지 또는 푸시 확인 — 집에서 `git pull` 만으로 최신 CLI 접근 가능하도록.

## 집 PC 에서 (퇴근 후)

### 1. 프로젝트 최신화

```bash
cd ~/Documents/stocker   # 실제 경로에 맞게
git fetch origin
git checkout main
git pull origin main
```

(또는 이미 main 에 머지됐으면 `git pull` 만.)

### 2. 캐시 DB 복원

Google Drive 웹에서 `minute_bars.db` 다운로드 → `data/minute_bars.db` 로 배치.

```bash
mkdir -p data
# 다운로드 파일 이동
mv ~/Downloads/minute_bars.db data/minute_bars.db

# 크기 확인 — 2.78 GB 내외여야 함
ls -lh data/minute_bars.db
```

### 3. 민감도 실행 (overnight)

```bash
mkdir -p data
# 워커 1 (RAM 여유 큼, ~9-10 h 예상). caffeinate 로 절전 차단.
caffeinate -i -s nohup nice -n 19 uv run python scripts/sensitivity.py \
  --loader=kis \
  --from 2025-04-22 --to 2026-04-21 \
  --output-markdown data/sensitivity_report.md \
  --output-csv data/sensitivity_metrics.csv \
  --resume data/sensitivity_metrics.csv \
  --workers 1 \
  > data/sensitivity_run.log 2>&1 &

# PID 기록
echo $! > data/sensitivity.pid
cat data/sensitivity.pid
disown
```

주의:
- `--resume` 와 `--output-csv` 를 **같은 경로**로 지정. 중도에 중단·재실행 시 완료된 조합이 자동 skip 됨.
- 첫 실행이면 `data/sensitivity_metrics.csv` 파일이 없어 자연스럽게 전체 32 조합 실행 (resume 무효, 경고 없이).
- freeze 발생하면 재부팅 후 동일 명령 재실행 — 완료된 조합은 CSV 에 이미 기록돼 있어 skip.

### 4. 러닝 상태 확인

```bash
ps -p $(cat data/sensitivity.pid) 2>/dev/null && echo "alive" || echo "done/dead"
tail -n 20 data/sensitivity_run.log
```

완료 여부는 `data/sensitivity_metrics.csv` 행 수로 판단 — 33 줄 (헤더 1 + 32 조합) 이면 완료.

```bash
wc -l data/sensitivity_metrics.csv
```

### 5. 완료 후 분석 (토요일 오전)

산출물 3 종 확인:
- `data/sensitivity_report.md` — Markdown 표 (정렬 `total_return_pct` 내림차순 기본)
- `data/sensitivity_metrics.csv` — 32 조합 메트릭 원본
- `data/sensitivity_run.log` — 실행 로그

세 게이트 (ADR-0019) 전부 통과 조합 탐색:
1. `max_drawdown_pct > -15%` (낙폭 절대값 15% 미만)
2. `win_rate × avg_pnl_ratio > 1.0`
3. `sharpe_ratio > 0`

CSV 를 pandas 또는 간단한 Python 필터로 검사:

```bash
uv run python - <<'EOF'
import csv
from decimal import Decimal
with open("data/sensitivity_metrics.csv") as f:
    rows = list(csv.DictReader(f))
passed = []
for r in rows:
    mdd = Decimal(r["max_drawdown_pct"])
    wr = Decimal(r["win_rate"])
    pnl = Decimal(r["avg_pnl_ratio"])
    sh = Decimal(r["sharpe_ratio"])
    if mdd > Decimal("-0.15") and wr * pnl > Decimal("1.0") and sh > Decimal("0"):
        passed.append(r)
print(f"통과 조합: {len(passed)} / {len(rows)}")
for p in passed:
    print(p)
EOF
```

### 6. 판정에 따른 후속

- **통과 조합 ≥ 1 건**: Issue #67 walk-forward 본 구현 PR 착수. 통과 조합으로 walk-forward 검증 → ADR-0019 세 게이트 최종 충족 확인 후 Phase 3 착수.
- **통과 조합 0 건**: ADR-0019 Step B (비용 가정 재검정) 이슈 활성화. 호가 스프레드 1 주 샘플 수집 후 `costs.py` 슬리피지 재보정.

어느 쪽이든 #74 에 CSV 요약 + 판정 결과 댓글 첨부 후 close.

## 문제 상황 대응

| 증상 | 조치 |
| --- | --- |
| 컴퓨터 freeze / 재부팅 | 전원 복구 후 같은 명령 재실행. `--resume` 이 자동 이어감. |
| Google Drive 다운로드 손상 (SHA 불일치) | 회사 PC 에서 `shasum -a 256 data/minute_bars.db` 미리 기록 → 집 PC 도 동일 값 확인. 불일치면 재다운로드. |
| `KisMinuteBarLoader` 가 실 KIS 호출 시도 | 캐시 DB 가 없거나 일부 범위 미포함. 로그에 `API 호출 횟수를 초과` 경고 다량 나오면 일시 정지 후 캐시 경로 재확인. |
| `UniverseLoadError` | `config/universe.yaml` 이 저장소에 포함돼 있어 일반적으로 발생 안 함. 커밋 누락 여부 확인. |

## 관련

- ADR-0019 (Phase 2 복구 로드맵)
- ADR-0020 (ProcessPool 병렬 실행 정책)
- Issue #74 (Step A 민감도 32 조합 실행)
- Issue #67 (walk-forward 본 구현 대기)
