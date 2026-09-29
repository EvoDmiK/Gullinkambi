# Gullinkambi

Gullinkambi는 Birds-Nest 환경의 Grafana 대시보드를 관리하는 저장소입니다. n8n과 Hermes 에이전트 실행, Hermes Kanban 작업, OpenViking, NAS 및 Mac mini의 상태를 한곳에서 관찰할 수 있도록 대시보드 JSON과 file provisioning 설정을 제공합니다.

Grafana 서버, Prometheus·PostgreSQL 같은 데이터 소스, 수집기, 자격 증명은 이 저장소에 포함하지 않습니다.

## 대시보드

| 대시보드 | 배포 방식 | 주요 내용 | 데이터 소스 |
| --- | --- | --- | --- |
| 통합 대시보드 | Grafana Git Sync | n8n 실행, Hermes Agent·Kanban, Orca 사용량, OpenViking | PostgreSQL/TimescaleDB, Prometheus |
| Docker 모니터링 | Grafana Git Sync | NAS와 Mac mini의 컨테이너 CPU, 메모리, 네트워크 | Prometheus/cAdvisor |
| Hermes 칸반 운영 현황 | File provisioning | 작업 상태, 처리량, 실패, 응답 없는 실행, 작업 종속성 | PostgreSQL/TimescaleDB |
| Mac mini 시스템 모니터링 | File provisioning | CPU, 메모리, 디스크, 네트워크, 온도, M4 전력·주파수 | Prometheus/node exporter |
| OpenViking Overview | File provisioning | exporter, 컨테이너 자원, 토큰·API 사용량, 큐와 로그 | Prometheus |

## 저장소 구조

```text
.
├── provisioning/
│   └── dashboards/
│       ├── git-sync/              # Dashboard API v2 형식의 Git Sync 배포본
│       ├── hermes-kanban/         # Hermes Kanban file provisioning JSON
│       ├── macmini/               # Mac mini file provisioning JSON
│       ├── openviking/            # OpenViking file provisioning JSON
│       ├── hermes-kanban.yml      # Grafana provider 정의
│       ├── macmini.yml
│       └── openviking.yml
├── source/                         # 편집 이력 보존용 원본과 UI spec
├── hermes-kanban-panels.sql        # Hermes Kanban 패널 쿼리 참고본
└── README.md
```

`provisioning/dashboards/git-sync/`의 파일은 `dashboard.grafana.app/v2` 리소스입니다. 나머지 세 디렉터리의 파일은 Grafana의 일반 dashboard JSON 형식입니다. 두 형식은 서로 바꾸어 가져올 수 없습니다.

`source/`는 배포 경로가 아닙니다. `source/Nest-Control-Room.json`은 현재 Git Sync 배포본과 같지만, `source/Aviary Control Room.json`은 이전 편집 참고본입니다. 운영 대시보드를 수정할 때는 `provisioning/dashboards/` 아래의 파일을 기준으로 사용합니다.

## 적용 방법

### Grafana Git Sync

Grafana의 Git Sync 설정에서 다음 값을 지정합니다.

| 항목 | 값 |
| --- | --- |
| Repository | `EvoDmiK/Gullinkambi` |
| Branch | `main` |
| Path | `provisioning/dashboards/git-sync` |

이 경로에는 `통합 대시보드`와 `Docker 모니터링`만 있습니다. `provisioning/dashboards/` 전체를 Git Sync 경로로 지정하면 일반 dashboard JSON과 provider YAML까지 섞이므로 사용하지 않습니다.

### File provisioning

저장소를 Grafana 호스트에 체크아웃한 뒤 `provisioning/dashboards` 전체를 컨테이너의 같은 경로에 읽기 전용으로 마운트합니다. Docker Compose에서는 다음과 같이 구성할 수 있습니다.

```yaml
services:
  grafana:
    volumes:
      - ./provisioning/dashboards:/etc/grafana/provisioning/dashboards:ro
```

provider 설정은 각 하위 디렉터리만 읽으며 60초마다 변경을 확인합니다.

| Provider | Grafana 폴더 | 읽는 경로 |
| --- | --- | --- |
| Hermes Kanban | `Hermes` | `/etc/grafana/provisioning/dashboards/hermes-kanban` |
| Mac mini | `Mac mini` | `/etc/grafana/provisioning/dashboards/macmini` |
| OpenViking | `Agents` | `/etc/grafana/provisioning/dashboards/openviking` |

기존 Birds-Nest의 dashboard provisioning 마운트와 동시에 같은 경로를 덮어쓰지 않도록 구성합니다. JSON을 변경한 뒤 Grafana가 갱신하지 않으면 컨테이너 로그에서 provisioning 오류를 확인하고 Grafana를 다시 시작합니다.

## 데이터 소스

대시보드가 참조하는 데이터 소스는 Grafana에 미리 등록되어 있어야 합니다.

- Git Sync 대시보드는 Prometheus UID `PBFA97CFB590B2093`과 PostgreSQL UID `ffskrzljzwr28b`, `cfrgthsaa3ev4b`를 참조합니다.
- Hermes Kanban은 PostgreSQL/TimescaleDB UID `ffskrzljzwr28b`와 `observability` 스키마의 Kanban 테이블을 사용합니다.
- Mac mini 대시보드는 `Prometheus` 데이터 소스 변수와 `macmini_node` job의 node exporter 메트릭을 사용합니다.
- OpenViking 대시보드는 이름이 `Prometheus`인 데이터 소스와 OpenViking exporter·컨테이너 메트릭을 사용합니다.
- Docker 모니터링은 NAS와 Mac mini에서 수집한 cAdvisor 메트릭을 사용합니다.

다른 Grafana 인스턴스에 적용할 때는 해당 인스턴스의 데이터 소스 이름과 UID에 맞게 JSON을 수정합니다. 대시보드가 열리더라도 데이터가 비어 있으면 먼저 데이터 소스 UID, Prometheus job·instance 레이블, PostgreSQL의 `observability` 스키마를 확인합니다.

## 수정 및 검증

File provisioning 대시보드는 저장소의 JSON을 원본으로 취급합니다. Grafana UI에서 변경한 내용은 다음 provisioning 갱신 때 파일 내용으로 돌아갈 수 있으므로, 유지할 변경은 JSON에 반영합니다.

커밋하기 전에 JSON 문법을 확인할 수 있습니다.

```bash
find provisioning source -name '*.json' -exec jq empty {} +
```

적용 후에는 다음 항목을 확인합니다.

1. Git Sync 상태에 오류가 없는지 확인합니다.
2. `Hermes`, `Mac mini`, `Agents` 폴더에 file provisioning 대시보드가 생성되었는지 확인합니다.
3. 각 대시보드의 변수 목록과 패널 쿼리가 정상적으로 데이터를 반환하는지 확인합니다.
4. `Data source not found` 오류가 있으면 JSON의 이름 또는 UID를 Grafana 설정과 맞춥니다.
