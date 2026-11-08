import {
  atom, Button, host, PALETTE_AREA, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS, Tip,
  usePluginI18n, useValue
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'coffee-vram'
const GIB = 2 ** 30
const POLL_MS = 2000
const HISTORY_MS = 15000
const FEED_SIZE = 8
const YEAR_S = 365 * 86400
const TOAST_ID = 'coffee-alert'

const EN = {
  title: 'Coffee', chipLabel: 'GPU memory', chipLabelRam: 'System memory',
  unknown: 'GPU memory unknown', ollamaDown: 'Ollama is not answering',
  offline: 'Coffee backend unavailable. Is coffee-vram in plugins.enabled, and was the gateway restarted?',
  gpu: 'GPU', ramLabel: 'System RAM', cpuLabel: 'CPU', of: '{used} / {total} GB',
  memLabel: 'Memory', computeLabel: 'Compute',
  chipMem: 'Memory {pct}%', chipCompute: 'Compute {pct}',
  other: 'Other apps {gb} GB', warnAt: 'warn {pct}%', holdAt: 'hold {pct}%',
  net: 'Net {rate}', temp: '{temp}°C',
  models: 'Loaded models', noModels: 'No models loaded', inRam: '+{gb} GB in system RAM', pinned: 'pinned',
  staysLoaded: 'stays loaded', minutesLeft: '{n} min left', unload: 'Unload', confirmUnload: 'Unload?',
  ctx: 'ctx {used}/{max}', ctxMax: 'ctx {max}', tps: '{n} tok/s',
  queue: 'Queue', queueEmpty: 'No calls waiting', running: 'running', queued: 'waiting', seconds: '{n}s',
  history: 'Last 15 minutes', events: 'Recent', noEvents: 'Nothing yet',
  sparkMem: 'Memory', sparkCompute: 'Compute',
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
    chipMem: '佔用 {pct}%', chipCompute: '使用 {pct}',
    other: '其他程式 {gb} GB', warnAt: '警告 {pct}%', holdAt: '暫停 {pct}%',
    net: '網路 {rate}', temp: '{temp}°C',
    models: '已載入模型', noModels: '沒有載入的模型', inRam: '+{gb} GB 在系統記憶體', pinned: '常駐',
    staysLoaded: '持續載入', minutesLeft: '剩 {n} 分鐘', unload: '卸載', confirmUnload: '確定卸載？',
    ctx: '上下文 {used}/{max}', ctxMax: '上下文 {max}', tps: '{n} tok/s',
    queue: '佇列', queueEmpty: '沒有等待中的呼叫', running: '執行中', queued: '等待中',
    history: '最近 15 分鐘', events: '最近事件', noEvents: '還沒有事件',
    sparkMem: '佔用率', sparkCompute: '使用率',
    noSharedQueue: '這個視窗直接讀取 GPU 記憶體；佇列在 agent 的行程裡。',
    open: 'Coffee：顯示 GPU 記憶體',
    gpuHint: 'GPU 專用區（NVML）。和工作管理員 → 記憶體（系統 RAM）不是同一塊。',
    ramHint: 'Windows 可見的系統記憶體。和 Coffee 用來排程模型的 GPU 專用區不是同一塊。'
  }
}

