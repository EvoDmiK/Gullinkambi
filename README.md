# Gullinkambi

![Gullinkambi](assets/Gullinkambi.png)

[한국어 문서](docs/README_KO.md)

`Gullinkambi` is a collection of Grafana dashboards for observing Kimdove's infrastructure. It brings n8n and Hermes Agent runs, Hermes Kanban tasks, OpenViking, NAS, and Mac mini health into one place through dashboard JSON and file provisioning definitions.

The project is named after Gullinkambi, the rooster from Norse mythology. In the *Völuspá*, Gullinkambi's crow awakens the warriors of Valhalla—a fitting image for a repository built to watch multiple systems and call attention to important changes.

> Grafana, data sources such as Prometheus and PostgreSQL, collectors, and credentials are not included in this repository.

## Dashboards

| Dashboard | Deployment | Coverage | Data sources |
| --- | --- | --- | --- |
| Unified dashboard | Grafana Git Sync | n8n runs, Hermes Agent and Kanban, Orca usage, OpenViking | PostgreSQL/TimescaleDB, Prometheus |
| Infrastructure monitoring | Grafana Git Sync | NAS and Mac mini hosts, Docker containers, M4 hardware | Prometheus/cAdvisor, node exporter, macmon |
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

- Shows total token usage, model call counts, daily token trends, and usage by model and thread.
- Tracks aggregation delay so both usage patterns and data freshness can be monitored.

#### 1-4. OpenViking monitoring

![OpenViking monitoring dashboard](assets/openviking%20monitoring%20dashboard.png)

- Shows exporter health, API requests, token usage, average request duration, and recent errors.
- Includes container CPU and memory, data size, session files, queue messages, and log metrics.

### 2. Infrastructure monitoring

![Docker monitoring dashboard](assets/macmini%20docker%20monitoring%20dashboard.png)

- Combines container and host health in the `NAS Docker`, `Mac mini Docker`, `NAS System`, and `Mac mini System` tabs.
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
- Infrastructure monitoring uses cAdvisor metrics from both hosts, the `node_exporter` and `macmini_node` node exporter jobs, and the `macmini_soc` macmon job.

### Infrastructure monitoring collection requirements

| Target | Prometheus job | Required metrics |
| --- | --- | --- |
| NAS system | `node_exporter` | `node_cpu_*`, `node_memory_*`, `node_filesystem_*`, `node_network_*`, `node_disk_*`, `node_load*`, `node_boot_time_seconds` |
| Mac mini system | `macmini_node` | The same standard node exporter metrics as the NAS |
| Mac mini M4 | `macmini_soc` | `macmon_cpu_*`, `macmon_gpu_*`, `macmon_ane_*`, `macmon_ram_*`, `macmon_sys_*` |

The infrastructure dashboard automatically populates `NAS Instance` and `Mac mini Instance` variables. If your Prometheus job names differ, update `node_exporter`, `macmini_node`, and `macmini_soc` in the dashboard JSON to match the actual scrape jobs. The NAS `node_exporter` target must be running for the NAS system and disk panels to receive data.

The current `macmini_node` target runs inside the OrbStack Linux VM, so its disk inventory, IOPS, and utilization describe OrbStack virtual block devices rather than the Mac's physical SSD. Native macOS disk/SMART monitoring requires a separate macOS collector or smartctl exporter. NAS RAID and SMART health are also outside the standard node exporter metric set and require dedicated exporters.

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
