# Gullinkambi

![Gullinkambi](assets/Gullinkambi.png)

[한국어 문서](docs/README_KO.md)

`Gullinkambi` is a collection of Grafana dashboards for observing Kimdove's infrastructure. It brings n8n and Hermes Agent runs, Hermes Kanban tasks, OpenViking, NAS, and Mac mini health into one place through dashboard JSON and file provisioning definitions.

The project is named after Gullinkambi, the rooster from Norse mythology. In the *Völuspá*, Gullinkambi's crow awakens the warriors of Valhalla—a fitting image for a repository built to watch multiple systems and call attention to important changes.

> Grafana, data sources such as Prometheus and PostgreSQL, collectors, and credentials are not included in this repository.

## Dashboards

| Dashboard | Deployment | Coverage | Data sources |
| --- | --- | --- | --- |
| Unified dashboard | Grafana Git Sync | n8n runs, Hermes Agent and Kanban, Orca usage, OpenViking, Codex, Claude, and Antigravity subscription usage | PostgreSQL/TimescaleDB, Prometheus |
| Infrastructure monitoring | Grafana Git Sync | NAS and Mac mini hosts, Docker containers, M4 hardware, Nginx Proxy Manager | Prometheus/cAdvisor, node exporter, macmon, NPM collector |
| Mac mini system monitoring (standalone) | File provisioning | CPU, memory, disk, network, temperature, M4 power and frequency | Prometheus/node exporter |
| OpenViking Overview | File provisioning | Exporter, container resources, token and API usage, queues, and logs | Prometheus |

### 1. Unified dashboard

#### 1-1. n8n monitoring

![n8n monitoring dashboard](assets/n8n%20monitoring%20dashboard.png)

- Shows recent and active runs, success rate, failure trends, and P50/P95 execution times at a glance.
- Includes workflow-level success rates, recent failures, collection health, and external API errors for investigating automation issues.

#### 1-2. Hermes Agent monitoring

![Hermes Agent monitoring dashboard](assets/hermes%20agent%20monitoring%20dashboard.png)

- Shows run count, success rate, completed and failed trends, execution time, and status distribution.
- Breaks down runs and token consumption by profile and model, alongside tool calls and recent failure details.

#### 1-3. Orca monitoring

![Orca monitoring dashboard](assets/orca%20monitoring%20dashboard.png)

- Shows Codex, Claude, and Antigravity total token usage, model call counts, daily token trends, and usage by model. Thread-level details remain available for Codex and Claude.
- Tracks aggregation delay so both usage patterns and data freshness can be monitored.
- Antigravity is aggregated on the Mac mini into privacy-safe daily model/project totals; prompts, responses, paths, and conversation IDs are never sent to the telemetry pipeline.

#### 1-4. OpenViking monitoring

![OpenViking monitoring dashboard](assets/openviking%20monitoring%20dashboard.png)

- Shows exporter health, API requests, token usage, average request duration, and recent errors.
- Includes container CPU and memory, data size, session files, queue messages, and log metrics.

#### 1-5. AI subscription monitoring

- Shows the current short-window and weekly subscription usage, next reset time, and collector status for Codex, Claude, and Antigravity.
- Compares quota, token, session, and model trends, and surfaces stale collection data separately from real usage changes.

### 2. Infrastructure monitoring

![Docker monitoring dashboard](assets/macmini%20docker%20monitoring%20dashboard.png)

- Combines container and host health in the `NAS Docker`, `Mac mini Docker`, `NAS System`, and `Mac mini System` tabs.
- The `NPM` tab shows Nginx Proxy Manager container health, per-host request rate, status code mix, 5xx ratio, response traffic, and SSL certificate expiry.
- The system tabs cover CPU, memory, filesystems, network, load, and detailed disk views for mounted-volume used and total capacity, IOPS, and I/O utilization. The Mac mini tab also includes M4 CPU/GPU utilization, temperature, power, and frequency.

### 3. Mac mini system monitoring

![Mac mini system monitoring dashboard](assets/macmini%20system%20monitoring%20dashboard.png)

- Monitors core system health, including CPU, memory, disk, network, load, and uptime.
- Includes M4 CPU/GPU utilization and temperature, power consumption, and operating frequency for detailed hardware-level visibility.

## Repository layout

~~~text
.
├── provisioning/
│   └── dashboards/
│       ├── git-sync/              # Dashboard API v2 resources deployed through Git Sync
│       ├── hermes-kanban/         # Hermes Kanban file-provisioned dashboard JSON
│       ├── macmini/               # Mac mini file-provisioned dashboard JSON
│       ├── openviking/            # OpenViking file-provisioned dashboard JSON
│       ├── hermes-kanban.yml      # Grafana provider definition
│       ├── macmini.yml
│       └── openviking.yml
├── docs/                           # Project documentation
│   └── README_KO.md               # Korean README
├── source/                         # Original files and UI specs retained for editing history
├── hermes-kanban-panels.sql        # Reference queries for Hermes Kanban panels
└── README.md
~~~

Files under `provisioning/dashboards/git-sync/` use the `dashboard.grafana.app/v2` resource format. The other three directories use Grafana's standard dashboard JSON format. These formats are not interchangeable.

