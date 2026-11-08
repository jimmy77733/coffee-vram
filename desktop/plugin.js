import {
  atom, Button, host, PALETTE_AREA, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS, Tip,
  usePluginI18n, useValue
} from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'coffee-vram'
const GIB = 2 ** 30
const POLL_MS = 2000
const FEED_SIZE = 8
const YEAR_S = 365 * 86400
const TOAST_ID = 'coffee-alert'

const EN = {
  title: 'Coffee', chipLabel: 'GPU memory', chipLabelRam: 'System memory',
  unknown: 'GPU memory unknown', ollamaDown: 'Ollama is not answering',
  offline: 'Coffee backend unavailable. Is coffee-vram in plugins.enabled, and was the gateway restarted?',
  gpu: 'GPU', ramLabel: 'System RAM', cpuLabel: 'CPU', of: '{used} / {total} GB',
  memLabel: 'Memory', computeLabel: 'Compute',
  chipMem: 'GPU {pct}%', chipCompute: 'Compute {pct}',
  chipRam: 'RAM {pct}%', chipCpu: 'CPU {pct}%', chipNet: '{rate}',
  other: 'Other apps {gb} GB', warnAt: 'warn {pct}%', holdAt: 'hold {pct}%',
  net: 'Net {rate}', temp: '{temp}°C',
  models: 'Loaded models', noModels: 'No models loaded', inRam: '+{gb} GB in system RAM', pinned: 'pinned',
  staysLoaded: 'stays loaded', minutesLeft: '{n} min left', unload: 'Unload', confirmUnload: 'Unload?',
  ctx: 'ctx {used}/{max}', ctxMax: 'ctx {max}', tps: '{n} tok/s',
  queue: 'Queue', queueEmpty: 'No calls waiting', running: 'running', queued: 'waiting', seconds: '{n}s',
  history: 'Last 15 minutes', events: 'Recent', noEvents: 'Nothing yet',
  perf: 'Performance', petToggleOn: 'Hide performance pet', petToggleOff: 'Show performance pet',
  petOnline: 'Performance pet running',
  petOffline: 'Performance pet offline',
  noSharedQueue: 'This window reads GPU memory directly; the queue lives in the agent process.',
  open: 'Coffee: show GPU memory',
  gpuHint: 'GPU carve-out (NVML). Not the same as Task Manager → Memory (system RAM).',
  ramHint: 'Windows-visible system RAM. Separate from the GPU carve-out Coffee budgets model loads against.'
}
const LOCALES = {
  en: EN,
  'zh-hant': {
    ...EN, chipLabel: 'GPU 記憶體', chipLabelRam: '系統記憶體',
    unknown: '無法讀取 GPU 記憶體', ollamaDown: 'Ollama 沒有回應',
    offline: '連不上 Coffee 後端。coffee-vram 有加進 plugins.enabled、而且重啟過 gateway 嗎？',
    gpu: 'GPU', ramLabel: '系統記憶體', cpuLabel: 'CPU',
    memLabel: '佔用率', computeLabel: '使用率',
    chipMem: 'GPU占用 {pct}%', chipCompute: '使用 {pct}',
    chipRam: '記憶體 {pct}%', chipCpu: 'CPU {pct}%', chipNet: '{rate}',
    other: '其他程式 {gb} GB', warnAt: '警告 {pct}%', holdAt: '暫停 {pct}%',
    net: '網路 {rate}', temp: '{temp}°C',
    models: '已載入模型', noModels: '沒有載入的模型', inRam: '+{gb} GB 在系統記憶體', pinned: '常駐',
    staysLoaded: '持續載入', minutesLeft: '剩 {n} 分鐘', unload: '卸載', confirmUnload: '確定卸載？',
    ctx: '上下文 {used}/{max}', ctxMax: '上下文 {max}', tps: '{n} tok/s',
    queue: '佇列', queueEmpty: '沒有等待中的呼叫', running: '執行中', queued: '等待中',
    history: '最近 15 分鐘', events: '最近事件', noEvents: '還沒有事件',
    perf: '效能狀態', petToggleOn: '關閉效能寵物', petToggleOff: '開啟效能寵物',
    petOnline: '效能寵物運行中',
    petOffline: '效能寵物離線',
    noSharedQueue: '這個視窗直接讀取 GPU 記憶體；佇列在 agent 的行程裡。',
    open: 'Coffee：顯示 GPU 記憶體',
    gpuHint: 'GPU 專用區（NVML）。和工作管理員 → 記憶體（系統 RAM）不是同一塊。',
    ramHint: 'Windows 可見的系統記憶體。和 Coffee 用來排程模型的 GPU 專用區不是同一塊。'
  }
}

const PET_PREF_KEY = 'coffee-vram.pet'

