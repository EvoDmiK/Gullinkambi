# Gullinkambi

Birds-Nest의 황금 볏 파수꾼. Grafana 대시보드로 Agent·n8n·Orca의 실행과 시스템 상태를 살피고 이상 징후를 포착합니다.

이 저장소는 Birds-Nest에서 분리한 **Grafana 대시보드 정의**를 관리합니다. Grafana 서버, 데이터베이스, Prometheus, Loki의 실행 설정이나 자격 증명은 포함하지 않습니다. 대시보드 관련 Git 커밋 기록도 함께 가져왔습니다.

## 구성

| 경로 | 적용 방식 | 내용 |
| --- | --- | --- |
| `git-sync/` | Grafana Git Sync | Aviary Control Room, Nest Control Room |
| `provisioning/dashboards/` | Grafana file provisioning | Hermes Kanban, Mac mini, OpenViking |
| `hermes-kanban-panels.sql` | 참고 자료 | Hermes Kanban 패널 쿼리 |

## 적용

1. Grafana Git Sync의 저장소를 `EvoDmiK/Gullinkambi`, 브랜치를 `main`, 경로를 `git-sync`로 설정합니다.
2. Grafana 컨테이너에 이 저장소의 `provisioning/dashboards`를 `/etc/grafana/provisioning/dashboards`로 읽기 전용 마운트합니다. 기존 `Birds-Nest`의 dashboard provisioning 마운트와 중복해서 사용하지 않습니다.
3. 데이터 소스는 Grafana 인스턴스에서 별도로 관리합니다. 대시보드에는 PostgreSQL/TimescaleDB, Prometheus 등의 기존 데이터 소스 UID가 들어 있으므로 다른 인스턴스에 적용할 때 UID를 확인하고 맞춥니다.
4. Git Sync 상태와 각 대시보드의 패널 데이터가 정상인지 확인합니다.

Grafana Git Sync의 Dashboard API v2 JSON과 file provisioning JSON은 형식이 다릅니다. `git-sync/` 파일을 일반 Import로 가져오거나 `provisioning/dashboards/` 파일을 Git Sync 경로에 넣지 않습니다.
