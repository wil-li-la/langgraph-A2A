# 安裝說明

## 前置條件

### 1. 確認 GitHub 權限

`cure` 和 `stretch3-zmq-core` 是 private GitHub repo 的依賴，安裝前請聯絡專案負責人，確認你的 GitHub 帳號已被加入以下 repo 的 collaborator：

| 套件 | Repo |
|------|------|
| `cure`（robot skills） | https://github.com/lnfu/cure |
| `stretch3-zmq-core`（ZMQ client） | https://github.com/lnfu/stretch3-zmq |

加入後，確認本機 Git 可以存取這兩個 repo（SSH key 或 HTTPS token 均可）。

### 2. Python 版本

需要 **Python 3.12**。

---

## 快速安裝（推薦）

```bash
git clone https://github.com/wil-li-la/langgraph-A2A.git
cd langgraph-A2A/backend
bash setup.sh
```

`setup.sh` 會自動完成以下所有步驟。

---

## 手動安裝步驟

```bash
# 1. Clone 專案
git clone https://github.com/wil-li-la/langgraph-A2A.git
cd langgraph-A2A/backend

# 2. 建立虛擬環境
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安裝 stretch3-zmq-core（LFS budget exceeded — skip LFS then install）
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/lnfu/stretch3-zmq.git /tmp/stretch3-zmq
.venv/bin/pip install /tmp/stretch3-zmq/packages/core

# 4. 安裝 cure skills（no-detection branch，--no-deps 避免 stretch3-zmq 衝突）
GIT_LFS_SKIP_SMUDGE=1 git clone --branch no-detection https://github.com/lnfu/cure.git /tmp/cure-no-detection
.venv/bin/pip install --no-deps /tmp/cure-no-detection
.venv/bin/pip install rerun-sdk pyzmq scipy

# 5. 安裝本專案本身（editable）
.venv/bin/pip install -e .
```

---

## 環境變數

複製範本並填入 API key：

```bash
cp .env.example .env
```

`.env` 必填欄位：

```
model_source=google          # 或 openai
GOOGLE_API_KEY=your_key      # model_source=google 時使用
OPENAI_API_KEY=your_key      # model_source=openai 時使用
```

---

## 啟動後端

```bash
source .venv/bin/activate
python -m app --host localhost --port 9999
```

---

## 常見問題

**`cure` 或 `stretch3-zmq-core` 安裝失敗（permission denied / 404）**
- 確認已被加入 collaborator，且本機 SSH key 或 HTTPS token 有效。
- 使用 `GIT_LFS_SKIP_SMUDGE=1` 可跳過 LFS 大檔下載，避免 LFS quota 問題。

**`python3.12` 找不到**
- macOS：`brew install python@3.12`
- 確認 `python3.12 --version` 可正常執行後再建立 venv。

**機器人無法連線**
- 確認機器人上的 ZMQ driver 已啟動：
  ```bash
  ssh stretch-se3-3099.local -l hello-robot
  cd Desktop/stretch3-zmq/
  uv run python -m stretch3_zmq.driver --config config.yaml
  ```
- Nav2 goto service（port 5557）尚未在 driver 實作，`navigate_avoidance` 暫時無法使用。
