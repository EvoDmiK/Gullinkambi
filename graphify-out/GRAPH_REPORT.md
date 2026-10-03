# Graph Report - main  (2026-10-03)

## Corpus Check
- 16 files · ~181,745 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: (none) 2)

## Summary
- 52 nodes · 46 edges · 11 communities (6 shown, 5 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `67d7e48f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- 1. 서비스 모니터링
- 1. Service monitoring
- Gullinkambi
- Gullinkambi
- 데이터 소스
- Data sources
- AGENTS.md
- rules/graphify.md
- ponytail.md
- workflows/graphify.md

## God Nodes (most connected - your core abstractions)
1. `Gullinkambi` - 6 edges
2. `1. Service monitoring` - 6 edges
3. `Gullinkambi` - 6 edges
4. `1. 서비스 모니터링` - 6 edges
5. `Data sources` - 5 edges
6. `데이터 소스` - 5 edges
7. `Dashboards` - 4 edges
8. `대시보드` - 4 edges
9. `Deployment` - 3 edges
10. `적용 방법` - 3 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Communities (11 total, 5 thin omitted)

### Community 0 - "1. 서비스 모니터링"
Cohesion: 0.22
Nodes (9): 1-1. n8n 모니터링 대시보드, 1-2. Hermes Agent 모니터링 대시보드, 1-3. Orca 모니터링 대시보드, 1-4. OpenViking 모니터링 대시보드, 1-5. AI 구독 모니터링 대시보드, 1. 서비스 모니터링, 2. 인프라 모니터링 대시보드, 3. MacMini 시스템 모니터링 대시보드 (+1 more)

### Community 1 - "1. Service monitoring"
Cohesion: 0.22
Nodes (9): 1-1. n8n monitoring, 1-2. Hermes Agent monitoring, 1-3. Orca monitoring, 1-4. OpenViking monitoring, 1-5. AI subscription monitoring, 1. Service monitoring, 2. Infrastructure monitoring, 3. Mac mini system monitoring (+1 more)

### Community 2 - "Gullinkambi"
Cohesion: 0.25
Nodes (6): File provisioning, Grafana Git Sync, Gullinkambi, 수정 및 검증, 저장소 구조, 적용 방법

### Community 3 - "Gullinkambi"
Cohesion: 0.33
Nodes (6): Deployment, Editing and validation, File provisioning, Grafana Git Sync, Gullinkambi, Repository layout

### Community 4 - "데이터 소스"
Cohesion: 0.40
Nodes (5): AI 구독 모니터링 수집 조건, Grafana 알람과 Discord 알림, Nginx Proxy Manager 수집 조건, 데이터 소스, 인프라 모니터링 수집 조건

### Community 5 - "Data sources"
Cohesion: 0.40
Nodes (5): AI subscription monitoring collection requirements, Data sources, Grafana alerting and Discord notifications, Infrastructure monitoring collection requirements, Nginx Proxy Manager collection requirements

## Knowledge Gaps
- **35 isolated node(s):** `graphify`, `Ponytail, lazy senior dev mode`, `Workflow: graphify`, `graphify`, `Ponytail (Lazy Senior Dev Mode)` (+30 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 39 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Gullinkambi` connect `Gullinkambi` to `1. Service monitoring`, `Gullinkambi`, `Data sources`?**
  _High betweenness centrality (0.424) - this node is a cross-community bridge._
- **Why does `Gullinkambi` connect `Gullinkambi` to `1. 서비스 모니터링`, `데이터 소스`?**
  _High betweenness centrality (0.424) - this node is a cross-community bridge._
- **Why does `Dashboards` connect `1. Service monitoring` to `Gullinkambi`?**
  _High betweenness centrality (0.217) - this node is a cross-community bridge._
- **What connects `graphify`, `Ponytail, lazy senior dev mode`, `Workflow: graphify` to the rest of the system?**
  _35 weakly-connected nodes found - possible documentation gaps or missing edges._