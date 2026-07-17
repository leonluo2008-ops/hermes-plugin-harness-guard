# hermes-plugin-harness-guard

Hermes 插件：通过 GLM-5.2 审查实现写入操作的结果正确性防护。

## 功能概述

每次工具调用都会被记录到审计日志中（零延迟）。写入操作（`write_file`、`patch`、`skill_manage`、危险的 `terminal` 命令）会在结果返回模型之前，自动触发 GLM-5.2 审查。

- **通过**：结果原样返回，模型正常收到
- **不通过**：结果被替换为警告信息，包含具体原因和修复建议。模型看到的是警告而非"操作成功"，可以据此自我修正

## 审查范围

### 会触发审查（调用 GLM-5.2，延迟约 10-20 秒）
- `write_file` — 任何文件写入
- `patch` — 任何文件编辑
- `skill_manage` — create / edit / patch / write_file / remove_file 操作
- `terminal` — 匹配危险模式的命令：`hermes config set/delete`、`rm -rf`、`git push --force`、`systemctl stop/restart`、`docker rm/stop`、`crontab`

### 不触发审查（零延迟）
- 所有只读工具：`read_file`、`search_files`、`web_search`、`web_extract`、`browser_*` 等
- `terminal` 中未匹配危险模式的命令
- `delegate_task`（子 agent 内部操作不受审查）
- `execute_code`、`cronjob`、`process` 等

### 无法覆盖
- 模型的纯文本回复（不经过工具调用 = 不触发 hook）
- 模型的内部推理过程
- 子 agent 内部操作

## 前置条件

- Hermes Agent（需支持 plugin）
- `httpx` 包：`pip install httpx`
- API key（按需设一个）:
  - `HARNESS_GUARD_API_KEY` — 任意 provider 的 key（推荐，最明确）
  - `ZAI_API_KEY` — Z.AI / GLM 默认（向后兼容）
  - `GLM_API_KEY` — 另一种 GLM 命名约定

## 安装

```bash
cd ~/.hermes/plugins
git clone https://github.com/leonluo2008-ops/hermes-plugin-harness-guard.git harness-guard
pip install httpx  # 如未安装
```

在 `~/.hermes/config.yaml` 中启用：

```yaml
plugins:
  enabled:
    - harness-guard
```

重启 Hermes gateway 即可加载。

## 配置项（环境变量）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HARNESS_GUARD_API_KEY` | （未设置） | API key（优先级最高） |
| `ZAI_API_KEY` | （未设置） | API key（fallback，向后兼容） |
| `GLM_API_KEY` | （未设置） | API key（fallback） |
| `HARNESS_GUARD_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | API base URL（OpenAI 兼容 chat completions） |
| `HARNESS_GUARD_MODEL` | `glm-5.2` | 模型名 |
| `HARNESS_GUARD_TIMEOUT_S` | `60` | 审查超时（秒，整数） |
| `HARNESS_GUARD_MAX_AUDIT_TRAIL_CHARS` | `4000` | 传给审查 prompt 的审计日志最大字符数 |
| `HARNESS_GUARD_DISABLE` | 未设置 | 设为任意值可禁用插件，无需修改 config |

### 常用预设示例

#### 用 Z.AI / GLM-5.2（默认，无需配置）

```bash
export ZAI_API_KEY="<your-zai-key>"
# 其他参数都用默认
```

#### 用任何 OpenAI 兼容的第三方 provider

```bash
export HARNESS_GUARD_API_KEY="<your-api-key>"
export HARNESS_GUARD_BASE_URL="<your-provider-base-url>"   # e.g. an OpenAI-compatible gateway
export HARNESS_GUARD_MODEL="<your-model-name>"
```

> **注意**：harness-guard 的 reviewer 走 OpenAI 兼容 chat completions 协议
> （`POST {base_url}/chat/completions`，Bearer auth）。
> 用前请确认你的 provider 支持这个 endpoint —— 可用 `curl` 自测：
>
> ```bash
> curl -X POST "${HARNESS_GUARD_BASE_URL}/chat/completions" \
>      -H "Authorization: Bearer ${HARNESS_GUARD_API_KEY}" \
>      -H "Content-Type: application/json" \
>      -d '{"model":"'"${HARNESS_GUARD_MODEL}"'","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
> ```
>
> 返回有效 JSON 即可。

## 审查超时

默认 60 秒。修改 `harness_guard/reviewer.py` 中的 `_TIMEOUT_S` 可调整。

## 审查规则

审查 prompt 检查以下四条规则：

1. **事实正确性**：写入的值必须基于审计日志中实际读取过的事实
2. **受保护文件**：`SOUL.md`、`.hermes.md`、`config.yaml`、`jobs.json` 的写入需要用户明确授权
3. **一致性检查**：写入内容必须与之前读取的内容和用户意图一致
4. **禁止幻觉**：凭空编造的值（API 密钥、URL、端口号、路径、配置字段名）会被标记

## 架构

```
每次工具调用
  ├─ post_tool_call hook → 写入审计日志（始终执行，约 0ms）
  └─ 如果是写入操作
       └─ transform_tool_result hook → GLM-5.2 审查（约 10-20s）
            ├─ 通过 → 结果原样返回
            └─ 不通过 → 结果替换为警告信息
```

- **故障开放（fail-open）**：API 报错、超时、密钥缺失时跳过审查，不会阻塞 agent
- **线程安全**：审计日志使用 `threading.Lock`
- **审计日志**：每会话 FIFO，上限 50 条；全局上限 10,000 条

## 卸载

```bash
hermes plugins disable harness-guard
rm -rf ~/.hermes/plugins/harness-guard
```

从 `config.yaml` 中移除：

```yaml
plugins:
  enabled: []
```

## 许可证

MIT