// Disk plugins are not scanned by Tailwind; theme variables only.
const CSS = `
.coffee-chip{display:flex;align-items:center;gap:4px;font-variant-numeric:tabular-nums;flex-wrap:wrap;max-width:min(72vw,720px)}
.coffee-chip-mark{color:var(--ui-text-tertiary);flex-shrink:0}
.coffee-chip[data-level=warn] .coffee-chip-mark{color:var(--ui-yellow)}
.coffee-chip[data-level=hold] .coffee-chip-mark{color:var(--ui-red)}
.coffee-chip[data-level=off] .coffee-chip-mark{color:var(--ui-text-quaternary)}
.coffee-chip-sep{color:var(--ui-text-quaternary);opacity:.7}
.coffee-chip-gpu{color:#7aa2f7}
.coffee-chip-util{color:#9ece6a}
.coffee-chip-temp{color:#ff9e64}
.coffee-chip-ram{color:#7dcfff}
.coffee-chip-cpu{color:#bb9af7}
.coffee-chip-net{color:#73daca}
.coffee-chip-ctx{color:#c0caf5}
.coffee-chip[data-level=off] .coffee-chip-gpu,
.coffee-chip[data-level=off] .coffee-chip-util,
.coffee-chip[data-level=off] .coffee-chip-temp,
.coffee-chip[data-level=off] .coffee-chip-ram,
.coffee-chip[data-level=off] .coffee-chip-cpu,
.coffee-chip[data-level=off] .coffee-chip-net,
.coffee-chip[data-level=off] .coffee-chip-ctx{color:var(--ui-text-quaternary)}
.coffee-popover{width:340px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 120px);overflow-y:auto}
.coffee-panel{display:flex;flex-direction:column;gap:14px;padding:10px;font-size:12px;line-height:17px;color:var(--ui-text-secondary)}
.coffee-pane{height:100%;overflow-y:auto}
.coffee-heading{font-size:11px;font-weight:600;letter-spacing:.02em;color:var(--ui-text-tertiary);margin-bottom:4px}
.coffee-figure{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.coffee-figure strong{font-size:15px;font-weight:600;color:var(--ui-text-primary);font-variant-numeric:tabular-nums}
.coffee-device{display:block;font-size:10px;line-height:13px;font-weight:500;color:var(--ui-text-quaternary);margin-bottom:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.coffee-gauge{position:relative;height:8px;margin-top:6px;border-radius:4px;background:var(--ui-bg-tertiary);overflow:hidden;display:flex}
.coffee-gauge>span{height:100%}
.coffee-gauge-thin{height:4px;margin-top:4px}
.coffee-meters{display:flex;flex-direction:column;gap:14px}
.coffee-gpu-block{display:flex;flex-direction:column;gap:8px}
.coffee-seg-other{background:var(--ui-text-quaternary)}
.coffee-seg-model{background:var(--ui-accent);border-right:1px solid var(--ui-bg-elevated)}
.coffee-seg-fill{background:var(--ui-accent)}
.coffee-seg-compute{background:var(--ui-text-secondary);opacity:.85}
.coffee-mark{position:absolute;top:0;bottom:0;width:1px}
.coffee-mark-warn{background:var(--ui-yellow)}
.coffee-mark-hold{background:var(--ui-red)}
.coffee-legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:4px;font-size:11px;color:var(--ui-text-tertiary)}
.coffee-net{margin-top:2px;font-size:11px;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums}
.coffee-figure-sub{font-size:12px;font-weight:500;color:var(--ui-text-secondary)}
.coffee-alert{padding:6px 8px;border-radius:4px;border:1px solid var(--ui-stroke-tertiary);color:var(--ui-text-secondary)}
.coffee-alert[data-level=alert]{border-color:var(--ui-red)}
.coffee-row{display:flex;align-items:center;gap:8px;min-height:24px}
.coffee-row-main{flex:1;min-width:0}
.coffee-name{display:block;color:var(--ui-text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.coffee-sub{display:block;font-size:11px;color:var(--ui-text-tertiary)}
.coffee-num{font-variant-numeric:tabular-nums;color:var(--ui-text-secondary);white-space:nowrap}
.coffee-empty{color:var(--ui-text-quaternary)}
.coffee-pet{display:flex;flex-direction:column;gap:4px}
.coffee-pet-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.coffee-pet-head .coffee-heading{margin-bottom:0}
.coffee-pet-canvas{display:block;width:100%;height:72px;border-radius:4px;background:var(--ui-bg-tertiary);image-rendering:pixelated}
.coffee-pet-toggle{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;padding:0;border-radius:4px;border:1px solid var(--ui-stroke-tertiary);background:transparent}
.coffee-pet-toggle-dot{display:block;width:8px;height:8px;border-radius:50%;background:var(--ui-text-quaternary)}
.coffee-pet-toggle[data-on] .coffee-pet-toggle-dot{background:var(--ui-accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--ui-accent) 28%,transparent)}
.coffee-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:var(--ui-text-quaternary)}
.coffee-dot[data-level=warn]{background:var(--ui-yellow)}
.coffee-dot[data-level=alert]{background:var(--ui-red)}
.coffee-time{font-size:11px;color:var(--ui-text-quaternary);font-variant-numeric:tabular-nums;flex-shrink:0}
`

const gb = bytes => (bytes / GIB).toFixed(1)
const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ''))
const clock = at => new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const kTokens = n => n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n)
const netRate = bps => bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} MB/s` : bps >= 1000 ? `${(bps / 1000).toFixed(0)} KB/s` : `${Math.round(bps)} B/s`

/** Text progress bar like Hermes context chip: [████░░░░░░] 56% */
function ctxBar(used, total) {
  if (!total || total <= 0) return null
  const pct = Math.min(100, Math.max(0, Math.round(used / total * 100)))
  const slots = 10
  const filled = Math.round(pct / 100 * slots)
  return { pct, bar: `[${'█'.repeat(filled)}${'░'.repeat(slots - filled)}]` }
}

function ghostColor(util) {
  // White at idle → cool blue → amber → hot pink/red as GPU compute rises.
  const u = Math.min(100, Math.max(0, util)) / 100
  const stops = [
    [0.00, [245, 245, 248]],
    [0.25, [180, 200, 230]],
    [0.50, [122, 162, 247]],
    [0.75, [224, 175, 104]],
    [1.00, [247, 118, 142]]
  ]
  let i = 0
  while (i < stops.length - 2 && u > stops[i + 1][0]) i++
  const [t0, c0] = stops[i]
  const [t1, c1] = stops[i + 1]
  const t = t1 === t0 ? 0 : (u - t0) / (t1 - t0)
  const rgb = c0.map((a, n) => Math.round(a + (c1[n] - a) * t))
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`
}


function createStore(ctx) {
  const status = atom(null)
  const offline = atom(false)
  const feed = atom([])
  const petOn = atom(readPetPref())
  let lastSeq = null
  let busy = false
  // Warning/error toasts already stay until dismissed (Hermes defaultDuration). While one
  // Coffee toast is open, skip further ones — same id would only replace in place.
  let toastOpen = false

  function announce(event) {
    if (event.level === 'info') return
    if (toastOpen) return
    toastOpen = true
    const kind = event.level === 'alert' ? 'error' : 'warning'
    host.notify({
      id: TOAST_ID,
      kind,
      title: 'Coffee',
      message: event.message,
      onDismiss: () => { toastOpen = false }
    })
    if (event.level === 'alert') ctx.os.notify({ title: 'Coffee', body: event.message })
  }

  async function poll() {
    if (busy) return
    busy = true
    try {
      const snap = await ctx.rest('/snapshot', { timeoutMs: 8000 })
      status.set(snap)
      offline.set(false)
      if (lastSeq === null || snap.last_event > lastSeq) {
        const { events, last } = await ctx.rest(`/events?since=${lastSeq ?? 0}`)
        if (lastSeq !== null) events.forEach(announce)
        // Collapse a burst of the same message (e.g. warn↔hold flicker before the gateway reloads).
        const merged = []
        for (const event of [...events.reverse(), ...feed.get()]) {
          const prev = merged[merged.length - 1]
          if (prev && prev.kind === event.kind && prev.message === event.message) continue
          merged.push(event)
        }
        feed.set(merged.slice(0, FEED_SIZE))
        lastSeq = last
      }
    } catch {
      offline.set(true)
    } finally {
      busy = false
    }
  }

  async function unload(model) {
    try {
      const result = await ctx.rest('/evict', { method: 'POST', body: { model } })
      host.notify({ kind: result.ok ? 'info' : 'warning', title: 'Coffee', message: result.message })
    } catch (error) {
      host.notifyError(error, `Could not unload ${model}`)
    }
    void poll()
  }

  function setPetOn(value) {
    writePetPref(value)
    petOn.set(Boolean(value))
  }

  void poll()
  ctx.setInterval(() => void poll(), POLL_MS)
  ctx.onEvent('plugin.coffee-vram.event', () => void poll())
  return { status, offline, feed, petOn, setPetOn, unload }
}

