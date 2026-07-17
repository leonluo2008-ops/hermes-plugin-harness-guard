# hermes-plugin-harness-guard

Hermes 插件：通过可配置的审查模型实现写入操作的结果正确性防护。

## 功能概述

每次工具调用都会被记录到审计日志中（零延迟）。写入操作（`write_file`、`patch`、`skill_manage`、危险的 `terminal` 命令）会在结果返回模型之前，自动触发审查模型审查。

- **通过**：结果原样返回，模型正常收到
- **不通过**：结果被替换为警告信息，包含具体原因和修复建议。模型看到的是警告而非"操作成功"，可以据此自我修正

## 审查范围

### 会触发审查（调用审查模型，延迟约 10-20 秒）

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

## 配置

### v1.2.0 起：插件自带 .env（推荐配置方式）

从 v1.2.0 起，harness-guard 在插件启动时**自动加载自身目录的 `.env`**，无需配置全局环境变量：

```bash
# 1. 复制模板
cp ~/.hermes/plugins/harness-guard/.env.example \
   ~/.hermes/plugins/harness-guard/.env

# 2. 编辑填值（去掉 # 注释 + 填真 key）
nano ~/.hermes/plugins/harness-guard/.env
```

### Provider 配置（v1.2.0 简化）

`.env` 只需要写 `HARNESS_GUARD_PROVIDER` + `HARNESS_GUARD_API_KEY`：

#### 选项 1：预设供应商（推荐）

预设值自动填好 base URL 和 model：

| `HARNESS_GUARD_PROVIDER=` | 自动 base URL | 自动 model |
|---|---|---|
| `glm` (默认) | `https://open.bigmodel.cn/api/coding/paas/v4` | `glm-5.2` |
| `minimax` | `https://api.minimaxi.com/v1` | `MiniMax-M3` |
| `juxin` | `https://api.jxincm.cn/v1` | `gemini-3.5-flash` |

```ini
HARNESS_GUARD_PROVIDER=glm
HARNESS_GUARD_API_KEY=sk-...
```

#### 选项 2：自定义覆盖（provider + 显式 base URL/model）

```ini
HARNESS_GUARD_PROVIDER=custom
HARNESS_GUARD_BASE_URL=https://api.example.com/v1
HARNESS_GUARD_MODEL=gpt-4
HARNESS_GUARD_API_KEY=sk-...
```

### 优先级

1. **进程环境变量**（如 `systemd Environment=` 或 shell `export`）—— **最高优先**
2. **plugin 自带 `.env`**（`~/.hermes/plugins/harness-guard/.env`）—— 默认

系统级环境变量始终覆盖 plugin `.env`，便于多机器部署时按机器覆盖。

### API key 解析顺序

1. `HARNESS_GUARD_API_KEY` — 推荐，最明确
2. `ZAI_API_KEY` / `GLM_API_KEY` — Z.AI / GLM fallback
3. `MINIMAX_CN_API_KEY` — Minimax fallback
4. `JUXIN_GEMINI_API_KEY` — Juxin fallback

**设置其中一个即可**，按优先级解析。

### 环境变量全表

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HARNESS_GUARD_PROVIDER` | `glm` | 供应商预设名（glm / minimax / juxin / custom） |
| `HARNESS_GUARD_API_KEY` | （未设置） | API key（优先级最高） |
| `HARNESS_GUARD_BASE_URL` | （用 preset 默认） | 覆盖 base URL（仅在 `PROVIDER=custom` 时必须） |
| `HARNESS_GUARD_MODEL` | （用 preset 默认） | 覆盖 model（仅在 `PROVIDER=custom` 时必须） |
| `HARNESS_GUARD_TIMEOUT_S` | `60` | 审查超时（秒，整数） |
| `HARNESS_GUARD_MAX_AUDIT_TRAIL_CHARS` | `4000` | 传给审查 prompt 的审计日志最大字符数 |
| `HARNESS_GUARD_DISABLE` | 未设置 | 设为任意值可禁用插件，无需修改 config |

### 自测 endpoint

不确定你的 provider 是否支持 OpenAI 兼容协议？先用 curl 自测：

```bash
curl -X POST "${HARNESS_GUARD_BASE_URL}/chat/completions" \
     -H "Authorization: Bearer ${HARNESS_GUARD_API_KEY}" \
     -H "Content-Type: application/json" \
     -d '{"model":"'"${HARNESS_GUARD_MODEL}"'","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
```

返回有效 JSON 即可。

## 审查规则

审查 prompt 检查以下四条规则：

1. **事实正确性**：写入的值必须基于审计日志中实际读取过的事实
2. **受保护文件**：`SOUL.md`、`.hermes.md`、`config.yaml`、`jobs.json` 的写入需要用户明确授权
3. **一致性检查**：写入内容必须与之前读取的内容和用户意图一致
4. **禁止幻觉**：凭空编造的值（API 密钥、URL、端口号、路径、模型名、配置字段名）会被标记

## 架构

```
每次工具调用
  ├─ post_tool_call hook → 写入审计日志（始终执行，约 0ms）
  └─ 如果是写入操作
       └─ transform_tool_result hook → 审查模型审查（约 10-20s）
            ├─ 通过 → 结果原样返回
            └─ 不通过 → 结果替换为警告信息
```

- **故障开放（fail-open）**：API 报错、超时、密钥缺失时跳过审查，不会阻塞 agent
- **线程安全**：审计日志使用 `threading.Lock`
- **审计日志**：每会话 FIFO，上限 50 条；全局上限 10,000 条
- **思考标签剥离**：v1.1.0 起，审查响应中的 `...` 块会自动剥离，避免 thinking 模型（GLM-5.2、MiniMax-M3、DeepSeek R1 等）误判 PASS/FAIL

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

## 更新日志

### v1.2.0（2026-07-17）

**新增**：

- ✅ **Pre-defined provider presets**（`glm` / `minimax` / `juxin`）：`.env` 里只需设 `HARNESS_GUARD_PROVIDER` + `HARNESS_GUARD_API_KEY`，base URL 和 model 自动映射
- ✅ **Fallback key 解析**：`MINIMAX_CN_API_KEY` / `JUXIN_GEMINI_API_KEY` 也作为插件 key 解析的备选
- ✅ **Custom provider**：`HARNESS_GUARD_PROVIDER=custom` 允许显式覆盖 base URL 和 model
- ✅ 重写 README：聚焦"provider 预设"作为主推荐配置模式
- ✅ 重写 `.env.example`：单一极简配置模板（只 4 行有效配置项）

**向后兼容**：

- v1.1.0 的 `HARNESS_GUARD_BASE_URL` / `HARNESS_GUARD_MODEL` / `HARNESS_GUARD_API_KEY` 等变量依然完整支持
- 不显式设置 `HARNESS_GUARD_PROVIDER` 时，默认 `glm` + Z.AI 端点 + `glm-5.2`（向后兼容 v1.0.0 / v1.1.0）

### v1.1.0（2026-07-09）

- ✅ `HARNESS_GUARD_*` 环境变量族（替代硬编码）
- ✅ Plugin 自带 `.env` 加载器（启动时自动读）
- ✅ `.env.example` 模板
- ✅ 剥离 `...` 标签（适配 thinking 模型）

### v1.0.0（2026-07-09）

- 初版发布：post_tool_call + transform_tool_result hooks，GLM-5.2 审查

## 许可证

MIT
