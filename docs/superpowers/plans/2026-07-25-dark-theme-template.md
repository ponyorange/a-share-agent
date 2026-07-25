# 深色配色模板（深海蓝）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有账号级配色设置上新增第三套模板 `deep_navy`（深海蓝），并消除深色主题下的固定浅色亮块。

**Architecture:** 扩展前后端模板白名单与默认色表，保持十色存储契约不变。`applyTheme` 按页面明度自适应派生 `--color-surface-muted`：浅色页面向正文色混合 4%，深色页面向卡片色混合 55%；该 token 替换 CSS 中硬编码的浅色辅助面。设置页通过现有 `THEME_TEMPLATES` 枚举自动露出新模板。

**Tech Stack:** FastAPI、Pydantic 2、pytest、React 19、TypeScript、Vitest、CSS 自定义属性。

## Global Constraints

- 仅新增一个深色模板 `deep_navy`；涨跌语义为蓝涨、琥珀跌。
- 不新增第 11 个可编辑色值；不新增系统偏好自动切换。
- 默认主题仍为 `modern_data`；不迁移已保存浅色主题。
- 对比度不足只警告，不阻止保存。
- 未经用户明确要求不创建 Git commit。
- 工作树已有未提交的账号主题功能；本计划在其上增量修改，不要回滚既有主题工作。

---

## File Map

### 修改

- `backend/app/advisor/ui_settings.py`：模板 ID、默认色。
- `backend/app/advisor/routes.py`：Pydantic `Literal` 允许 `deep_navy`。
- `backend/tests/test_ui_settings.py`：保存深海蓝与未知模板拒绝。
- `backend/tests/test_ui_settings_routes.py`：PUT `deep_navy` 通过。
- `frontend-advisor/src/theme/theme.ts`：模板、校验、`surface-muted` 派生。
- `frontend-advisor/src/theme/theme.test.ts`：三套模板与派生色测试。
- `frontend-advisor/src/pages/SettingsPage.tsx`：模板标签「深海蓝」。
- `frontend-advisor/src/pages/SettingsPage.test.tsx`：选择/恢复深海蓝。
- `frontend-advisor/src/styles.css`：`--bg2` 与 body 渐变改用 `--color-surface-muted`。

---

### Task 1: 后端允许 deep_navy

**Files:**
- Modify: `backend/app/advisor/ui_settings.py`
- Modify: `backend/app/advisor/routes.py`
- Modify: `backend/tests/test_ui_settings.py`
- Modify: `backend/tests/test_ui_settings_routes.py`

**Interfaces:**
- Produces: `ThemeId` includes `"deep_navy"`
- Produces: `DEFAULT_THEMES["deep_navy"]` with exact design colors
- Consumes: existing `save_ui_settings` / `get_ui_settings`

- [ ] **Step 1: 写失败测试**

在 `test_ui_settings.py` 增加：

```python
def test_save_accepts_deep_navy(fake_db):
    colors = {
        "page_bg": "#101724",
        "surface": "#192335",
        "text_primary": "#F2F5FA",
        "text_muted": "#99A7BB",
        "border": "#303E55",
        "brand": "#8793FF",
        "market_up": "#70A9F8",
        "market_down": "#F1B85B",
        "success": "#61C28F",
        "error": "#F17C8E",
    }
    saved = ui.save_ui_settings("u1", active_template="deep_navy", colors=colors)
    assert saved["active_template"] == "deep_navy"
    assert saved["colors"]["page_bg"] == "#101724"
    assert ui.get_ui_settings("u1")["active_template"] == "deep_navy"
```

在 `test_ui_settings_routes.py` 增加 PUT `deep_navy` 返回 200 的用例。

- [ ] **Step 2: 运行确认失败**

Run:

```bash
PYTHONPATH="/Users/orange/Desktop/code/share-data/backend" \
"/Users/orange/Desktop/code/share-data/backend/.venv/bin/pytest" -q \
backend/tests/test_ui_settings.py::test_save_accepts_deep_navy
```

Expected: FAIL，未知模板或缺少默认色。

