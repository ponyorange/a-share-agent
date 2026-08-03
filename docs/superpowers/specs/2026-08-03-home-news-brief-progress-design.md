# 首页 Agent 解读阶段进度

日期：2026-08-03  
状态：已确认  
关联：`docs/superpowers/specs/2026-08-02-home-news-brief-design.md`、`docs/superpowers/specs/2026-08-02-home-news-stock-picks-design.md`

## 目标

刷新首页「Agent 解读」时展示**阶段文案**，让用户知道当前在做什么（整理资讯 / 撰写解读 / 筛选观察股），而不是只有「生成中…」。

## 已确认决策

| 项 | 决策 |
|----|------|
| 粒度 | 仅阶段文案（无百分比、无工具名） |
| 通道 | 方案 A：brief 文档 `progress` 字段 + 现有 ~2s 轮询 |
| 不做 | SSE、假进度计时、改双段流水线业务逻辑 |

## 数据

`home_news_briefs` / API 响应增加：

```text
progress: { phase: string, message: string } | null
```

| phase | message |
|-------|---------|
| `news` | 整理今日资讯… |
| `brief` | 撰写市场解读… |
| `picks` | 筛选资讯驱动观察股… |

- `running`：按阶段写入 `progress`
- `ready` / `idle`：`progress = null`
- `failed`：以 `error` 为主；可不保留 progress

## 后台写入点

1. 线程启动 / 取新闻包前 → `news`
2. 简报 LLM 前 → `brief`
3. 选股 Agent 前 → `picks`
4. 成功 → `status=ready`，`progress=null`

## 前端

- `running`：按钮「生成中…」；其下展示 `progress.message`，缺省回退「正在生成解读…」
- 轮询间隔不变（~2s）
- 非 running 不展示进度行

## 测试

- 刷新启动时 public 含 `progress.phase == news`（或等价初始阶段）
- 流水线中 mock 断言阶段切换被写入
- 前端：running + progress 展示文案；无 progress 时回退文案

## 验收

1. 点「刷新解读」后可见阶段中文提示，且会随阶段变化（轮询可见）
2. 完成后进度消失，展示解读结果
3. 不引入 SSE  
