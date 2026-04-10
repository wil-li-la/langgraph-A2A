# Frontend Design System

> Robot Task Dashboard + Teleop 的視覺設計規範。

---

## 設計哲學

**終端機風格、觸控優先、高對比**。為 11" iPad 及桌面瀏覽器設計。

- 全介面使用 **dark mode** (`<html className="dark">`)
- **高對比度**：文字至少 70% 亮度，邊框至少 22%，確保 iPad 可讀性
- **觸控友善**：所有可互動元素最小 44px 高度，推薦 48-56px
- 全數據文字使用 **monospace**

---

## 色彩系統

### 核心色票

| Token | HSL (Dark Mode) | 用途 |
|---|---|---|
| `--background` | `0 0% 6%` | 頁面底色 |
| `--foreground` | `0 0% 95%` | 主要文字 |
| `--card` | `0 0% 9%` | 面板背景 |
| `--border` | `0 0% 22%` | 邊框（高可見度） |
| `--muted-foreground` | `0 0% 70%` | 次要文字、標籤（高對比） |
| `--secondary` | `0 0% 16%` | 次要按鈕背景 |
| `--destructive` | `0 63% 31%` | 錯誤/RUNSTOP（唯一允許色彩） |

### WorkflowGraph 節點狀態（SVG）

| 狀態 | Fill | Border | Text |
|---|---|---|---|
| `completed` | green 8% | green 40% | white 90% |
| `active` | blue 12% | blue 70% | white 100% |
| `pending` | white 2% | white 10% | white 40% |
| `error` | red 15% | red 60% | red 90% |

---

## 字型

| 元素 | 字型 | 大小 | 備註 |
|---|---|---|---|
| 區塊標題 | `font-mono` | `text-sm` (14px) | `font-medium tracking-wide` UPPERCASE |
| 按鈕文字 | `font-mono` | `text-sm` (14px) | `font-medium` |
| 內容文字 | `font-mono` | `text-xs` (12px) | 最小可讀大小 |
| 小標籤 | `font-mono` | `text-[11px]` | 僅用於非互動標籤 |

**規則：不使用 `text-[10px]` 或更小。最小字體 11px。**

---

## 觸控目標規範

| 元素 | 最小高度 | 推薦高度 |
|---|---|---|
| 主要按鈕 (RUNSTOP, Drive) | `h-14` (56px) | `h-16` (64px) |
| 一般按鈕 (Gripper, Home, Speed) | `h-12` (48px) | `h-14` (56px) |
| 輸入框 | `h-10` (40px) | `h-12` (48px) |
| 小型按鈕 (nav links) | `h-8` (32px) | — |

**規則：所有可互動元素最小 44px 高度。**

---

## 間距

| 場景 | Class |
|---|---|
| 面板之間 | `gap-3` |
| 面板內 padding | `p-3` |
| 標題到內容 | `mb-2` |
| 小元素之間 | `gap-2` |

---

## 頁面佈局

### Dashboard（兩欄式）

```
┌─ Nav Bar [Robot IP] [Connect] ● Stretch 3 [Dashboard] [Teleop] ┐
├───────────────────────────────┬──────────────────────────────────┤
│  TASK — LANGGRAPH             │  Skills / Mode / Log             │
│  (Phase Groups 水平佈局)       │  (堆疊，Log 填滿剩餘空間)         │
├───────────────────────────────┤                                  │
│  VIDEO & MAP                  │                                  │
│  [Head] [Gripper] [Map]       │                                  │
└───────────────────────────────┴──────────────────────────────────┘
```

### Teleop（三欄式）

```
┌─ Nav Bar ────────────────────────────────────────────────────────┐
├────────────┬──────────────────────────────────┬──────────────────┤
│ MOBILITY   │  2×2 Camera Grid                 │  MANIPULATION    │
│ Runstop    │  [Overhead] [Realsense]          │  Joints          │
│ Drive WASD │  [Gripper]  [Nav Map]            │  Gripper         │
│ Head       │                                  │  Home            │
│ Speed      │  TTS / Chat                      │                  │
└────────────┴──────────────────────────────────┴──────────────────┘
```

---

## 元件規範

### Panel
```tsx
<div className="rounded-md border border-border bg-card p-3">
  <h2 className="mb-2 font-mono text-sm font-medium tracking-wide text-foreground">
    PANEL TITLE
  </h2>
</div>
```

### 按鈕
- 主要操作：`h-14 font-mono text-sm font-medium`
- 危險操作（RUNSTOP）：`h-16 font-mono text-base font-bold` + red accent
- 方向控制（WASD/Head）：`aspect-square font-mono text-sm font-medium`

### 動畫
僅允許：`animate-ping`（LIVE 圓點）、SVG `animate`（active 節點 pulse）
