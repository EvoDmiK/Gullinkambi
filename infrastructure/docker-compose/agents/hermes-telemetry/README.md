# Hermes telemetry collector

Mac mini의 활성 Hermes profile DB와 `kanban.db`,
`kanban/boards/*/kanban.db`, Orca의 Codex/Claude/Antigravity 일별 사용량 파일을 읽어 NAS
n8n webhook으로 개인정보가 제거된 실행 통계를 전송한다.
`HERMES_TELEMETRY_EXCLUDED_PROFILES`에 지정한 legacy profile DB는 수집하지 않는다.

## 수집 범위

- 프로필, source, 모델, provider
- 시작·종료 시각, 종료 사유, 처리 시간
- 메시지·API·도구 호출 수
- 입력·출력·reasoning·cache token
- 예상·실제 비용과 pricing metadata
- 도구 이름별 호출 수
- `/goal` 상태, 턴 수, 시작·종료 시각, 처리 시간
- Kanban 보드, task 상태, assignee, 생성·시작·완료 시각
- Kanban run/event/link 메타데이터와 dependency/comment/attachment/run 개수
- Orca Codex/Claude/Antigravity의 일별 모델·프로젝트·모델 호출 수와 입력/cache/output/reasoning token
- Orca Codex/Claude 세션 ID(쓰레드)별 일일 모델 호출 수와 token
- Orca 쓰레드 이름(Codex `name`/`title`, Claude `aiTitle`)

대화 본문, system prompt, reasoning, tool argument, user/channel 식별자, 파일 경로는 전송하지 않는다. `tool_calls`에서는 SQLite JSON 함수로 함수명만 추출하므로 tool argument가 collector 프로세스로 들어오지 않는다. 스킬 이름은 Hermes SQLite에 별도 구조화 필드가 없으므로 현재 collector에서는 추론하지 않는다.

Telemetry schema v2는 프로필 DB의 `state_meta`에 저장된 `goal:*` 상태를 읽는다. Goal 본문과 judge 사유는 전송하지 않으며, `done`/`paused`/`cleared` 상태에서 `last_turn_at`이 있는 실행만 처리 시간을 계산한다. 컨텍스트 압축으로 같은 goal이 여러 session에 복제돼도 `profile + created_at` 기준으로 한 번만 집계한다.

Kanban에서도 task `body`, `result`, comment body, event payload, attachment filename/path, workspace path, run summary/error 본문은 전송하지 않는다. 기본값에서는 task title도 전송하지 않고 `title_hash`만 보낸다. Grafana 테이블에서 제목이 꼭 필요하면 운영상 안전하다고 판단한 뒤 `HERMES_TELEMETRY_INCLUDE_KANBAN_TITLES=true`를 설정한다.

Orca 사용량의 과거 구간은 Orca가 만든 `orca-codex-usage.json`,
`orca-claude-usage.json`과 로컬 LaunchAgent가 만든
`orca-antigravity-usage.json` 중 `repoId` 또는 `worktreeId`가 있는 집계만 사용한다.
내장 usage scanner가 늦게 갱신되는 경우를 보완하기 위해 최근 7일은 Codex
`token_usage_record`와 Claude `assistant.message.usage`를 직접 다시 집계한다.
대화·reasoning·tool 본문은 추출하거나 전송하지 않는다. 프로젝트 경로는 전송하지
않으며 화면용 프로젝트명은 Orca worktree 경로에서 저장소 이름을 추출한다.
원래 식별자는 SHA-256 해시로 대체한다.
쓰레드 이름은 사용자가 지정하거나 에이전트가 생성한 문자열을 최대 160자로 잘라
평문 전송한다. 이름에 민감한 문구를 넣으면 Grafana와 TimescaleDB에도 표시된다.
참조 스킬은 세션 로그에서 `SKILL.md`를 읽는 도구 호출이나 명시적 `Skill` 호출이
확인된 경우에만 이름을 전송한다. 파일 경로나 본문은 전송하지 않는다. 파일을 읽은
기록만으로 실제 적용 여부를 확정할 수 없으며, 스킬별 토큰 사용량도 계산하지 않는다.
쓰레드별 집계는 Codex/Claude 원본 세션 로그에서 Orca 사용량 파일의 `sessionId`와
일치하는 세션 ID를 사용한다. 로그가 남아 있는 Orca-managed worktree 세션 전체를
처음 한 번 소급 집계하고, 이후에는 변경된 파일만 다시 읽는다. 과거 일별 합계만
남아 원본 세션 로그가 없는 구간은 쓰레드별로 복원할 수 없다.

