# Gullinkambi
![Gullinkambi](../assets/Gullinkambi.png)


`Gullinkambi`는 김둘기의 인프라 환경을 관찰하기 위한 Grafana 대시보드 저장소입니다.  
n8n과 Hermes 에이전트의 실행 상태, Hermes Kanban 작업, OpenViking, NAS와 Mac mini의 상태를 한곳에서 볼 수 있도록 대시보드 JSON과 file provisioning 설정을 제공합니다.

이름은 북유럽 신화의 수탉 굴린캄비에서 따왔습니다.  
《무녀의 예언》에서 굴린캄비는 울음으로 발할라의 전사들을 깨웁니다. 여러 시스템의 상태를 살피고 중요한 변화를 알아차리기 위한 이 저장소의 역할과 맞닿아 있습니다.



※ Grafana 서버, Prometheus·PostgreSQL 같은 데이터 소스, 수집기, 자격 증명은 이 저장소에 포함하지 않습니다.

## 대시보드

| 대시보드 | 배포 방식 | 주요 내용 | 데이터 소스 |
| --- | --- | --- | --- |
| 통합 대시보드 | Grafana Git Sync | n8n 실행, Hermes Agent·Kanban, Orca 사용량, OpenViking | PostgreSQL/TimescaleDB, Prometheus |
| 인프라 모니터링 | Grafana Git Sync | NAS·Mac mini 시스템과 Docker 컨테이너, M4 하드웨어 | Prometheus/cAdvisor, node exporter, macmon |
| Mac mini 시스템 모니터링 (독립 뷰) | File provisioning | CPU, 메모리, 디스크, 네트워크, 온도, M4 전력·주파수 | Prometheus/node exporter |
| OpenViking Overview | File provisioning | exporter, 컨테이너 자원, 토큰·API 사용량, 큐와 로그 | Prometheus |


### 1. 통합 대시보드

#### 1-1. n8n 모니터링 대시보드

![n8n monitoring dashboard](../assets/n8n%20monitoring%20dashboard.png)

- 최근 실행 수와 성공률, 실행 중인 작업, 실패 추이와 처리 시간(P50·P95)을 한눈에 확인할 수 있습니다.
- 워크플로우별 성공률과 최근 실패 내역, 데이터 수집 상태 및 외부 API 오류도 함께 제공하여 n8n 자동화의 전반적인 상태와 장애 원인을 추적할 수 있습니다.

#### 1-2. Hermes Agent 모니터링 대시보드

![hermes agent monitoring dashboard](../assets/hermes%20agent%20monitoring%20dashboard.png)

- Hermes Agent의 실행 수와 성공률, 완료·실패 추세, 처리 시간 및 상태별 건수를 보여 줍니다.
- 프로필과 모델별 실행·토큰 사용량, 도구 호출 횟수, 최근 실패 상세를 함께 확인할 수 있어 에이전트의 성능과 사용 패턴을 분석하기 좋습니다.

#### 1-3. Orca 모니터링 대시보드

![ORCA monitoring dashboard](../assets/orca%20monitoring%20dashboard.png)

- Orca의 전체 토큰 사용량과 모델 호출 수, 일별 토큰 사용 추이 및 모델·스레드별 사용량을 보여 줍니다.
- 최신 사용량 데이터의 집계 지연도 함께 표시하여 비용과 사용 패턴뿐 아니라 수집 데이터의 최신성까지 점검할 수 있습니다.

#### 1-4. OpenViking 모니터링 대시보드

![OpenViking monitoring dashboard](../assets/openviking%20monitoring%20dashboard.png)

- Exporter 상태와 API 요청 수, 토큰 사용량, 평균 요청 시간 및 최근 오류를 확인할 수 있습니다. 
- 컨테이너 CPU·메모리, 데이터 크기, 세션 파일, 큐 메시지와 로그 지표도 함께 제공하여 OpenViking 서비스와 실행 환경을 통합적으로 관찰할 수 있습니다.

### 2. 인프라 모니터링 대시보드

![Docker monitoring dashboard](../assets/macmini%20docker%20monitoring%20dashboard.png)

- `NAS Docker`, `Mac mini Docker`, `NAS 시스템`, `Mac mini 시스템` 탭에서 컨테이너와 호스트 상태를 한 대시보드에서 확인할 수 있습니다.
- 시스템 탭은 CPU·메모리·파일시스템·네트워크·디스크 I/O와 부하를 제공하며, Mac mini에서는 M4 CPU·GPU의 사용률, 온도, 전력 및 동작 주파수도 함께 보여 줍니다.

### 3. MacMini 시스템 모니터링 대시보드

![MacMini system monitoring dashboard](../assets/macmini%20system%20monitoring%20dashboard.png)

- Mac mini의 CPU, 메모리, 디스크, 네트워크, 부하와 업타임 등 기본 시스템 상태를 모니터링합니다. 
- M4 CPU·GPU의 사용률과 온도, 전력 소비 및 동작 주파수까지 제공하여 시스템 부하와 하드웨어 상태를 상세하게 확인할 수 있습니다.

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
├── docs/                           # 프로젝트 문서
│   └── README_KO.md                 # 한국어 README
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

이 경로에는 `통합 대시보드`와 `인프라 모니터링`만 있습니다. `provisioning/dashboards/` 전체를 Git Sync 경로로 지정하면 일반 dashboard JSON과 provider YAML까지 섞이므로 사용하지 않습니다.

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
- 독립 Mac mini 대시보드는 `Prometheus` 데이터 소스 변수와 `macmini_node` job의 node exporter 메트릭을 사용합니다.
- OpenViking 대시보드는 이름이 `Prometheus`인 데이터 소스와 OpenViking exporter·컨테이너 메트릭을 사용합니다.
- 인프라 모니터링은 NAS와 Mac mini의 cAdvisor 메트릭, `node_exporter`·`macmini_node` node exporter job, `macmini_soc` macmon job을 사용합니다.

### 인프라 모니터링 수집 조건

| 대상 | Prometheus job | 필요한 메트릭 |
| --- | --- | --- |
| NAS 시스템 | `node_exporter` | `node_cpu_*`, `node_memory_*`, `node_filesystem_*`, `node_network_*`, `node_disk_*`, `node_load*`, `node_boot_time_seconds` |
| Mac mini 시스템 | `macmini_node` | NAS와 동일한 node exporter 기본 메트릭 |
| Mac mini M4 | `macmini_soc` | `macmon_cpu_*`, `macmon_gpu_*`, `macmon_ane_*`, `macmon_ram_*`, `macmon_sys_*` |

인프라 대시보드는 `NAS 인스턴스`와 `Mac mini 인스턴스` 변수를 자동으로 생성합니다. Prometheus의 job 이름이 다르면 대시보드 JSON의 `node_exporter`, `macmini_node`, `macmini_soc`를 실제 scrape job 이름에 맞게 변경해야 합니다. NAS의 RAID·SMART 상태는 표준 node exporter 범위에 포함되지 않으므로 필요하면 전용 exporter를 별도로 연결합니다.

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
