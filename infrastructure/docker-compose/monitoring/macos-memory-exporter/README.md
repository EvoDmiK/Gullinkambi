# macOS memory exporter

Prometheus endpoint: `http://host.docker.internal:9092/metrics`

The LaunchAgent runs the exporter natively on macOS so it can read Mach VM and
memory-pressure counters that are not available inside the OrbStack VM.

## Grafana single-panel setup

Use a **Stat** visualization with `Show = Calculate` and
`Calculation = Last not null`. Add these instant queries to one panel:

| Ref | Display name | PromQL | Unit |
| --- | --- | --- | --- |
| A | 앱/익명 | `macos_memory_breakdown_bytes{job="macmini_soc",instance="macmini",type="application"}` | bytes (IEC) |
| B | Wired | `macos_memory_breakdown_bytes{job="macmini_soc",instance="macmini",type="wired"}` | bytes (IEC) |
| C | 압축 | `macos_memory_breakdown_bytes{job="macmini_soc",instance="macmini",type="compressed"}` | bytes (IEC) |
| D | 캐시·회수 가능 | `macos_memory_breakdown_bytes{job="macmini_soc",instance="macmini",type="cache_reclaimable"}` | bytes (IEC) |
| E | 기타 여유 | `macos_memory_breakdown_bytes{job="macmini_soc",instance="macmini",type="free_other"}` | bytes (IEC) |
| F | Swap | `macos_swap_used_bytes{job="macmini_soc",instance="macmini"}` | bytes (IEC) |
| G | Pressure 가용률 | `100 * macos_memory_pressure_available_ratio{job="macmini_soc",instance="macmini"}` | percent (0-100) |
| H | Pressure 상태 | `macos_memory_pressure_state{job="macmini_soc",instance="macmini"}` | none |

For query H, configure value mappings:

- `1` → `정상` (green)
- `2` → `경고` (yellow)
- `4` → `위험` (red)

The A-E values are disjoint and sum to total physical RAM. Pressure availability
overlaps allocated memory, and swap lives on disk, so F-H must not be included in
a RAM pie or stacked-RAM total.

## Metric meaning

- **앱/익명**: macmon-compatible used memory after separating Wired and compressed memory.
- **Wired**: kernel/system memory that cannot be paged out.
- **압축**: physical RAM occupied by the macOS compressor.
- **캐시·회수 가능**: approximation based on file-backed and purgeable pages.
- **기타 여유**: residual unoccupied memory after the above accounting.
- **Pressure 가용률**: kernel estimate of memory that is free or reclaimable; it overlaps allocated memory.
- **Pressure 상태**: discrete macOS state (`1` normal, `2` warning, `4` critical).
- **Swap**: memory pages currently moved to disk.
