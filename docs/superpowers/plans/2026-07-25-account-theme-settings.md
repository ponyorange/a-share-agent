# 账号级浅色配色设置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `frontend-advisor` 提供两套浅色配色模板、十项可编辑语义色、账号级后端同步，并将主题统一应用于基础面板和 Agent 面板。

**Architecture:** 后端以 `user_ui_settings` 单文档保存当前模板和完整基础色值，通过鉴权的 GET/PUT 接口读写。前端以主题领域模块管理模板、校验、派生色、对比度和用户隔离缓存，以 `ThemeProvider` 负责加载、保存和全局应用；设置页只维护未保存草稿。现有 CSS 先从“品牌色兼任涨跌/状态色”迁移为独立语义 token，再由主题模块写入自定义属性。

**Tech Stack:** FastAPI、Pydantic 2、PyMongo、pytest、React 19、TypeScript 6、React Router 7、Vitest 4、Testing Library、原生 CSS 自定义属性。

## Global Constraints

- 整个应用只提供浅色主题，基础面板和 Agent 面板使用同一账号主题。
- 内置模板仅为 `modern_data`（蓝涨、琥珀跌）和 `classic_market`（红涨、绿跌）。
- 后端保存当前模板和全部十个基础语义色，不保存派生透明色。
- 用户切换模板会覆盖当前草稿；存在未保存修改时先确认。
- 对比度不足只警告，不阻止保存；非法或缺失的 `#RRGGBB` 必须阻止提交。
- 登录后以后端为准；后端不可用时回退到按用户 ID 隔离的浏览器缓存，再回退到现代数据模板。
- 不引入颜色、表单、状态管理或 CSS 第三方依赖。
- 不增加深色模式、多主题命名、主题历史、管理员主题或自动保存。
- 未经用户明确要求不创建 Git commit；每项任务以测试通过和复核为检查点。

---

## File Map

### 新增

- `backend/app/advisor/ui_settings.py`：模板常量、颜色校验、默认读取和 MongoDB upsert。
- `backend/tests/test_ui_settings.py`：主题存储、默认值、用户隔离和服务层校验。
- `backend/tests/test_ui_settings_routes.py`：GET/PUT 鉴权路由与 Pydantic 严格校验。
- `frontend-advisor/src/theme/theme.ts`：主题类型、模板、颜色规范化、CSS 应用和对比度计算。
- `frontend-advisor/src/theme/theme.test.ts`：主题领域逻辑测试。
- `frontend-advisor/src/theme/themeApi.ts`：UI 设置 GET/PUT。
- `frontend-advisor/src/theme/themeStorage.ts`：按用户 ID 隔离的缓存和首屏 bootstrap。
- `frontend-advisor/src/theme/ThemeProvider.tsx`：账号主题加载、保存、错误回退和 Context。
- `frontend-advisor/src/theme/ThemeProvider.test.tsx`：缓存、服务端覆盖、保存失败和退出回退测试。
- `frontend-advisor/src/components/ThemeColorField.tsx`：颜色选择器与十六进制输入。
- `frontend-advisor/src/pages/SettingsPage.tsx`：模板、草稿、预览、警告、重置和保存交互。
- `frontend-advisor/src/pages/SettingsPage.test.tsx`：设置页主要交互测试。

### 修改

- `backend/app/advisor/routes.py`：增加 UI 设置请求模型和 GET/PUT 路由。
- `backend/app/db.py`：为 `user_ui_settings.user_id` 建唯一索引。
- `frontend-advisor/src/main.tsx`：React 挂载前应用当前缓存主题。
- `frontend-advisor/src/App.tsx`：挂载 ThemeProvider，增加基础设置导航和路由，退出时恢复默认主题。
- `frontend-advisor/src/App.test.tsx`：设置入口、路由和 Provider 接入回归。
- `frontend-advisor/src/styles.css`：浅色默认 token、语义颜色迁移、设置页和预览样式。

---

### Task 1: 后端主题领域与持久化

**Files:**
- Create: `backend/app/advisor/ui_settings.py`
- Create: `backend/tests/test_ui_settings.py`

**Interfaces:**
- Produces: `ThemeId = Literal["modern_data", "classic_market"]`
- Produces: `COLOR_KEYS: tuple[str, ...]`
- Produces: `DEFAULT_THEMES: dict[str, dict[str, str]]`
- Produces: `default_ui_settings() -> dict[str, Any]`
- Produces: `normalize_colors(colors: Mapping[str, Any]) -> dict[str, str]`
- Produces: `get_ui_settings(user_id: str) -> dict[str, Any]`
- Produces: `save_ui_settings(user_id: str, *, active_template: str, colors: Mapping[str, Any]) -> dict[str, Any]`
- Consumes: `app.db.get_db`

- [ ] **Step 1: 写服务层失败测试**

