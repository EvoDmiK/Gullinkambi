# Gullinkambi 관측 인프라

관측 파이프라인 구현·설정의 원본(SSOT) 디렉터리입니다. Birds-Nest에서 공개 가능한 파일을 가져와 시작했으며, 이후 변경은 이 저장소에서 하고 Birds-Nest 쪽 사본은 더 이상 원본으로 취급하지 않습니다. 이 디렉터리 추가만으로 서비스 재시작·워크플로우 활성화·DB 변경은 수행하지 않습니다. 기존 Gullinkambi `Jenkinsfile`도 수정하지 않았습니다.

최초 가져온 출처는 [source-manifest.json](source-manifest.json)의 저장소·기준 커밋·파일 목록에 기록되어 있습니다. 두 저장소는 자동으로 동기화되지 않으므로, Birds-Nest에 남은 사본을 고쳐야 할 일이 생기면 이 저장소를 먼저 고친 뒤 옮깁니다.

## 포함 범위

| 경로 | 내용 |
| --- | --- |
| `docker-compose/agents/hermes-telemetry/` | Hermes·Kanban·Orca collector, 호출·작업 분석 도구, MLflow exporter, Dockerfile, 합성 예시·JSON schema, 기존 테스트 |
| `docker-compose/agents/alloy/` | Mac mini Docker·Hermes 로그 수집 및 비밀정보 마스킹 설정 |
| `docker-compose/agents/compose.observability.reference.yml` | 기존 agents Compose에서 Alloy·telemetry 서비스만 발췌한 참조본 |
| `docker-compose/homelab/timescaledb/` | DB 초기화 스크립트와 `001`~`011` 마이그레이션 |
| `docker-compose/homelab/n8n/workflows/hermes-telemetry-ingest.json` | credential 연결과 운영 메타데이터를 제거하고 비활성화한 import용 workflow |
| `docker-compose/homelab/mlflow/` | 기존 MLflow Dockerfile·실행 스크립트; 실행하면 컨테이너를 교체하므로 검토 후 사용 |
| `docker-compose/homelab/compose.observability.reference.yml` | TimescaleDB·MLflow·Orca exporter 서비스 참조본 |
| `docker-compose/monitoring/` | NAS/Mac mini 모니터링 Compose, Prometheus 설정, AI 구독·macOS 메모리·NPM·OpenViking exporter |
| `docker-data/monitoring/` | NAS Prometheus·Loki·Alloy·Tailscale 정적 설정과 Grafana datasource 예시; 런타임 데이터가 아님 |

관측 대상 서비스 자체, 다른 프로젝트의 n8n 업무 workflow와 Swallow 대시보드는 복사하지 않았습니다. n8n 실행 통계 패널은 기존 n8n DB의 `execution_entity`를 읽으므로 별도의 전체 n8n 설치본을 포함하지 않습니다. n8n Error Observer 이벤트를 생성하는 workflow는 이번 소스에서 찾지 못했으며, 해당 패널에는 기존 별도 수집 경로가 필요합니다.

## 비밀정보와 예시값

- 실제 `.env`, 비밀번호, API/OAuth token, DB·사용량 원본·대화·실행 로그·MLflow artifact는 포함하지 않습니다.
- 실제 비밀번호가 있는 원본 datasource YAML은 복사하지 않았습니다. 대신 환경변수만 사용하는 `docker-data/monitoring/grafana/provisioning/datasources/datasources.yml.example`을 제공합니다. Swallow 전용 datasource도 제외했습니다.
- n8n workflow는 Header Auth 및 TimescaleDB credential을 다시 지정해야 합니다. 운영 export의 사용자·공유 정보, Git 동기화 trigger, pinned data와 instance/version ID는 가져오지 않았습니다. import 후에도 비활성 상태입니다.
- 로컬 사용자 경로는 `/Users/example`, SSD 경로는 `/Volumes/Example`, NAS/Mac mini 주소는 문서용 `192.0.2.10`/`192.0.2.11`, 도메인은 `example.com`으로 바꿨습니다. `.plist`는 설치 전에 실제 사용자·Python·출력 경로로 수정해야 하며 launchd는 환경변수를 자동 치환하지 않습니다.
- schema/example JSON과 테스트 fixture는 구현에 포함된 예시이며, 운영 DB에서 추출한 데이터가 아닙니다.

## 호스트에 적용할 때