## 1. NAS TimescaleDB 설치

새 데이터베이스는 Compose 초기화 시 `init-telemetry.sh`가 자동 실행된다. 이미 생성된 데이터베이스에는 NAS에서 한 번 수동 실행한다.

```bash
cd /volume3/Gullinkambi/infrastructure/docker-compose/homelab
sudo docker compose exec -T timescaledb \
  bash /docker-entrypoint-initdb.d/20-init-telemetry.sh
```

`bash`를 명시하므로 NAS bind mount가 실행 권한을 보존하지 않거나 `noexec`로 취급돼도 스크립트를 읽어 실행할 수 있다.

스크립트는 다음 객체를 만든다.

- `observability.hermes_runs`: session별 최신 상태
- `observability.hermes_run_events`: 변경 이력 hypertable
- `observability.hermes_goal_runs`: `/goal` 실행별 최신 상태
- `observability.hermes_kanban_tasks`: Kanban task별 최신 상태
- `observability.hermes_kanban_task_runs`: Kanban worker run별 최신 상태
- `observability.hermes_kanban_task_events`: Kanban event별 최신 상태
- `observability.hermes_kanban_task_links`: Kanban dependency link
- `observability.orca_usage_daily`: Orca agent/model/project별 일별 token과 turn 집계
- `observability.orca_thread_usage_daily`: Orca 쓰레드/agent/model/project별 일별 token과 호출 수
- `observability.ingest_hermes_run(...)`: n8n 전용 idempotent ingest 함수
- `observability.ingest_hermes_telemetry(...)`: n8n 공통 ingest dispatcher

## 2. n8n workflow

NAS n8n에서 `docker-compose/homelab/n8n/workflows/hermes-telemetry-ingest.json`을 import한다.

1. Header Auth credential을 생성한다.
   - Header name: `Authorization`
   - Header value: `Bearer <긴 임의 토큰>`
2. TimescaleDB credential을 생성한다.
   - Host: `timescaledb`
   - Port: `5432`
   - Database/User/Password: TimescaleDB app credential
3. import된 두 credential placeholder를 실제 credential로 교체한다.
4. workflow를 활성화한다.

Webhook의 `Respond`는 `When Last Node Finishes`로 설정한다. 그래야 TimescaleDB
수집 함수가 실패하면 collector에 성공 응답을 먼저 보내지 않는다.

운영 webhook URL은 일반적으로 다음 형태다.

```text
https://<n8n-host>/webhook/hermes-telemetry
```

Grafana에는 TimescaleDB app user를 재사용하지 말고 `observability` 전용 read-only role을 사용한다.

```sql
CREATE ROLE grafana_reader LOGIN PASSWORD '<별도 비밀번호>';
GRANT CONNECT ON DATABASE timeseries TO grafana_reader;
GRANT USAGE ON SCHEMA observability TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA observability TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA observability
  GRANT SELECT ON TABLES TO grafana_reader;
```

NAS의 Grafana는 별도 monitoring compose에 있으므로 datasource는 Grafana UI에서 NAS Docker network 기준으로 설정한다.

```text
Compose path: /volume3/Gullinkambi/infrastructure/docker-compose/monitoring
Datasource host: timescaledb:5432
Database: timeseries
Network: homelab_default
```

Grafana 패널 SQL 초안은 다음 파일에 있다.

```text
/volume3/Gullinkambi/hermes-kanban-panels.sql
```

## 3. Mac mini 설정

`docker-compose/agents/.env`에 다음 값을 넣는다.

```dotenv
HERMES_TELEMETRY_WEBHOOK_URL=https://<n8n-host>/webhook/hermes-telemetry
HERMES_TELEMETRY_WEBHOOK_TOKEN=<Header Auth와 동일한 토큰>
HERMES_TELEMETRY_INTERVAL_SECONDS=60
HERMES_TELEMETRY_INITIAL_BACKFILL_HOURS=24
HERMES_TELEMETRY_RESCAN_HOURS=168
HERMES_TELEMETRY_INCLUDE_KANBAN_TITLES=false
HERMES_TELEMETRY_COLLECT_ORCA_USAGE=true
# 쉼표로 구분한 host상의 Orca-managed worktree root
HERMES_TELEMETRY_ORCA_WORKTREE_ROOTS=/Users/example/orca,/Volumes/Example/orca
HERMES_TELEMETRY_EXCLUDED_PROFILES=default,analyzer,critic,devel,orchestrator,quick,reporter,researcher,writer
```