```python
# backend/tests/test_ui_settings.py
from copy import deepcopy

import pytest

from app.advisor import ui_settings as ui


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def find_one(self, query, projection=None):
        doc = self.docs.get(query["user_id"])
        return deepcopy(doc) if doc else None

    def update_one(self, query, update, upsert=False):
        uid = query["user_id"]
        current = self.docs.get(uid, {})
        if uid not in self.docs:
            current.update(deepcopy(update.get("$setOnInsert", {})))
        current.update(deepcopy(update["$set"]))
        self.docs[uid] = current


class FakeDb:
    def __init__(self):
        self.user_ui_settings = FakeCollection()


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(ui, "get_db", lambda: db)
    return db


def test_missing_settings_returns_modern_data_without_writing(fake_db):
    result = ui.get_ui_settings("u1")
    assert result["active_template"] == "modern_data"
    assert result["colors"] == ui.DEFAULT_THEMES["modern_data"]
    assert fake_db.user_ui_settings.docs == {}


def test_save_normalizes_hex_and_isolates_users(fake_db):
    first = dict(ui.DEFAULT_THEMES["modern_data"], brand="#abcdef")
    second = dict(ui.DEFAULT_THEMES["classic_market"], brand="#123456")
    saved = ui.save_ui_settings("u1", active_template="modern_data", colors=first)
    ui.save_ui_settings("u2", active_template="classic_market", colors=second)
    assert saved["colors"]["brand"] == "#ABCDEF"
    assert ui.get_ui_settings("u1")["colors"]["brand"] == "#ABCDEF"
    assert ui.get_ui_settings("u2")["colors"]["brand"] == "#123456"


@pytest.mark.parametrize(
    "colors",
    [
        {},
        dict(ui.DEFAULT_THEMES["modern_data"], brand="red"),
        dict(ui.DEFAULT_THEMES["modern_data"], extra="#FFFFFF"),
    ],
)
def test_save_rejects_incomplete_invalid_or_extra_colors(fake_db, colors):
    with pytest.raises(ValueError):
        ui.save_ui_settings("u1", active_template="modern_data", colors=colors)


def test_save_rejects_unknown_template(fake_db):
    with pytest.raises(ValueError, match="模板"):
        ui.save_ui_settings(
            "u1",
            active_template="dark",
            colors=ui.DEFAULT_THEMES["modern_data"],
        )
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && pytest -q tests/test_ui_settings.py`

Expected: FAIL，提示无法导入 `app.advisor.ui_settings`。

- [ ] **Step 3: 实现模板、严格颜色校验和 Mongo upsert**

```python
# backend/app/advisor/ui_settings.py
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from ..db import get_db

COLOR_KEYS = (
    "page_bg", "surface", "text_primary", "text_muted", "border",
    "brand", "market_up", "market_down", "success", "error",
)
THEME_IDS = ("modern_data", "classic_market")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

DEFAULT_THEMES = {
    "modern_data": {
        "page_bg": "#F6F7FB", "surface": "#FFFFFF",
        "text_primary": "#273247", "text_muted": "#778195",
        "border": "#E5E8F1", "brand": "#6673D9",
        "market_up": "#3568B8", "market_down": "#A96918",
        "success": "#377659", "error": "#A84C5B",
    },
    "classic_market": {
        "page_bg": "#F7F8FA", "surface": "#FFFFFF",
        "text_primary": "#2A3140", "text_muted": "#6F7A8C",
        "border": "#E4E7ED", "brand": "#526FC1",
        "market_up": "#C24B5A", "market_down": "#328268",
        "success": "#2F7A5B", "error": "#B54759",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_ui_settings() -> dict[str, Any]:
    return {
        "active_template": "modern_data",
        "colors": dict(DEFAULT_THEMES["modern_data"]),
        "updated_at": None,
    }


def normalize_colors(colors: Mapping[str, Any]) -> dict[str, str]:
    if set(colors) != set(COLOR_KEYS):
        raise ValueError("配色字段必须完整且不能包含额外字段")
    normalized: dict[str, str] = {}
    for key in COLOR_KEYS:
        value = colors[key]
        if not isinstance(value, str) or not HEX_RE.fullmatch(value):
            raise ValueError(f"{key} 必须是 #RRGGBB")
        normalized[key] = value.upper()
    return normalized


def _public(doc: Mapping[str, Any]) -> dict[str, Any]:
    updated = doc.get("updated_at")
    return {
        "active_template": doc["active_template"],
        "colors": normalize_colors(doc["colors"]),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
    }


def get_ui_settings(user_id: str) -> dict[str, Any]:
    doc = get_db().user_ui_settings.find_one({"user_id": user_id}, {"_id": 0})
    return _public(doc) if doc else default_ui_settings()


def save_ui_settings(
    user_id: str, *, active_template: str, colors: Mapping[str, Any]
) -> dict[str, Any]:
    if active_template not in THEME_IDS:
        raise ValueError("未知配色模板")
    normalized = normalize_colors(colors)
    now = _now()
    get_db().user_ui_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "active_template": active_template,
                "colors": normalized,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return get_ui_settings(user_id)
```

