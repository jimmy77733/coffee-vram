# RESEARCH — Spark 實測

量測日期 2026-11-05。腳本 `scripts/measure_spark.py`（只用標準庫；NVML、CUDA driver 都走 ctypes），
原始 JSON 存 `measurements/raw/`（不進 git）。除了載入／卸載測試模型 `qwen3.5:9b`，全程唯讀。

## 環境

| 項目 | 值 |
|---|---|
| GPU | NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)，driver 617.42 |
| 實體記憶體 | 128 GiB（`GetPhysicallyInstalledSystemMemory`） |
| Windows 看得到的 RAM | 64.0 GiB（`GlobalMemoryStatusEx.ullTotalPhys`） |
| Ollama | 0.40.14，`127.0.0.1:11434` |
| Ollama 使用者環境變數 | `OLLAMA_KEEP_ALIVE=30m`、`OLLAMA_CONTEXT_LENGTH=131072`、`OLLAMA_MAX_LOADED_MODELS=1` |

## 1. 記憶體遙測：各來源回報什麼

| 來源 | total | 空載 used / free | 載入 qwen3.5:9b 後 used / free | 會跟著變？ |
|---|---|---|---|---|
| NVML `nvmlDeviceGetMemoryInfo` | 67,108,864,000 B（62.50 GiB） | 3.18 / 59.32 GiB | 14.07 / 48.43 GiB | **會**，卸載後回到 3.15 GiB |
| NVML `nvmlDeviceGetMemoryInfo_v2` | 同上，reserved 14 MiB | used 少 reserved 那 14 MiB | 同左 | 會 |
| nvidia-smi CSV | 64000 MiB | 3247 / 60740 MiB | 14395 / 49592 MiB | 會（與 NVML 一致） |
| CUDA `cuDeviceTotalMem_v2` | 117,826,244,608 B（109.73 GiB） | — | — | 不變 |
| CUDA `cuMemGetInfo_v2` | 109.73 GiB | free 109.54 GiB | free 109.54 GiB | **不會**（數值固定，不能當即時指標） |
| CUDA `INTEGRATED` 屬性 | `1`（unified memory） | | | |
| OS 可用 RAM | 64.0 GiB | 44.47 GiB | 42.77 GiB | 只少 1.7 GiB |

讀法：

- 128 GiB 實體記憶體裡，約 64 GiB 切給 GPU 當 carve-out，Windows 看不到這一塊。NVML／nvidia-smi 的 total 就是這塊 carve-out。
- 模型先載進 carve-out，所以 NVML used 會精準跟著模型增減，OS 可用 RAM 幾乎不動。
- CUDA 回報的 109.7 GiB 是 allocator 的總池，等於 carve-out 加上 Windows 共享 GPU 記憶體。超出 carve-out 的部分落在系統 RAM，速度明顯變慢（核心 `hardware.py` 也記錄過這個「soft cliff」）。
- 單一行程的用量拿不到：WDDM 下 NVML `ComputeRunningProcesses_v3` 每個行程都回 `0xFFFFFFFFFFFFFFFF`（NOT_AVAILABLE），nvidia-smi 回 `[N/A]`。Ollama 的 `llama-server.exe` 只會以 PID 形式出現在清單中。
- nvidia-smi 會把 NPU 列成第 1 號裝置，各欄位都是 `[N/A]`，解析時要濾掉。

### 對設計的影響

- 主要遙測後端：用 ctypes 直接呼叫 `nvml.dll`，不加 `nvidia-ml-py` 依賴。nvidia-smi 只當備援。
- 預算以 NVML total（62.5 GiB carve-out）為準，`warn_pct`／`hold_pct` 都對它計算。比它大的模型由 Ollama 自己切一部分給 CPU（見 §6）。
- 用量歸屬：Ollama 模型以 `/api/ps` 的 `size_vram` 為準。外部用量 ＝ NVML used − 空載基線 − Σ Ollama `size_vram`。
- 載入後實際增加的 NVML used（11.69 GB）比 Ollama 回報的 `size`（10.63 GB）多 1.06 GB，約 10%，剛好吻合 `estimate_overhead_pct` 預設的 10。
- `cuMemGetInfo` 不能用在即時取樣。

## 2. Ollama `/v1` 與 `keep_alive`

每次請求後都讀 `/api/ps` 的 `expires_at`，換算成剩餘秒數：