function thresholdMarks(snap, active) {
  if (!active) return []
  return [
    jsx('span', { className: 'coffee-mark coffee-mark-warn', style: { left: `${snap.warn_pct}%` }, key: 'warn' }),
    jsx('span', { className: 'coffee-mark coffee-mark-hold', style: { left: `${snap.hold_pct}%` }, key: 'hold' })
  ]
}

function thresholdLegend(snap, t, active) {
  if (!active) return []
  return [
    jsx('span', { key: 'warn', children: fill(t('warnAt'), { pct: snap.warn_pct }) }),
    jsx('span', { key: 'hold', children: fill(t('holdAt'), { pct: snap.hold_pct }) })
  ]
}

function GpuMeter({ snap }) {
  const t = usePluginI18n(ID)
  if (!snap.total) return jsx('div', { className: 'coffee-empty', children: t('unknown') })
  const host = snap.host || {}
  const marks = (snap.alert_source || 'gpu') === 'gpu'
  const pct = bytes => `${Math.max(0, bytes / snap.total * 100)}%`
  const memPct = Math.round(snap.used_pct)
  const util = host.gpu_util_pct
  const utilLabel = util == null ? '—' : `${Math.round(util)}%`
  const temp = snap.show_gpu_temp !== false && host.gpu_temp_c != null
    ? fill(t('temp'), { temp: host.gpu_temp_c })
    : null
  return jsxs('div', { className: 'coffee-gpu-block', children: [
    host.gpu_name
      ? jsx('div', { className: 'coffee-device', title: host.gpu_name, children: host.gpu_name })
      : null,
    jsx('div', { className: 'coffee-figure', children:
      jsxs('strong', { children: [
        jsx(Tip, { label: t('gpuHint'), children: jsx('span', { children: t('gpu') }) }),
        temp ? ` · ${temp}` : null
      ] }) }),
    jsxs('div', { children: [
      jsx('div', { className: 'coffee-figure-sub', children:
        `${t('memLabel')} ${fill(t('of'), { used: gb(snap.used), total: gb(snap.total) })} · ${memPct}%` }),
      jsxs('div', { className: 'coffee-gauge', role: 'img',
        'aria-label': `${t('memLabel')} ${memPct}%`, children: [
        jsx('span', { className: 'coffee-seg-other', style: { width: pct(snap.external) } }),
        ...snap.loaded.map(m => jsx(Tip, { label: `${m.name} · ${gb(m.size_vram)} GB`, children:
          jsx('span', { className: 'coffee-seg-model', style: { width: pct(m.size_vram) } }) }, m.name)),
        ...thresholdMarks(snap, marks)
      ] }),
      jsxs('div', { className: 'coffee-legend', children: [
        jsx('span', { children: fill(t('other'), { gb: gb(Math.max(0, snap.external)) }) }),
        ...thresholdLegend(snap, t, marks)
      ] })
    ] }),
    jsxs('div', { children: [
      jsx('div', { className: 'coffee-figure-sub', children: `${t('computeLabel')} ${utilLabel}` }),
      jsx('div', { className: 'coffee-gauge coffee-gauge-thin', role: 'img',
        'aria-label': `${t('computeLabel')} ${utilLabel}`,
        children: util == null
          ? null
          : jsx('span', { className: 'coffee-seg-compute',
            style: { width: `${Math.min(100, Math.max(0, util))}%` } }) })
    ] })
  ] })
}

function RamMeter({ snap }) {
  const t = usePluginI18n(ID)
  const host = snap.host || {}
  if (snap.show_system_ram === false || !host.ram_total) return null
  const marks = snap.alert_source === 'system_ram'
  const pct = Math.min(100, Math.max(0, host.ram_pct || 0))
  return jsxs('div', { children: [
    host.ram_name
      ? jsx('div', { className: 'coffee-device', title: host.ram_name, children: host.ram_name })
      : null,
    jsxs('div', { className: 'coffee-figure', children: [
      jsxs('strong', { children: [
        jsx(Tip, { label: t('ramHint'), children: jsx('span', { children: t('ramLabel') }) }),
        ' ', fill(t('of'), { used: gb(host.ram_used), total: gb(host.ram_total) })
      ] }),
      jsx('span', { className: 'coffee-num', children: `${Math.round(pct)}%` })
    ] }),
    jsxs('div', { className: 'coffee-gauge', role: 'img',
      'aria-label': `${t('ramLabel')} ${Math.round(pct)}%`, children: [
      jsx('span', { className: 'coffee-seg-fill', style: { width: `${pct}%` } }),
      ...thresholdMarks(snap, marks)
    ] }),
    marks ? jsx('div', { className: 'coffee-legend', children: thresholdLegend(snap, t, true) }) : null
  ] })
}

function CpuMeter({ snap }) {
  const t = usePluginI18n(ID)
  const host = snap.host || {}
  if (snap.show_cpu === false || host.cpu_pct == null) return null
  const pct = Math.min(100, Math.max(0, host.cpu_pct))
  return jsxs('div', { children: [
    host.cpu_name
      ? jsx('div', { className: 'coffee-device', title: host.cpu_name, children: host.cpu_name })
      : null,
    jsxs('div', { className: 'coffee-figure', children: [
      jsx('strong', { children: t('cpuLabel') }),
      jsx('span', { className: 'coffee-num', children: `${Math.round(pct)}%` })
    ] }),
    jsx('div', { className: 'coffee-gauge', role: 'img', 'aria-label': `${t('cpuLabel')} ${Math.round(pct)}%`,
      children: jsx('span', { className: 'coffee-seg-fill', style: { width: `${pct}%` } }) })
  ] })
}

function Meters({ snap }) {
  const t = usePluginI18n(ID)
  const host = snap.host || {}
  const net = snap.show_network !== false && host.net_bps != null
    ? jsx('div', { className: 'coffee-net', children: fill(t('net'), { rate: netRate(host.net_bps) }) })
    : null
  return jsxs('div', { className: 'coffee-meters', children: [
    jsx(GpuMeter, { snap }),
    jsx(RamMeter, { snap }),
    jsx(CpuMeter, { snap }),
    net
  ] })
}

