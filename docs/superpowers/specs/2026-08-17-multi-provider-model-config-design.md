# 多模型提供方与分模块模型配置设计

## 目标

1. Agent 面板「DeepSeek 配置」改为「模型配置」，支持 DeepSeek、Kimi、千问三家，各自保存 API Key。
2. 八个功能槽位可独立选择「提供方 + 模型」；提供方下拉只列出已配置 Key 的；模型下拉只列出用户勾选的。
3. 切换提供方时，该槽位模型自动改为该提供方的默认模型。
4. 旧用户仅有顶层 DeepSeek Key 时，读设置即迁到新结构，行为与现在一致。

## 已确认决策

| 项 | 决策 |
|----|------|
| Key | 三家各自存；模块独立选提供方 + 模型 |
| 未配 Key 的提供方 | 模块下拉不出现，不能选 |
| 槽位 | 主 Agent、模拟盘、首页解读、定时任务、政策雷达、打板晋级、委员会快速、委员会深度 |
| Data Agent | 跟随主 Agent，不单独配 |
| 今日关注 LLM 备注 | 跟随首页解读槽位 |
| 委员会 | 两行：快速档 / 深度档，各自选提供方 + 模型；不再读 `config.yaml` 的 `committee.models` |
| 联网综述 `web_research` | 仅当主 Agent 槽位为 DeepSeek 且 DeepSeek Key 已配且开关打开时挂到主 Agent；Tavily 仍独立 |
| 模型清单 | 保存 Key 或点「刷新模型」时拉该提供方 `/models`；复选框勾选可用模型；默认预勾精选 id（仅当出现在返回列表中） |
| 架构 | 同一份 `user_llm_settings`，提供方层 + 槽位层 |
| Kimi 参数 | 不传 `temperature` / `top_p` / `presence_penalty` / `frequency_penalty`；思考模式用对方默认，不加开关 |
| 千问参数 | 继续传 temperature；不加 `enable_thinking` 开关 |
| 非目标 | 自定义 base_url、思考模式 UI、更多提供方、按槽位调温度、真实外网 Key 测试 |

## 提供方目录

写死在后端，用户不可改 base_url。

| `provider` | 显示名 | `base_url` | 系统默认模型 | 默认预勾（须出现在 `/models`） |
|------------|--------|------------|--------------|--------------------------------|
| `deepseek` | DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` | `deepseek-v4-flash`, `deepseek-v4-pro` |
| `kimi` | Kimi | `https://api.moonshot.cn/v1` | `kimi-k2.6` | `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k3` |
| `qwen` | 千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.7-plus` | `qwen3.7-flash`, `qwen3.7-plus`, `qwen3.8-max` |

文档链接（设置页）：

- DeepSeek：`https://api-docs.deepseek.com/zh-cn/`
- Kimi：`https://platform.kimi.com/docs/api/overview`
- 千问：`https://platform.qianwenai.com/docs/developer-guides/getting-started/text-generation-models`

`default_model` 计算：若系统默认模型在 `enabled_models` 中则用它，否则用 `enabled_models` 中按 `/models` 返回顺序的第一项。

## 数据模型

集合仍为 `user_llm_settings`。新文档形状：

```text
user_llm_settings
  user_id
  providers:
    deepseek | kimi | qwen:
      api_key_enc
      key_hint
      last_validated_at
      configured_at
      available_models: [{id}]     # /models 快照
      enabled_models: [id]         # 用户勾选，顺序与 available 求交后保持 available 顺序
      default_model: id
      models_synced_at
  slots:
    agent | paper | home | monitor | policy | limitup
    | committee_quick | committee_deep:
      provider
      model
  web_research_enabled
  tavily_enabled
  tavily_api_key_enc
  tavily_key_hint
  tavily_validated_at
  created_at
  updated_at
```

未配置的提供方在 `providers` 中可以缺省，读时按三家补齐公开字段（`configured: false`，空列表）。

`configured`（顶层）= 至少一家 `api_key_enc` 存在。

### 旧文档迁移

若存在顶层 `api_key_enc` 且 `providers.deepseek.api_key_enc` 不存在：

