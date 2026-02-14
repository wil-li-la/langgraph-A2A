# Frontend Design System

> Robot Task Dashboard 的視覺設計規範，確保所有元件遵循一致的風格語言。

---

## 設計哲學

**終端機風格、資訊密集、單色系**。Dashboard 的核心是「一眼掌握」—— 用最少的裝飾傳達最多的狀態資訊。

- 全介面使用 **dark mode** (`<html className="dark">`)，無淺色主題
- 以 **opacity 層級** 表達資訊階層，而非色彩
- 全數據文字使用 **monospace**，僅頁面標題可用 sans-serif

---

## 色彩系統

所有顏色透過 CSS custom properties 定義在 `styles/globals.css`，由 Tailwind 的 `hsl(var(--xxx))` 語法引用。

### 核心色票

| Token | HSL (Dark Mode) | 用途 |
|---|---|---|
| `--background` | `0 0% 3.9%` | 頁面底色 |
| `--foreground` | `0 0% 98%` | 主要文字 |
| `--card` | `0 0% 3.9%` | 面板背景 |
| `--card-foreground` | `0 0% 98%` | 面板內文字 |
| `--border` | `0 0% 14.9%` | 邊框、分隔線 |
| `--muted` | `0 0% 14.9%` | 次要背景 |
| `--muted-foreground` | `0 0% 63.9%` | 次要文字、標籤 |
| `--primary` | `0 0% 98%` | 主要互動元素 |
| `--secondary` | `0 0% 14.9%` | 次要按鈕背景 |
| `--destructive` | `0 62.8% 30.6%` | 錯誤狀態（唯一允許的色彩） |

### 設計規則

```
✅ 用 opacity 區分層級（foreground/90, /40, /10）
✅ 用 border 分隔區塊
❌ 不使用彩色（綠/藍/黃）表示狀態
❌ 不使用 shadow
❌ 不使用漸層
```

### WorkflowGraph 節點狀態

在 SVG 繪製的 Workflow 圖中，用 opacity 表達狀態：

| 狀態 | Fill Alpha | Border Alpha | Text Alpha | 附加效果 |
|---|---|---|---|---|
| `completed` | `0.08` | `0.30` | `0.9` | — |
| `active` | `0.12` | `0.60` | `1.0` | pulse 動畫 |
| `pending` | `0.02` | `0.10` | `0.4` | — |
| `error` | `0.03` | `0.15` | `0.7` | — |

---

## 字型

定義在 `app/layout.tsx`，使用 Google Fonts 的 **Geist** 家族。

| 元素 | 字型 | Tailwind Class | 大小 | 字重 |
|---|---|---|---|---|
| 頁面標題 | Geist (sans) | `font-sans` | `text-lg` | `font-medium` |
| 區塊標題 | System Mono | `font-mono` | `text-sm` | `font-medium tracking-wide` |
| 內容文字 | System Mono | `font-mono` | `text-xs` | `font-normal` |
| 極小標籤 | System Mono | `font-mono` | `text-[10px]` | `font-normal` |

### UPPERCASE 標題格式

所有區塊標題（如 `CONNECT ROBOT`、`TASK — LANGGRAPH`）使用全大寫：

```tsx
<h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
  TASK &mdash; LANGGRAPH
</h2>
```

---

## 排版 Grid

```
┌─────────────────────────────────────────────────────────┐
│  Robot Task Dashboard                     [font-sans]   │
├──────────────┬───────────────────┬───────────────────────┤
│  CONNECT     │  TASK —           │  REQUIRED             │
│  ROBOT       │  LANGGRAPH        │  SKILLS               │
│  [1fr]       │  [1.2fr]          │  [0.6fr]              │
├──────────────┴─────────┬─────────┴───────────────────────┤
│  MAIN VIEW             │  VIDEO STREAMING                │
│  [1fr]                 │  [1fr]                          │
└────────────────────────┴─────────────────────────────────┘
```

