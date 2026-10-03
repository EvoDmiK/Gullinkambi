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
| 서비스 모니터링 | Grafana Git Sync | n8n 실행, Hermes Agent·Kanban, Orca 사용량, OpenViking, Codex·Claude·Antigravity 구독 사용량 | PostgreSQL/TimescaleDB, Prometheus |
| 인프라 모니터링 | Grafana Git Sync | NAS·Mac mini 시스템과 Docker 컨테이너, M4 하드웨어, Nginx Proxy Manager | Prometheus/cAdvisor, node exporter, macmon, NPM 수집기 |
| Mac mini 시스템 모니터링 (독립 뷰) | File provisioning | CPU, 메모리, 디스크, 네트워크, 온도, M4 전력·주파수 | Prometheus/node exporter |
| OpenViking Overview | File provisioning | exporter, 컨테이너 자원, 토큰·API 사용량, 큐와 로그 | Prometheus |


### 1. 서비스 모니터링

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

- Orca에서 Codex·Claude·Antigravity의 전체 토큰 사용량과 모델 호출 수, 일별 토큰 추이 및 모델별 사용량을 보여 줍니다. 쓰레드별 상세는 Codex와 Claude에만 제공됩니다.
- 최신 사용량 데이터의 집계 지연도 함께 표시하여 비용과 사용 패턴뿐 아니라 수집 데이터의 최신성까지 점검할 수 있습니다.
- Antigravity는 Mac mini에서 모델·프로젝트별 일일 수치와 대화별 수치를 저장소 이름 기준으로 집계합니다. Codex·Claude와 마찬가지로 대화 제목은 쓰레드 이름으로 전송하지만, 프롬프트·응답·경로·원본 대화 ID(해시만 전송)는 telemetry pipeline으로 전송하지 않습니다.

#### 1-4. OpenViking 모니터링 대시보드

![OpenViking monitoring dashboard](../assets/openviking%20monitoring%20dashboard.png)

- Exporter 상태와 API 요청 수, 토큰 사용량, 평균 요청 시간 및 최근 오류를 확인할 수 있습니다. 
- 컨테이너 CPU·메모리, 데이터 크기, 세션 파일, 큐 메시지와 로그 지표도 함께 제공하여 OpenViking 서비스와 실행 환경을 통합적으로 관찰할 수 있습니다.

#### 1-5. AI 구독 모니터링 대시보드

- Codex, Claude, Antigravity 구독의 현재 단기·주간 사용률, 다음 초기화 시각, 수집기 상태를 한눈에 보여 줍니다.
- 구독 사용률, 토큰, 세션, 모델별 추이를 비교하고 실제 사용량 변화와 수집 지연을 분리해서 확인할 수 있습니다.

### 2. 인프라 모니터링 대시보드

![Docker monitoring dashboard](../assets/macmini%20docker%20monitoring%20dashboard.png)

- `NAS Docker`, `Mac mini Docker`, `NAS 시스템`, `Mac mini 시스템` 탭에서 컨테이너와 호스트 상태를 한 대시보드에서 확인할 수 있습니다.
- `NPM` 탭은 Nginx Proxy Manager 컨테이너 상태, 프록시 호스트별 요청 수, 상태 코드 분포, 5xx 비율, 응답 트래픽, SSL 인증서 만료일을 보여 줍니다.
- 시스템 탭은 CPU·메모리·파일시스템·네트워크·부하와 함께 마운트 볼륨의 사용량/전체 용량, IOPS, I/O 사용률을 제공하며, Mac mini에서는 M4 CPU·GPU의 사용률, 온도, 전력 및 동작 주파수도 함께 보여 줍니다.

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

이 경로에는 `서비스 모니터링`과 `인프라 모니터링`만 있습니다. `provisioning/dashboards/` 전체를 Git Sync 경로로 지정하면 일반 dashboard JSON과 provider YAML까지 섞이므로 사용하지 않습니다.

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
- 인프라 모니터링은 NAS와 Mac mini의 cAdvisor 메트릭, `node_exporter`·`macmini_node` node exporter job, `macmini_soc` macmon job을 사용합니다. `NPM` 탭은 아래에 정리한 정규화된 `npm_*` 메트릭도 사용합니다.

### AI 구독 모니터링 수집 조건

서비스 모니터링의 `AI 구독` 탭은 아래와 같이 정규화된 Prometheus 메트릭을 사용합니다. exporter와 자격 증명은 이 저장소에 포함하지 않습니다.