Antigravity는 대화 DB를 container에 마운트하지 않는다. Mac 사용자 권한으로 실행되는
`antigravity_orca_usage.py`가 ORCA worktree에 속한 대화에서 토큰 숫자·모델·날짜와 대화
제목만 추출해 JSON을 같은 파일에 덮어써 갱신한다. container가 이 파일 하나를 bind mount하므로
(rename으로 교체하면 container는 삭제된 inode를 본다) inode를 유지해야 한다. collector는 읽을 수 없거나
잘린 JSON을 그 주기만 건너뛴다. `dailyAggregates`는 일별 합계,
`threadAggregates`는 대화별 합계다. 프로젝트 이름은 collector와 같은 규칙으로
`orca/` 아래 `workspaces/<저장소>/<브랜치>`(예: `orca/kimdove/home/workspaces/<저장소>/…`)의 저장소 이름을 쓴다(`workspaces`가 없는 경우 `orca/<저장소>/…`의 저장소 이름을 사용). 대화 ID는 해시만 기록하고,
경로·프롬프트·응답은 기록하지 않는다. 다음 명령으로 LaunchAgent를 설치한다.

```bash
mkdir -p ~/Library/Logs/Gullinkambi \
  ~/Library/Application\ Support/Gullinkambi
cp docker-compose/agents/hermes-telemetry/antigravity_orca_usage.py \
  ~/Library/Application\ Support/Gullinkambi/
cp docker-compose/agents/hermes-telemetry/com.gullinkambi.antigravity-orca-usage.plist \
  ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.gullinkambi.antigravity-orca-usage 2>/dev/null || true
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.gullinkambi.antigravity-orca-usage.plist
launchctl kickstart -k gui/$(id -u)/com.gullinkambi.antigravity-orca-usage
```

plist는 외장 볼륨(SSD)의 대화 DB를 읽는 인터프리터로 Homebrew Python
(`/opt/homebrew/opt/python@3.14/bin/python3.14`)을 쓴다. 시스템 `/usr/bin/python3`를 LaunchAgent로 실행하면
macOS가 접근을 막아 SQLite가 `authorization denied`로 실패한다. 출력은 collector가 mount하는
`/Volumes/Example/app-data/orca/app-data/orca-antigravity-usage.json`이다.

LaunchAgent 실행본은 macOS가 외장 볼륨의 저장소 스크립트 접근을 차단하지 않도록
사용자 `Application Support` 아래에 복사한다. 스크립트를 갱신한 경우 plist뿐 아니라
이 실행본도 다시 복사한 뒤 LaunchAgent를 재시작한다.

Codex 쓰레드 이름도 같은 방식으로 Mac에서 준비한다. Codex 상태 DB(`~/.codex/state_5.sqlite`)는
WAL 모드라 Codex가 `-wal`/`-shm` 파일을 지웠다 다시 만든다. 이 파일들을 하나씩 bind mount하면
container 안에서 파일이 사라져 `cannot read Codex thread names: unable to open database file`이
반복되고 쓰레드 이름이 비게 된다. `codex_thread_names_snapshot.py`가 `threads` 테이블의
`id`, `name`, `title`만 rollback-journal SQLite로 복사해 디렉터리 안에서 원자적으로 교체하고,
collector는 그 디렉터리를 읽기 전용으로 마운트한다. Codex 상태 DB의 다른 테이블은 container에
노출하지 않는다.

```bash
cp docker-compose/agents/hermes-telemetry/codex_thread_names_snapshot.py \
  ~/Library/Application\ Support/Gullinkambi/
cp docker-compose/agents/hermes-telemetry/com.gullinkambi.codex-thread-names.plist \
  ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.gullinkambi.codex-thread-names 2>/dev/null || true
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.gullinkambi.codex-thread-names.plist
```

스냅샷은 `~/Library/Application Support/Gullinkambi/codex-state/threads.sqlite`에 60초마다 갱신된다.

Compose는 다음 usage 파일과 세션 디렉터리를 collector에 읽기 전용으로 마운트한다.
Orca 계정과 인증 파일은 collector에 노출하지 않는다. 세션 로그는 로컬 collector가
token usage 레코드만 streaming parse하며 전송 payload에는 본문과 경로가 포함되지
않는다. Codex 쓰레드 이름은 `id`, `name`, `title`만 담은 스냅샷에서 조회한다.

