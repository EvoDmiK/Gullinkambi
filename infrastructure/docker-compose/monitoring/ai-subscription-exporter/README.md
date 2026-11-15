# AI subscription exporter

Codex, Claude Code, Google Antigravity의 개인 구독 사용률을 Prometheus 형식으로 노출하는 macOS용 로컬 exporter입니다. 로그인 토큰이나 설정 파일 자체는 노출하지 않습니다.

## 데이터 경로

- Codex: `codex app-server`의 `account/rateLimits/read`와 `account/usage/read`
- Claude: Claude Code OAuth 토큰으로 조회한 계정 전체 5시간·7일 사용률(`/api/oauth/usage`), 실패하면 Claude Code status line 입력의 `rate_limits.five_hour`와 `rate_limits.seven_day`
- Antigravity: `agy -p "/usage" --output-format json`의 Gemini·Claude/GPT 5시간·주간 quota
- HTTP: `127.0.0.1:9819/metrics`

Claude는 Claude Code 로그인 토큰(macOS 키체인 `Claude Code-credentials`, 없으면 `~/.claude/.credentials.json`)으로 `https://api.anthropic.com/api/oauth/usage`를 `CLAUDE_USAGE_INTERVAL_SECONDS`(기본 300초)마다 조회합니다. 어느 기기나 클라이언트에서 쓴 사용량도 계정 전체 값으로 반영됩니다. 이 엔드포인트는 공식 문서화된 API가 아니므로 응답 형식이 바뀌면 조회가 실패할 수 있고, 그때는 아래 status line 캐시로 되돌아갑니다. 토큰은 읽기만 하며 갱신하지 않습니다(refresh하면 Claude Code 로그인 세션이 바뀔 수 있습니다). 토큰이 만료된 경우 Claude Code를 한 번 사용하면 갱신됩니다. 토큰 값은 로그나 지표에 출력하지 않습니다.

Claude status line에는 첫 API 응답 이후 Pro/Max 구독의 5시간·7일 제한이 전달됩니다. `claude_statusline_capture.py`는 전체 입력 중 사용률과 모델명만 `~/.cache/gullinkambi/claude-status.json`에 저장합니다.

Claude는 모든 status line 입력에 `rate_limits`를 포함하지 않습니다. 제한 정보가 없는 입력은 기존 정상 캐시를 덮어쓰지 않으며, 마지막 정상 캐시는 `CLAUDE_MAX_AGE_SECONDS`까지 사용합니다. 유효한 캐시가 없거나 최대 허용 나이를 초과했을 때만 Claude의 `ai_subscription_collector_up`이 `0`이 됩니다.

## Mac mini 설치

Codex, Claude Code, Antigravity CLI에 로그인한 macOS 사용자로 실행합니다. Antigravity CLI는 `/usage`를 JSON으로 조회할 수 있는 버전을 사용합니다.

```bash
cd /Volumes/Example/Birds-Nest/docker-compose/monitoring/ai-subscription-exporter
python3 install.py --configure-claude-statusline
```

기존 Claude status line 설정이 있으면 installer가 원본 명령을 `~/.config/gullinkambi/claude-statusline-upstream.json`에 보관하고 multiplexer로 연결합니다. 구독 사용량을 캐시한 다음 기존 status line 명령에 동일한 JSON을 전달하므로 기존 표시와 hook 동작을 유지합니다. 제거할 때는 원본 설정을 복구합니다.

설치 후 Claude Code에서 메시지를 한 번 전송해야 status line에 구독 제한 값이 들어옵니다.

```bash
curl http://127.0.0.1:9819/metrics
launchctl print gui/$(id -u)/com.gullinkambi.ai-subscription-exporter
```

제거:

```bash
python3 install.py --uninstall
```

## 환경 변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `AI_SUBSCRIPTION_EXPORTER_HOST` | `127.0.0.1` | HTTP bind 주소 |
| `AI_SUBSCRIPTION_EXPORTER_PORT` | `9819` | HTTP 포트 |
| `AI_SUBSCRIPTION_CACHE_SECONDS` | `60` | CLI 재조회 간격 |
| `CODEX_BIN` | `codex` | Codex 실행 파일 |
| `CODEX_TIMEOUT_SECONDS` | `15` | Codex app-server 제한 시간 |
| `CLAUDE_USAGE_API` | `1` | `0`이면 OAuth 사용량 조회를 끄고 status line 캐시만 사용 |
| `CLAUDE_USAGE_INTERVAL_SECONDS` | `300` | Claude 사용량 API 조회 간격 |
| `CLAUDE_USAGE_TIMEOUT_SECONDS` | `10` | Claude 사용량 API 제한 시간 |
| `CLAUDE_USAGE_URL` | `https://api.anthropic.com/api/oauth/usage` | 사용량 조회 주소 |
| `CLAUDE_STATUS_FILE` | `~/.cache/gullinkambi/claude-status.json` | status line 캐시 |
| `CLAUDE_MAX_AGE_SECONDS` | `86400` | Claude 캐시 최대 허용 나이 |
| `ANTIGRAVITY_BIN` | `agy` | Antigravity CLI 실행 파일 |
| `ANTIGRAVITY_TIMEOUT_SECONDS` | `45` | Antigravity `/usage` 제한 시간. 정상 응답이 20초 이상 걸릴 수 있어 Prometheus scrape timeout보다 짧게 설정합니다. |

Antigravity는 `provider="antigravity"`와 `pool="gemini|third_party"`로 구분합니다. `remaining_fraction`은 0~100 범위의 사용률로 변환하고, 5시간 구간은 `window="session"`, 주간 구간은 `window="weekly"`로 정규화합니다. Codex·Claude·Antigravity 수집은 병렬 실행해 Prometheus scrape 지연을 줄입니다.

## Grafana 알람

Gullinkambi는 `provisioning/alerting/ai-subscription.yml`에서 다음 Grafana-managed alert를 평가합니다.

| 규칙 | 조건 | 지속 시간 |
| --- | --- | --- |
| AI subscription collector down | Codex, Claude 또는 Antigravity pool의 `ai_subscription_collector_up < 1` | 5분 |
| Codex subscription data stale | Codex 마지막 성공 수집이 15분 이상 지연 | 5분 |
| AI weekly quota high | Codex, Claude 또는 Antigravity pool의 주간 사용률 85% 초과 | 10분 |

Claude는 실제 메시지 처리 시 status line이 갱신되므로 15분 stale 규칙을 적용하지 않습니다. 대신 마지막 유효 캐시를 보존하고 기본 24시간 만료 여부를 `ai_subscription_collector_up`에 반영합니다. 빈 `rate_limits` 입력 때문에 collector down 알람이 발생한다면 실행 중인 파일에 캐시 보존 수정이 배포됐는지 확인하세요.

OrbStack은 `host.docker.internal` 요청을 호스트 loopback으로 전달하므로 기본 `127.0.0.1` bind로 접근할 수 있습니다.

## 검증

외부 패키지는 필요하지 않습니다.

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/gullinkambi-ai-subscription-pycache \
  python3 -m py_compile exporter.py claude_statusline_capture.py claude_statusline_mux.py install.py
```

참고 문서:

- <https://learn.chatgpt.com/docs/app-server>
- <https://code.claude.com/docs/en/statusline#rate-limit-usage>
- <https://antigravity.google/docs/cli/commands/usage/>
- <https://antigravity.google/docs/cli/headless/>
