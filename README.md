# hermes-plugin-harness-guard

Hermes plugin: post-execution result-correctness guard via GLM-5.2 review.

## What it does

Every tool call is recorded in an audit log (zero latency). Write operations (`write_file`, `patch`, `skill_manage`, dangerous `terminal` commands) trigger an automatic review by GLM-5.2 before the result reaches the model.

- **PASS**: result flows through unchanged
- **FAIL**: result is replaced with a warning explaining the issue and how to fix it. The model sees the warning instead of the "success" result, and can self-correct

## Scope

### Reviewed (triggers GLM-5.2, ~10-20s latency)
- `write_file` — any file write
- `patch` — any file edit
- `skill_manage` — create/edit/patch/write_file/remove_file actions
- `terminal` — commands matching dangerous patterns: `hermes config set/delete`, `rm -rf`, `git push --force`, `systemctl stop/restart`, `docker rm/stop`, `crontab`

### Not reviewed (zero latency)
- All read-only tools: `read_file`, `search_files`, `web_search`, `web_extract`, `browser_*`, etc.
- `terminal` commands not matching dangerous patterns
- `delegate_task` (sub-agent internals are not audited)
- `execute_code`, `cronjob`, `process`, etc.

### Not covered
- Pure text responses from the model (no tool call = no hook)
- Model's internal reasoning process
- Sub-agent internal operations

## Prerequisites

- Hermes Agent with plugin support
- `httpx` package: `pip install httpx`
- GLM-5.2 API key set as `ZAI_API_KEY` in `~/.hermes/.env`

## Install

```bash
cd ~/.hermes/plugins
git clone https://github.com/leonluo2008-ops/hermes-plugin-harness-guard.git harness-guard
pip install httpx  # if not already installed
```

Then enable in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - harness-guard
```

Restart Hermes gateway to load.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `ZAI_API_KEY` | (required) | GLM-5.2 API key |
| `HARNESS_GUARD_DISABLE` | unset | Set any value to disable the plugin without removing from config |

## Review timeout

Default: 60 seconds. Edit `harness_guard/reviewer.py` → `_TIMEOUT_S` to adjust.

## Review behavior

The review prompt checks four rules:
1. **Factual correctness**: values must be based on facts actually read in the audit trail
2. **Protected files**: `SOUL.md`, `.hermes.md`, `config.yaml`, `jobs.json` require user authorization
3. **Consistency**: writes must be consistent with what was read
4. **No hallucination**: invented values (API keys, URLs, ports, paths) are flagged

## Architecture

```
Every tool call
  ├─ post_tool_call hook → audit log (always, ~0ms)
  └─ if write operation
       └─ transform_tool_result hook → GLM-5.2 review (~10-20s)
            ├─ PASS → result unchanged
            └─ FAIL → result replaced with warning
```

- **Fail-open**: API errors, timeouts, missing key → skip review silently (never blocks the agent)
- **Thread-safe**: audit log uses `threading.Lock`
- **Audit trail**: per-session FIFO, 50 entries max; global cap 10,000 entries

## Uninstall

```bash
hermes plugins disable harness-guard
rm -rf ~/.hermes/plugins/harness-guard
```

Then remove from `config.yaml`:
```yaml
plugins:
  enabled: []
```

## License

MIT