| 메트릭 | 필수 레이블 | 의미 |
| --- | --- | --- |
| `ai_subscription_quota_used_percent` | `provider`, `window`, 선택 `pool` | 0~100 범위의 현재 구독 사용률 |
| `ai_subscription_quota_reset_timestamp_seconds` | `provider`, `window`, 선택 `pool` | 다음 초기화 시각의 Unix timestamp(초) |
| `ai_subscription_tokens_total` | `provider`, `type`, `model` | 단조 증가하는 토큰 누적값 |
| `ai_subscription_sessions_total` | `provider` | 단조 증가하는 세션 누적값 |
| `ai_subscription_collector_up` | `provider`, 선택 `pool` | 최근 수집 성공은 `1`, 실패는 `0` |
| `ai_subscription_last_success_timestamp_seconds` | `provider`, 선택 `pool` | 마지막 성공 수집 시각의 Unix timestamp(초) |

`provider`는 `codex`, `claude`, `antigravity`, `window`는 `session`과 `weekly`를 사용합니다. Antigravity는 `pool="gemini"` 또는 `pool="third_party"`를 추가하며, 대시보드와 알림은 `gemini` pool만 다룹니다. 배포 수집기는 Codex rate limit, Claude Code status line 제한, Antigravity CLI `/usage` JSON을 이 스키마로 정규화합니다. 수집기와 macOS LaunchAgent installer는 `Birds-Nest/docker-compose/monitoring/ai-subscription-exporter`에 있으며, Mac mini Prometheus가 `host.docker.internal:9819`를 scrape하고 NAS Prometheus가 federation으로 정규화 지표를 가져옵니다. 수집기는 신뢰할 수 있는 모니터링 네트워크에서만 사용하고 저장된 로그인 자격 증명을 Grafana에 노출하지 마세요.

Claude는 모든 status line 입력에 `rate_limits`를 포함하지 않습니다. 따라서 수집기는 유효한 제한 정보가 있을 때만 Claude 캐시를 교체하고, 제한 정보가 없는 입력에서는 마지막 정상 스냅샷을 보존합니다. 유효한 캐시가 없거나 `CLAUDE_MAX_AGE_SECONDS`의 기본값인 24시간을 초과했을 때만 `ai_subscription_collector_up{provider="claude"}`가 `0`이 되므로, 일시적인 빈 입력으로 인한 오탐을 막으면서 실제 데이터 노후화는 계속 감지합니다.