- [ ] **Step 4: 运行服务层测试**

Run: `cd backend && pytest -q tests/test_ui_settings.py`

Expected: PASS，6 个参数化后的测试场景全部通过。

- [ ] **Step 5: 检查变更**

Run: `git diff --check -- backend/app/advisor/ui_settings.py backend/tests/test_ui_settings.py`

Expected: 无输出。仅在用户明确授权提交后提交此任务。

---

### Task 2: 后端鉴权 API 与索引

**Files:**
- Modify: `backend/app/advisor/routes.py:56-60, 140-178`
- Modify: `backend/app/db.py:153-159`
- Create: `backend/tests/test_ui_settings_routes.py`

**Interfaces:**
- Consumes: Task 1 的 `get_ui_settings`、`save_ui_settings`、`COLOR_KEYS`
- Produces: `GET /api/advisor/ui/settings`
- Produces: `PUT /api/advisor/ui/settings`
- Produces: `user_ui_settings.user_id` 唯一索引

- [ ] **Step 1: 写路由失败测试**

```python
# backend/tests/test_ui_settings_routes.py
from fastapi.testclient import TestClient

from app.advisor import routes
from app.advisor.ui_settings import DEFAULT_THEMES
from app.auth import get_current_user
from app.main import app


def test_get_and_put_ui_settings_use_authenticated_user(monkeypatch):
    calls = []
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    monkeypatch.setattr(
        routes,
        "get_ui_settings",
        lambda uid: {
            "active_template": "modern_data",
            "colors": DEFAULT_THEMES["modern_data"],
            "updated_at": None,
        },
    )
    monkeypatch.setattr(
        routes,
        "save_ui_settings",
        lambda uid, **body: calls.append((uid, body)) or {
            **body,
            "updated_at": "2026-07-25T05:30:00+00:00",
        },
    )
    try:
        get_response = TestClient(app).get("/api/advisor/ui/settings")
        put_response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={
                "active_template": "classic_market",
                "colors": DEFAULT_THEMES["classic_market"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert get_response.status_code == 200
    assert put_response.status_code == 200
    assert calls[0][0] == "u1"
    assert calls[0][1]["active_template"] == "classic_market"


def test_put_rejects_extra_or_invalid_color_fields():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    colors = dict(DEFAULT_THEMES["modern_data"], extra="#FFFFFF")
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={"active_template": "modern_data", "colors": colors},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
```

- [ ] **Step 2: 运行路由测试并确认失败**

Run: `cd backend && pytest -q tests/test_ui_settings_routes.py`

Expected: FAIL，GET 返回 404。

- [ ] **Step 3: 增加严格请求模型和路由**

在 `routes.py` 导入 `Literal` 及 Task 1 服务，并增加禁止额外字段的 Pydantic 模型：

```python
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from .ui_settings import get_ui_settings, save_ui_settings


class UiColorsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_bg: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    surface: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_primary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_muted: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    border: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    brand: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    market_up: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    market_down: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    success: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    error: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class UiSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_template: Literal["modern_data", "classic_market"]
    colors: UiColorsBody


@router.get("/ui/settings")
def ui_settings_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    return get_ui_settings(_bind(user))


@router.put("/ui/settings")
def ui_settings_put(
    body: UiSettingsBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    uid = _bind(user)
    try:
        return save_ui_settings(
            uid,
            active_template=body.active_template,
            colors=body.colors.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: 增加唯一索引**

在 `ensure_indexes()` 的 per-user 设置区域增加：

```python
try:
    db.user_ui_settings.create_index(
        [("user_id", ASCENDING)], unique=True
    )
except Exception:
    pass
```

- [ ] **Step 5: 运行后端相关测试**

Run: `cd backend && pytest -q tests/test_ui_settings.py tests/test_ui_settings_routes.py`

Expected: PASS。

- [ ] **Step 6: 运行后端回归**

Run: `cd backend && pytest -q`

Expected: PASS；若环境缺少外部服务，仅记录与本改动无关且可复现的既有失败，不修改主题代码掩盖环境问题。

- [ ] **Step 7: 检查变更**

Run: `git diff --check -- backend/app/advisor/ui_settings.py backend/app/advisor/routes.py backend/app/db.py backend/tests/test_ui_settings.py backend/tests/test_ui_settings_routes.py`

Expected: 无输出。仅在用户明确授权提交后提交此任务。

---

### Task 3: 前端主题领域、派生色与缓存

**Files:**
- Create: `frontend-advisor/src/theme/theme.ts`
- Create: `frontend-advisor/src/theme/theme.test.ts`
- Create: `frontend-advisor/src/theme/themeStorage.ts`

**Interfaces:**
- Produces: `ThemeId`
- Produces: `ThemeColors`
- Produces: `ThemeSettings`
- Produces: `THEME_TEMPLATES`
- Produces: `normalizeHex(value: string): string | null`
- Produces: `applyTheme(settings: ThemeSettings): void`
- Produces: `getContrastWarnings(colors: ThemeColors): ContrastWarning[]`
- Produces: `readCachedTheme(userId: string): ThemeSettings | null`
- Produces: `writeCachedTheme(userId: string, settings: ThemeSettings): void`
- Produces: `bootstrapTheme(userId?: string): void`

- [ ] **Step 1: 写主题领域失败测试**

```ts
// frontend-advisor/src/theme/theme.test.ts
import { beforeEach, expect, it } from 'vitest'
import {
  THEME_TEMPLATES,
  applyTheme,
  getContrastWarnings,
  normalizeHex,
} from './theme'
import { readCachedTheme, writeCachedTheme } from './themeStorage'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('style')
})