```text
~/Library/Application Support/orca/orca-codex-usage.json
~/Library/Application Support/orca/orca-claude-usage.json
~/Library/Application Support/orca/orca-antigravity-usage.json
~/.codex/sessions/
~/Library/Application Support/Gullinkambi/codex-state/  (threads 스냅샷)
~/.claude/projects/
```

실제 전송 전에 privacy-safe payload를 확인한다.

```bash
docker compose -f docker-compose/agents/docker-compose.yml \
  run --rm hermes-telemetry --dry-run
```

collector를 시작한다.

```bash
docker compose -f docker-compose/agents/docker-compose.yml \
  up -d --build hermes-telemetry
```

상태와 로그를 확인한다.

```bash
docker compose -f docker-compose/agents/docker-compose.yml ps hermes-telemetry
docker compose -f docker-compose/agents/docker-compose.yml logs --tail 100 hermes-telemetry
```

## 증분·재시도 동작

- 첫 실행은 기본 24시간을 backfill한다.
- 이후 최근 7일과 이전에 추적하던 open session을 다시 확인한다.
- session 필드와 도구 통계의 SHA-256 fingerprint가 바뀐 경우만 전송한다.
- Kanban은 `updated_at` 컬럼에 의존하지 않고, 각 board의 task/run/event/link row에서 개인정보를 제거한 fingerprint를 계산한다.
- Orca 일별 집계는 `agent + date + model + project hash`로 식별하고 값이 바뀐 경우만 다시 전송한다.
- Orca 쓰레드별 집계는 `agent + thread ID + date + model + project hash`로 식별하고 원본 세션 로그 전체를 파일별 캐시로 추적한다.
- 최근 raw Codex/Claude session 파일은 mtime/size와 개인정보가 제거된 파일별 집계만 cursor에 캐시하므로, 변경되지 않은 대화 로그를 매 분 다시 읽지 않는다.
- Antigravity 대화 DB는 Mac 로컬 변환기만 읽고, telemetry container와 NAS에는 집계 JSON만 전달한다.
  대화별 집계는 해시된 쓰레드 ID와 대화 제목으로 `orca_thread_usage`에 전송한다.
- n8n 전송이 성공해야 cursor를 저장한다.
- 네트워크 실패 시 cursor를 갱신하지 않아 다음 주기에 다시 보낸다.
- Orca 사용량 수집 revision(`ORCA_USAGE_REVISION`, `ORCA_THREAD_USAGE_REVISION`)이 바뀌면 `cursor.json`의 수집 캐시(`orca_usage`, `orca_thread_usage`, 원본 세션 파일 캐시)를 초기화하여 전체 기간의 원본 세션 로그와 일별 집계를 처음부터 다시 스캔·전송(replay)한다.
  - 전송 데이터의 고유 식별자(`event_id`)는 작업 경로 해시·세션 ID·날짜·모델 기반으로 생성되므로, 프로젝트 라벨 추출 규칙이 변경되더라도 기존 레코드와 동일한 `event_id`를 유지한다.
  - TimescaleDB 적재 함수(`ingest_orca_usage`, `ingest_orca_thread_usage`)는 `ON CONFLICT (event_id) DO UPDATE` 절을 통해 라벨과 fingerprint가 변경된 기존 행을 새 프로젝트 이름으로 덮어써 보정한다.
  - 단, 원본 로그 파일이 이미 로테이션되거나 삭제된 과거 날짜의 행은 재스캔 대상에서 제외되므로 기존 DB의 라벨이 자동 갱신되지 않고 남아있을 수 있다(수동 SQL 보정 필요).

cursor는 `hermes_telemetry_state` Docker volume의 `/var/lib/hermes-telemetry/cursor.json`에 저장된다.

Telemetry schema v3 배포 순서는 TimescaleDB 초기화 스크립트 재실행, n8n workflow
재-import, collector 재빌드 순서다. 갱신된 n8n workflow는 rollout 중 기존 collector를
유지할 수 있도록 schema v2와 v3를 모두 받지만, 기존 workflow는 v3를 거부하므로
collector를 먼저 올리지 않는다.

## ORCA 호출 단위 이벤트 (`orca_call_events`)

