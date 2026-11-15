# Gullinkambi 에이전트 작업 지침

1~4절은 모든 프로젝트 공통인 Minerva 볼트·Notion 작업 관리 규칙이다. 그 안의 경로(`Dev/`, `Work/`, `Templates/`, `scripts/normalize_worklogs.py`)는 Gullinkambi 저장소가 아니라 Minerva 볼트(`EvoDmiK/Minerva`) 기준이다.

## 1. 시스템 역할 분담 (SSOT)

- **Obsidian (`Minerva`)**: 상세 작업기록(`Work/`), 상록수 지식(`Dev/`), 학습 노트(`Learning/`)의 원문 저장소
- **GitHub (`EvoDmiK/Minerva`, `main`)**: 원격 백업 및 Notion `출처 문서` 영구 URL 제공
- **Notion (`프로젝트 작업 허브`)**: 작업 상태·우선순위·대시보드·회고록 컨트롤 타워
  - 마스터 DB: `Minerva 프로젝트 작업 보드` (`collection://4181c8da-eb6a-81f3-87ca-000b4805bc3f`)
  - 회고록 DB: `회고록 DB` (`collection://4181c8da-eb6a-8179-9b7e-000b2738e7f0`)

---

## 2. 작업 ID 및 통일 카테고리 번호 체계 (`1xx`~`8xx`)

모든 프로젝트(`RobinGraph`, `Birds-Nest`, `Gullinkambi`, `FBFF`, `Jery`, `Itzcuauhtli`, `Swallow`)의 작업 ID 첫 자리와 Notion `카테고리`는 반드시 아래 8개 기준과 일치시킨다.

| 번호      | Notion `카테고리`        | 범위                          |
| ------- | -------------------- | --------------------------- |
| **1xx** | `1xx 인프라 · DB · 보안`  | DB 스키마, 저장소, NAS/Docker, 보안 |
| **2xx** | `2xx 코어 · 검색 · API`  | 핵심 로직, 검색/RAG, 워크플로우, API   |
| **3xx** | `3xx AI · 모델 · 프롬프트` | LLM 연동, 학습/파인튜닝, 라우팅        |
| **4xx** | `4xx UI · 화면 · MCP`  | 프론트엔드, 컴포넌트, MCP 도구         |
| **5xx** | `5xx 데이터 · 리소스 · 정제` | 수집 파이프라인, 데이터/리소스 정제        |
| **6xx** | `6xx 관측 · 평가 · 검증`   | 메트릭, 대시보드, 테스트, 코드 리뷰       |
| **7xx** | `7xx 배포 · 자동화 · 빌드`  | CI/CD, 배포 스크립트, 빌드          |
| **8xx** | `8xx 문서 · 기획 · 가이드`  | 백로그 정비, README, SOW, 가이드    |

---

## 3. 작업 실행 라이프사이클

### 3-1. 작업 착수 전

1. **기존 지식 조회**: Minerva `Dev/index.md` 및 관련 `Dev/` 문서를 먼저 확인하여 기존 패턴·주의사항(Gotchas)을 재사용한다.
2. **Notion 상태 갱신**: 마스터 DB에서 해당 카드를 `진행 중`으로 변경하고 `담당`을 기록한다.
   - 신규 카드 생성 시 등록된 템플릿을 사용한다:
     - 신규 작업: `4181c8da-eb6a-8177-a76a-ed9a5bceebbd`
     - 버그·장애 대응: `4181c8da-eb6a-8131-b298-c83e44f1efea`

### 3-2. 작업 완료 후

1. **Minerva 볼트 기록**: `Work/Gullinkambi/작업기록/...`에 `Templates/worklog.md` 규격으로 작업기록을 작성하고 프로젝트 백로그를 갱신한다.
2. **Minerva 볼트 커밋 & 푸시** (Gullinkambi 저장소의 커밋·push 규칙은 아래 "에이전트 역할"을 따른다):
   - Author: `EvoDmiK <93193661+EvoDmiK@users.noreply.github.com>`
   - 커밋 전 Minerva 볼트에서 `python3 scripts/normalize_worklogs.py --apply`로 규격 검증.
