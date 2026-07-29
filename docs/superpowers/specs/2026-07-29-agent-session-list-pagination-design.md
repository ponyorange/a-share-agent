# Agent 会话列表游标分页 Design

## Goal

侧栏/抽屉的「对话记录」支持按时间游标分页与上滑加载更早会话，避免一次拉全量，也避免硬顶截断导致旧会话不可见。

## Non-Goals

- 单会话内消息分页
- 会话搜索 / 归档
- 虚拟列表（首屏 20、按页追加即可）

## API

`GET /api/advisor/agent/sessions`

| 参数 | 说明 |
|------|------|
| `limit` | 默认 20，范围 1–50 |
| `before` | 可选，上一页最旧一条的 `updated_at`（ISO） |
| `before_id` | 可选，与 `before` 同条的 `session_id`，打破并列 |

排序：`updated_at desc, session_id desc`。

查询更旧一页：

```
(updated_at < before) OR (updated_at == before AND session_id < before_id)
```

响应：

```json
{
  "sessions": [ { "session_id", "title", "updated_at", "message_count" } ],
  "has_more": true
}
```

实现：`limit+1` 探测 `has_more`，返回前 `limit` 条。移除「全局最多 40 条就截断」的列表上限；单次请求仍封顶 50。

## Frontend

- 首屏：无游标拉最近一页，替换列表
- 侧栏与抽屉列表滚近底部（上拉）→ 若 `has_more` 且未在加载，用当前列表最旧项作游标追加
- 新建 / 删除 / 发消息后刷新：重置为首屏（丢掉已加载的更旧页）
- 加载中显示「加载更早对话…」；`has_more=false` 且已有会话时显示「没有更早对话」

## Compatibility

无 `before` 时行为与旧「最近 N 条」一致；旧客户端忽略 `has_more` 仍可用。