Codex/Claude 세션 로그에서 호출 한 건당 토큰 메타데이터(해시된 ID, 모델, 토큰 수)만 추출해
`observability.orca_call_events`에 적재한다. 프롬프트·응답·경로·도구 인자는 보내지 않는다.
기본값은 꺼짐(`HERMES_TELEMETRY_COLLECT_ORCA_CALLS=false`)이다.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `HERMES_TELEMETRY_COLLECT_ORCA_CALLS` | `false` | 호출 단위 수집 켜기 |
| `HERMES_TELEMETRY_ORCA_ORIGIN_NAMESPACE` | `orca-local` | 이벤트 ID에 들어가는 출처 구분자. 바꾸면 같은 호출이 새 이벤트로 중복 적재되므로 고정한다 |
| `HERMES_TELEMETRY_ORCA_CALL_LOOKBACK_HOURS` | `48` | 이 시간 안에 수정된 로그만 읽는다 |
| `HERMES_TELEMETRY_ORCA_CALL_BATCH_LIMIT` | `500` | 한 주기에 보낼 최대 이벤트 수. 나머지는 다음 주기에 이어서 보낸다 |

`schema_version`은 3 그대로이고 새 배열만 추가된다. 호출은 변경분만(이벤트별 지문) 전송하고,
전송이 모두 성공한 뒤에만 상태에 기록한다.

**배포 순서(반드시 지킨다):**

1. TimescaleDB: `migrations/001`, `005`를 적용한다(디스패처가 `orca_call_event`를 받는다).
2. n8n: `homelab/n8n/workflows/hermes-telemetry-ingest.json`의 Code 노드에 `orca_call_events` 배열을 추가한다.
3. collector 이미지를 다시 빌드하고(`orca_calls.py` 포함) 마지막에 플래그를 켠다.

2번 전에 플래그를 켜면 기존 workflow가 알 수 없는 배열을 무시하고 성공을 돌려주므로 collector는 전송 완료로
기록하고, 그 이벤트는 다시 보내지 않는다. 이 경우 collector를 멈추고 상태 파일의 `orca_calls`,
`orca_call_files` 키를 삭제한 뒤 다시 시작하면 조회 범위(48시간) 안의 호출을 다시 보낸다(DB가 중복을 걸러낸다).
끄려면 플래그를 `false`로 되돌린다. 이미 적재된 행은 `TRUNCATE observability.orca_call_events`로 지운다.

## ORCA 작업 단위 표시 (`orca_task.py`)

호출을 "작업/시도(attempt)" 단위로 묶으려면 시작과 끝을 표시한다. 같은 세션에서 `begin`~`end` 사이에 일어난
호출이 그 attempt로 귀속되고, 대시보드의 `작업/시도별 사용량·결과` 표와 MLflow Run에 반영된다.

```bash
# 작업 디렉터리에서, 가장 최근 Claude/Codex 세션을 자동으로 잡는다 (15분 안에 활동한 세션만)
python3 orca_task.py begin --task 2026-10-10-orca-panel --auto-session
# ... 작업 ...
python3 orca_task.py end --task 2026-10-10-orca-panel --status completed \
  --test-id unit --exit-code 0 --passed 108 --failed 0
```

- 세션을 직접 지정하려면 `--session <ID>`. 세션 ID는 로컬에서 해시되어 원본이 웹훅·DB에 남지 않는다.
- `--attempt a2`처럼 시도를 나눌 수 있다(기본 `a1`). `end`를 다시 실행하면 새 revision으로 기록된다.
- `begin` 후 `end`를 잊으면 창이 24시간 뒤 자동으로 닫힌다(그 이후 호출은 귀속되지 않는다).
- 웹훅 주소·토큰은 환경변수 `HERMES_TELEMETRY_WEBHOOK_URL`/`_TOKEN` 또는 `--env-file agents/.env`로 읽는다.
  `--dry-run`은 보낼 내용을 출력만 한다.
- `end`의 상태는 에이전트가 **보고한** 값(`verified` 아님)이다. 검증됨(`evidence_verified`)은 NAS의
  `ORCA_APPROVED_VERIFIERS`에 등록된 verifier가 실제 증거와 함께 보낸 경우에만 인정한다.