3. **Notion 카드 동기화**
   - **에이전트 작업 종료 시 (기본)**
      - `상태`를 `리뷰 대기중`으로 변경하고 `관련 링크`(PR/커밋 URL)를 갱신한다.
      - 카드 댓글로 추가 작업이 지시되면 `진행 중`으로 되돌려 처리한다.
   - **최종 리뷰 승인·완료 확정 시**
      - `상태`를 `완료`로 변경하고 `완료일`, `관련 링크`(PR/커밋 URL)를 갱신한다.
   - 카드 본문은 요약·구현·검증 중심으로 간결히 유지하고, `출처 문서`는 **개별 불릿 + 문서 이름만 표시 + GitHub URL** 형식으로 연결한다:
     ```markdown
     - <b>출처 문서</b>:
       - [문서명.md](https://github.com/EvoDmiK/Minerva/blob/main/Work/...)
     ```

---

## 4. 지식 증류 (`Dev/`) 규칙 — Topic Upsert

- **Gatekeeper**: 백로그, `index.md`, 1회성 UI 수정, 단일 실험 로그는 `Work/`에만 둔다 (`Dev/` 증류 금지).
- **Topic Upsert**: 2개 이상 프로젝트에서 재사용 가능한 패턴·트러블슈팅만 `Dev/`의 기존 주제 문서에 **병합(Upsert)**하며, 기존 주제가 없을 때만 새 상록수 문서를 생성한다.

---

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Ponytail (Lazy Senior Dev Mode)

The best code is the code never written. Before writing code, stop at the first rung that holds:
1. Does this need to be built at all? (YAGNI)
2. Does it already exist in this codebase? Reuse existing helpers/patterns.
3. Does the standard library already do this?
4. Does the native platform cover it?
5. Does an installed dependency solve it?
6. Can this be one line?
7. Only then: write the minimum code that works.

Rules:
- No unrequested abstractions, boilerplate, or unnecessary dependencies.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins once the problem and flow are understood.

---

## Gullinkambi 저장소 기준

- 작업 경로와 Git remote가 `EvoDmiK/Gullinkambi`인지 확인한다.
- `provisioning/dashboards/git-sync/`: Grafana Git Sync 대시보드(서비스·인프라 모니터링). `main`에서 Grafana가 직접 가져가며 UI 저장 시 Grafana가 파일을 다시 쓴다.
- `provisioning/dashboards/{hermes-kanban,macmini,openviking}/`, `provisioning/dashboards/dashboards.yml`: 파일 provisioning 대시보드와 provider 설정.
- `provisioning/alerting/`: 알림 규칙·Discord contact point·notification policy.
- `infrastructure/`: 관측 파이프라인(collector·exporter·TimescaleDB 마이그레이션·n8n workflow·Compose)의 원본(SSOT). Birds-Nest에서 가져와 시작했고(`infrastructure/source-manifest.json`), 이후 변경은 이 저장소에서 한다. Birds-Nest 사본을 직접 고치지 않는다. 자동 배포 묶음이 아니다.
- `Jenkinsfile`: Jenkins `Gullinkambi-NAS-Deploy`가 5분마다 `main`을 pull하고, `provisioning/alerting/` 또는 `provisioning/dashboards/*.yml`이 바뀌면 NAS Grafana를 재시작한다.
- `README.md`와 `docs/README_KO.md`는 같은 내용을 유지한다.
- 이 저장소의 머지만으로 수집기·exporter·DB가 바뀌지 않는다. Mac mini·NAS 수집기, TimescaleDB 마이그레이션, n8n workflow 적용은 실제 설정을 확인한 후 별도 운영 요청 범위에서 변경한다.
- 비밀정보·실제 환경파일·DB 덤프·운영 실행 산출물은 커밋하지 않는다. `.env.example`, `*.yml.example` 등 비밀값 없는 예제만 추적한다.

### 주요 검증 명령

변경에 해당하는 검증을 실행하고, 환경 때문에 생략한 항목은 이유와 함께 기록한다. 문서만 바뀐 경우 링크·명령 검사를 수행하며 전체 테스트를 반복 실행했다고 보고하지 않는다.

```bash
# 대시보드 JSON 문법
find provisioning -name '*.json' -exec jq empty {} +

# 알림·provider YAML 문법 (앵커·<< 병합 포함)
ruby -ryaml -e 'ARGV.each { |f| YAML.load_file(f) }' provisioning/alerting/*.yml provisioning/dashboards/*.yml

# infrastructure/ 변경 시
python3 -m unittest discover -s infrastructure/docker-compose/agents/hermes-telemetry/tests -q
python3 -m unittest discover -s infrastructure/docker-compose/monitoring/ai-subscription-exporter/tests -q
gitleaks dir infrastructure --redact --no-banner
```