`source/` is not a deployment path. `source/Nest-Control-Room.json` mirrors the current Git Sync resource, while `source/Aviary Control Room.json` is retained as an earlier editing reference. Make production dashboard changes under `provisioning/dashboards/`.

## Deployment

### Grafana Git Sync

Configure Grafana Git Sync with the following values:

| Setting | Value |
| --- | --- |
| Repository | `EvoDmiK/Gullinkambi` |
| Branch | `main` |
| Path | `provisioning/dashboards/git-sync` |

This path contains only the unified and infrastructure monitoring dashboards. Do not point Git Sync at all of `provisioning/dashboards/` because it also contains standard dashboard JSON and provider YAML files.

### File provisioning

Check out the repository on the Grafana host and mount the entire `provisioning/dashboards` directory read-only at the same path inside the container:

~~~yaml
services:
  grafana:
    volumes:
      - ./provisioning/dashboards:/etc/grafana/provisioning/dashboards:ro
~~~

Each provider reads only its assigned subdirectory and checks for changes every 60 seconds.

| Provider | Grafana folder | Path |
| --- | --- | --- |
| Hermes Kanban | `Hermes` | `/etc/grafana/provisioning/dashboards/hermes-kanban` |
| Mac mini | `Mac mini` | `/etc/grafana/provisioning/dashboards/macmini` |
| OpenViking | `Agents` | `/etc/grafana/provisioning/dashboards/openviking` |

Avoid mounting another Birds-Nest dashboard provisioning directory over the same path. If Grafana does not pick up a JSON change, inspect its container logs for provisioning errors and restart Grafana if necessary.

## Data sources

The referenced data sources must already be registered in Grafana.

- Git Sync dashboards reference Prometheus UID `PBFA97CFB590B2093` and PostgreSQL UIDs `ffskrzljzwr28b` and `cfrgthsaa3ev4b`.
- Hermes Kanban uses PostgreSQL/TimescaleDB UID `ffskrzljzwr28b` and the Kanban tables in the `observability` schema.
- The standalone Mac mini dashboard uses the `Prometheus` data source variable and node exporter metrics from the `macmini_node` job.
- OpenViking uses the data source named `Prometheus` together with OpenViking exporter and container metrics.
- Infrastructure monitoring uses cAdvisor metrics from both hosts, the `node_exporter` and `macmini_node` node exporter jobs, and the `macmini_soc` macmon job. Its `NPM` tab also uses the normalized `npm_*` metrics described below.

### AI subscription monitoring collection requirements

The `AI subscription` tab in the unified dashboard expects normalized Prometheus metrics. Exporters and credentials are intentionally outside this repository.

| Metric | Required labels | Meaning |
| --- | --- | --- |
| `ai_subscription_quota_used_percent` | `provider`, `window`, optional `pool` | Current quota consumption from 0 to 100 |
| `ai_subscription_quota_reset_timestamp_seconds` | `provider`, `window`, optional `pool` | Next reset as a Unix timestamp in seconds |
| `ai_subscription_tokens_total` | `provider`, `type`, `model` | Monotonic token counter |
| `ai_subscription_sessions_total` | `provider` | Monotonic session counter |
| `ai_subscription_collector_up` | `provider`, optional `pool` | Last collection result: `1` for success, `0` for failure |
| `ai_subscription_last_success_timestamp_seconds` | `provider`, optional `pool` | Unix timestamp of the last successful collection |

Use `codex`, `claude`, and `antigravity` for `provider`, and `session` and `weekly` for `window`. Antigravity adds `pool="gemini"` or `pool="third_party"`. The deployed collector maps Codex rate limits, Claude Code status-line limits, and the Antigravity CLI `/usage` JSON into this schema. Its source and macOS LaunchAgent installer live in `Birds-Nest/docker-compose/monitoring/ai-subscription-exporter`; Mac mini Prometheus scrapes it through `host.docker.internal:9819`, and NAS Prometheus imports the normalized metrics through federation. Keep the collector on a trusted monitoring network and never expose stored login credentials to Grafana.

Claude does not include `rate_limits` in every status-line payload. The collector therefore replaces its Claude cache only when a payload contains valid limits; payloads without limits preserve the last valid snapshot. `ai_subscription_collector_up{provider="claude"}` becomes `0` only when no valid cache exists or the snapshot is older than `CLAUDE_MAX_AGE_SECONDS` (24 hours by default), avoiding transient false alerts without hiding genuinely stale data.