전달 경로: `orca_task.py` → 웹훅 → n8n(`orca_task_manifests`, `orca_task_results` 배열) → DB 테이블
(migration 006) → NAS의 `orca-exporter`가 `ORCA_PIPELINE_FROM_DB=1`일 때 귀속·snapshot·MLflow 발행.
배포 순서는 migration 006 → n8n Code 노드 → exporter 이미지 순이다(n8n이 먼저 바뀌어야 CLI가 보낸 내용이 유실되지 않는다).

## 검증된 결과 (`orca_verify.py`)

`orca_task.py end`의 상태는 에이전트가 **보고한** 값이라 `unverified`로 기록된다. `evidence_verified`는 verifier가
테스트를 **직접 실행한** 결과에 서명을 붙여 보냈을 때만 인정된다.

```bash
python3 orca_verify.py --task 2026-10-10-orca-panel --test-id unit -- python3 -m unittest discover -s tests
```

- 명령은 이 도구가 실행하므로 종료 코드는 도구가 정한다. 명령의 종료 코드가 0이 아니면 이 도구도 1로 끝난다.
- 서명: HMAC-SHA256. 키는 환경변수 `ORCA_VERIFIER_KEY` 또는 `~/.config/orca-task/verifier.key`(0600).
  NAS의 `orca-exporter`에도 같은 `ORCA_VERIFIER_KEY`와 `ORCA_APPROVED_VERIFIERS=orca-verifier`가 있어야 한다.
  키가 설정되면 서명이 없거나 틀린 결과는 `verifier` 이름을 써도 `unverified`로 내려간다.
- 전송되는 것: 종료 코드, 테스트 개수(unittest·pytest 요약을 인식할 때), 출력의 sha256. **명령줄과 출력은 보내지 않는다.**
  출력 전체는 `~/.local/state/orca-task/evidence/`에 0600으로 남아 체크섬을 나중에 대조할 수 있다.
- 에이전트의 보고와 verifier의 증거는 별개로 취합된다. `end`를 검증 뒤에 다시 실행해도 검증은 유지된다.
- 한계: 키는 같은 사용자 계정에 있는 비밀이라, "에이전트가 그렇다고 말함"과 "verifier가 실행함"을 구분할 뿐
  키 파일을 읽을 수 있는 악의적 프로세스까지 막지는 못한다.

## Grafana 시작 쿼리

프로필별 완료·실패 수:

```sql
SELECT
  time_bucket('$__interval', ended_at) AS time,
  profile,
  count(*) FILTER (WHERE status = 'completed') AS completed,
  count(*) FILTER (WHERE status = 'failed') AS failed
FROM observability.hermes_runs
WHERE $__timeFilter(ended_at)
  AND status IN ('completed', 'failed')
GROUP BY 1, 2
ORDER BY 1;
```

프로필별 p95 처리 시간:

```sql
SELECT
  time_bucket('$__interval', ended_at) AS time,
  profile,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) / 1000.0 AS p95_seconds
FROM observability.hermes_runs
WHERE $__timeFilter(ended_at)
  AND status IN ('completed', 'failed')
GROUP BY 1, 2
ORDER BY 1;
```

Kanban 상태별 task 수:

```sql
SELECT
  board,
  status,
  count(*) AS tasks
FROM observability.hermes_kanban_tasks
WHERE $__timeFilter(created_at)
GROUP BY board, status
ORDER BY board, status;
```

실행 중이거나 막힌 Kanban task:

```sql
SELECT
  COALESCE(last_heartbeat_at, started_at, created_at) AS time,
  board,
  task_id,
  assignee,
  status,
  latest_run_status,
  latest_run_outcome,
  consecutive_failures,
  block_kind,
  run_count
FROM observability.hermes_kanban_tasks
WHERE status IN ('running', 'blocked', 'triage')
ORDER BY time DESC
LIMIT 100;
```

Kanban worker run 결과 추이:

```sql
SELECT
  time_bucket('$__interval', COALESCE(ended_at, started_at)) AS time,
  board,
  status,
  count(*) AS runs
FROM observability.hermes_kanban_task_runs
WHERE $__timeFilter(COALESCE(ended_at, started_at))
GROUP BY 1, board, status
ORDER BY 1, board, status;
```

Orca 모델별 token 사용량:

```sql
SELECT
  agent,
  model,
  sum(request_count) AS requests,
  sum(total_tokens) AS total_tokens
FROM observability.orca_usage_daily
WHERE $__timeFilter(usage_date::timestamptz)
GROUP BY agent, model
ORDER BY total_tokens DESC;
```