- [ ] **Step 3: 实现最小改动**

`ui_settings.py`：

```python
ThemeId = Literal["modern_data", "classic_market", "deep_navy"]
THEME_IDS: tuple[ThemeId, ...] = ("modern_data", "classic_market", "deep_navy")

DEFAULT_THEMES["deep_navy"] = {
    "page_bg": "#101724",
    "surface": "#192335",
    "text_primary": "#F2F5FA",
    "text_muted": "#99A7BB",
    "border": "#303E55",
    "brand": "#8793FF",
    "market_up": "#70A9F8",
    "market_down": "#F1B85B",
    "success": "#61C28F",
    "error": "#F17C8E",
}
```

`routes.py`：

```python
active_template: Literal["modern_data", "classic_market", "deep_navy"]
```

- [ ] **Step 4: 运行后端相关测试**

Run:

```bash
PYTHONPATH="/Users/orange/Desktop/code/share-data/backend" \
"/Users/orange/Desktop/code/share-data/backend/.venv/bin/pytest" -q \
backend/tests/test_ui_settings.py backend/tests/test_ui_settings_routes.py
```

Expected: PASS。

- [ ] **Step 5: 检查变更**

Run: `git diff --check -- backend/app/advisor/ui_settings.py backend/app/advisor/routes.py backend/tests/test_ui_settings.py backend/tests/test_ui_settings_routes.py`

不要 commit。

---

### Task 2: 前端模板与 surface-muted 派生

**Files:**
- Modify: `frontend-advisor/src/theme/theme.ts`
- Modify: `frontend-advisor/src/theme/theme.test.ts`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Produces: `ThemeId = 'modern_data' | 'classic_market' | 'deep_navy'`
- Produces: `THEME_TEMPLATES.deep_navy`
- Produces: `applyTheme` writes `--color-surface-muted`
- Produces: CSS `--bg2: var(--color-surface-muted)`

- [ ] **Step 1: 写失败测试**

更新 `theme.test.ts`：

```ts
it('三套模板都有且仅有十个基础语义色', () => {
  const expected = [/* ten keys */].sort()
  for (const id of ['modern_data', 'classic_market', 'deep_navy'] as const) {
    expect(Object.keys(THEME_TEMPLATES[id].colors).sort()).toEqual(expected)
  }
})

it('应用深海蓝时写入 surface-muted 派生色', () => {
  applyTheme(THEME_TEMPLATES.deep_navy)
  expect(document.documentElement.style.getPropertyValue('--color-page-bg')).toBe('#101724')
  expect(document.documentElement.style.getPropertyValue('--color-surface-muted')).toBe('rgb(21, 30, 45)')
  expect(document.documentElement.dataset.themeTemplate).toBe('deep_navy')
})
```

- [ ] **Step 2: 运行确认失败**

Run:

```bash
npm --prefix "/Users/orange/Desktop/code/share-data/frontend-advisor" exec -- \
  vitest run --root "/Users/orange/Desktop/code/share-data/frontend-advisor" \
  src/theme/theme.test.ts
```

Expected: FAIL，缺少 `deep_navy` 或 `--color-surface-muted`。

- [ ] **Step 3: 实现模板与混合函数**

在 `theme.ts`：

```ts
export type ThemeId = 'modern_data' | 'classic_market' | 'deep_navy'

// THEME_TEMPLATES.deep_navy 使用设计文档精确色值

function mixHex(a: string, b: string, weightTowardB = 0.5): string {
  const [ar, ag, ab] = hexToRgb(normalizeHex(a) ?? a)
  const [br, bg, bb] = hexToRgb(normalizeHex(b) ?? b)
  const t = weightTowardB
  const mix = (x: number, y: number) => Math.round(x * (1 - t) + y * t)
  return `rgb(${mix(ar, br)}, ${mix(ag, bg)}, ${mix(ab, bb)})`
}

function surfaceMuted(colors: ThemeColors): string {
  return luminance(colors.page_bg) >= 0.5
    ? mixHex(colors.page_bg, colors.text_primary, 0.04)
    : mixHex(colors.page_bg, colors.surface, 0.55)
}

// applyTheme 内：
root.style.setProperty('--color-surface-muted', surfaceMuted(settings.colors))
```