이 디렉터리는 자동 배포 묶음이 아닙니다. `compose.observability.reference.yml` 두 개는 공유 stack의 일부를 발췌한 **참조본**으로, 그대로 단독 기동할 수 없습니다. agents 참조본은 기존 `hermes-app` 서비스, homelab 참조본은 `postgres-n8n`과 `proxy_manager` network가 필요합니다. 기존 stack에 필요한 정의를 병합하고 host 경로와 환경변수를 맞추세요.

1. monitoring의 `.env.example`을 로컬 `.env`로 복사하고 실제 호스트 주소·비밀번호를 설정합니다. homelab/agents 참조본의 환경변수는 각 호스트의 기존 비공개 설정에서 준비합니다.
2. NAS monitoring Compose의 정적 설정 mount는 `/volume3/Gullinkambi/infrastructure/docker-data/monitoring/`, 대시보드·alerting mount는 `/volume3/Gullinkambi/provisioning/`를 가리킵니다. 다른 checkout 경로라면 수정합니다. NPM·Hermes·OpenViking의 read-only 데이터 mount는 여전히 Birds-Nest의 외부 런타임 경로를 가리키며 실제 경로로 맞춰야 합니다.
3. datasource `.yml.example`을 같은 디렉터리의 로컬 `datasources.yml`로 복사합니다. 제공한 Compose는 `OBSERVABILITY_DB_*`와 `N8N_DB_*` 환경변수를 Grafana에 전달합니다. 각 DB에 별도의 읽기 전용 계정과 필요한 조회 권한을 먼저 준비합니다. 예시는 TLS `require`이므로 DB 환경에 맞춰 검토합니다. Prometheus와 두 PostgreSQL UID는 기존 대시보드와 맞췄습니다. Loki UID는 실제 패널의 datasource 매핑을 확인합니다.
4. 새 TimescaleDB에는 초기화 스크립트, 기존 DB에는 필요한 마이그레이션을 순서대로 적용합니다. 자동 초기화 스크립트만으로 `001`~`011` 마이그레이션이 모두 적용되지는 않습니다. 세부 순서·권한은 SQL과 telemetry README를 확인하고 별도 개발 DB에서 먼저 검증합니다.
5. n8n import 후 Header Auth·TimescaleDB credential을 연결하고 collector의 webhook URL/token을 호스트에서 설정합니다. 활성화 전에 테스트 이벤트가 DB에 적재되고 Grafana에서 조회되는지 확인합니다.

## 보안 기본값 변경 시 주의

기존 호스트에 다시 배포할 때 아래 변경을 확인합니다.

- `docker.sock`은 `docker-socket-proxy`만 마운트합니다. Alloy와 cAdvisor는 내부 전용 `docker_socket` 네트워크로 읽기 API(`CONTAINERS`, `INFO`, `VERSION`, `EVENTS`, `NETWORKS`)만 사용하고, cAdvisor는 privileged 없이 실행합니다.
- Mac mini Prometheus는 `MACMINI_PROMETHEUS_BIND_ADDR`(LAN IP)에만 열리고 `--web.enable-lifecycle`이 꺼집니다. 설정을 다시 읽으려면 `/-/reload` 대신 컨테이너를 재시작합니다.
- 호스트 exporter(`ai-subscription-exporter`, `macos-memory-exporter`)의 기본 bind는 `127.0.0.1`입니다. OrbStack에서는 `host.docker.internal`로 계속 scrape됩니다. 기존 LaunchAgent plist는 다시 설치해야 반영됩니다.
- MLflow와 TimescaleDB는 기본적으로 `127.0.0.1`에만 바인딩합니다(`MLFLOW_BIND_ADDR`, `TIMESCALE_BIND_ADDR`). `run.sh`는 `MLFLOW_ALLOWED_HOSTS` 목록에 있는 Host만 허용합니다. MLflow 이미지는 UID 10001로 실행하므로, 기존 artifact 디렉터리를 `chown -R 10001:10001` 해야 합니다.
- collector와 `orca_task`는 webhook 토큰이 비어 있으면 전송하지 않고 종료합니다.

## 복사 후 검증

저장소 루트에서 실행합니다.

```bash
python3 -m unittest discover -s infrastructure/docker-compose/agents/hermes-telemetry/tests -q
python3 -m unittest discover -s infrastructure/docker-compose/monitoring/ai-subscription-exporter/tests -q
gitleaks dir infrastructure --redact --no-banner
```

DB 통합 테스트는 `ORCA_TEST_DB_CONTAINER`를 지정한 별도의 폐기 가능한 테스트 DB에서만 실행합니다. 지정하지 않은 상태에서 skip된 테스트는 통과로 간주하지 않습니다. 운영 n8n → DB → Grafana 통합 검증이나 배포는 이 복사 작업에 포함되지 않습니다.