1. 把顶层 Key / hint / `last_validated_at` / `configured_at` 写入 `providers.deepseek`。
2. `available_models` 先空；`enabled_models` 含当时 `model`（若有）以及默认预勾 id 去重。
3. `default_model` 优先用当时 `model`，否则系统默认。
4. 八个槽位均设为 `{provider: "deepseek", model: <当时 model 或默认>}`。
5. `$unset` 顶层 `api_key_enc` / `key_hint` / `model` / `base_url` / `provider` / `last_validated_at` / `configured_at`。

在 `public_llm_settings` 与 `resolve_llm_credentials` 入口执行一次并写回，避免新旧字段并存。

## 架构

```text
设置页 /agent/settings
  ├─ PUT /providers/{id}          存 Key → ping → /models → 默认勾选
  ├─ POST /providers/{id}/models/refresh
  ├─ DELETE /providers/{id}       清 Key；占用槽位改到其它可用提供方
  └─ PUT /settings                勾选列表 + 八个槽位 + 联网开关

业务调用
  build_chat_model(user_id, slot=...)
    → resolve_llm_credentials(user_id, slot)
    → ChatOpenAI(api_key, base_url, model, …按提供方裁剪参数)

主 Agent 工具挂载
  web_research  ⟺  开关开 ∧ DeepSeek Key ∧ slots.agent.provider == "deepseek"
  tavily 路径   ⟺  原逻辑不变
```

槽位与调用点：

| slot | 调用点 |
|------|--------|
| `agent` | `agent/graph.py`、`agent/data_agent/graph.py` |
| `paper` | `paper_trader/decide.py` |
| `home` | `home_news_brief.py`、`home_news_stock_picks.py`、`agent/recommendations.py` |
| `monitor` | `monitor/llm_watch.py` |
| `policy` | `policy_watch/interpret.py` |
| `limitup` | `limitup_promote.py` |
| `committee_quick` / `committee_deep` | `committee/agents.py`（`tier="quick"|"deep"` 映射到对应槽位） |

`web_tool_flags` 拆成两层，避免首页与主 Agent 混用：

- 主 Agent 挂载 `web_research`：`web_research_enabled` ∧ DeepSeek Key ∧ `slots.agent.provider == "deepseek"`。
- 首页 `_maybe_fetch_web_items`：`web_research_enabled` ∧ DeepSeek Key（不看首页槽位用哪家），凭证读 `providers.deepseek`。

`web_research` 工具内部仍走 DeepSeek Anthropic 端点，模型名继续用 `config.yaml` 的 `agent_web.web_research.model`，与主对话槽位解耦。凭证改为读 `providers.deepseek`，不再读全局唯一 Key。

## API

均需登录。前缀 `/api/advisor/llm`。

### GET `/settings`

公开 JSON，不含明文 Key：

```json
{
  "configured": true,
  "providers": {
    "deepseek": {
      "configured": true,
      "key_hint": "sk-a…wxyz",
      "last_validated_at": "…",
      "available_models": [{"id": "deepseek-v4-flash"}],
      "enabled_models": ["deepseek-v4-flash"],
      "default_model": "deepseek-v4-flash",
      "models_synced_at": "…"
    },
    "kimi": {"configured": false, "key_hint": null, "available_models": [], "enabled_models": [], "default_model": "kimi-k2.6", "models_synced_at": null, "last_validated_at": null},
    "qwen": {"configured": false, "key_hint": null, "available_models": [], "enabled_models": [], "default_model": "qwen3.7-plus", "models_synced_at": null, "last_validated_at": null}
  },
  "slots": {
    "agent": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "paper": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "home": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "monitor": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "policy": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "limitup": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "committee_quick": {"provider": "deepseek", "model": "deepseek-v4-flash"},
    "committee_deep": {"provider": "deepseek", "model": "deepseek-v4-flash"}
  },
  "web_research_enabled": true,
  "tavily_enabled": false,
  "tavily_configured": false,
  "tavily_key_hint": null,
  "tavily_validated_at": null
}
```

无任何 Key 时 `configured=false`，各 `slots.*` 为 `null`。

未配置提供方的 `default_model` 仍返回系统默认，供前端在保存 Key 前展示预期默认值。