function remaining(model, t) {
  if (model.pinned) return t('pinned')
  if (!model.expires_at) return ''
  const left = model.expires_at - Date.now() / 1000
  return left > YEAR_S ? t('staysLoaded') : fill(t('minutesLeft'), { n: Math.max(0, Math.round(left / 60)) })
}

function ModelRow({ model, busy, store, showStats }) {
  const t = usePluginI18n(ID)
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const timer = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(timer)
  }, [armed])
  const ctxLabel = (() => {
    if (!showStats || !model.context_length) return ''
    const used = model.prompt_tokens || 0
    const bar = ctxBar(used, model.context_length)
    const base = used
      ? fill(t('ctx'), { used: kTokens(used), max: kTokens(model.context_length) })
      : fill(t('ctxMax'), { max: kTokens(model.context_length) })
    return bar ? `${base} ${bar.bar} ${bar.pct}%` : base
  })()
  const details = [
    model.spill ? fill(t('inRam'), { gb: gb(model.spill) }) : '',
    remaining(model, t),
    ctxLabel,
    showStats && model.tps != null ? fill(t('tps'), { n: model.tps }) : ''
  ].filter(Boolean)
  return jsxs('div', { className: 'coffee-row', children: [
    jsxs('div', { className: 'coffee-row-main', children: [
      jsx('span', { className: 'coffee-name', title: model.name, children: model.name }),
      details.length > 0 && jsx('span', { className: 'coffee-sub', children: details.join(' · ') })
    ] }),
    jsx('span', { className: 'coffee-num', children: `${gb(model.size_vram)} GB` }),
    !busy && jsx(Button, {
      variant: 'ghost', size: 'micro', 'aria-label': `${t('unload')}: ${model.name}`,
      onClick: () => { if (armed) { setArmed(false); void store.unload(model.name) } else setArmed(true) },
      children: armed ? t('confirmUnload') : t('unload')
    })
  ] })
}

const PET_W = 320
const PET_H = 72
const PET_GROUND = 58
const PET_X = 36
const PET_FOOT = 14

function paintPixels(ctx, pixels, ox, oy, color, scale = 2) {
  ctx.fillStyle = color
  for (const [x, y] of pixels) {
    ctx.fillRect(ox + x * scale, oy + y * scale, scale, scale)
  }
}

// Ghost only — white idle, util recolors at runtime.
const GHOST = {
  runA: [[1, 0], [2, 0], [3, 0], [0, 1], [1, 1], [2, 1], [3, 1], [4, 1], [0, 2], [1, 2], [3, 2], [4, 2],
    [0, 3], [1, 3], [2, 3], [3, 3], [4, 3], [0, 4], [1, 4], [2, 4], [3, 4], [4, 4], [0, 5], [2, 5], [4, 5],
    [0, 6], [2, 6], [4, 6]],
  runB: [[1, 0], [2, 0], [3, 0], [0, 1], [1, 1], [2, 1], [3, 1], [4, 1], [0, 2], [1, 2], [3, 2], [4, 2],
    [0, 3], [1, 3], [2, 3], [3, 3], [4, 3], [0, 4], [1, 4], [2, 4], [3, 4], [4, 4], [1, 5], [3, 5],
    [1, 6], [3, 6]],
  // Flat duck: empty top rows so raised aerial threats pass over the head.
  duck: [[0, 4], [1, 4], [2, 4], [3, 4], [4, 4], [0, 5], [1, 5], [2, 5], [3, 5], [4, 5],
    [0, 6], [2, 6], [4, 6]],
  idle: [[1, 0], [2, 0], [3, 0], [0, 1], [1, 1], [2, 1], [3, 1], [4, 1], [0, 2], [1, 2], [3, 2], [4, 2],
    [0, 3], [1, 3], [2, 3], [3, 3], [4, 3], [0, 4], [1, 4], [2, 4], [3, 4], [4, 4], [0, 5], [2, 5], [4, 5],
    [0, 6], [2, 6], [4, 6]]
}

// Ground hazards (jump). Aerial hazards that can hit a standing pet (duck). Clouds = weather only.
const OB_SPRITES = {
  cactus: { pixels: [[1, 0], [1, 1], [0, 2], [1, 2], [2, 2], [1, 3], [1, 4], [1, 5], [1, 6]], color: '#9ece6a' },
  cactusTall: { pixels: [[1, 0], [1, 1], [0, 1], [1, 2], [2, 2], [1, 3], [0, 3], [1, 4], [1, 5], [1, 6], [1, 7]], color: '#73daca' },
  rock: { pixels: [[1, 4], [2, 4], [0, 5], [1, 5], [2, 5], [3, 5], [0, 6], [1, 6], [2, 6], [3, 6]], color: '#a9b1d6' },
  crate: { pixels: [[0, 3], [1, 3], [2, 3], [3, 3], [0, 4], [3, 4], [0, 5], [1, 5], [2, 5], [3, 5], [0, 6], [1, 6], [2, 6], [3, 6]], color: '#e0af68' },
  spikes: { pixels: [[0, 5], [2, 5], [4, 5], [0, 6], [1, 6], [2, 6], [3, 6], [4, 6]], color: '#f7768e' },
  bird: { pixels: [[0, 1], [1, 0], [1, 1], [2, 1], [3, 0], [3, 1], [4, 1]], color: '#7aa2f7' },
  bat: { pixels: [[0, 1], [1, 0], [2, 1], [3, 0], [4, 1], [1, 2], [2, 2], [3, 2]], color: '#bb9af7' },
  // Wider, brighter decorative clouds (weather-only, never collide).
  cloud: {
    pixels: [
      [2, 0], [3, 0], [4, 0], [5, 0], [6, 0],
      [1, 1], [2, 1], [3, 1], [4, 1], [5, 1], [6, 1], [7, 1],
      [0, 2], [1, 2], [2, 2], [3, 2], [4, 2], [5, 2], [6, 2], [7, 2], [8, 2],
      [1, 3], [2, 3], [3, 3], [4, 3], [5, 3], [6, 3], [7, 3]
    ],
    color: '#ffffff'
  }
}

const GROUND_KINDS = ['cactus', 'cactusTall', 'rock', 'crate', 'spikes']
const AERIAL_KINDS = ['bird', 'bat'] // drawn low enough to clip a standing ghost

function readPetPref() {
  try {
    const raw = localStorage.getItem(PET_PREF_KEY)
    if (raw === '0' || raw === 'false') return false
    if (raw === '1' || raw === 'true') return true
  } catch { /* private mode */ }
  return true
}