| 屬性 | 值 |
|---|---|
| 最大寬度 | `max-w-[1400px]`，置中 |
| 間距 | `gap-4` (16px) |
| 面板 padding | `p-4` (16px) |
| 面板圓角 | `rounded-md` |
| 面板邊框 | `border border-border` |
| 面板背景 | `bg-card` |

---

## 元件規範

### Panel（面板）

所有面板共用相同結構：

```tsx
<div className="rounded-md border border-border bg-card p-4">
  <h2 className="mb-3 font-mono text-sm font-medium tracking-wide text-foreground">
    PANEL TITLE
  </h2>
  {/* 面板內容 */}
</div>
```

### WorkflowGraph（SVG 工作流程圖）

- 節點尺寸：`180 × 56 px`，間距 `24px`
- Start / End 節點：`rx="28"`（藥丸形）
- 一般節點：`rx="8"`（圓角矩形）
- Decision 節點：標籤加 `?` 前綴
- Error 節點：排列在主流程右側
- 連線：虛線 `strokeDasharray="6 3"`，分支用 Bézier 曲線
- Active 節點動畫：`opacity 1 → 0.3 → 1`，2 秒循環

### ConnectPanel（連線狀態）

- Robot 選擇器：shadcn `Select`，寬度 `w-[200px]`
- 連線狀態框：`border border-border` muted 背景
- Skill toggle：`variant="secondary"` (loaded) / `variant="outline"` (unloaded)

### SkillsPanel（技能列表）

- 垂直卡片列表
- 狀態指示點：
  - `bg-foreground`：已載入 + 必要
  - `bg-muted-foreground`：僅必要
  - `bg-muted-foreground/30`：非必要

### VideoPanel（影像串流）

- 2 欄 grid：Gripper cam / Map view
- LIVE 指示器：`animate-ping` 圓點 + "LIVE" 文字
- 無訊號：camera/map SVG icon placeholder

### LIVE / OFFLINE 指示器

用於表示後端 API 連線狀態：

```tsx
{/* LIVE */}
<span className="relative flex h-1.5 w-1.5">
  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
</span>
<span className="font-mono text-[10px] text-foreground">LIVE</span>

{/* OFFLINE */}
<span className="font-mono text-[10px] text-muted-foreground/50">OFFLINE</span>
```

---

## 元件庫 (shadcn/ui)

使用 shadcn/ui（Radix primitives + Tailwind）。已安裝的元件位於 `components/ui/`：

- `Button` — 按鈕
- `Select` — 下拉選擇器
- `Tabs` — 分頁
- `Toast` (Sonner) — 通知

### 新增元件原則

1. 優先使用已安裝的 shadcn/ui 元件組合
2. 需要新 Radix primitive 時，用 `npx shadcn@latest add <component>` 安裝
3. 自訂元件放在 `components/` 根目錄，UI primitives 放在 `components/ui/`

---

## 動畫

只允許兩種動畫：

| 動畫 | 用途 | 實現 |
|---|---|---|
| **Pulse** | Workflow active 節點 | SVG `animate` 元素，opacity 循環 |
| **Ping** | LIVE 指示圓點 | Tailwind `animate-ping` |

```
❌ 不在面板加入 transition
❌ 不使用 hover 動畫（除 Button 元件自帶的）
❌ 不使用頁面切換動畫
```

---

## 間距參考

| 場景 | Class |
|---|---|
| 面板之間 | `gap-4` |
| 面板內 padding | `p-4` |
| 標題到內容 | `mb-3` |
| 小元素之間 | `gap-1.5` |
| 分隔線 | `<div className="h-px bg-border" />` |

---

## Checklist：新增功能前確認

- [ ] 是否使用 `font-mono`？（數據文字必須）
- [ ] 是否避免彩色？僅用 opacity 區分層級
- [ ] 面板是否遵循 `rounded-md border border-border bg-card p-4` 格式？
- [ ] 標題是否全大寫 + `tracking-wide`？
- [ ] 是否只使用 `border` 而非 `shadow`？
- [ ] 新動畫是否必要？（預設不加動畫）