### PUT `/providers/{id}`

`id` ∈ `deepseek|kimi|qwen`。Body：`{ "api_key": "…" }`。

1. 用 OpenAI SDK：`base_url` 取目录，先 `models.list()`，再对 ping 模型做短 chat（`max_tokens=8`，内容 `ping`）。ping 模型 = 系统默认（若在 list 中）否则 list 第一项；若 list 失败则用系统默认 ping。
2. Kimi 的 ping / 后续 chat **不传** temperature 等固定参数。
3. ping 失败：不写库，HTTP 502，`detail` 含提供方名称（如「Kimi 校验失败: …」）。
4. ping 成功、list 失败：仍写 Key，`available_models=[]`，`enabled_models` 先放系统默认，`models_synced_at=null`。前端提示列表失败，可点刷新。
5. ping 成功、list 成功：写入快照；`enabled_models` = 默认预勾 ∩ 返回 id；若交集为空则 `enabled_models` = 全部返回 id 的前 3 个（不足 3 则全要）。重算 `default_model`。
6. 若这是用户第一把 Key：八个空槽位全部设为 `{provider: id, model: default_model}`。已有槽位不改。
7. 返回与 GET 相同的公开设置。

### POST `/providers/{id}/models/refresh`

须已有该提供方 Key。用已存 Key 再拉 `/models`。

- 失败：保留旧快照，HTTP 502。
- 成功：更新 `available_models` / `models_synced_at`；`enabled_models` 与新 id 求交，保持原相对顺序。交为空则恢复默认预勾（再 ∩ 新列表；仍空则取新列表前 3）。重算 `default_model`。槽位若 `provider==id` 且 `model` 不在新 `enabled_models` 中，改为该提供方 `default_model`。
- 返回公开设置。

### DELETE `/providers/{id}`

清除该提供方 Key 与模型缓存字段。占用该提供方的槽位改到「仍配置的提供方」中按 `deepseek → kimi → qwen` 的第一家，模型用那家 `default_model`。若没有剩余提供方，所有槽位置 `null`。Tavily 与其它提供方不动。返回公开设置。

### PUT `/settings`

Body（字段均可选，但至少一项）：

```json
{
  "enabled_models": {"deepseek": ["deepseek-v4-flash"], "kimi": ["kimi-k2.6"]},
  "slots": {
    "agent": {"provider": "kimi", "model": "kimi-k2.6"}
  },
  "web_research_enabled": true,
  "tavily_enabled": false,
  "tavily_api_key": "…"
}
```

校验：

- `enabled_models[id]` 仅当该提供方已配置；必须是 `available_models` 的子集；至少 1 个。若 `available_models` 仍为空，允许只含系统默认 id。
- 取消勾选后重算 `default_model`；若某槽位仍指向该提供方且模型不在新勾选中，改为新 `default_model`。
- `slots`：提供方必须已配置；模型必须在该提供方 `enabled_models` 中（保存时以本次 body 与现有合并后的勾选为准）。切换提供方时前端已把模型改成默认；后端仍校验，不替前端「猜」未传的槽位。
- 不允许把已配置提供方的勾选清空。
- Tavily 规则与现在相同。

兼容一版：若 body 仍带顶层 `api_key`，视为写入 DeepSeek（与 `PUT /providers/deepseek` 相同校验与拉模型）。若带顶层 `model` 且未传 `slots`，把**当前 `provider==deepseek` 的槽位**模型更新为该值（须通过勾选校验；DeepSeek 未配置则 400）。新字段优先；旧字段只为未刷新的旧前端不立刻 400。

### DELETE `/settings`

清除**全部**提供方 Key 与模型缓存，所有槽位置 `null`。Tavily 与联网开关保留（与现网「清 DeepSeek、留 Tavily」一致）。前端主路径用每张卡片的 `DELETE /providers/{id}`；此端点留给「全部清除」与测试。

### DELETE `/settings/tavily`

不变。

## 运行时

`resolve_llm_credentials(user_id, slot: str) -> {api_key, model, base_url, provider}`：