// Disk plugins are not scanned by Tailwind; theme variables only.
const CSS = `
.coffee-chip{display:flex;align-items:center;gap:4px;font-variant-numeric:tabular-nums;color:var(--ui-text-tertiary)}
.coffee-chip[data-level=warn]{color:var(--ui-yellow)}
.coffee-chip[data-level=hold]{color:var(--ui-red)}
.coffee-chip[data-level=off]{color:var(--ui-text-quaternary)}
.coffee-popover{width:340px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 120px);overflow-y:auto}
.coffee-panel{display:flex;flex-direction:column;gap:14px;padding:10px;font-size:12px;line-height:17px;color:var(--ui-text-secondary)}
.coffee-pane{height:100%;overflow-y:auto}
.coffee-heading{font-size:11px;font-weight:600;letter-spacing:.02em;color:var(--ui-text-tertiary);margin-bottom:4px}
.coffee-figure{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.coffee-figure strong{font-size:15px;font-weight:600;color:var(--ui-text-primary);font-variant-numeric:tabular-nums}
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
.coffee-spark .coffee-path-compute{stroke:var(--ui-text-tertiary);stroke-width:1.25;opacity:.45}
.coffee-alert{padding:6px 8px;border-radius:4px;border:1px solid var(--ui-stroke-tertiary);color:var(--ui-text-secondary)}
.coffee-alert[data-level=alert]{border-color:var(--ui-red)}
.coffee-row{display:flex;align-items:center;gap:8px;min-height:24px}
.coffee-row-main{flex:1;min-width:0}
.coffee-name{display:block;color:var(--ui-text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.coffee-sub{display:block;font-size:11px;color:var(--ui-text-tertiary)}
.coffee-num{font-variant-numeric:tabular-nums;color:var(--ui-text-secondary);white-space:nowrap}
.coffee-empty{color:var(--ui-text-quaternary)}
.coffee-spark-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:4px}
.coffee-spark-head .coffee-heading{margin-bottom:0}
.coffee-spark-now{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--ui-text-primary);flex-shrink:0}
.coffee-spark-wrap{display:grid;grid-template-columns:28px 1fr;column-gap:6px;height:64px;overflow:hidden}
.coffee-spark-axis{display:flex;flex-direction:column;justify-content:space-between;align-items:flex-end;padding:1px 0;font-size:10px;line-height:10px;color:var(--ui-text-quaternary);font-variant-numeric:tabular-nums}
.coffee-spark-plot{position:relative;min-width:0;min-height:0;overflow:hidden;border-radius:2px}
.coffee-spark{display:block;width:100%;height:100%}
.coffee-spark path{fill:none;stroke:var(--ui-accent);stroke-width:1.5;vector-effect:non-scaling-stroke}
.coffee-spark .coffee-line-warn{stroke:var(--ui-yellow);stroke-dasharray:3 3;opacity:.7}
.coffee-spark .coffee-line-hold{stroke:var(--ui-red);stroke-dasharray:3 3;opacity:.7}
.coffee-spark-legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:4px;font-size:10px;line-height:14px;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums}
.coffee-spark-legend>span::before{content:"";display:inline-block;width:8px;height:0;margin-right:4px;border-top:1px dashed currentColor;vertical-align:middle}
.coffee-spark-legend .coffee-leg-warn{color:var(--ui-yellow)}
.coffee-spark-legend .coffee-leg-hold{color:var(--ui-red)}
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

function createStore(ctx) {
  const status = atom(null)
  const offline = atom(false)
  const history = atom([])
  const feed = atom([])
  let lastSeq = null
  let historyAt = 0
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
      if (Date.now() - historyAt > HISTORY_MS) {
        historyAt = Date.now()
        history.set((await ctx.rest('/history')).points)
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

  void poll()
  ctx.setInterval(() => void poll(), POLL_MS)
  ctx.onEvent('plugin.coffee-vram.event', () => void poll())
  return { status, offline, history, feed, unload }
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
  const details = [
    model.spill ? fill(t('inRam'), { gb: gb(model.spill) }) : '',
    remaining(model, t),
    showStats && model.context_length && model.prompt_tokens
      ? fill(t('ctx'), { used: kTokens(model.prompt_tokens), max: kTokens(model.context_length) })
      : showStats && model.context_length ? fill(t('ctxMax'), { max: kTokens(model.context_length) }) : '',
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

/** Fritsch–Carlson monotone cubic: smooth, no overshoot, keeps flat-region sample jitter. */
function monotoneCubicPath(coords) {
  const n = coords.length
  if (n < 2) return ''
  const fmt = v => v.toFixed(3)
  if (n === 2) {
    return `M ${fmt(coords[0].x)} ${fmt(coords[0].y)} L ${fmt(coords[1].x)} ${fmt(coords[1].y)}`
  }
  const dx = [], dy = [], m = []
  for (let i = 0; i < n - 1; i++) {
    dx[i] = coords[i + 1].x - coords[i].x
    dy[i] = coords[i + 1].y - coords[i].y
    m[i] = dx[i] !== 0 ? dy[i] / dx[i] : 0
  }
  const t = new Array(n)
  t[0] = m[0]
  t[n - 1] = m[n - 2]
  for (let i = 1; i < n - 1; i++) {
    t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2
  }
  for (let i = 0; i < n - 1; i++) {
    if (Math.abs(m[i]) < 1e-12) {
      t[i] = 0
      t[i + 1] = 0
      continue
    }
    const a = t[i] / m[i]
    const b = t[i + 1] / m[i]
    const s = a * a + b * b
    if (s > 9) {
      const scale = 3 / Math.sqrt(s)
      t[i] = scale * a * m[i]
      t[i + 1] = scale * b * m[i]
    }
  }
  let d = `M ${fmt(coords[0].x)} ${fmt(coords[0].y)}`
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i]
    d += ` C ${fmt(coords[i].x + h / 3)} ${fmt(coords[i].y + t[i] * h / 3)}, `
      + `${fmt(coords[i + 1].x - h / 3)} ${fmt(coords[i + 1].y - t[i + 1] * h / 3)}, `
      + `${fmt(coords[i + 1].x)} ${fmt(coords[i + 1].y)}`
  }
  return d
}

function Spark({ points, snap }) {
  const t = usePluginI18n(ID)
  if (points.length < 2 || !snap.total) return null
  const first = points[0].t
  const span = Math.max(1e-6, points[points.length - 1].t - first)
  // Full float precision — no Math.round on sample %. Clamp only to keep the stroke in-box.
  const y = pct => Math.min(99.2, Math.max(0.8, 100 - Math.min(100, Math.max(0, pct))))
  const memCoords = points.map(p => {
    const total = p.total || snap.total
    return {
      x: Math.min(100, Math.max(0, (p.t - first) / span * 100)),
      y: y(total ? (p.used / total) * 100 : 0)
    }
  })
  const utilPts = points.filter(p => p.util != null)
  const utilCoords = utilPts.length >= 2 ? utilPts.map(p => ({
    x: Math.min(100, Math.max(0, (p.t - first) / span * 100)),
    y: y(p.util)
  })) : []
  // One decimal on the label so a flat 94.7→95.1 still moves; not integer rounding.
  const nowPct = Number(snap.used_pct).toFixed(1)
  const utilNow = snap.host?.gpu_util_pct
  const nowLabel = utilNow == null ? `${nowPct}%` : `${nowPct}% · ${Math.round(utilNow)}%`
  return jsxs('div', { children: [
    jsxs('div', { className: 'coffee-spark-head', children: [
      jsx('div', { className: 'coffee-heading', children: t('history') }),
      jsx('span', { className: 'coffee-spark-now', children: nowLabel })
    ] }),
    jsxs('div', { className: 'coffee-spark-wrap', children: [
      jsxs('div', { className: 'coffee-spark-axis', 'aria-hidden': true, children: [
        jsx('span', { children: '100%' }),
        jsx('span', { children: '0%' })
      ] }),
      jsx('div', { className: 'coffee-spark-plot', children:
        jsxs('svg', { className: 'coffee-spark', viewBox: '0 0 100 100', preserveAspectRatio: 'none',
          role: 'img', 'aria-label': `${t('history')} ${nowLabel}`, children: [
          jsx('line', { className: 'coffee-line-warn', x1: 0, x2: 100, y1: y(snap.warn_pct), y2: y(snap.warn_pct) }),
          jsx('line', { className: 'coffee-line-hold', x1: 0, x2: 100, y1: y(snap.hold_pct), y2: y(snap.hold_pct) }),
          utilCoords.length >= 2 && jsx('path', { className: 'coffee-path-compute', d: monotoneCubicPath(utilCoords) }),
          jsx('path', { d: monotoneCubicPath(memCoords) })
        ] }) })
    ] }),
    jsxs('div', { className: 'coffee-spark-legend', children: [
      jsx('span', { children: t('sparkMem') }),
      utilCoords.length >= 2 && jsx('span', { children: t('sparkCompute') }),
      jsx('span', { className: 'coffee-leg-warn', children: fill(t('warnAt'), { pct: snap.warn_pct }) }),
      jsx('span', { className: 'coffee-leg-hold', children: fill(t('holdAt'), { pct: snap.hold_pct }) })
    ] })
  ] })
}

function Panel({ store }) {
  const t = usePluginI18n(ID)
  const snap = useValue(store.status)
  const offline = useValue(store.offline)
  const points = useValue(store.history)
  const feed = useValue(store.feed)
  if (!snap) {
    return jsx('div', { className: 'coffee-panel', children: jsx('div', { className: 'coffee-empty', children: offline ? t('offline') : '…' }) })
  }
  const busy = new Set(snap.queue.filter(item => item.state === 'running').map(item => item.model))
  const now = Date.now() / 1000
  return jsxs('div', { className: 'coffee-panel', children: [
    offline && jsx('div', { className: 'coffee-alert', 'data-level': 'alert', role: 'status', children: t('offline') }),
    !snap.ollama_up && jsx('div', { className: 'coffee-alert', role: 'status', children: t('ollamaDown') }),
    jsx(Meters, { snap }),
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
    snap.mode === 'advanced' && jsx(Spark, { points, snap }),
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

function chipLabel(snap, t) {
  // Occupancy (memory) + compute util only — never RAM/CPU/network on the chip.
  if (!snap?.total) return '—'
  const mem = fill(t('chipMem'), { pct: Math.round(snap.used_pct) })
  const util = snap.host?.gpu_util_pct
  const compute = fill(t('chipCompute'), { pct: util == null ? '—' : `${Math.round(util)}%` })
  return `${mem} · ${compute}`
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
  const label = chipLabel(snap, t)
  return jsxs(Popover, { open: isOpen, onOpenChange: value => open.set(value), children: [
    jsx(PopoverTrigger, { asChild: true, children: jsx(Button, {
      variant: 'ghost', size: 'micro', 'aria-label': `${t('chipLabel')} ${label}`,
      children: jsxs('span', { className: 'coffee-chip', 'data-level': level, children: [jsx('span', { 'aria-hidden': true, children: '◆' }), ' ', label] })
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