it('两套模板都有且仅有十个基础语义色', () => {
  const expected = [
    'page_bg', 'surface', 'text_primary', 'text_muted', 'border',
    'brand', 'market_up', 'market_down', 'success', 'error',
  ].sort()
  expect(Object.keys(THEME_TEMPLATES.modern_data.colors).sort()).toEqual(expected)
  expect(Object.keys(THEME_TEMPLATES.classic_market.colors).sort()).toEqual(expected)
})

it('规范化颜色并拒绝非法值', () => {
  expect(normalizeHex('#abcdef')).toBe('#ABCDEF')
  expect(normalizeHex('abcdef')).toBeNull()
  expect(normalizeHex('#abcd')).toBeNull()
})

it('应用基础色和派生柔和色', () => {
  applyTheme(THEME_TEMPLATES.modern_data)
  const root = document.documentElement.style
  expect(root.getPropertyValue('--color-market-up')).toBe('#3568B8')
  expect(root.getPropertyValue('--color-market-up-soft')).toBe('rgba(53, 104, 184, 0.14)')
})

it('低对比度只产生警告', () => {
  const colors = {
    ...THEME_TEMPLATES.modern_data.colors,
    text_primary: '#F6F7FB',
  }
  expect(getContrastWarnings(colors).some((item) => item.field === 'text_primary')).toBe(true)
})