참고: [Codex app-server 계정 endpoint](https://learn.chatgpt.com/docs/app-server), [Claude Code status line rate limit](https://code.claude.com/docs/en/statusline#rate-limit-usage), [Antigravity 모델 quota](https://antigravity.google/docs/cli/commands/usage/), [Antigravity headless JSON](https://antigravity.google/docs/cli/headless/).

### Grafana 알람과 Discord 알림

`provisioning/alerting/`의 파일은 인프라 가용성, 디스크·온도, n8n, Hermes, Orca, OpenViking, Nginx Proxy Manager, AI 구독 상태를 감시하는 Grafana-managed alert 23개를 provisioning합니다. `notifications.yml`은 firing과 resolved 알림을 `Gullinkambi Discord` contact point로 전달합니다. Webhook 실값은 `GF_DISCORD_WEBHOOK_URL` 환경변수로만 주입하고 저장소에 커밋하지 않습니다.

AI 구독 관련 규칙은 다음과 같습니다.

| 규칙 | 조건 | 지속 시간 |
| --- | --- | --- |
| AI subscription collector down | Codex, Claude 또는 Antigravity Gemini의 `ai_subscription_collector_up < 1` | 5분 |
| Codex subscription data stale | Codex 마지막 성공 수집이 15분 이상 지연 | 5분 |
| AI weekly quota high | Codex, Claude 또는 Antigravity Gemini의 주간 사용률이 50%, 70%, 85%, 95%를 넘을 때마다 주간 구간당 한 번씩 알림 | 10분 |
| AI 5-hour quota high | Claude 또는 Antigravity Gemini의 5시간 사용률 80% 초과 (Codex는 5시간 한도 없음) | 2분 |

Claude status line은 Claude Code가 메시지를 처리할 때 갱신되므로 Claude에는 15분 stale 규칙을 적용하지 않습니다. 대신 마지막 유효 스냅샷과 24시간 캐시 만료로 collector 상태를 판단합니다. Discord는 `alertname`, `service`, `severity`로 알람을 묶고 최초 알림은 30초 대기하며, 그룹 갱신은 5분, 미복구 반복 알림은 4시간 간격으로 전송합니다(주간 사용률 구간 알림은 반복하지 않음). resolved 알림도 활성화되어 있습니다. Provisioning된 Discord 전용 템플릿은 같은 그룹의 alert instance를 한 개의 색상 embed 카드로 합치고, 발생·복구 요약과 Source·Silence·Dashboard·Panel 링크만 간결하게 표시합니다.

Claude 값은 Mac mini에서 Claude Code CLI를 사용할 때만 갱신되며, VS Code 확장 등 다른 환경의 사용량은 다음 갱신 때 계정 전체 사용률에 함께 반영됩니다. 그래서 `AI 구독` 탭과 사용률 알림 규칙은 초기화 시각이 이미 지난 구간을 제외하고, 해당 패널에 `초기화됨 · 갱신 대기`를 표시합니다.

Discord 알림의 `Source`와 `Silence` 링크가 `localhost`를 가리키지 않도록 Grafana의 외부 기준 URL을 설정합니다.

```dotenv
GF_SERVER_DOMAIN=monitoring.dove-nest.com
GF_SERVER_ROOT_URL=https://monitoring.dove-nest.com/
```

### 인프라 모니터링 수집 조건

| 대상 | Prometheus job | 필요한 메트릭 |
| --- | --- | --- |
| NAS 시스템 | `node_exporter` | `node_cpu_*`, `node_memory_*`, `node_filesystem_*`, `node_network_*`, `node_disk_*`, `node_load*`, `node_boot_time_seconds` |
| Mac mini 시스템 | `macmini_node` | NAS와 동일한 node exporter 기본 메트릭 |
| Mac mini M4 | `macmini_soc` | `macmon_cpu_*`, `macmon_gpu_*`, `macmon_ane_*`, `macmon_ram_*`, `macmon_sys_*` |

인프라 대시보드는 `NAS 인스턴스`와 `Mac mini 인스턴스` 변수를 자동으로 생성합니다. Prometheus의 job 이름이 다르면 대시보드 JSON의 `node_exporter`, `macmini_node`, `macmini_soc`를 실제 scrape job 이름에 맞게 변경해야 합니다. NAS 시스템과 디스크 패널에 데이터가 들어오려면 NAS의 `node_exporter` 대상이 실행 중이어야 합니다.

현재 `macmini_node` 대상은 OrbStack Linux VM 안에서 실행되므로 디스크 장치 목록, IOPS, 사용률은 Mac의 물리 SSD가 아니라 OrbStack 가상 블록 장치를 나타냅니다. macOS 물리 디스크와 SMART 상태를 확인하려면 macOS 네이티브 수집기 또는 smartctl exporter를 별도로 연결해야 합니다. NAS의 RAID·SMART 상태도 표준 node exporter 범위에 포함되지 않으므로 전용 exporter가 필요합니다.

### Nginx Proxy Manager 수집 조건

`NPM` 탭의 컨테이너 패널은 NAS `nginx-proxy-manager` 컨테이너의 cAdvisor 메트릭을 사용하므로 별도 설정 없이 동작합니다. 트래픽과 인증서 패널은 아래의 정규화된 Prometheus 메트릭을 사용합니다. AI 구독 exporter와 마찬가지로 수집기는 이 저장소에 포함하지 않습니다. `Birds-Nest/docker-compose/monitoring/npm-exporter`가 `proxy-host-*_access.log`를 이어서 읽고, NPM SQLite DB에서 프록시 호스트와 인증서 만료일을 읽으므로 인증서 개인키는 마운트하지 않습니다. NAS Prometheus는 이를 `npm_exporter` job으로 수집합니다. `host` 레이블은 요청의 Host 헤더가 아니라 NPM에 등록된 프록시 호스트의 첫 번째 도메인을 사용하므로 레이블 수가 제한됩니다. 이 외에 `npm_collector_up`과 `npm_log_parse_errors_total`도 제공합니다.

| 메트릭 | 필수 레이블 | 의미 |
| --- | --- | --- |
| `npm_http_requests_total` | `host`, `status` | 프록시 호스트와 세 자리 HTTP 상태 코드별로 단조 증가하는 요청 누적값 |
| `npm_http_response_bytes_total` | `host` | 프록시 호스트별로 단조 증가하는 응답 바이트 누적값 |
| `npm_certificate_expiry_timestamp_seconds` | `domain` | SSL 인증서 만료 시각의 Unix timestamp(초) |
| `npm_collector_last_success_timestamp_seconds` | 없음 | 마지막 성공 수집 시각의 Unix timestamp(초) |

| 규칙 | 조건 | 지속 시간 |
| --- | --- | --- |
| Nginx Proxy Manager down | cAdvisor가 2분 동안 `nginx-proxy-manager` 컨테이너를 관측하지 못함 | 3분 |
| Nginx Proxy Manager 5xx elevated | 초당 0.05건을 넘는 요청을 처리하는 프록시 호스트의 5xx 비율이 5% 초과 | 10분 |
| Nginx Proxy Manager certificate expiring | 인증서 만료까지 14일 미만 | 10분 |
| Nginx Proxy Manager collector stale | 마지막 성공 수집이 15분 이상 지연 | 5분 |

5xx와 인증서 규칙은 `noDataState: OK`를 사용합니다. 수집 지연 규칙은 `Alerting`을 사용하므로 수집기가 Prometheus에서 사라지는 상황도 알림으로 전달됩니다.

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