function writePetPref(on) {
  try { localStorage.setItem(PET_PREF_KEY, on ? '1' : '0') } catch { /* ignore */ }
}

function PetRun({ snap, backendOffline, enabled, onToggle }) {
  const t = usePluginI18n(ID)
  const canvasRef = useRef(null)
  const stateRef = useRef({
    scroll: 0, frame: 0, nextSpawn: 1.2, nextCloud: 2.5, nextWeather: 12, nextObId: 1,
    obstacles: [], clouds: [], particles: [],
    weather: 'clear', weatherFlash: 0,
    action: 'run', dodgeId: 0, dodgeStartX: 0, dodgeProg: 0, last: 0,
    _live: false, _util: 0, _enabled: true
  })
  const live = !backendOffline && Array.isArray(snap.loaded) && snap.loaded.length > 0
  const util = Math.min(100, Math.max(0, snap.host?.gpu_util_pct ?? 0))

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const ctx = canvas.getContext('2d')
    if (!ctx) return undefined
    let raf = 0
    const css = getComputedStyle(canvas)
    const mute = css.getPropertyValue('--ui-text-quaternary').trim() || '#5f6368'
    const ground = css.getPropertyValue('--ui-stroke-tertiary').trim() || '#3c4043'
    const weatherKinds = ['clear', 'rain', 'snow', 'thunder', 'fog']
    const weatherColor = {
      clear: null,
      rain: '#7aa2f7',
      snow: '#d7dbe7',
      thunder: '#bb9af7',
      fog: '#a9b1d6'
    }

    const tick = nowMs => {
      const st = stateRef.current
      const dt = st.last ? Math.min(0.05, (nowMs - st.last) / 1000) : 0
      st.last = nowMs
      const online = st._enabled && st._live
      const speed = online ? 28 + st._util * 1.05 : 0
      const baseY = PET_GROUND - PET_FOOT
      const landY = PET_GROUND - 1
      // Distance-locked dodge vs pet front edge — independent of GPU util / scroll speed.
      const jumpH = 38
      const petFront = PET_X + 10 // right edge of the 5×2 ghost sprite
      const JUMP_START_LEAD = 34 // jump when ground obstacle left edge is this close
      const DUCK_START_LEAD = 16 // duck later — only when aerial left edge is nearly at the pet
      const JUMP_LAND_PAST = 26 // land after ground obstacle right edge clears petFront
      const DUCK_RISE_PAST = 14 // stand up soon after aerial right edge clears the pet

      if (online && dt > 0) {
        // Coming back online from idle must resume running or dodge never arms.
        if (st.action === 'idle') st.action = 'run'
        st.scroll += speed * dt
        st.frame += dt * (4 + st._util / 18)
        st.nextSpawn -= dt
        st.nextCloud -= dt
        st.nextWeather -= dt
        if (st.weatherFlash > 0) st.weatherFlash = Math.max(0, st.weatherFlash - dt)

        if (st.nextWeather <= 0) {
          const pool = weatherKinds.filter(w => w !== st.weather)
          st.weather = pool[Math.floor(Math.random() * pool.length)]
          st.particles = []
          st._precipAcc = 0
          st.nextWeather = 10 + Math.random() * 20 // 10–30s
          if (st.weather === 'thunder') {
            st.weatherFlash = 0.18
            st._strikeSoon = 0.05
          }
        }

        // Weather particles — rate-limited (per-frame spawn was culling flakes before they hit the floor).
        const wx = st.weather
        st._precipAcc = (st._precipAcc || 0) + dt
        if (wx === 'rain' || wx === 'snow' || wx === 'thunder') {
          const interval = wx === 'snow' ? 0.055 : 0.028 // ~18 snow/s, ~36 rain/s
          while (st._precipAcc >= interval) {
            st._precipAcc -= interval
            const isSnow = wx === 'snow'
            const flake = 2 + (Math.random() < 0.4 ? 1 : 0)
            st.particles.push({
              kind: isSnow ? 'snow' : 'rain',
              x: Math.random() * PET_W,
              y: -4 - Math.random() * 10,
              vy: isSnow ? 72 + Math.random() * 38 : 100 + Math.random() * 70,
              vx: isSnow ? -8 + Math.random() * 6 : -speed * 0.12,
              life: 12,
              grounded: false,
              len: isSnow ? flake : 4 + Math.floor(Math.random() * 3),
              size: flake
            })
          }
          if (wx === 'thunder') {
            st._strikeSoon = (st._strikeSoon ?? 1.6) - dt
            if (st._strikeSoon <= 0) {
              const sx = 24 + Math.random() * (PET_W - 48)
              const segs = []
              let lx = sx
              let ly = 0
              segs.push({ x: lx, y: ly })
              while (ly < landY) {
                ly = Math.min(landY, ly + 5 + Math.random() * 7)
                lx += -7 + Math.random() * 14
                segs.push({ x: lx, y: ly })
              }
              st.particles.push({
                kind: 'lightning', segs, life: 0.22, grounded: true
              })
              st.weatherFlash = 0.2
              st._strikeSoon = 1.4 + Math.random() * 2.8
            }
          }
        } else if (wx === 'fog') {
          st._precipAcc = 0
          if (st.particles.filter(p => p.kind === 'fog').length < 8 && Math.random() < 0.35) {
            st.particles.push({
              kind: 'fog',
              x: PET_W + 4,
              y: 8 + Math.random() * (PET_GROUND - 18),
              vy: -2 + Math.random() * 4,
              vx: -(10 + Math.random() * 14),
              life: 2.4,
              size: 10 + Math.random() * 16
            })
          }
        } else {
          st._precipAcc = 0
        }
        for (const p of st.particles) {
          if (p.kind === 'lightning') {
            p.life -= dt
            continue
          }
          if (p.kind === 'fog') {
            p.x += (p.vx || 0) * dt
            p.y += (p.vy || 0) * dt
            p.life -= dt * 0.22
            continue
          }
          if (p.grounded) {
            // Snow piles stay put on the ground; rain splash fades fast.
            p.life -= dt * (p.kind === 'snow' ? 0.55 : 2.4)
            continue
          }
          p.x += (p.vx || 0) * dt
          p.y += (p.vy || 0) * dt
          const tip = p.kind === 'rain' ? p.y + (p.len || 5) : p.y + (p.size || 2)
          if (tip >= landY) {
            p.grounded = true
            p.y = landY - (p.kind === 'rain' ? (p.len || 5) : (p.size || 2))
            p.vy = 0
            p.vx = 0
            p.life = p.kind === 'snow' ? 1.6 : 0.28
          }
        }
        // Prefer dropping spent grounded / offscreen particles — never cull mid-fall flakes first.
        st.particles = st.particles.filter(p => {
          if (p.kind === 'fog') return p.x > -40 && p.life > 0
          if (p.kind === 'lightning') return p.life > 0
          return p.life > 0 && p.x > -10 && p.x < PET_W + 10
        })
        if (st.particles.length > 160) {
          const falling = []
          const rest = []
          for (const p of st.particles) {
            if ((p.kind === 'snow' || p.kind === 'rain') && !p.grounded) falling.push(p)
            else rest.push(p)
          }
          const keepRest = rest.slice(-40)
          st.particles = falling.concat(keepRest).slice(0, 160)
        }

        if (st.nextCloud <= 0) {
          st.clouds.push({
            x: PET_W + 14,
            y: 2 + Math.random() * 8,
            drift: 0.42 + Math.random() * 0.28
          })
          st.nextCloud = (st.weather === 'clear' ? 2.8 : 2.0) + Math.random() * 2.6
        }
        for (const cloud of st.clouds) cloud.x -= speed * cloud.drift * dt
        st.clouds = st.clouds.filter(c => c.x > -40)

        if (st.nextSpawn <= 0) {
          const last = st.obstacles[st.obstacles.length - 1]
          const minGap = 120 + speed * 0.85
          if (!last || last.x < PET_W - minGap) {
            const aerial = Math.random() < 0.36
            const kind = aerial
              ? AERIAL_KINDS[Math.floor(Math.random() * AERIAL_KINDS.length)]
              : GROUND_KINDS[Math.floor(Math.random() * GROUND_KINDS.length)]
            const drawY = aerial
              ? baseY - 10 - Math.random() * 4
              : PET_GROUND - (kind === 'cactusTall' ? 16 : kind === 'crate' ? 12 : PET_FOOT)
            st.obstacles.push({
              id: st.nextObId++,
              kind, x: PET_W + 20, y: drawY, aerial, cleared: false, width: aerial ? 14 : 16
            })
          }
          st.nextSpawn = 1.6 + Math.random() * 2.0
        }

        // Constant world scroll — dodge length is locked in pixels, not speed.
        for (const ob of st.obstacles) ob.x -= speed * dt
        st.obstacles = st.obstacles.filter(ob => ob.x > -36)

        const findDodge = id => st.obstacles.find(o => o.id === id) || null

        if (st.action === 'run') {
          // Nearest uncleared obstacle still relevant to the pet (not already fully past).
          let best = null
          let bestLeft = Infinity
          for (const ob of st.obstacles) {
            if (ob.cleared) continue
            const left = ob.x
            const right = ob.x + ob.width
            // Skip only if the whole obstacle is already well past the pet.
            if (right < petFront - 8) continue
            if (left < bestLeft) {
              bestLeft = left
              best = ob
            }
          }
          if (best) {
            const left = best.x
            const right = best.x + best.width
            const lead = best.aerial ? DUCK_START_LEAD : JUMP_START_LEAD
            // Arm when left edge enters the lead band; keep a small overshoot so high-speed frames can't skip it.
            const inStartBand = left <= petFront + lead && right >= petFront - 6
            if (inStartBand) {
              best.cleared = true
              st.action = best.aerial ? 'duck' : 'jump'
              st.dodgeId = best.id
              st.dodgeStartX = left
              st.dodgeProg = 0
              st._jumpH = jumpH
            }
          }
        } else if (st.action === 'jump' || st.action === 'duck') {
          const ob = findDodge(st.dodgeId)
          const past = st.action === 'duck' ? DUCK_RISE_PAST : JUMP_LAND_PAST
          if (!ob) {
            st.action = 'run'
            st.dodgeId = 0
            st.dodgeProg = 0
          } else {
            const left = ob.x
            const right = left + ob.width
            // Progress 0→1 from takeoff left-edge to "right edge cleared pet by `past`".
            const endLeft = petFront - past - ob.width
            const span = Math.max(8, st.dodgeStartX - endLeft)
            st.dodgeProg = Math.min(1, Math.max(0, (st.dodgeStartX - left) / span))
            // Land / stand up only after the obstacle's right edge is left of the pet (+ margin).
            if (right <= petFront - past) {
              st.action = 'run'
              st.dodgeId = 0
              st.dodgeProg = 0
            }
          }
        }
      } else {
        st.obstacles = []
        st.clouds = []
        st.particles = []
        st.action = 'idle'
        st.dodgeId = 0
        st.dodgeProg = 0
        st.nextSpawn = 1.2
        st.nextCloud = 2.5
        st.nextWeather = 12
        st.weather = 'clear'
        st.weatherFlash = 0
        st._strikeSoon = 0
        st._precipAcc = 0
      }

      ctx.clearRect(0, 0, PET_W, PET_H)

      // Weather wash / fog (behind world). Lightning white flash drawn later on top.
      const wc = weatherColor[st.weather]
      if (online && st.weather === 'fog' && wc) {
        ctx.fillStyle = wc
        ctx.globalAlpha = 0.22
        ctx.fillRect(0, 0, PET_W, PET_GROUND)
        ctx.globalAlpha = 1
      } else if (online && st.weather === 'thunder' && wc) {
        ctx.fillStyle = wc
        ctx.globalAlpha = 0.1
        ctx.fillRect(0, 0, PET_W, PET_GROUND)
        ctx.globalAlpha = 1
      } else if (online && (st.weather === 'rain' || st.weather === 'snow') && wc) {
        ctx.fillStyle = wc
        ctx.globalAlpha = 0.08
        ctx.fillRect(0, 0, PET_W, PET_GROUND)
        ctx.globalAlpha = 1
      }

      for (const cloud of st.clouds) {
        paintPixels(ctx, OB_SPRITES.cloud.pixels, cloud.x, cloud.y, OB_SPRITES.cloud.color, 2)
      }

      // Falling rain/snow + fog (before ground). Grounded snow redrawn after floor.
      if (online && st.particles.length) {
        for (const p of st.particles) {
          if (p.kind === 'fog') {
            ctx.fillStyle = weatherColor.fog
            ctx.globalAlpha = Math.min(0.28, p.life * 0.14)
            ctx.fillRect(p.x, p.y, p.size || 12, 5)
            ctx.globalAlpha = 1
          } else if (p.kind === 'snow' && !p.grounded) {
            ctx.fillStyle = '#f5f7fb'
            const s = p.size || 2
            ctx.fillRect(p.x, p.y, s, s)
          } else if (p.kind === 'rain') {
            const rainColor = st.weather === 'thunder' ? weatherColor.thunder : weatherColor.rain
            ctx.fillStyle = rainColor
            ctx.globalAlpha = p.grounded ? Math.min(1, p.life * 3) : 1
            if (p.grounded) {
              ctx.fillRect(p.x - 1, landY - 1, 3, 1)
              ctx.fillRect(p.x, landY - 2, 1, 1)
            } else {
              ctx.fillRect(p.x, p.y, 1, p.len || 5)
            }
            ctx.globalAlpha = 1
          }
        }
      }

      ctx.fillStyle = ground
      ctx.fillRect(0, PET_GROUND, PET_W, 2)
      if (online) {
        ctx.fillStyle = mute
        const phase = st.scroll % 18
        for (let x = -phase; x < PET_W; x += 18) ctx.fillRect(x, PET_GROUND + 4, 8, 1)
        // Snow resting on the ground line (visible pile)
        for (const p of st.particles) {
          if (p.kind !== 'snow' || !p.grounded) continue
          ctx.fillStyle = '#ffffff'
          ctx.globalAlpha = Math.min(1, p.life * 0.85)
          const s = p.size || 2
          ctx.fillRect(p.x, PET_GROUND - s, s, s)
          if (s > 2) ctx.fillRect(p.x - 1, PET_GROUND - 1, s + 2, 1)
          ctx.globalAlpha = 1
        }
        for (const ob of st.obstacles) {
          const spr = OB_SPRITES[ob.kind] || OB_SPRITES.cactus
          paintPixels(ctx, spr.pixels, ob.x, ob.y, spr.color, 2)
        }
      }

      let petY = baseY
      let sprite = GHOST.idle
      let color = mute
      if (online) {
        color = ghostColor(st._util)
        if (st.action === 'jump') {
          const u = Math.min(1, Math.max(0, st.dodgeProg || 0))
          const h = st._jumpH || jumpH
          petY = Math.max(2, baseY - Math.sin(u * Math.PI) * h)
          sprite = GHOST.runA
        } else if (st.action === 'duck') {
          petY = baseY
          sprite = GHOST.duck
        } else {
          sprite = Math.floor(st.frame) % 2 === 0 ? GHOST.runA : GHOST.runB
        }
      }
      paintPixels(ctx, sprite, PET_X, petY, color, 2)

      // Yellow lightning bolts (over world), then white scene flash.
      if (online) {
        for (const p of st.particles) {
          if (p.kind !== 'lightning' || !p.segs || p.segs.length < 2) continue
          const fade = Math.max(0, Math.min(1, p.life * 5))
          ctx.lineJoin = 'round'
          ctx.lineCap = 'round'
          ctx.strokeStyle = `rgba(255,255,255,${0.85 * fade})`
          ctx.lineWidth = 3
          ctx.beginPath()
          ctx.moveTo(p.segs[0].x, p.segs[0].y)
          for (let i = 1; i < p.segs.length; i++) ctx.lineTo(p.segs[i].x, p.segs[i].y)
          ctx.stroke()
          ctx.strokeStyle = `rgba(255,214,74,${fade})`
          ctx.lineWidth = 1.5
          ctx.beginPath()
          ctx.moveTo(p.segs[0].x, p.segs[0].y)
          for (let i = 1; i < p.segs.length; i++) ctx.lineTo(p.segs[i].x, p.segs[i].y)
          ctx.stroke()
        }
        if (st.weather === 'thunder' && st.weatherFlash > 0) {
          ctx.fillStyle = '#ffffff'
          ctx.globalAlpha = Math.min(0.72, st.weatherFlash * 3.6)
          ctx.fillRect(0, 0, PET_W, PET_H)
          ctx.globalAlpha = 1
        }
      }

      // GPU stress: red breathing border intensity scales with util.
      if (online && st._util > 2) {
        const u = st._util / 100
        const breath = 0.5 + 0.5 * Math.sin(nowMs * 0.001 * (1.1 + u * 2.2))
        const alpha = u * (0.18 + 0.62 * breath)
        const edge = Math.max(1, Math.round(1 + u * 2.5))
        ctx.fillStyle = `rgba(255, 64, 64, ${alpha})`
        ctx.fillRect(0, 0, PET_W, edge)
        ctx.fillRect(0, PET_H - edge, PET_W, edge)
        ctx.fillRect(0, 0, edge, PET_H)
        ctx.fillRect(PET_W - edge, 0, edge, PET_H)
      }

      if (!st._enabled) {
        ctx.fillStyle = mute
        ctx.font = '10px monospace'
        ctx.fillText('OFF', PET_W - 28, 14)
      } else if (!st._live) {
        ctx.fillStyle = mute
        ctx.font = '10px monospace'
        ctx.fillText('OFFLINE', PET_W - 58, 14)
      }

      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  useEffect(() => {
    stateRef.current._live = live
    stateRef.current._util = util
    stateRef.current._enabled = enabled
  }, [live, util, enabled])

  return jsxs('div', { className: 'coffee-pet', children: [
    jsxs('div', { className: 'coffee-pet-head', children: [
      jsx('div', { className: 'coffee-heading', children: t('perf') }),
      jsx(Button, {
        variant: 'ghost', size: 'micro', className: 'coffee-pet-toggle',
        'data-on': enabled || undefined,
        'aria-pressed': enabled,
        'aria-label': enabled ? t('petToggleOn') : t('petToggleOff'),
        onClick: () => onToggle(!enabled),
        children: jsx('span', { className: 'coffee-pet-toggle-dot', 'aria-hidden': true })
      })
    ] }),
    jsx('canvas', {
      ref: canvasRef, className: 'coffee-pet-canvas', width: PET_W, height: PET_H,
      style: enabled ? undefined : { display: 'none' },
      role: 'img', 'aria-hidden': !enabled,
      'aria-label': live ? t('petOnline') : t('petOffline')
    })
  ] })
}

function Panel({ store }) {
  const t = usePluginI18n(ID)
  const snap = useValue(store.status)
  const offline = useValue(store.offline)
  const feed = useValue(store.feed)
  const petOn = useValue(store.petOn)
  if (!snap) {
    return jsx('div', { className: 'coffee-panel', children: jsx('div', { className: 'coffee-empty', children: offline ? t('offline') : '…' }) })
  }
  const busy = new Set(snap.queue.filter(item => item.state === 'running').map(item => item.model))
  const now = Date.now() / 1000
  return jsxs('div', { className: 'coffee-panel', children: [
    offline && jsx('div', { className: 'coffee-alert', 'data-level': 'alert', role: 'status', children: t('offline') }),
    !snap.ollama_up && jsx('div', { className: 'coffee-alert', role: 'status', children: t('ollamaDown') }),
    jsx(Meters, { snap }),
    jsx(PetRun, {
      snap, backendOffline: offline, enabled: petOn, onToggle: store.setPetOn
    }),
    jsxs('div', { children: [
      jsx('div', { className: 'coffee-heading', children: t('models') }),
      snap.loaded.length
        ? snap.loaded.map(model => jsx(ModelRow, {
          model, busy: busy.has(model.name), store, showStats: snap.show_model_stats !== false
        }, model.name))
        : jsx('div', { className: 'coffee-empty', children: t('noModels') })
    ] }),
    jsxs('div', { children: [
      jsx('div', { className: 'coffee-heading', children: t('queue') }),
      snap.queue.length
        ? snap.queue.map(item => jsxs('div', { className: 'coffee-row', children: [
          jsx('span', { className: 'coffee-row-main coffee-name', children: item.model }),
          jsx('span', { className: 'coffee-sub', children: t(item.state === 'running' ? 'running' : 'queued') }),
          jsx('span', { className: 'coffee-num', children: fill(t('seconds'), { n: Math.round(now - (item.started_at || item.enqueued_at)) }) })
        ] }, item.id))
        : jsx('div', { className: 'coffee-empty', children: snap.shared_queue ? t('queueEmpty') : t('noSharedQueue') })
    ] }),
    snap.mode === 'advanced' && jsxs('div', { children: [
      jsx('div', { className: 'coffee-heading', children: t('events') }),
      feed.length
        ? feed.map(event => jsxs('div', { className: 'coffee-row', children: [
          jsx('span', { className: 'coffee-dot', 'data-level': event.level }),
          jsx('span', { className: 'coffee-row-main', children: event.message }),
          jsx('span', { className: 'coffee-time', children: clock(event.at) })
        ] }, event.seq))
        : jsx('div', { className: 'coffee-empty', children: t('noEvents') })
    ] })
  ] })
}

function chipParts(snap, t) {
  // Mirror the info-panel toggles: only append metrics the user enabled in settings.
  if (!snap) return []
  const host = snap.host || {}
  const parts = []
  if (snap.total) {
    parts.push({ kind: 'gpu', text: fill(t('chipMem'), { pct: Math.round(snap.used_pct) }) })
    const util = host.gpu_util_pct
    parts.push({ kind: 'util', text: fill(t('chipCompute'), { pct: util == null ? '—' : `${Math.round(util)}%` }) })
  }
  if (snap.show_gpu_temp !== false && host.gpu_temp_c != null) {
    parts.push({ kind: 'temp', text: fill(t('temp'), { temp: host.gpu_temp_c }) })
  }
  if (snap.show_system_ram !== false && host.ram_total) {
    parts.push({ kind: 'ram', text: fill(t('chipRam'), { pct: Math.round(host.ram_pct || 0) }) })
  }
  if (snap.show_cpu !== false && host.cpu_pct != null) {
    parts.push({ kind: 'cpu', text: fill(t('chipCpu'), { pct: Math.round(host.cpu_pct) }) })
  }
  if (snap.show_network !== false && host.net_bps != null) {
    parts.push({ kind: 'net', text: fill(t('chipNet'), { rate: netRate(host.net_bps) }) })
  }
  if (snap.show_model_stats !== false && Array.isArray(snap.loaded)) {
    const model = snap.loaded.find(m => m.context_length || m.tps != null)
    if (model) {
      if (model.context_length) {
        const used = model.prompt_tokens || 0
        const bar = ctxBar(used, model.context_length)
        const label = used
          ? fill(t('ctx'), { used: kTokens(used), max: kTokens(model.context_length) })
          : fill(t('ctxMax'), { max: kTokens(model.context_length) })
        parts.push({
          kind: 'ctx',
          text: bar ? `${label} ${bar.bar} ${bar.pct}%` : label
        })
      }
      if (model.tps != null) parts.push({ kind: 'ctx', text: fill(t('tps'), { n: model.tps }) })
    }
  }
  return parts
}

function memLevel(snap, offline) {
  // Chip colour follows memory occupancy thresholds only (not system-RAM alert_source).
  if (offline || !snap || !snap.total || !snap.ollama_up) return 'off'
  if (snap.used_pct >= snap.hold_pct) return 'hold'
  if (snap.used_pct >= snap.warn_pct) return 'warn'
  return 'ok'
}

function Chip({ store, open }) {
  const t = usePluginI18n(ID)
  const snap = useValue(store.status)
  const offline = useValue(store.offline)
  const isOpen = useValue(open)
  const level = memLevel(snap, offline)
  const parts = chipParts(snap, t)
  const label = parts.map(p => p.text).join(' · ') || '—'
  const children = [jsx('span', { className: 'coffee-chip-mark', 'aria-hidden': true, children: '◆', key: 'mark' })]
  parts.forEach((part, i) => {
    if (i) children.push(jsx('span', { className: 'coffee-chip-sep', 'aria-hidden': true, children: '·', key: `sep-${i}` }))
    children.push(jsx('span', { className: `coffee-chip-${part.kind}`, children: part.text, key: `p-${i}` }))
  })
  if (!parts.length) children.push(jsx('span', { className: 'coffee-chip-sep', children: '—', key: 'empty' }))
  return jsxs(Popover, { open: isOpen, onOpenChange: value => open.set(value), children: [
    jsx(PopoverTrigger, { asChild: true, children: jsx(Button, {
      variant: 'ghost', size: 'micro', 'aria-label': `${t('chipLabel')} ${label}`,
      children: jsx('span', { className: 'coffee-chip', 'data-level': level, children })
    }) }),
    jsx(PopoverContent, { side: 'top', align: 'end', className: 'coffee-popover', 'aria-label': t('title'), children: jsx(Panel, { store }) })
  ] })
}

export default {
  id: ID,
  name: 'Coffee',
  description: 'Managing local models, as easy as a cup of coffee.',
  defaultEnabled: false,
  register(ctx) {
    ctx.i18n.register(LOCALES)
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    const store = createStore(ctx)
    const open = atom(false)
    ctx.registerMany([
      { id: 'chip', area: STATUSBAR_AREAS.right, order: 20, render: () => jsx(Chip, { store, open }) },
      {
        id: 'pane', area: 'panes', title: 'Coffee',
        data: { placement: 'right', dock: { pane: 'files', pos: 'bottom' }, width: '300px', height: '420px' },
        render: () => jsx('div', { className: 'coffee-pane', children: jsx(Panel, { store }) })
      },
      { id: 'open', area: PALETTE_AREA, data: { id: 'coffee-vram.open', label: EN.open, keywords: ['vram', 'gpu', 'ollama', 'coffee', 'coffee-vram'], run: () => open.set(true) } }
    ])
  }
}