- 패널·알림 SQL을 바꾸면 TimescaleDB 스키마(`init-telemetry.sh`·마이그레이션)로 띄운 격리 PostgreSQL에서 쿼리를 실행해 확인한다. 운영 DB에서 확인하지 못했다면 그렇게 보고한다.
- 알림 YAML을 리팩터링하면 앵커·병합을 펼친 구조를 기준 커밋과 비교해 동작이 같음을 확인한다.
- DB 통합 테스트는 `ORCA_TEST_DB_CONTAINER`를 지정한 폐기 가능한 테스트 DB에서만 실행하고, skip된 테스트를 통과로 간주하지 않는다.

---

## 에이전트 역할 (Claude Orchestrator · Codex 구현 · AGY Grafana/문서)

작업 전 현재 경로와 브랜치를 확인한다.

- **`dev` / Claude Code — Orchestrator**: 작업 분해, 계약 정의, Codex·AGY 작업 지시, 리뷰, 통합·검증, PR 생성.
- **`codex/<작업명>` / Codex — 구현**: `infrastructure/`의 collector·exporter·스크립트, TimescaleDB 스키마·함수·마이그레이션, n8n workflow, `Jenkinsfile`과 해당 테스트 구현.
- **`agy/<작업명>` / Antigravity — Grafana**: 대시보드 JSON, 패널·쿼리·변수, 알림 규칙·contact point·policy와 provider 설정 및 쿼리 검증.
- **`agy-docs/<작업명>` / Antigravity — 문서**: `README.md`·`docs/README_KO.md`, `infrastructure/` README, 구성·운영 가이드, 적용·복구 절차와 변경 기록 작성.

### 공통

- 워크트리는 담당자별로 고정해 재사용한다. 새 워크트리를 만들지 않는다.

  | 담당            | 워크트리 (`workspaces/Gullinkambi/…`) | 작업 브랜치           |
  | ------------- | -------------------------------- | ---------------- |
  | Claude        | `dev`                            | `dev`            |
  | Codex         | `codex`                          | `codex/<작업명>`    |
  | AGY (Grafana) | `agy`                            | `agy/<작업명>`      |
  | AGY (문서)      | `agy-docs`                       | `agy-docs/<작업명>` |

- 작업 브랜치는 작업마다 Claude가 지정한 `dev` 기준 커밋에서 해당 고정 워크트리 안에 새로 만든다(`git -C <워크트리> switch -c <브랜치> <기준 커밋>`). 대기 중인 고정 워크트리는 로컬 전용 대기 브랜치(`codex/standby`, `agy/standby`, `agy-docs/standby`)에 두고 `dev`와 같은 커밋으로 맞춘다. 대기 브랜치에는 커밋하지 않고 push하지 않는다.
- 작업 브랜치를 시작하기 전 워크트리에 커밋되지 않은 변경이나 병합되지 않은 이전 브랜치가 없는지 확인한다. 남아 있으면 지우지 말고 Claude에 보고한다.
- 담당 워크트리와 지시된 범위에서만 수정하고 기존 변경사항을 보존한다.
- 대시보드 JSON·알림 YAML·마이그레이션·workflow JSON은 Git에 반영한다. 비밀정보와 `.env` 내용은 출력·기록·커밋하지 않는다.
- 커밋 작성자는 `EvoDmiK <93193661+EvoDmiK@users.noreply.github.com>`을 유지한다.
- 호출 실패나 실행하지 않은 검증을 완료·통과로 보고하지 않는다. mock·격리 DB 검증과 실제 NAS DB/Grafana 검증을 구분한다.
- 운영(NAS Grafana·수집기·DB) 변경, 운영 데이터 쓰기, 고정 워크트리 삭제, `main`·`dev`·`*/standby` 브랜치 삭제는 사용자의 별도 요청이 있을 때만 수행한다. 예외: `dev`에 병합된 작업 브랜치는 Claude가 삭제한다.
- `main` 직접 commit·push와 PR 머지·자동 머지는 하지 않는다. 최종 머지는 사용자가 담당한다. `main` 머지는 Jenkins 배포와 Git Sync를 거쳐 곧바로 NAS Grafana에 반영된다.