| 試驗 | 送出的 keep_alive | 剩餘秒數 | 結論 |
|---|---|---|---|
| `/v1/chat/completions` 首次載入，不帶 | — | 1800 | 用 `OLLAMA_KEEP_ALIVE=30m` |
| `/v1` 頂層 `"keep_alive": "2m"` | 2m | 120 | **接受** |
| `/v1` 頂層 `"keep_alive": 420` | 420 | 420 | **接受**（整數視為秒） |
| `/v1` 頂層 `"keep_alive": "90s"`，再發一次不帶 | 90s → 無 | 90 → 90 | **沿用上一次的值** |
| `/api/generate` 不帶 | — | 90 | 原生 API 也沿用 |
| `/api/generate` `"keep_alive": "3m"`（對照組） | 3m | 180 | 接受 |
| `/api/generate` `"keep_alive": -1`，再發一次 `/v1` 不帶 | -1 → 無 | 永久 → 永久 | 沿用 |
| `/api/generate` `"keep_alive": 0` | 0 | 立即卸載 | 卸載可用 |

OpenAI SDK 的 `extra_body` 會被合併到 JSON 頂層，所以「頂層」就是 Hermes 透過 `extra_body` 送出時的樣子。

### 對設計的影響

- `keep_alive` 一旦設定就會一直生效，直到下次被覆寫，所以保溫不必改寫 Hermes 的請求。Coffee 用原生 `POST /api/generate {"model", "keep_alive"}`（不帶 prompt）就能保溫或延長；卸載用 `keep_alive: 0`。
- 「最後一次設定者勝出」：使用者或其他工具設的值會蓋過 Coffee 的設定，反過來也一樣。保溫要定期重設，不能只設一次。

## 3. `/v1` 會忽略 `num_ctx`（與先前從原始碼推論的相反）

| 請求 | 耗時 | 載入後 `context_length` | `size_vram` |
|---|---|---|---|
| `/v1` 不帶 | 5.64 s（載入） | 131072 | 10,626,982,215 |
| `/v1` `"options": {"num_ctx": 8192}` | 0.10 s（沒有重載） | 131072 | 不變 |
| `/v1` 頂層 `"num_ctx": 8192` | 0.10 s | 131072 | 不變 |
| `/api/generate` `"options": {"num_ctx": 8192}` | 5.42 s（重載） | **8192** | 5,862,210,928 |
| 再發一次 `/v1` 不帶 | 5.75 s（重載回去） | 131072 | 10,626,982,215 |

### 對設計的影響

- Hermes 的 custom/ollama provider 走 `/v1`，所以它在 `extra_body.options.num_ctx` 帶的值，在 Ollama 0.40.14 **不生效**。模型實際的 context 由伺服器決定：`OLLAMA_CONTEXT_LENGTH`，未設定則用 Ollama 預設值。
- 估算器不能用請求裡的 `num_ctx`。順序如下：
  1. 先看 `/api/ps` 上次觀察到的 `context_length`（依模型記錄）。
  2. 再看 `OLLAMA_CONTEXT_LENGTH`。
  3. 最後才用模型的最大 context。
- `/v1` 和原生 API 交錯使用、而 ctx 不同時，每次都會重載（約 5.5 s）。保溫 ping 不帶 `options`，所以不會觸發重載。

## 4. 估算器校準：qwen3.5:9b（Q4_K_M）

- 模型檔 6,550,825,550 B，參數 8.95 B。架構 `qwen35`：32 層，`full_attention_interval=4`，也就是只有 8 層是完整注意力（其餘為線性注意力）。KV 頭數 4，key/value 長度 256。
- 理論 KV（f16）：8 層 × 2 × 4 × 256 × 2 B ＝ **32,768 B/token**；在 131072 ctx 下是 4.29 GB。如果天真地以 32 層計算，會是 17.2 GB，高估 4 倍。
- 實測斜率：(10,626,982,215 − 5,862,210,928) ÷ (131072 − 8192) ＝ **38,776 B/token**，比理論值多 18%，多出的是隨 ctx 成長的 compute buffer。
- 實測截距（ctx→0）：5,544,557,936 B，比模型檔小。模型檔可能含有沒載入的視覺投影權重，尚未確認。
- 估算器必須讀 `full_attention_interval` 這類混合注意力欄位（Qwen-Next 系列同理），否則會嚴重高估。