References: [Codex app-server account endpoints](https://learn.chatgpt.com/docs/app-server), [Claude Code status-line rate limits](https://code.claude.com/docs/en/statusline#rate-limit-usage), [Antigravity model quotas](https://antigravity.google/docs/cli/commands/usage/), [Antigravity headless JSON](https://antigravity.google/docs/cli/headless/).

### Grafana alerting and Discord notifications

Files under `provisioning/alerting/` provision 22 Grafana-managed rules for infrastructure availability, disk and temperature capacity, n8n, Hermes, Orca, OpenViking, Nginx Proxy Manager, and AI subscription monitoring. `notifications.yml` routes firing and resolved notifications to the `Gullinkambi Discord` contact point. The webhook is supplied only through `GF_DISCORD_WEBHOOK_URL`; never commit its value.

The AI subscription rules are:

| Rule | Condition | For |
| --- | --- | --- |
| AI subscription collector down | Codex, Claude, or an Antigravity pool has `ai_subscription_collector_up < 1` | 5 minutes |
| Codex subscription data stale | Last successful Codex collection is more than 15 minutes old | 5 minutes |
| AI weekly quota high | Codex, Claude, or an Antigravity pool exceeds 85% weekly usage | 10 minutes |

Claude intentionally has no 15-minute stale rule because its status line refreshes when Claude Code handles a message. Its collector state instead uses the last valid snapshot and the 24-hour cache limit. Discord groups alerts by `alertname`, `service`, and `severity`, waits 30 seconds before the first notification, groups updates every 5 minutes, repeats unresolved alerts every 4 hours, and sends resolved notifications. A provisioned Discord template combines every alert instance in the group into one color-coded embed card with concise firing or resolved summaries and Source, Silence, Dashboard, and Panel links.

Set Grafana's canonical public URL so notification `Source` and `Silence` links never point to `localhost`:

```dotenv
GF_SERVER_DOMAIN=monitoring.dove-nest.com
GF_SERVER_ROOT_URL=https://monitoring.dove-nest.com/
```

### Infrastructure monitoring collection requirements

| Target | Prometheus job | Required metrics |
| --- | --- | --- |
| NAS system | `node_exporter` | `node_cpu_*`, `node_memory_*`, `node_filesystem_*`, `node_network_*`, `node_disk_*`, `node_load*`, `node_boot_time_seconds` |
| Mac mini system | `macmini_node` | The same standard node exporter metrics as the NAS |
| Mac mini M4 | `macmini_soc` | `macmon_cpu_*`, `macmon_gpu_*`, `macmon_ane_*`, `macmon_ram_*`, `macmon_sys_*` |

The infrastructure dashboard automatically populates `NAS Instance` and `Mac mini Instance` variables. If your Prometheus job names differ, update `node_exporter`, `macmini_node`, and `macmini_soc` in the dashboard JSON to match the actual scrape jobs. The NAS `node_exporter` target must be running for the NAS system and disk panels to receive data.

The current `macmini_node` target runs inside the OrbStack Linux VM, so its disk inventory, IOPS, and utilization describe OrbStack virtual block devices rather than the Mac's physical SSD. Native macOS disk/SMART monitoring requires a separate macOS collector or smartctl exporter. NAS RAID and SMART health are also outside the standard node exporter metric set and require dedicated exporters.

### Nginx Proxy Manager collection requirements

The container panels in the `NPM` tab use cAdvisor metrics for the NAS `nginx-proxy-manager` container and work without extra setup. The traffic and certificate panels expect the following normalized Prometheus metrics. As with the AI subscription exporter, the collector that parses NPM access logs and reads certificate data is kept outside this repository.

| Metric | Required labels | Meaning |
| --- | --- | --- |
| `npm_http_requests_total` | `host`, `status` | Monotonic request counter per proxy host and three-digit HTTP status |
| `npm_http_response_bytes_total` | `host` | Monotonic counter of response bytes sent per proxy host |
| `npm_certificate_expiry_timestamp_seconds` | `domain` | SSL certificate expiry as a Unix timestamp in seconds |
| `npm_collector_last_success_timestamp_seconds` | none | Unix timestamp in seconds of the last successful collection |

| Rule | Condition | For |
| --- | --- | --- |
| Nginx Proxy Manager down | cAdvisor has not seen the `nginx-proxy-manager` container for 2 minutes | 3m |
| Nginx Proxy Manager 5xx elevated | A proxy host's 5xx ratio exceeds 5% while it serves more than 0.05 req/s | 10m |
| Nginx Proxy Manager certificate expiring | A certificate expires in fewer than 14 days | 10m |
| Nginx Proxy Manager collector stale | The last successful collection is more than 15 minutes old | 5m |

The 5xx, certificate, and stale-collector rules use `noDataState: OK`, so they stay quiet until the collector is deployed. Once it runs, consider switching the stale-collector rule to `Alerting` so that a collector disappearing from Prometheus is also reported.

When installing these dashboards in another Grafana instance, update data source names and UIDs as needed. If a dashboard loads without data, check its data source UID, Prometheus job and instance labels, and the PostgreSQL `observability` schema first.

## Editing and validation

File-provisioned dashboard JSON is the source of truth. Changes made in the Grafana UI can be overwritten during the next provisioning refresh, so persist long-lived changes in the JSON files.

Validate JSON syntax before committing:

~~~bash
find provisioning source -name '*.json' -exec jq empty {} +
~~~

After deployment:

1. Confirm that Git Sync reports no errors.
2. Confirm that the `Hermes`, `Mac mini`, and `Agents` folders contain the file-provisioned dashboards.
3. Verify that dashboard variables and panel queries return data.
4. If Grafana reports `Data source not found`, align the JSON data source name or UID with the Grafana instance.