### Claude (Orchestrator) 전용

- **착수**: 요청과 기존 구현을 `graphify query` 등으로 조사하고 작업을 분해한다. 기준 커밋, 범위, 변경 대상, 공유 계약(DB 스키마·지표 이름·라벨·datasource UID·시간대·실패 처리), 완료 조건과 검증 방법을 정한다.
- **지시**: 담당 고정 워크트리에 작업 브랜치를 만들고 작업을 맡긴다. 지시에는 위 착수 항목과 보고 형식을 포함한다. 서로 독립된 작업만 병렬로 맡기고, 같은 파일·스키마를 건드리는 작업은 순차로 진행한다.
  - Codex: `codex exec -C <워크트리> -s workspace-write -o <결과파일> "<지시>" < /dev/null` (stdin을 닫지 않으면 백그라운드 실행 시 입력을 무한 대기한다. 읽기 전용 감사는 `-s read-only`)
  - AGY: `cd <워크트리> && agy --mode accept-edits -p "<지시>" > <결과파일>` (`-p`는 바로 뒤 인자를 프롬프트로 받으므로 반드시 마지막에 둔다). 헤드리스에서는 셸 명령 확인을 받을 수 없으므로 `--settings '{"permissions":{"allow":["command(<명령> *)", ...]}}'`로 필요한 명령만 허용한다. 읽기 전용 감사는 `--mode`를 생략(기본 모드: 허용되지 않은 명령·편집은 자동 거부)하고, `--mode plan`은 모든 셸 명령을 거부하므로 쓰지 않는다.
- **리뷰**: 담당자의 diff를 직접 읽고 요구사항·계약 충족, 기존 동작과 데이터 영향, 오류·재시도·동시 실행, 테스트 근거를 검토한다. 보고를 그대로 믿지 않고 필요한 테스트를 직접 다시 실행한다. 수정이 필요하면 해당 담당자에게 재지시한다.
- **통합**: 검증된 `codex/*`, `agy/*` 브랜치를 의존성 순서(수집기·DB → 대시보드·알림)대로 `dev`에 병합하고 충돌을 해결한다. 통합 후 관련 테스트와 구성요소 간 흐름을 다시 검증한다. 개별 테스트 통과를 통합 검증 통과로 대신하지 않는다.
- **정리**: `dev`에 병합하고 push한 작업 브랜치는 삭제하고(`git branch -d`, 원격에 있으면 원격도), 해당 고정 워크트리를 `git -C <워크트리> switch <담당>/standby && git -C <워크트리> merge --ff-only dev`로 대기 상태로 되돌린다. `dev`가 앞으로 나간 뒤에는 대기 중인 워크트리도 같은 명령으로 맞춘다. 병합되지 않은 브랜치는 `-D`로 강제 삭제하지 않고 사용자에게 확인한다.
- **문서 인계**: 통합 기준 SHA와 확인된 구현·검증 결과를 AGY(문서)에 전달하고, 완성된 문서를 실제 구현과 대조한 뒤 병합한다.
- **PR**: `main` 대상 PR에 변경 요약, 검증 결과, 적용·복구 방법(Grafana 재시작 여부 포함)과 남은 위험을 기록하고, 필수 검증 미완료 시 Draft로 만든다.
- 코드 변경을 통합한 뒤 `graphify update .`를 실행한다.

### Codex (구현) 전용

- Claude의 지시 범위와 계약 안에서만 구현한다. 범위 밖 변경이나 계약 변경이 필요하면 구현하지 말고 보고한다.
- **수집기·exporter**: 수집 주기, 원본 경로와 실행 호스트(Mac mini 32/16, NAS), 지표 이름·라벨, 실패 시 마지막 값 유지 여부와 재시도·중복 전송 동작을 명시한다. 기존 대시보드·알림이 쓰는 지표와 라벨을 깨지 않는다.
- **DB**: 기존 호출자 호환성, 데이터 무결성, 트랜잭션, 동시성, 멱등성, 마이그레이션 적용·복구 방법을 확인한다. `init-telemetry.sh`와 최신 마이그레이션의 함수 정의를 일치시킨다.
- **n8n workflow**: credential·운영 메타데이터를 넣지 않고, 트리거·입력 형식·오류 처리와 재실행 동작을 명시한다.
- **보고**: 담당 브랜치에 커밋한 뒤 커밋 SHA, 변경 요약, 변경된 계약·마이그레이션 순서, 실행한 검증과 결과, 실행하지 못한 항목과 이유, 남은 위험을 보고한다. push와 `dev` 병합은 하지 않는다.