## 5. `OLLAMA_MAX_LOADED_MODELS=1`

Ollama 自己最多只常駐一個模型；切換模型時，Ollama 會等目前的請求做完再換。在這個設定下，DESIGN 的「LRU 逐出閒置模型」大多由 Ollama 自己完成。Coffee 剩下的價值是：

- 載入前預測放不放得下：carve-out 已被外部程式佔用時，預先 hold。
- 偵測外部用量（Hermes 自己的 llama-server、ComfyUI、遊戲等）。
- 讓使用者看到狀態與通知。
- 保溫。

要不要把這個值調高、改由 Coffee 管理多模型共存，待決定。

## 6. 主模型實測：比 carve-out 還大的模型

`qwen3.8-flash-next-huihui:iq3s`：架構 `qwen4exp`，176.9B MoE（512 個專家，每次啟用 10 個），IQ3_S，模型檔 78.84 GiB。
用 `scripts/measure_load.py` 經 `/v1` 載入，每秒取樣 NVML、PDH（`\GPU Adapter Memory(*)\Dedicated/Shared Usage`）和 OS 記憶體。

| 時間點 | NVML used | PDH Dedicated | PDH Shared | OS 可用 RAM | 可用 commit |
|---|---|---|---|---|---|
| 空載 | 2.84 GiB | 2.84 | 0.07 | 44.30 | 120.8 |
| 第 5 秒 | 53.78 | 53.79 | 0.45 | 43.56 | 42.4 |
| 第 27–43 秒 | 53.8 | 53.8 | 0.55 | 41.2 → 17.4 | 42.5 |
| 載入完成（46.3 秒） | 59.21 | 59.21 | 0.61 | 16.47 | 35.2 |
| 卸載後 | 2.97 | 2.97 | 0.18 | 44.87 | 120.9 |

- `/api/ps` 回報 `size = size_vram = 52.28 GiB`，ctx 131072。也就是說，Ollama 回報的只是放在 GPU 上的部分。
- Ollama 會自己把模型裝進 carve-out：GPU 先拿走約 51 GiB，剩下約 27 GiB 權重放在系統 RAM，由 CPU 跑（OS 可用 RAM 掉了 27.8 GiB）。**沒有溢出到 Windows 共享 GPU 記憶體**，Shared 一直在 0.6 GiB 以下。
- 載入後 carve-out 用掉 94.7%（59.21 / 62.5）。NVML 增量 56.37 GiB，對比 Ollama 回報的 52.28 GiB，多 7.8%，`estimate_overhead_pct` 預設的 10 仍然偏保守。
- 從磁碟冷載入要 46 秒，所以切換模型的代價很高。
- PDH Dedicated 和 NVML 一致，所以不需要額外的 PDH 依賴。`scripts/winpdh.py` 只留作量測工具。

### 對設計的影響

- 容量仍然以 NVML total（carve-out）為準。CUDA 池的 109.7 GiB 不是 Ollama 會用的空間。
- 模型比 GPU 剩餘空間大時，不算「放不下」：Ollama 會把 GPU 填滿，其餘交給 CPU。所以政策把 GPU 需求的上限設為：hold 線 − 桌面基線 − 釘選模型。不再提早放行並發警報，只在第一次遇到時發一則 `partial` 提示。
- 桌面基線取最近 15 分鐘外部用量（NVML used − Ollama）的最低值。高出基線的部分（ComfyUI、遊戲）視為暫時佔用，值得等待。
- 常駐模型不需要額外空間，因為 `/v1` 不會讓它以更大的 ctx 重載。所以主模型常駐在 94.7% 時，呼叫會直接放行，不發警告。

## 7. 和核心 `probe_budget` 的差異（README／PR 用）

在這台機器上，核心 `hermes_cli/local_runtime/hardware.py` 會讀到 `INTEGRATED=1`，於是以 CUDA 池 109.7 GiB 為容量；即時預算取 min(池, smi free + OS 可用)，約 103 GiB。這是為 Hermes **自己的** llama-server 規劃單一模型的啟動視窗。

Coffee 的出發點不同：它看的是外部 Ollama 在 carve-out（62.5 GiB）內的多模型排隊、逐出和保溫，預算以 carve-out 為準，因為實測 Ollama 只會用到這一塊，放不下的部分交給 CPU（§6）。