`isThemeId` 接受 `deep_navy`。

`styles.css`：

```css
:root {
  --color-surface-muted: rgb(238, 239, 244); /* modern_data 派生值 */
  --bg2: var(--color-surface-muted);
}
body {
  background:
    radial-gradient(1200px 600px at 10% -10%, var(--color-brand-soft), transparent 55%),
    linear-gradient(180deg, var(--color-page-bg), var(--color-surface-muted) 60%, var(--color-page-bg));
}
```

搜索并替换其余硬编码浅色辅助面（`#f0f2f7`、`#f8f9fc`、`#eef1f7`、`#e8ebf3`）。

- [ ] **Step 4: 运行主题测试**

Expected: PASS。

- [ ] **Step 5: 检查变更**

Run: `rg -n "#f0f2f7" frontend-advisor/src/styles.css` → 无匹配。不要 commit。

---

### Task 3: 设置页露出深海蓝并回归

**Files:**
- Modify: `frontend-advisor/src/pages/SettingsPage.tsx`
- Modify: `frontend-advisor/src/pages/SettingsPage.test.tsx`

**Interfaces:**
- Produces: `TEMPLATE_LABELS.deep_navy = '深海蓝'`

- [ ] **Step 1: 写设置页测试**

```tsx
it('可选择深海蓝并恢复其默认色', async () => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<SettingsPage />)
  await user.click(screen.getByRole('radio', { name: '深海蓝' }))
  expect(screen.getByLabelText('页面背景（十六进制）')).toHaveValue('#101724')
  await user.clear(screen.getByLabelText('品牌主色（十六进制）'))
  await user.type(screen.getByLabelText('品牌主色（十六进制）'), '#123456')
  await user.click(screen.getByRole('button', { name: '恢复模板默认值' }))
  expect(screen.getByLabelText('品牌主色（十六进制）')).toHaveValue('#8793FF')
})
```

- [ ] **Step 2: 运行确认失败**

Expected: FAIL，找不到「深海蓝」。

- [ ] **Step 3: 增加标签**

```ts
const TEMPLATE_LABELS: Record<ThemeId, string> = {
  modern_data: '现代数据',
  classic_market: '经典行情',
  deep_navy: '深海蓝',
}
```

模板网格已枚举 `THEME_TEMPLATES`，无需改布局。

- [ ] **Step 4: 运行前端相关测试**

```bash
npm --prefix "/Users/orange/Desktop/code/share-data/frontend-advisor" exec -- \
  vitest run --root "/Users/orange/Desktop/code/share-data/frontend-advisor" \
  src/theme/theme.test.ts src/pages/SettingsPage.test.tsx src/theme/ThemeProvider.test.tsx
```

Expected: PASS。

- [ ] **Step 5: 全量前端回归**

```bash
npm --prefix "/Users/orange/Desktop/code/share-data/frontend-advisor" exec -- \
  vitest run --root "/Users/orange/Desktop/code/share-data/frontend-advisor"
```

Expected: 全部通过。

- [ ] **Step 6: 后端相关回归**

```bash
PYTHONPATH="/Users/orange/Desktop/code/share-data/backend" \
"/Users/orange/Desktop/code/share-data/backend/.venv/bin/pytest" -q \
backend/tests/test_ui_settings.py backend/tests/test_ui_settings_routes.py
```

Expected: PASS。

- [ ] **Step 7: 最终检查**

```bash
git diff --check
git status --short
```

不要 commit，除非用户明确要求。

---

## Spec Coverage Check

| 需求 | Task |
|---|---|
| `deep_navy` 模板色值 | 1, 2 |
| API / ThemeId 白名单 | 1 |
| 设置页露出与恢复 | 3 |
| `--color-surface-muted` 消除浅色亮块 | 2 |
| 默认仍为 modern_data | 1, 2（不改默认路径） |
| 不迁移已有浅色主题 | 无需代码；验收说明 |