- 槽位为 `null` 或提供方无 Key：`ValueError("尚未配置 API Key，请先在模型配置中填写")`。
- 模型不在勾选中：仍用槽位记录的模型名调用一次（防御刷新竞态），但保存路径会纠正；不在运行时静默换模型，以免用户看不到。

`build_chat_model(..., slot=..., tier=None)`：

- 必须传 `slot` 或 `tier` 之一：`tier="quick"|"deep"` 时用 `committee_quick` / `committee_deep`，忽略 `slot`；否则 `slot` 必填，禁止默认落到主 Agent。
- 不再读取 `committee.models`。
- `provider=="kimi"`：构造 `ChatOpenAI` 时不传 `temperature`（以及 top_p / presence_penalty / frequency_penalty）。其它提供方保持现有 temperature。

缺凭证的 HTTP 映射仍为 403，文案统一：**「请先在模型配置中填写 API Key」**。不再出现「请先配置 DeepSeek API Key」。

## 前端

- 导航 `AGENT_NAV_LINKS`：`DeepSeek 配置` → `模型配置`。
- 页面 `/agent/settings`：hero 改为配置多家模型；三张提供方卡片（Key、保存校验、清除、模型复选框、刷新模型）；功能模块八行双下拉；联网搜索区块。
- 提供方下拉选项 = `providers[id].configured`。
- 模型下拉选项 = 该行当前提供方的 `enabled_models`。
- 切换提供方：该行 `model` 立即设为该提供方 `default_model`。
- 无任何 Key：模块区 disabled，提示先配置提供方。
- 主 Agent 槽位不是 `deepseek` 时：DeepSeek 联网综述 checkbox disabled，旁注「仅主 Agent 使用 DeepSeek 时可用」；不自动改已存的 `web_research_enabled`。
- 政策雷达 / 打板晋级 / 助手加载中等所有「DeepSeek 已配置 / 未配置 / 请先配置」改为「模型」或「API Key」中性文案；链到 `/agent/settings` 的链接文字改为「模型配置」。

## 错误处理

| 场景 | 行为 |
|------|------|
| Key ping 失败 | 不保存，502 |
| ping 成功、`/models` 失败 | 保存 Key，空快照，页面可刷新 |
| 刷新 `/models` 失败 | 保留旧快照，502 |
| 刷新后勾选求交为空 | 恢复默认预勾（再求交 / 前 3） |
| 槽位模型被取消勾选或从 list 消失 | 改为该提供方当前 `default_model` |
| 清除提供方 | 槽位改到剩余第一家；否则槽位清空 |
| 保存槽位时提供方未配或模型未勾选 | 400 |
| 已配置提供方勾选为空 | 400 |

## 测试

后端（mock OpenAI `models.list` 与 chat ping，不打外网）：

- 旧文档迁到 `providers.deepseek` 且八槽位填满。
- 保存 / 清除提供方；第一把 Key 填满空槽位；清除后槽位改到剩余提供方。
- 刷新模型：求交、交空恢复默认、占用中的模型回退。
- PUT slots 校验：未配提供方、未勾选模型。
- `resolve_llm_credentials(slot)`；委员会 `tier` 映射。
- 主 Agent 槽位非 DeepSeek 时 `web_tool_flags` / 挂载不包含 `web_research`；DeepSeek Key 仍在时首页补新闻仍可用。
- Kimi 的 `build_chat_model` 不把 temperature 传给 `ChatOpenAI`。

前端：

- 导航文案「模型配置」。
- 未配 Key 的提供方不出现在模块下拉。
- 切换提供方后模型变为该提供方 `default_model`。
- 主 Agent 非 DeepSeek 时联网综述禁用。
- 打板晋级 / 政策雷达等缺 Key 引导链到「模型配置」。

## 文案替换范围

所有用户可见「DeepSeek 配置 / 请先配置 DeepSeek / DeepSeek 已配置」改为中性「模型配置 / API Key」。系统提示词里「你是……（DeepSeek）」可改为不绑定提供方名称。不改 Tavily 文案，不改 `web_research` 工具说明里「服务端联网」的技术含义；工具描述若写死 DeepSeek，改为仅在该工具挂载时出现，可保留「DeepSeek 联网综述」作为功能名。
