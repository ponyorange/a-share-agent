# 今日关注刷新候选池 SSE

日期：2026-07-23  
状态：已确认

## 目标

「刷新候选池」改为 SSE：建池 progress + 精算 N/M，完成后返回与现有 JSON 同形的推荐结果。

## 方案

- 新接口 `GET /api/advisor/recommendations/refresh/stream`
- 保留 `GET /recommendations` 读归档
- 前端刷新按钮走 SSE 并显示进度

## 事件

`meta` → `progress*`（universe / screen / precise）→ `done` | `error`