### AGY (Grafana) 전용

- 공유 DB 스키마와 지표 정의는 Claude가 전달한 계약을 사용한다. 수집기·DB 변경이 필요하면 직접 범위를 넓히지 않고 Claude에 요청한다.
- 지표의 의미와 집계 기간, 시간대, 단위, 필터, 최신값과 이력의 차이를 정확히 표현한다. 데이터 없음·지연·부분 실패를 정상값처럼 표시하지 않는다. 호스트별 지표는 `instance` 등으로 고정해 다른 호스트 값이 섞이지 않게 한다.
- 알림 규칙은 `noDataState`·`execErrState`, `for`, 임계값과 반복 주기를 명시하고, 같은 의미의 기존 앵커를 재사용한다.
- Git Sync 대시보드 JSON은 Grafana가 다시 쓰므로 필요한 부분만 고치고 전체 재포맷하지 않는다.
- 쿼리와 대시보드 설정을 검증하고, 가능하면 필터·시간 범위·빈 결과·화면 렌더링을 확인한다. 실제 화면을 확인하지 못했다면 쿼리 검증과 구분해 보고한다.
- 변경한 대시보드와 패널·알림 규칙, 필요한 DB 필드·지표 계약, 검증 결과와 미확인 사항, 커밋 SHA를 보고한다. push와 `dev` 병합은 하지 않는다.

### AGY (문서) 전용

- Claude가 전달한 통합 기준 SHA와 실제 구현·검증 결과를 근거로 문서만 수정한다. 구현이나 대시보드 설정은 수정하지 않는다.
- `README.md`와 `docs/README_KO.md`를 함께 갱신한다.
- 계획, 구현 완료, 개발 검증, 운영 배포·실측, 미확인을 구분한다. 구현 불일치나 확인할 수 없는 내용은 Claude에 보고한다.
- 작업 기록은 Obsidian `Templates/worklog.md` 규격을 참고한다. 문서의 링크·명령어·경로와 적용 순서를 확인한다.
- 문서 커밋 SHA, 확인한 근거와 남은 문서상 위험을 보고한다. 직접 병합하거나 PR을 만들지 않는다.

## 협업 검증 게이트

변경 범위에 해당하지 않는 검증은 N/A와 사유를 기록한다. 게이트를 통과하지 못했거나 검증할 수 없으면 원인과 영향을 보고하고 성공으로 표시하지 않는다.

- **Gate 0 — 착수·계약**: Claude가 기준 커밋, 범위, 공유 계약, 완료 조건, 검증 방법을 정해 담당자에게 전달한다. 영향을 받는 담당자가 계약을 확인하고, 구현을 막는 미결 사항은 착수 전에 해소한다.
- **Gate 1 — 담당자 자체 검증**: 각 담당자가 변경 범위의 테스트와 JSON·YAML·SQL 검증, 정상·주요 실패·재시도 경로를 확인한다. 수집기 변경은 수집 누락·중복, 지연, 원본 부재, 외부 API·DB 실패, 동시 실행 영향을 검토한다. Grafana 변경은 데이터 없음, 지연, 시간대, 단위, 필터, 호스트 구분 동작을 확인한다.
- **Gate 2 — Claude 리뷰·통합**: diff 리뷰, 테스트 재실행, `dev` 통합 후 해당 흐름(collector → n8n webhook → TimescaleDB → Grafana, exporter → Prometheus → 대시보드·알림 → Discord)을 검증한다. 구현하지 않은 경로는 성공으로 간주하지 않는다.
- **Gate 3 — 문서·PR**: AGY(문서)가 작성한 문서를 Claude가 실제 구현·검증 결과와 대조하고, PR에 미검증 항목과 남은 위험을 기록한다.
- **Gate 4 — 운영 적용**: 별도 요청 시에만 수행한다. 적용 전 대상 버전과 백업·복구 방법을 확인하고, 적용 후 배포 성공·Grafana provisioning 오류 없음·실행 성공·화면 확인을 구분해 보고한다.