it('缓存按用户隔离', () => {
  writeCachedTheme('u1', THEME_TEMPLATES.modern_data)
  writeCachedTheme('u2', THEME_TEMPLATES.classic_market)
  expect(readCachedTheme('u1')?.active_template).toBe('modern_data')
  expect(readCachedTheme('u2')?.active_template).toBe('classic_market')
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/theme/theme.test.ts`

Expected: FAIL，提示无法解析主题模块。

- [ ] **Step 3: 实现主题类型、模板和 CSS 映射**

`theme.ts` 使用以下公开结构：

```ts
export type ThemeId = 'modern_data' | 'classic_market'

export type ThemeColors = {
  page_bg: string
  surface: string
  text_primary: string
  text_muted: string
  border: string
  brand: string
  market_up: string
  market_down: string
  success: string
  error: string
}

export type ThemeSettings = {
  active_template: ThemeId
  colors: ThemeColors
  updated_at?: string | null
}

export type ContrastWarning = {
  field: keyof ThemeColors
  against: keyof ThemeColors
  ratio: number
  minimum: number
}
```

实现细节：

- `THEME_TEMPLATES` 精确使用设计文档中的两组色值，并以 `Object.freeze` 防止编辑器修改模板对象。
- `normalizeHex` 只接受 `^#[0-9A-Fa-f]{6}$` 并返回大写。
- `hexToRgba(hex, alpha)` 将 `#RRGGBB` 转为 `rgba(r, g, b, alpha)`。
- `applyTheme` 设置十个基础属性以及 brand/up/down/success/error 的 `0.14` soft 属性，并设置 `document.documentElement.dataset.themeTemplate`。
- WCAG 计算使用线性化 sRGB：

```ts
const linear = (channel: number) => {
  const value = channel / 255
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}
const luminance = (hex: string) =>
  0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
const contrast = (a: string, b: string) =>
  (Math.max(luminance(a), luminance(b)) + 0.05) /
  (Math.min(luminance(a), luminance(b)) + 0.05)
```

- `getContrastWarnings` 检查主/次文字分别对 page_bg 和 surface，阈值 `4.5`；检查 brand、market_up、market_down、success、error 对 surface，阈值 `3`。

- [ ] **Step 4: 实现用户隔离缓存和 bootstrap**

`themeStorage.ts`：

```ts
import { THEME_TEMPLATES, applyTheme, isThemeSettings, type ThemeSettings } from './theme'

const key = (userId: string) => `advisor_theme:${userId}`

export function readCachedTheme(userId: string): ThemeSettings | null {
  try {
    const raw = localStorage.getItem(key(userId))
    const parsed: unknown = raw ? JSON.parse(raw) : null
    return isThemeSettings(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function writeCachedTheme(userId: string, settings: ThemeSettings) {
  localStorage.setItem(key(userId), JSON.stringify(settings))
}

export function bootstrapTheme(userId?: string) {
  applyTheme(
    (userId ? readCachedTheme(userId) : null) ?? THEME_TEMPLATES.modern_data,
  )
}
```

`isThemeSettings` 必须验证模板 ID、恰好十个字段及每个值均为合法十六进制色，不能仅类型断言 JSON。

- [ ] **Step 5: 运行主题测试**

Run: `cd frontend-advisor && npx vitest run src/theme/theme.test.ts`

Expected: PASS。

- [ ] **Step 6: 检查变更**

Run: `git diff --check -- frontend-advisor/src/theme/theme.ts frontend-advisor/src/theme/theme.test.ts frontend-advisor/src/theme/themeStorage.ts`

Expected: 无输出。仅在用户明确授权提交后提交此任务。

---

### Task 4: 前端 API 与 ThemeProvider 同步

**Files:**
- Create: `frontend-advisor/src/theme/themeApi.ts`
- Create: `frontend-advisor/src/theme/ThemeProvider.tsx`
- Create: `frontend-advisor/src/theme/ThemeProvider.test.tsx`

**Interfaces:**
- Consumes: Task 3 的 `ThemeSettings`、`applyTheme`、缓存函数
- Produces: `fetchThemeSettings(): Promise<ThemeSettings>`
- Produces: `saveThemeSettings(settings: ThemeSettings): Promise<ThemeSettings>`
- Produces: `ThemeProvider({ userId, children })`
- Produces: `useTheme(): { settings, loading, error, save }`

- [ ] **Step 1: 写 Provider 失败测试**

```tsx
// frontend-advisor/src/theme/ThemeProvider.test.tsx
import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { THEME_TEMPLATES } from './theme'
import { ThemeProvider, useTheme } from './ThemeProvider'

const fetchThemeSettings = vi.hoisted(() => vi.fn())
const saveThemeSettings = vi.hoisted(() => vi.fn())
vi.mock('./themeApi', () => ({ fetchThemeSettings, saveThemeSettings }))

function Harness() {
  const theme = useTheme()
  return (
    <>
      <span>{theme.settings.active_template}</span>
      <button
        onClick={() => {
          void theme.save(THEME_TEMPLATES.classic_market).catch(() => undefined)
        }}
      >
        保存
      </button>
    </>
  )
}

beforeEach(() => {
  localStorage.clear()
  fetchThemeSettings.mockReset()
  saveThemeSettings.mockReset()
})

it('先用缓存，再由服务端覆盖并写回缓存', async () => {
  localStorage.setItem('advisor_theme:u1', JSON.stringify(THEME_TEMPLATES.classic_market))
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.modern_data)
  render(<ThemeProvider userId="u1"><Harness /></ThemeProvider>)
  expect(screen.getByText('classic_market')).toBeInTheDocument()
  await screen.findByText('modern_data')
  expect(document.documentElement.style.getPropertyValue('--color-brand')).toBe('#6673D9')
})

it('保存失败时保持原已应用主题', async () => {
  const user = userEvent.setup()
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.modern_data)
  saveThemeSettings.mockRejectedValue(new Error('网络错误'))
  render(<ThemeProvider userId="u1"><Harness /></ThemeProvider>)
  await screen.findByText('modern_data')
  await user.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(screen.getByText('modern_data')).toBeInTheDocument())
  expect(document.documentElement.style.getPropertyValue('--color-brand')).toBe('#6673D9')
})

it('退出账号后恢复现代数据默认主题', async () => {
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.classic_market)
  const view = render(<ThemeProvider userId="u1"><Harness /></ThemeProvider>)
  await screen.findByText('classic_market')
  view.rerender(<ThemeProvider userId={null}><Harness /></ThemeProvider>)
  expect(await screen.findByText('modern_data')).toBeInTheDocument()
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/theme/ThemeProvider.test.tsx`

Expected: FAIL，提示 Provider 模块不存在。

- [ ] **Step 3: 实现 API**

```ts
// frontend-advisor/src/theme/themeApi.ts
import { authFetch } from '../auth'
import type { ThemeSettings } from './theme'

export function fetchThemeSettings() {
  return authFetch<ThemeSettings>('/api/advisor/ui/settings')
}

export function saveThemeSettings(settings: ThemeSettings) {
  return authFetch<ThemeSettings>('/api/advisor/ui/settings', {
    method: 'PUT',
    body: JSON.stringify({
      active_template: settings.active_template,
      colors: settings.colors,
    }),
  })
}
```

- [ ] **Step 4: 实现 Provider**

Provider 必须：

- 以 `userId` 对应缓存或现代数据模板初始化。
- `userId` 变化时使用递增 request token，忽略旧账号的迟到响应。
- 加载成功后依次执行 `setSettings`、`applyTheme`、`writeCachedTheme`。
- 加载失败时保留缓存/默认值并暴露非阻断 `error`。
- `save(draft)` 只在 API 成功后应用和缓存返回值；失败直接抛给设置页，不能提前调用 `applyTheme`。
- `userId` 为空时恢复现代数据模板且不请求后端。

Context 精确接口：

```ts
type ThemeContextValue = {
  settings: ThemeSettings
  loading: boolean
  error: string | null
  save: (draft: ThemeSettings) => Promise<ThemeSettings>
}
```

- [ ] **Step 5: 运行 Provider 测试**

Run: `cd frontend-advisor && npx vitest run src/theme/ThemeProvider.test.tsx`

Expected: PASS。

- [ ] **Step 6: 检查变更**

Run: `git diff --check -- frontend-advisor/src/theme/themeApi.ts frontend-advisor/src/theme/ThemeProvider.tsx frontend-advisor/src/theme/ThemeProvider.test.tsx`

Expected: 无输出。仅在用户明确授权提交后提交此任务。

---

### Task 5: 设置页编辑、预览与保存

**Files:**
- Create: `frontend-advisor/src/components/ThemeColorField.tsx`
- Create: `frontend-advisor/src/pages/SettingsPage.tsx`
- Create: `frontend-advisor/src/pages/SettingsPage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes: `useTheme()`、`THEME_TEMPLATES`、`normalizeHex`、`getContrastWarnings`
- Produces: `ThemeColorField({ field, label, value, error, onChange })`
- Produces: 基础面板 `/settings` 页面主体

- [ ] **Step 1: 写设置页失败测试**

```tsx
// frontend-advisor/src/pages/SettingsPage.test.tsx
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { THEME_TEMPLATES } from '../theme/theme'
import SettingsPage from './SettingsPage'

const save = vi.hoisted(() => vi.fn())
vi.mock('../theme/ThemeProvider', () => ({
  useTheme: () => ({
    settings: THEME_TEMPLATES.modern_data,
    loading: false,
    error: null,
    save,
  }),
}))

beforeEach(() => {
  save.mockReset()
  save.mockImplementation(async (draft) => draft)
})

it('编辑色值后只在保存时提交完整主题', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')
  await user.clear(brand)
  await user.type(brand, '#123456')
  expect(save).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '保存并应用' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      active_template: 'modern_data',
      colors: expect.objectContaining({ brand: '#123456' }),
    }),
  )
})

it('低对比度警告不阻止保存', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const text = screen.getByLabelText('主文字（十六进制）')
  await user.clear(text)
  await user.type(text, '#F6F7FB')
  expect(screen.getByRole('status')).toHaveTextContent('对比度')
  await user.click(screen.getByRole('button', { name: '保存并应用' }))
  expect(save).toHaveBeenCalled()
})

it('切换模板会确认并覆盖草稿', async () => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')
  await user.clear(brand)
  await user.type(brand, '#123456')
  await user.click(screen.getByRole('radio', { name: '经典行情' }))
  expect(window.confirm).toHaveBeenCalled()
  expect(screen.getByLabelText('品牌主色（十六进制）')).toHaveValue('#526FC1')
})

it('非法十六进制值阻止保存，恢复按钮恢复当前模板', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')
  await user.clear(brand)
  await user.type(brand, 'blue')
  expect(screen.getByRole('button', { name: '保存并应用' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '恢复模板默认值' }))
  expect(brand).toHaveValue('#6673D9')
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/pages/SettingsPage.test.tsx`

Expected: FAIL，提示页面不存在。

- [ ] **Step 3: 实现颜色字段**

`ThemeColorField` 同时渲染：

- `<input type="color">`，`aria-label="${label}（颜色选择器）"`。
- `<input type="text">`，`aria-label="${label}（十六进制）"`，保留用户正在输入的原始值。
- 非法格式时显示字段错误并设置 `aria-invalid="true"`。
- 原生颜色选择器变更时传入大写 `#RRGGBB`。

- [ ] **Step 4: 实现设置页状态机**

页面状态：

```ts
const { settings, loading, error: loadError, save } = useTheme()
const [draft, setDraft] = useState<ThemeSettings>(() => cloneTheme(settings))
const [dirty, setDirty] = useState(false)
const [saving, setSaving] = useState(false)
const [message, setMessage] = useState<string | null>(null)
const [saveError, setSaveError] = useState<string | null>(null)
```

行为要求：

- Provider 设置更新且页面无未保存修改时同步草稿。
- 每次编辑克隆 `colors`，禁止修改 `THEME_TEMPLATES`。
- 模板切换在 `dirty` 时调用 `window.confirm('切换模板会覆盖当前未保存的配色，继续吗？')`。
- 恢复按钮使用当前 `draft.active_template` 的模板值。
- 预览以 `style` 局部变量实现，不调用全局 `applyTheme`。
- 保存前将全部合法值规范为大写；保存成功后以返回值更新草稿并显示“配色已保存并应用”；失败保留草稿。
- 对比度警告使用 `role="status"`；格式错误使用 `role="alert"`。

- [ ] **Step 5: 增加设置页样式**

在 `styles.css` 增加语义类：

- `.theme-settings`
- `.theme-template-grid` / `.theme-template-card`
- `.theme-editor-layout`
- `.theme-color-grid` / `.theme-color-field`
- `.theme-swatch-input` / `.theme-hex-input`
- `.theme-preview` / `.theme-preview-card`
- `.theme-contrast-warning`

桌面编辑区与预览区双列；`@media (max-width: 768px)` 下单列。颜色输入触控高度至少 `44px`，预览不得依赖真实业务请求。

- [ ] **Step 6: 运行设置页测试**

Run: `cd frontend-advisor && npx vitest run src/pages/SettingsPage.test.tsx`

Expected: PASS。

- [ ] **Step 7: 检查变更**

Run: `git diff --check -- frontend-advisor/src/components/ThemeColorField.tsx frontend-advisor/src/pages/SettingsPage.tsx frontend-advisor/src/pages/SettingsPage.test.tsx frontend-advisor/src/styles.css`

Expected: 无输出。仅在用户明确授权提交后提交此任务。

---

### Task 6: 应用接入、路由与账号生命周期

**Files:**
- Modify: `frontend-advisor/src/main.tsx:1-13`
- Modify: `frontend-advisor/src/App.tsx:1-219`
- Modify: `frontend-advisor/src/App.test.tsx`

**Interfaces:**
- Consumes: `bootstrapTheme`、`ThemeProvider`、`SettingsPage`
- Produces: 基础导航「设置」和 `/settings`
- Produces: 登录加载、账号切换、退出恢复默认主题

- [ ] **Step 1: 扩充 App 失败测试**

在 `App.test.tsx` mock Provider 为透明包装并新增设置页 mock：

```tsx
import type { ReactNode } from 'react'

vi.mock('./theme/ThemeProvider', () => ({
  ThemeProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('./pages/SettingsPage', () => ({
  default: () => <h1>配色设置</h1>,
}))

it('基础导航提供设置入口并渲染设置路由', () => {
  render(
    <MemoryRouter initialEntries={['/settings']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('link', { name: '设置' })).toHaveAttribute('href', '/settings')
  expect(screen.getByRole('heading', { name: '配色设置' })).toBeInTheDocument()
})
```

另写 `main` bootstrap 单元测试或将入口调用抽成可测试的 `initializeTheme()`，验证它以 `getUser()?.id` 调用 `bootstrapTheme`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/App.test.tsx`

Expected: FAIL，找不到「设置」链接或设置页标题。

- [ ] **Step 3: 接入首屏 bootstrap**

在 `main.tsx` 的 `createRoot` 前执行：

```ts
import { getUser } from './auth'
import { bootstrapTheme } from './theme/themeStorage'

bootstrapTheme(getUser()?.id)
```

默认 CSS 本身也必须是现代数据模板，确保 localStorage 不可用时仍是浅色。

- [ ] **Step 4: 在 App 挂载 Provider 与设置路由**

- 使用现有 `user` state 的 `user?.id ?? null` 作为 `ThemeProvider` 的 `userId`。
- Provider 包裹登录页和已登录应用，使登出后的 `null` 变化可以恢复默认主题。
- 基础导航在「我的策略」后增加 `<NavLink to="/settings">设置</NavLink>`。
- 增加 `<Route path="/settings" element={<SettingsPage />} />`。
- 保持 Agent 导航不增加重复设置入口；Agent 页面仍受同一全局主题影响。

- [ ] **Step 5: 运行 App 与 Provider 测试**

Run: `cd frontend-advisor && npx vitest run src/App.test.tsx src/theme/ThemeProvider.test.tsx`

Expected: PASS。

- [ ] **Step 6: 检查变更**

Run: `git diff --check -- frontend-advisor/src/main.tsx frontend-advisor/src/App.tsx frontend-advisor/src/App.test.tsx`

Expected: 无输出。仅在用户明确授权提交后提交此任务。

---

### Task 7: 全局浅色语义迁移与完整回归

**Files:**
- Modify: `frontend-advisor/src/styles.css`
- Modify as needed for semantic class names only: `frontend-advisor/src/components/AdviceCard.tsx`
- Test: existing `frontend-advisor/src/**/*.test.{ts,tsx}`

**Interfaces:**
- Consumes: Task 3 写入的 `--color-*` CSS 属性
- Produces: 整个基础面板和 Agent 面板的浅色、可切换语义样式

- [ ] **Step 1: 将 `:root` 默认值改为现代数据模板**

用以下 token 替换旧深绿 token，并让页面在 JavaScript 未运行时也是现代数据浅色：

```css
:root {
  --color-page-bg: #f6f7fb;
  --color-surface: #ffffff;
  --color-text-primary: #273247;
  --color-text-muted: #778195;
  --color-border: #e5e8f1;
  --color-brand: #6673d9;
  --color-brand-soft: rgba(102, 115, 217, 0.14);
  --color-market-up: #3568b8;
  --color-market-up-soft: rgba(53, 104, 184, 0.14);
  --color-market-down: #a96918;
  --color-market-down-soft: rgba(169, 105, 24, 0.14);
  --color-success: #377659;
  --color-success-soft: rgba(55, 118, 89, 0.14);
  --color-error: #a84c5b;
  --color-error-soft: rgba(168, 76, 91, 0.14);

  --bg0: var(--color-page-bg);
  --bg1: var(--color-surface);
  --bg2: #f0f2f7;
  --ink: var(--color-text-primary);
  --muted: var(--color-text-muted);
  --line: var(--color-border);
  --accent: var(--color-brand);
  --accent-soft: var(--color-brand-soft);
  --sell: var(--color-market-down);
  --sell-soft: var(--color-market-down-soft);
  --hold: #8b6f2f;
  --hold-soft: rgba(139, 111, 47, 0.14);
}
```

兼容别名只用于逐步替换；任务结束时业务语义不得继续依赖 `--sell` 表示成功或 `--accent` 表示上涨。

- [ ] **Step 2: 迁移明确语义状态**

至少完成以下映射：

```css
.up { color: var(--color-market-up); }
.down { color: var(--color-market-down); }
.status.ok { color: var(--color-success); }
.status.error { color: var(--color-error); }
.action-buy {
  color: var(--color-brand);
  background: var(--color-brand-soft);
}
.action-sell {
  color: var(--color-error);
  background: var(--color-error-soft);
}
.committee-timeline li[data-status='completed'] .committee-timeline-dot {
  background: var(--color-success);
}
.committee-timeline li[data-status='aborted'] .committee-timeline-dot {
  background: var(--color-error);
}
```

进度、选中、链接、focus ring 使用 brand；行情数字使用 market-up/down；请求成功使用 success；失败、危险操作和 aborted 使用 error。`.factor-fill` 使用 brand 渐变，不再混合涨跌色。

- [ ] **Step 3: 将深色硬编码背景迁为浅色 token**

逐项处理 `styles.css` 中旧深色来源：

- `rgba(22, 36, 28, ...)` → `var(--color-surface)` 或基于 surface 的透明背景。
- `rgba(12, 20, 16, ...)`、`#0c1410` → `var(--color-page-bg)` 或 `#F0F2F7`。
- 白色低透明 hover → `var(--color-brand-soft)` 或 `#F0F2F7`。
- 珊瑚红/青绿硬编码 → 对应 brand、market、success 或 error 语义。
- 文本硬编码亮色 → `var(--ink)`、`var(--muted)` 或对应语义色。
- body 背景改为以 `--color-brand-soft` 为轻微径向光晕、`--color-page-bg` 为底的浅色背景。

运行库存搜索：

Run: `rg -n "#0f1a14|#0c1410|rgba\\(22, 36, 28|rgba\\(12, 20, 16|232, 93, 76|60, 184, 154" frontend-advisor/src/styles.css`

Expected: 无匹配。其余硬编码颜色逐条确认仅为中性派生值或无法由十个用户色直接表达的固定辅助色。

- [ ] **Step 4: 检查两套主题下的关键页面**

启动后手动检查桌面和 `390px` 宽度：

- `/` 今日关注：上涨/下跌、按钮、卡片、表格。
- `/portfolio` 与 `/paper`：盈亏、买卖操作、危险确认。
- `/agent`：聊天气泡、输入框、快捷问题、移动更多菜单。
- `/agent/committee`：completed/running/failed/aborted 状态。
- `/settings`：模板卡、十个字段、警告、预览和保存状态。

分别应用 modern_data 和 classic_market；确认经典行情为红涨绿跌，现代数据为蓝涨琥珀跌，且成功/错误色不随涨跌语义串色。

- [ ] **Step 5: 运行前端单测**

Run: `cd frontend-advisor && npm test`

Expected: PASS。

- [ ] **Step 6: 运行 lint 与生产构建**

Run: `cd frontend-advisor && npm run lint && npm run build`

Expected: 两条命令退出码均为 0。

- [ ] **Step 7: 运行后端最终回归**

Run: `cd backend && pytest -q`

Expected: PASS，或仅有已单独确认与主题改动无关的环境型既有失败。

- [ ] **Step 8: 最终差异检查**

Run: `git diff --check && git status --short`

Expected: `git diff --check` 无输出；状态只包含本计划列出的实现、测试和设计/计划文档。仅在用户明确授权提交后创建 commit。

