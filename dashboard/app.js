/* Celeris Control Tower. Reads only dashboard/data/*.json, written by `python run.py export`. No numbers are typed in here. */
"use strict";

const $ = s => document.querySelector(s);
const ST = { f: { fam: "", sku: "", reg: "", ch: "", cust: "" }, view: "overview", unit: "units", zoom: "recent", part: "all", charts: [] };
let D, M, A, Q, DR, SO;           // series, meta, anomalies, quality, drivers, stockouts
let NH, NF, ALLM, NAMES;          // history months, forecast months, all month labels, filter value names
const Z = 1.2816;                 // P10/P90 are 1.2816 standard deviations from the middle
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------------ formatting */
const fmt = n => n == null || isNaN(n) ? "–" : Math.round(n).toLocaleString("en-US");
const fmtK = n => { if (n == null || isNaN(n)) return "–"; const a = Math.abs(n);
  return a >= 1e9 ? (n / 1e9).toFixed(2) + "B" : a >= 1e6 ? (n / 1e6).toFixed(2) + "M" : a >= 1e4 ? (n / 1e3).toFixed(0) + "k" : a >= 1e3 ? (n / 1e3).toFixed(1) + "k" : fmt(n); };
const money = n => (n < 0 ? "−$" : "$") + fmtK(Math.abs(n));
const pct = (v, d = 1) => v == null || !isFinite(v) ? "–" : (v * 100).toFixed(d) + "%";
const sgn = (v, d = 1) => v == null || !isFinite(v) ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const sum = a => a.reduce((x, y) => x + (y || 0), 0);
const MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthName = m => MN[+m.slice(5) - 1] + " " + m.slice(0, 4);
/* Inverse of the normal distribution (Acklam's approximation): the z for a service level. */
function probit(p) {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const lo = 0.02425; let q, r;
  if (p < lo) { q = Math.sqrt(-2 * Math.log(p)); return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1); }
  if (p > 1 - lo) return -probit(1 - p);
  q = p - 0.5; r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

const FEATURE_NAMES = { month_pattern: "Month-of-year pattern", r_same_month_last_year: "Same month last year", r_level_x_pattern: "Level × month pattern",
  r_same_month_2y_ago: "Same month 2 years ago", r_same_month_3y_ago: "Same month 3 years ago", r_std_12: "How jumpy the series is", Product_Family: "Product family",
  r_order_size_12: "Usual order size", r_median_order_12: "Typical order size", r_max_12: "Biggest month, last 12", r_last_order_size: "Size of the last order",
  history_months: "Months of history", zero_share_12: "Share of zero months", orders_12: "Orders in the last 12 months", age_at_target: "Product age",
  log_scale: "Series size", Customer_Name: "Customer", Region: "Region", cycle: "Ordering rhythm", Powertrain: "Powertrain", r_level_3: "Last 3 months",
  r_last_value: "Last month", r_level_6: "Last 6 months", r_level_24: "Last 24 months", h: "Months ahead", target_month: "Calendar month",
  target_year: "Year", holiday_share: "Holiday share", region_growth_12: "Region growth", family_growth_12: "Family growth",
  family_region_growth_12: "Family × region growth", customer_growth_12: "Customer growth", sku_growth_12: "Product growth (all regions)",
  is_rhythm_month: "Order month in rhythm", months_since_order: "Months since last order", gap_at_target: "Months since last order, at target",
  moy_order_rate: "How often this calendar month had an order", r_ly_prev: "Month before, last year", r_ly_next: "Month after, last year",
  adi: "Average months between orders", cv2: "How much order sizes vary", demand_class: "Demand class",
  trend_12_vs_prev_12: "Trend (12 vs prior 12)", customer_shutdown: "Customer shutdown", customer_capacity_up: "Customer capacity added",
  EV_Adoption_Index: "EV adoption", price_rise_pct: "January price rise", months_to_eol: "Months to end of life", region_age: "Region age" };
const featName = k => FEATURE_NAMES[k] || k.replace(/_vs_level_window$/, " vs its last 12 months").replace(/_lag(\d)$/, ", $1 months before").replace(/_/g, " ");
const KIND = { data_error: ["Data error", "err"], data_loss: ["Data lost", "loss"], real_event: ["Real event", "real"], master_data: ["Master data", "master"] };
const CLASS_TEXT = { smooth: "orders most months, steady sizes", erratic: "orders most months, sizes jump", intermittent: "gaps between orders, steady sizes", lumpy: "gaps and jumpy sizes" };

const ICON = {
  overview: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
  health: '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  explorer: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  anomalies: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  stockouts: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  forecast: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  replay: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 8v4l3 2"/>',
  lifecycle: '<path d="M20 12a8 8 0 0 1-14 5.3L4 15"/><path d="M4 12a8 8 0 0 1 14-5.3L20 9"/><path d="M20 4v5h-5M4 20v-5h5"/>',
  whatif: '<path d="M4 20v-6M4 10V4M12 20v-8M12 8V4M20 20v-4M20 12V4M2 14h4M10 8h4M18 16h4"/>',
  procure: '<path d="M3 4h2l2.4 11h10.8L21 7H6.2"/><circle cx="9" cy="19.5" r="1.3"/><circle cx="17" cy="19.5" r="1.3"/>',
  exceptions: '<path d="M5 21V4"/><path d="M5 4h12l-2 4 2 4H5"/>',
  drivers: '<path d="M22 12h-4l-3 8L9 4l-3 8H2"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  play: '<path d="M7 5l12 7-12 7z" fill="currentColor"/>',
  doc: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  brief: '<path d="M9 4h6v3H9z"/><path d="M15 5h3v16H6V5h3"/><path d="M9 12l2 2 4-4"/>',
  check: '<path d="M5 12l4 4 10-10"/>',
};
const icon = n => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICON[n] || ""}</svg>`;

/* ------------------------------------------------------------------ data */
async function load() {
  const get = n => fetch("data/" + n).then(r => { if (!r.ok) throw new Error(n + " " + r.status); return r.json(); });
  [D, M, A, Q, DR, SO] = await Promise.all(["series.json", "meta.json", "anomalies.json", "quality.json", "drivers.json", "stockouts.json"].map(get));
  NH = D.months.length; NF = D.fc_months.length; ALLM = [...D.months, ...D.fc_months];
  D.n = D.sku.length;
  D.skuId = D.sku.map(i => D.skus[i].id);
  D.skuIndex = Object.fromEntries(D.skus.map((s, i) => [s.id, i]));
  NAMES = { fam: D.families, reg: D.regions, ch: D.channels, cust: D.customers };
  $("#generated").textContent = "Data built " + M.generated;
}

function sel() {
  const f = ST.f, out = [];
  const sk = f.sku ? D.skuIndex[f.sku] : null;
  for (let i = 0; i < D.n; i++) {
    if (f.fam !== "" && D.fam[i] !== +f.fam) continue;
    if (sk != null && D.sku[i] !== sk) continue;
    if (f.reg !== "" && D.reg[i] !== +f.reg) continue;
    if (f.ch !== "" && D.ch[i] !== +f.ch) continue;
    if (f.cust !== "" && D.cust[i] !== +f.cust) continue;
    out.push(i);
  }
  return out;
}
const filtered = () => Object.values(ST.f).some(x => x !== "");
function scopeText() {
  const p = [];
  if (ST.f.fam !== "") p.push(D.families[+ST.f.fam]); if (ST.f.sku) p.push(ST.f.sku);
  if (ST.f.reg !== "") p.push(D.regions[+ST.f.reg]); if (ST.f.ch !== "") p.push(D.channels[+ST.f.ch]);
  if (ST.f.cust !== "") p.push(D.customers[+ST.f.cust]);
  return p.length ? p.join(" · ") : "whole company";
}
/* Monthly total over the selected series. Months where none of them existed yet stay empty (missing is not zero). */
function sumHist(idx, key) {
  const out = new Float64Array(NH), seen = new Uint8Array(NH);
  for (const i of idx) { const a = D[key][i], o = D.h0[i]; if (o == null) continue;
    for (let j = 0; j < a.length; j++) { seen[o + j] = 1; if (a[j] != null) out[o + j] += a[j]; } }
  return Array.from(out, (v, m) => seen[m] ? v : null);
}
function sumFc(idx, key) {
  const out = new Float64Array(NF);
  for (const i of idx) { const a = D[key][i], o = D.f0[i]; if (o == null) continue;
    for (let j = 0; j < a.length; j++) if (a[j] != null) out[o + j] += a[j]; }
  return Array.from(out);
}
/* Range for a total: sigma_total = sqrt((1-rho) sum sigma_i^2 + rho (sum sigma_i)^2), calibrated in the replay test. */
function rangeOver(idx, m0 = 0, m1 = NF - 1, scale = null) {
  const rho = M.model.total_ranges.rho;
  let e = 0, s = 0, s2 = 0;
  for (const i of idx) { const o = D.f0[i]; if (o == null) continue;
    for (let j = 0; j < D.exp[i].length; j++) { const m = o + j; if (m < m0 || m > m1) continue;
      const k = scale ? scale[i] : 1, sig = (D.p90[i][j] - D.p10[i][j]) / (2 * Z) * k;
      e += D.exp[i][j] * k; s += sig; s2 += sig * sig; } }
  const sd = Math.sqrt((1 - rho) * s2 + rho * s * s);
  return { e, sd, lo: Math.max(0, e - Z * sd), hi: e + Z * sd };
}
function band(idx) {
  const E = [], LO = [], HI = [], P50 = sumFc(idx, "p50");
  for (let m = 0; m < NF; m++) { const r = rangeOver(idx, m, m); E.push(r.e); LO.push(r.lo); HI.push(r.hi); }
  return { E, LO, HI, P50 };
}
/* Replay-test accuracy for a selection, split by months ahead, origin and (optionally) a group. */
function accuracy(idx, groupKey = null) {
  const bt = D.bt, no = bt.origins.length;
  const mk = () => ({ a: 0, m: 0, b: 0, em: 0, eb: 0 });
  const tot = mk(), byH = Array.from({ length: 19 }, mk), byO = Array.from({ length: no }, mk), byG = {};
  const cell = Array.from({ length: no * 19 }, () => ({ a: 0, m: 0, b: 0 }));
  for (const i of idx) { const A_ = bt.act[i]; if (!A_ || !A_.length) continue;
    for (let o = 0; o < no; o++) for (let h = 0; h < 19; h++) { const a = A_[o][h]; if (a == null) continue;
      const m = bt.model[i][o][h] ?? 0, b = bt.base[i][o][h] ?? 0, em = Math.abs(m - a), eb = Math.abs(b - a);
      for (const t of [tot, byH[h], byO[o]]) { t.a += a; t.m += m; t.b += b; t.em += em; t.eb += eb; }
      const c = cell[o * 19 + h]; c.a += a; c.m += m; c.b += b;
      if (groupKey) { const g = groupKey === "cls" ? bt.cls[i][o] : D[groupKey][i]; const t = byG[g] || (byG[g] = mk());
        t.a += a; t.m += m; t.b += b; t.em += em; t.eb += eb; } } }
  const fin = t => ({ ...t, wm: t.a ? t.em / t.a : null, wb: t.a ? t.eb / t.a : null, bm: t.a ? (t.m - t.a) / t.a : null, bb: t.a ? (t.b - t.a) / t.a : null });
  const f = M.model.bias_factor; let ta = 0, tm = 0, tb = 0;
  for (const c of cell) { ta += c.a; tm += Math.abs(c.m * f - c.a); tb += Math.abs(c.b - c.a); }
  const total = { wm: ta ? tm / ta : null, wb: ta ? tb / ta : null };
  return { tot: fin(tot), total, byH: byH.map(fin), byO: byO.map(fin), byG: Object.fromEntries(Object.entries(byG).map(([k, v]) => [k, fin(v)])) };
}
const seriesName = i => `${D.skuId[i]} · ${D.regions[D.reg[i]]}`;
/* Sum of a series over the last n calendar months of history (0 for series that ended earlier). */
function lastN(i, key, n, skip = 0) {
  const o = D.h0[i]; if (o == null) return 0; const a = D[key][i]; let t = 0;
  for (let j = 0; j < a.length; j++) { const m = o + j; if (m >= NH - n - skip && m < NH - skip && a[j] != null) t += a[j]; }
  return t;
}
/* Events whose scope fits the current filters (a strike at one customer is not shown when another is selected). */
function scopeOf(s) {
  const [t, v] = String(s || "").split(": "); if (!v) return {};
  if (t === "Region") return { reg: v };
  if (t === "Customer") { const [c, r] = v.split(" | "); return { cust: c, reg: r }; }
  if (t === "Product_Family") return { fam: v };
  return {};
}
const storyFor = () => (M.story || []).filter(e => { const s = scopeOf(e.scope);
  for (const k of ["fam", "reg", "cust"]) if (s[k] && ST.f[k] !== "" && NAMES[k][+ST.f[k]] !== s[k]) return false;
  return true; });

/* ------------------------------------------------------------------ charts */
function mount(el, option) {
  const node = typeof el === "string" ? $(el) : el;
  const old = echarts.getInstanceByDom(node); if (old) { old.dispose(); ST.charts = ST.charts.filter(c => c !== old); }
  if (node._ro) node._ro.disconnect();
  const c = echarts.init(node, null, { renderer: "canvas" });
  c.setOption(option); ST.charts.push(c);
  // redraw whenever the chart's box changes size (menu opened, scrollbar, window), not only on window resize
  if (window.ResizeObserver) { node._ro = new ResizeObserver(() => { if (!c.isDisposed() && node.isConnected) c.resize(); }); node._ro.observe(node); }
  if (option.dataZoom) {
    zoomReset(node, c);
    // plain wheel events go to the page (scroll); only pinch / Ctrl + scroll reach the chart's zoom
    if (!node._wheel) { node._wheel = true; node.addEventListener("wheel", e => { if (!e.ctrlKey) e.stopPropagation(); }, { capture: true }); }
  }
  return c;
}
/* Zoom on time charts: drag the bar under the chart, or pinch / Ctrl + scroll on the chart. Plain scrolling moves
   the page, so a chart never zooms by accident while scrolling past it. */
const COARSE = matchMedia("(pointer: coarse)").matches;
function zoomBar(startValue, slider = true) {
  const inside = { type: "inside", startValue, zoomOnMouseWheel: "ctrl", moveOnMouseWheel: false, moveOnMouseMove: !COARSE, preventDefaultMouseMove: !COARSE };
  if (!slider) return [inside];
  return [inside, { type: "slider", startValue, height: 18, bottom: 4, showDataShadow: false, borderColor: css("--line"), backgroundColor: css("--sunk"),
    fillerColor: css("--hist-soft"), handleStyle: { color: css("--surface"), borderColor: css("--hist") }, moveHandleStyle: { color: css("--hist-soft") },
    textStyle: { color: css("--muted"), fontSize: 11 }, labelFormatter: (i, m) => monthName(m) }];
}
/* "Reset zoom" appears on a chart while it is zoomed and puts it back to how it opened. */
function zoomReset(node, c) {
  const old = node.querySelector(":scope > .zreset"); if (old) old.remove();
  const z0 = c.getOption().dataZoom[0], start = z0.start, end = z0.end;
  const b = document.createElement("button");
  b.type = "button"; b.className = "zreset"; b.hidden = true; b.textContent = "Reset zoom";
  b.onclick = () => { c.dispatchAction({ type: "dataZoom", start, end }); b.hidden = true; };
  node.appendChild(b);
  c.on("datazoom", () => { const z = c.getOption().dataZoom[0]; b.hidden = Math.abs(z.start - start) < 0.5 && Math.abs(z.end - end) < 0.5; });
}
const tip = () => ({ backgroundColor: css("--surface"), borderColor: css("--line"), textStyle: { color: css("--ink"), fontSize: 12 }, extraCssText: "box-shadow:0 6px 20px rgba(0,0,0,.12);border-radius:8px" });
function base(extra = {}) {
  const ink2 = css("--ink-2"), grid = css("--grid"), muted = css("--muted");
  return {
    textStyle: { fontFamily: getComputedStyle(document.body).fontFamily, color: ink2 },
    grid: { left: 56, right: 18, top: 30, bottom: 34, containLabel: false },
    tooltip: { trigger: "axis", ...tip(), valueFormatter: v => v == null ? "–" : fmt(v) },
    // on a phone the legend stays on one line (with arrows) instead of wrapping into the plot
    legend: { type: innerWidth < 700 ? "scroll" : "plain", top: 0, right: 0, left: innerWidth < 700 ? 0 : "auto", textStyle: { color: ink2, fontSize: 12 }, icon: "roundRect", itemWidth: 12, itemHeight: 8,
      pageIconColor: css("--ink-2"), pageIconInactiveColor: css("--line"), pageTextStyle: { color: muted } },
    xAxis: { type: "category", axisLine: { lineStyle: { color: css("--axis") } }, axisTick: { show: false }, axisLabel: { color: muted } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: grid } }, axisLabel: { color: muted, formatter: v => fmtK(v) } },
    animationDuration: REDUCED ? 0 : 500,
    ...extra,
  };
}
const yearAxis = data => ({ ...base().xAxis, data, axisLabel: { color: css("--muted"), interval: (i, v) => v.endsWith("-01"), formatter: v => v.slice(0, 4) } });
/* History + forecast on one continuous timeline, forecast clearly marked, P10-P90 band, optional event pins. */
function timeline(el, h, f, opts = {}) {
  const pad = (arr, before, after) => [...Array(before).fill(null), ...arr, ...Array(after).fill(null)];
  const join = arr => { const a = pad(arr, NH, 0); a[NH - 1] = h.main[NH - 1]; return a; };
  const lo = pad(f.lo, NH, 0), diff = pad(f.hi.map((v, k) => v - f.lo[k]), NH, 0);
  const cur = opts.money ? "$" : "";
  const series = [
    { name: "P10", type: "line", data: lo, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false }, silent: true },
    { name: "80% range (P10–P90)", type: "line", data: diff, stack: "band", symbol: "none", lineStyle: { opacity: 0 },
      areaStyle: { color: css("--fc"), opacity: 0.2 }, itemStyle: { color: css("--fc"), opacity: 0.35 }, silent: true },
    { name: h.label, type: "line", data: pad(h.main, 0, NF), symbol: "none", lineStyle: { width: 2, color: css("--hist") }, itemStyle: { color: css("--hist") },
      markArea: { silent: true, itemStyle: { color: css("--fc"), opacity: 0.05 }, data: [[{ xAxis: D.fc_months[0], name: "Forecast" }, { xAxis: D.fc_months[NF - 1] }]],
        label: { color: css("--fc"), fontSize: 11, fontWeight: 600, position: "insideTopLeft" } },
      markLine: { silent: true, symbol: "none", lineStyle: { color: css("--muted"), type: "dashed" }, label: { formatter: "Today", color: css("--muted"), fontSize: 11 },
        data: [{ xAxis: D.months[NH - 1] }] } },
    { name: f.label, type: "line", data: join(f.main), symbol: "none", lineStyle: { width: 2.4, color: css("--fc") }, itemStyle: { color: css("--fc") } },
  ];
  if (h.second) series.push({ name: h.secondLabel, type: "line", data: pad(h.second, 0, NF), symbol: "none", lineStyle: { width: 1.2, color: css("--muted") }, itemStyle: { color: css("--muted") } });
  if (f.p50) series.push({ name: "Typical month (P50)", type: "line", data: join(f.p50), symbol: "none", lineStyle: { width: 1.4, type: "dashed", color: css("--fc") }, itemStyle: { color: css("--fc") } });
  const pins = [];
  if (opts.story) opts.story.forEach((e, k) => {
    const m = ALLM.indexOf(e.start); if (m < 0) return;
    const y = m < NH ? h.main[m] : f.main[m - NH]; if (y == null) return;
    pins.push({ coord: [e.start, y], value: k + 1, e });
  });
  if (pins.length) series.push({ name: "Events", type: "line", data: [], symbol: "pin", itemStyle: { color: css("--ink-2") },
    markPoint: { symbol: "pin", symbolSize: 34, data: pins, label: { color: css("--bg"), fontSize: 11, fontWeight: 700, offset: [0, -2] },
      itemStyle: { color: css("--ink-2"), borderColor: css("--surface"), borderWidth: 1.5 }, emphasis: { itemStyle: { color: css("--fc") } } } });
  const zoomStart = opts.zoom === "all" ? D.months[0] : (opts.start || "2023-01");
  const o = base({
    xAxis: yearAxis(ALLM),
    yAxis: { ...base().yAxis, axisLabel: { color: css("--muted"), formatter: v => cur + fmtK(v) } },
    legend: { ...base().legend, data: series.map(s => s.name).filter(n => n !== "P10")
      .map(n => n === "Typical month (P50)" ? { name: n, icon: "path://M0 3h4v2H0zM6 3h4v2H6zM12 3h4v2h-4z" } : n) },
    tooltip: { ...base().tooltip, formatter: ps => {
      const m = ps[0].axisValue, k = ALLM.indexOf(m), fc = k >= NH ? k - NH : -1;
      let s = `<b>${monthName(m)}</b>${fc >= 0 ? ' <span style="color:' + css("--fc") + '">forecast</span>' : ""}<br>`;
      ps.forEach(p => { if (p.seriesName === "P10" || p.seriesName === "80% range (P10–P90)" || p.value == null) return;
        s += `${p.marker}${p.seriesName}: <b>${cur + fmt(p.value)}</b><br>`; });
      if (fc >= 0) s += `<span style="color:${css("--muted")}">80% range: ${cur + fmt(f.lo[fc])} – ${cur + fmt(f.hi[fc])}</span>`;
      const ev = (opts.story || []).filter(e => m >= e.start && m <= e.end);
      if (ev.length) s += `<div style="margin-top:4px;max-width:280px;white-space:normal;color:${css("--ink-2")}">${ev.map(e => "● " + esc(e.label)).join("<br>")}</div>`;
      return s; } },
    dataZoom: zoomBar(zoomStart),
    grid: { left: 62, right: 18, top: 40, bottom: 52 },
    series,
  });
  const c = mount(el, o);
  if (opts.onPin) { c.on("mouseover", p => { if (p.componentType === "markPoint") opts.onPin(p.data.e, p.data.value); });
    c.on("click", p => { if (p.componentType === "markPoint") opts.onPin(p.data.e, p.data.value, true); }); }
  return c;
}
function hbar(el, labels, values, opts = {}) {
  return mount(el, base({
    grid: { left: opts.left || 150, right: opts.right || 54, top: opts.legend ? 28 : 8, bottom: 22 },
    tooltip: { ...base().tooltip, trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: opts.fmt || (v => fmt(v)) },
    xAxis: { type: opts.log ? "log" : "value", splitLine: { lineStyle: { color: css("--grid") } }, axisLabel: { color: css("--muted"), formatter: opts.axisFmt || (v => fmtK(v)) }, min: opts.min, max: opts.max },
    yAxis: { type: "category", data: labels, inverse: true, axisTick: { show: false }, axisLine: { lineStyle: { color: css("--axis") } },
      axisLabel: { color: css("--ink-2"), width: (opts.left || 150) - 12, overflow: "truncate" } },
    series: (Array.isArray(values[0]) || (values[0] && values[0].data) ? values : [{ data: values }]).map(v => ({
      type: "bar", barMaxWidth: 15, barGap: "20%", name: v.name, data: v.data || v, stack: opts.stack,
      itemStyle: { color: v.color || (opts.colors ? (p => opts.colors[p.dataIndex]) : css("--hist")), borderRadius: 4 },
      label: opts.labels ? { show: true, position: "right", color: css("--ink-2"), fontSize: 11, formatter: p => (opts.fmt || fmt)(p.value) } : undefined,
      markLine: opts.zero ? { silent: true, symbol: "none", data: [{ xAxis: 0 }], lineStyle: { color: css("--axis") }, label: { show: false } } : undefined })),
    legend: opts.legend ? base().legend : { show: false },
  }));
}
function animateNums(root) {
  if (REDUCED) return;
  root.querySelectorAll("[data-n]").forEach(el => {
    const n = +el.dataset.n, f = { k: fmtK, $: money, p: x => pct(x) }[el.dataset.f] || fmtK, t0 = performance.now();
    const step = t => { const k = Math.min(1, (t - t0) / 650), e = 1 - Math.pow(1 - k, 3); el.textContent = f(n * e); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}

/* ------------------------------------------------------------------ shell */
/* Views follow the brief: one app, two parts (training data / prediction) sharing the same filters.
   kind: req = one of the 5 required views, opt = one of the 7 optional views listed in the brief. */
const VIEWS = [
  ["Start", "overview", "Executive overview", "req", "both"],
  ["Start", "brief", "Brief checklist", "", "both"],
  ["Part 1 · Training data", "health", "Data health", "req", "hist"],
  ["Part 1 · Training data", "explorer", "Demand explorer", "req", "hist"],
  ["Part 1 · Training data", "anomalies", "Anomaly monitor", "req", "hist"],
  ["Part 1 · Training data", "stockouts", "Stockouts & lost sales", "opt", "hist"],
  ["Part 2 · Prediction", "forecast", "Forecast & accuracy", "req", "fc"],
  ["Part 2 · Prediction", "replay", "Backtest replay", "", "fc"],
  ["Part 2 · Prediction", "lifecycle", "Lifecycle: EOL & launches", "opt", "fc"],
  ["Part 2 · Prediction", "whatif", "What-if scenarios", "opt", "fc"],
  ["Part 2 · Prediction", "procure", "Procurement plan (P90)", "opt", "plan"],
  ["Part 2 · Prediction", "exceptions", "Exception list", "opt", "plan"],
  ["Drivers & data", "drivers", "Macro lead / lag heatmap", "opt", "data"],
  ["Drivers & data", "download", "Data download", "opt", "data"],
];
const VIEW = Object.fromEntries(VIEWS.map(v => [v[1], v]));
// the "Show" switch above the menu: all views, or only those for Part 1 (training data) or Part 2 (prediction)
const HIDDEN = { all: "", p1: "Part 2 · Prediction", p2: "Part 1 · Training data" };
function buildNav() {
  let g = "", h = "";
  document.querySelectorAll("#parts button").forEach(b => b.setAttribute("aria-pressed", b.dataset.p === ST.part));
  for (const [grp, id, name, kind] of VIEWS) {
    if (grp === HIDDEN[ST.part]) continue;
    if (grp !== g) { h += `<h4>${grp}</h4>`; g = grp; }
    h += `<button data-v="${id}" ${id === ST.view ? 'aria-current="page"' : ""}>${icon(id)}<span>${name}</span>${kind === "req" ? `<span class="req" title="One of the 5 required views">required</span>` : ""}</button>`;
  }
  $("#nav").innerHTML = h;
}
function showPart(p) {
  ST.part = p;
  try { localStorage.setItem("celeris-part", p); } catch (e) { /* storage blocked */ }
  if ((VIEW[ST.view] || [])[0] === HIDDEN[p]) go("overview"); else buildNav();
}
function go(v) { ST.view = v; buildNav(); render(); window.scrollTo({ top: 0 }); $("#rail").classList.remove("open"); }
function buildFilters() {
  const opt = (arr, all) => `<option value="">${all}</option>` + arr.map((x, i) => `<option value="${i}">${esc(x)}</option>`).join("");
  $("#f-fam").innerHTML = opt(D.families, "All");
  $("#f-reg").innerHTML = opt(D.regions, "All");
  $("#f-ch").innerHTML = opt(D.channels, "All");
  $("#f-cust").innerHTML = opt(D.customers, "All");
  $("#sku-list").innerHTML = D.skus.map(s => `<option value="${s.id}">${esc(s.desc)}</option>`).join("");
  for (const k of ["fam", "reg", "ch", "cust"]) $("#f-" + k).onchange = e => {
    const f = { [k]: e.target.value };
    if (k === "fam" && ST.f.sku && D.skus[D.skuIndex[ST.f.sku]].family !== D.families[+e.target.value]) f.sku = "";
    setFilters(f); };
  $("#f-sku").onchange = e => { const v = e.target.value.trim().toUpperCase(); const ok = D.skuIndex[v] != null;
    setFilters(ok ? { sku: v, fam: String(D.families.indexOf(D.skus[D.skuIndex[v]].family)) } : { sku: "" }); };
  $("#f-reset").onclick = () => setFilters({ fam: "", sku: "", reg: "", ch: "", cust: "" });
}
function syncFilters() {
  for (const k of ["fam", "reg", "ch", "cust"]) { $("#f-" + k).value = ST.f[k]; $("#fs-" + k).classList.toggle("on", ST.f[k] !== ""); }
  $("#f-sku").value = ST.f.sku; $("#fs-sku").classList.toggle("on", !!ST.f.sku);
  $("#f-reset").hidden = !filtered();
}
function setFilters(f) { ST.f = { ...ST.f, ...f }; render(); }
/* Filters given by name (from findings, events, the finder). Unnamed filters are cleared. */
function applyNamed(view, named = {}) {
  const f = { fam: "", sku: "", reg: "", ch: "", cust: "" };
  for (const [k, v] of Object.entries(named)) { if (k === "sku") { if (D.skuIndex[v] != null) { f.sku = v; f.fam = String(D.families.indexOf(D.skus[D.skuIndex[v]].family)); } continue; }
    const i = (NAMES[k] || []).indexOf(v); if (i >= 0) f[k] = String(i); }
  ST.f = f; go(view);
}
function writeHash() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(ST.f)) if (v !== "") p.set(k, k === "sku" ? v : NAMES[k][+v]);
  const q = p.toString().replace(/\+/g, "%20");
  try { history.replaceState(null, "", "#" + ST.view + (q ? "?" + q : "")); } catch (e) { /* file:// or sandbox */ }
}
function readHash() {
  const raw = location.hash.slice(1), qi = raw.indexOf("?"), v = qi < 0 ? raw : raw.slice(0, qi);
  if (VIEWS.some(x => x[1] === v)) ST.view = v;
  const p = new URLSearchParams(qi < 0 ? "" : raw.slice(qi + 1));
  const named = {}; for (const k of ["fam", "reg", "ch", "cust", "sku"]) if (p.get(k)) named[k] = p.get(k);
  const f = { fam: "", sku: "", reg: "", ch: "", cust: "" };
  for (const [k, val] of Object.entries(named)) { if (k === "sku") { if (D.skuIndex[val] != null) f.sku = val; continue; } const i = NAMES[k].indexOf(val); if (i >= 0) f[k] = String(i); }
  ST.f = f;
}
function render() {
  ST.charts.forEach(c => c.dispose()); ST.charts = [];
  clearInterval(ST.timer);
  syncFilters();
  const idx = sel();
  const v = $("#view");
  v.onclick = null;
  v.classList.remove("fade"); void v.offsetWidth; v.classList.add("fade");
  writeHash();
  if (!idx.length && !["health", "drivers", "download", "brief"].includes(ST.view)) {
    v.innerHTML = `<div class="empty">No series match these filters.<br><br><button class="btn" id="e-reset">Clear filters</button></div>`;
    $("#e-reset").onclick = () => setFilters({ fam: "", sku: "", reg: "", ch: "", cust: "" }); return; }
  try { (RENDER[ST.view] || RENDER.overview)(v, idx); animateNums(v); }
  catch (err) { console.error(err); v.innerHTML = `<div class="empty">This view failed to draw: ${esc(err.message)}</div>`; }
}
const EYEBROW = { hist: ["--hist", "Part 1 · Training data · Jan 2019 – Oct 2026"], fc: ["--fc", "Part 2 · Prediction · Nov 2026 – May 2028"],
  both: ["--ink-2", "Both parts"], plan: ["--good", "Part 2 · Prediction · planning tool"], data: ["--violet", "Drivers & data"] };
const head = (kind, title, text, right = "", idx = null) => {
  const v = VIEW[ST.view] || [], tag = v[3] === "req" ? `<span class="badge req">Required view</span>` : v[3] === "opt" ? `<span class="badge">Optional view</span>` : "";
  return `<div class="vh"><div><div class="eyebrow"><span class="dot" style="background:var(${EYEBROW[kind][0]})"></span>${EYEBROW[kind][1]}${tag}${idx ? ` <span class="scope"><b>${fmt(idx.length)}</b> series · ${esc(scopeText())}</span>` : ""}</div><h1>${title}</h1>${text ? `<p>${text}</p>` : ""}</div>${right ? `<div class="right">${right}</div>` : ""}</div>`;
};
const kpi = (l, v, d = "", cls = "", n = null, f = "k") => `<div class="kpi ${cls}"><span class="l">${l}</span><span class="v"${n != null && isFinite(n) ? ` data-n="${n}" data-f="${f}"` : ""}>${v}</span>${d ? `<span class="d">${d}</span>` : ""}</div>`;
const seg = (id, opts, cur) => `<div class="seg" id="${id}" role="group">${opts.map(([k, l]) => `<button data-k="${k}" aria-pressed="${k === cur}">${l}</button>`).join("")}</div>`;
function onSeg(id, fn) { const el = $("#" + id); if (el) el.onclick = e => { const b = e.target.closest("button"); if (b) fn(b.dataset.k); }; }
function toast(t) { const el = $("#toast"); el.textContent = t; el.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("on"), 1800); }
const foot = () => "";

/* ------------------------------------------------------------------ views */
const RENDER = {};

RENDER.overview = (v, idx) => {
  const dem = sumHist(idx, "demand"), shp = sumHist(idx, "shipped"), rev = sumHist(idx, "revenue");
  const b = band(idx), frev = sumFc(idx, "frev");
  const last12 = sum(dem.slice(-12));
  const n12 = rangeOver(idx, 0, 11), n19 = rangeOver(idx);
  const rev19 = sum(frev), revLast12 = sum(rev.slice(-12));
  const acc = accuracy(idx);
  const ch = n12.e / last12 - 1;
  const story = storyFor();
  const who = filtered() ? `For ${esc(scopeText())}, customers` : "Customers";
  v.innerHTML = head("both", "Executive overview", "", "", idx) +
    `<div class="hero">
      <p class="statement">${who} are expected to ask for <em>${fmtK(n19.e)} units</em> worth <em>${money(rev19)}</em> from Nov 2026 to May 2028.</p>
      <p class="lede">Next 12 months: ${fmtK(n12.e)} units, <span class="${ch >= 0 ? "up" : "down"}">${sgn(ch)}</span> on the last 12. Forecast made at the end of October 2026.</p>
    </div>
    <div class="kpis">
      ${kpi("19-month forecast", fmtK(n19.e) + " units", `80% range ${fmtK(n19.lo)} – ${fmtK(n19.hi)}`, "fc", null)}
      ${kpi("19-month revenue", money(rev19), `at 2027–28 list prices (+${M.price_rises["2027"]}%, +${M.price_rises["2028"]}%)`, "fc", rev19, "$")}
      ${kpi("Next 12 months", fmtK(n12.e), `<b class="${ch >= 0 ? "up" : "down"}">${sgn(ch)}</b> on the last 12 (${fmtK(last12)} asked for)`, "fc", n12.e)}
      ${kpi("Error on the monthly total", acc.total.wm == null ? "–" : pct(acc.total.wm), acc.total.wm == null ? "no tested history here" : `backtest; best simple method ${pct(acc.total.wb)}`, "neutral", acc.total.wm, "p")}
    </div>
    <section class="panel">
      <div class="card-h"><div><h2>Units: history into forecast</h2><p class="sub">Blue: what customers asked for (rebuilt where stock ran out). Grey: what shipped. Orange: the forecast, with its 80% range shaded. Numbered pins mark events; hover one to read it.</p></div>
        <div class="right">${seg("zoom", [["recent", "Since 2023"], ["all", "All years"]], ST.zoom)}</div></div>
      <div class="chart tall" id="c-tl"></div>
      <div id="pin-info" class="note" style="min-height:22px">${story.length ? `${story.length} events marked on the timeline.` : ""}</div>
    </section>
    <section class="panel">
      <h2>Revenue: history into forecast</h2><p class="sub">Recorded revenue (USD) and expected forecast revenue at the announced list prices, with its 80% range.</p>
      <div class="chart" id="c-rv"></div>
    </section>
    <section class="panel">
      <h2>Mix by family, region and channel</h2><p class="sub">Units asked for in the last 12 months (blue) against the next 12 months (orange).</p>
      <div class="grid g3"><div><h3>By family</h3><div class="chart short" id="c-mf"></div></div><div><h3>By region</h3><div class="chart short" id="c-mr"></div></div><div><h3>By channel</h3><div class="chart short" id="c-mc"></div></div></div>
    </section>
    <section class="panel">
      <h2>What to act on</h2><p class="sub">Findings computed from the pipeline outputs. Each opens the view that shows the evidence, with the right filters set.</p>
      <div class="flist" id="finds">${M.findings.map((x, k) => `<div class="frow"><div class="fval">${esc(x.value)}</div>
        <div><div class="ftag">${esc(x.tag)}</div><h3>${esc(x.title)}</h3><p>${esc(x.text)}</p></div>
        <div class="fdo"><p><b>Action:</b> ${esc(x.action)}</p><button class="go" data-k="${k}">Show the evidence ${icon("arrow")}</button></div></div>`).join("")}</div>
    </section>${foot()}`;
  onSeg("zoom", k => { ST.zoom = k; render(); });
  $("#finds").onclick = e => { const b = e.target.closest(".go"); if (b) { const l = M.findings[+b.dataset.k].link; Object.assign(ST, l.state || {}); applyNamed(l.view, l.f); } };
  const onPin = (e, n) => {
    const s = scopeOf(e.scope), canFilter = Object.keys(s).length && !Object.entries(s).every(([k, val]) => ST.f[k] !== "" && NAMES[k][+ST.f[k]] === val);
    $("#pin-info").innerHTML = `<b>${n}. ${esc(e.label)}</b> · ${monthName(e.start)}${e.end !== e.start ? " – " + monthName(e.end) : ""} · ${esc(e.text)}${canFilter ? ` <button class="btn" id="pin-go" style="margin-left:6px;padding:2px 9px">Show only this</button>` : ""}`;
    const g = $("#pin-go"); if (g) g.onclick = () => applyNamed("overview", s);
  };
  timeline("#c-tl", { main: dem, label: "Asked for (history)", second: shp, secondLabel: "Shipped" }, { main: b.E, lo: b.LO, hi: b.HI, p50: b.P50, label: "Forecast (expected)" }, { story, onPin, zoom: ST.zoom });
  const scale = {}; for (const i of idx) { const e = sum(D.exp[i]); scale[i] = e > 0 ? sum(D.frev[i]) / e : 0; }
  const lo = [], hi = [];
  for (let m = 0; m < NF; m++) { const r = rangeOver(idx, m, m, scale); lo.push(r.lo); hi.push(r.hi); }
  timeline("#c-rv", { main: rev, label: "Revenue (history)" }, { main: frev, lo, hi, label: "Forecast revenue (expected)" }, { money: true, zoom: ST.zoom });
  for (const [el, key, names] of [["#c-mf", "fam", D.families], ["#c-mr", "reg", D.regions], ["#c-mc", "ch", D.channels]]) {
    const L = {}, N = {};
    for (const i of idx) { const g = D[key][i];
      L[g] = (L[g] || 0) + lastN(i, "demand", 12);
      if (D.f0[i] != null) N[g] = (N[g] || 0) + sum(D.exp[i].slice(0, Math.max(0, 12 - D.f0[i]))); }
    const keys = Object.keys({ ...L, ...N }).sort((a, b2) => (N[b2] || 0) - (N[a] || 0));
    hbar(el, keys.map(k => names[k]), [{ name: "Last 12 months", data: keys.map(k => L[k] || 0), color: css("--hist") },
      { name: "Next 12 months", data: keys.map(k => N[k] || 0), color: css("--fc") }], { left: 132, legend: true });
  }
};

/* Every requirement of the problem statement, how it is met, and where to see it. Counts are read from the data files. */
function briefItems() {
  const iss = t => sum(Q.issues.filter(i => i.issue.includes(t)).map(i => i.rows));
  const ba = w => Q.before_after.find(r => r.what === w) || {};
  const types = {}; A.rows.forEach(r => types[A.types[r[2]]] = (types[A.types[r[2]]] || 0) + 1);
  const typeN = t => sum(Object.entries(types).filter(([k]) => k.toLowerCase().includes(t)).map(x => x[1]));
  const methods = M.methods.length, rows = fmt(M.model.rows), src = Q.demand_checks.demand_sources;
  return [
  ["1 · Cleaning", [
    ["Core", "Profile all files. Standardise labels and dates. De-duplicate. One clean row per Date-SKU-Region.", `All 7 files profiled. ${fmt(ba("Month spellings").before)} month spellings, ${fmt(ba("SKU spellings").before)} SKU spellings, ${fmt(ba("Region names").before)} region and ${fmt(ba("Customer names").before)} customer names standardised; ${fmt(iss("Record_ID"))} repeated records removed (latest Load_Timestamp kept).`, "health"],
    ["Advanced", "Reconcile units × price = revenue. Fix unit and currency errors. Handle missing rows vs zeros. Clean the indicators.", "Units recovered from revenue ÷ price; prices in cents, in euros and per pack of 10, units 10× / 100× too large and revenue with swapped digits fixed; a missing row is never read as zero; macro indicators de-duplicated and repaired.", "health"],
    ["Expert", "Tested, one-command pipeline. Machine-readable data-quality report.", "python run.py rebuilds everything from the raw files and python -m pytest runs the automated checks; outputs/reports/data_quality.json lists every issue with rows, action, evidence and examples.", "download"]]],
  ["2 · Anomalies", [
    ["Core", "Flag outliers and separate errors from real events. Anomaly register.", `${fmt(A.rows.length)} flagged series-months, each tagged as data error, data lost, real event or master-data conflict, with a reason and an action.`, "anomalies"],
    ["Advanced", "Structural breaks, early buying before price rises, holidays, stockouts. Estimate unconstrained demand.", `New region (Middle East & Africa, 2022), Helios capacity step, early buying before the 9% price rise, holiday and shutdown effects measured against a placebo range; demand rebuilt for ${fmt(src.stockout_requested + src.shortage_estimated)} stockout and shortage months.`, "stockouts"],
    ["Expert", "Ordering-cycle changes, customer switches, lost accounts. Master data vs transactions.", `${fmt(typeN("rhythm"))} ordering-rhythm changes, ${fmt(M.accounts.facts.switches)} account switches (no customer lost outright), ${fmt(typeN("master launch"))} sales before the master launch date and a business-unit conflict in the product master.`, "anomalies"]]],
  ["3 · Features & drivers", [
    ["Core", "Calendar features, lags, rolling stats, intermittency class.", "Model inputs include calendar and holiday features, lags, rolling levels, ordering rhythm and the Syntetos–Boylan class (smooth / erratic / intermittent / lumpy).", "explorer"],
    ["Advanced", "Which indicator leads which family, and at what lag. Event and lifecycle features.", `Lead/lag heatmap of ${new Set(DR.summary.map(r => r.indicator)).size} indicators × ${new Set(DR.summary.map(r => r.family)).size} families; holidays by exact date, price rises, customer shutdowns, launch and end-of-life inputs.`, "drivers"],
    ["Expert", "Is the lag structure stable over time? The shift to electric vehicles.", `Every link re-estimated in 2021–23 and 2024–26 and marked stable (${DR.summary.filter(r => r.stable).length} links) or not; EV-only parts' share of demand over time.`, "drivers"]]],
  ["4 · Forecasting", [
    ["Core", "2 baselines + 1 better model, time-based backtest.", `${methods - 1} baselines and a global LightGBM model, all scored on the same ${rows} backtest rows.`, "forecast"],
    ["Advanced", "Intermittent-demand methods; a global ML model; rolling-origin backtest with a 19-month horizon.", `Croston-SBA and TSB; one global LightGBM model (average of 4 set-ups); ${D.bt.origins.length} origins × 19 months.`, "replay"],
    ["Expert", "New launches via Replaces_SKU, EOL, events in the forecast window. P10/P50/P90 ranges. Forecasts that add up across the hierarchy.", "Launches ramp up from the product they replace; 0 after end of life; the Orion 2027 shutdown and price rises are in the forecast; calibrated P10/P50/P90; every total is the sum of its series.", "lifecycle"]]],
  ["5 · Dashboard", [
    ["Core", "The 5 required views.", "Executive overview, Data health, Demand explorer, Anomaly monitor, Forecast & accuracy (marked \"required\" in the menu).", "overview"],
    ["Advanced", "+ 2 optional views.", "All 7 optional views listed in the brief: macro lead/lag heatmap, what-if, procurement plan from P90, lifecycle, stockouts and lost sales, exception list, data download.", "drivers"],
    ["Expert", "+ 4 optional views, deployed or polished.", "Deployed at celeris-control-tower.vercel.app and runs locally. One app with two parts and shared filters, one continuous timeline, reads only pipeline outputs.", "overview"]]],
  ];
}
function briefRules() {
  const iss = t => sum(Q.issues.filter(i => i.issue.includes(t)).map(i => i.rows));
  return [
  ["1", "Date is authoritative", `Year/Month ignored where they disagree with Date (${fmt(iss("Year/Month disagree"))} rows); only the impossible year 2062 was corrected, proved by the load timestamp.`],
  ["2", "A repeated Record_ID: the latest Load_Timestamp is correct", `${fmt(iss("Record_ID"))} repeated rows removed, the latest load kept.`],
  ["3", "0 = no order; a missing row is not a zero", `${fmt(Q.demand_checks.demand_sources.imputed_missing)} missing months estimated from the ordering rhythm and left out of training.`],
  ["4", "One USD price per SKU, same in every region, changing on 1 January", "Cents, euro and per-pack prices replaced by the list price; the 2022 surcharge kept as a real event."],
  ["5", "No sales after EOL or before a planned launch", `Forecast is 0 after end of life (${fmt(iss("after the SKU's EOL"))} test rows) and before a planned launch.`],
  ["6", "Customers order on their own cycle, which can change", "Ordering rhythm (every 1, 2 or 3 months) is a model input; changes are in the anomaly register."],
  ["7", "Forecast unconstrained demand", "The model learns from what customers asked for, rebuilt from the stockout file and the chip shortage, not from shipments."],
  ];
}
RENDER.brief = (v) => {
  v.innerHTML = head("both", "Brief checklist", "Every requirement in the problem statement, how this solution meets it, and where to see it. Core, Advanced and Expert tiers are delivered in all five workstreams.") +
    briefItems().map(([ws, items]) => `<section class="panel"><h2>Workstream ${ws}</h2><div class="checks">${items.map(([tier, req, how, view]) => `<div class="chk">
      <span class="tier ${tier.toLowerCase()}">${tier}</span><div><p class="req-t">${esc(req)}</p><p class="how">${esc(how)}</p></div>
      <button class="go" data-v="${view}">${esc(VIEW[view][2])} ${icon("arrow")}</button></div>`).join("")}</div></section>`).join("") +
    `<section class="panel"><h2>Business rules</h2><div class="checks">${briefRules().map(([n, r, how]) => `<div class="chk"><span class="tier rule">Rule ${n}</span><div><p class="req-t">${esc(r)}</p><p class="how">${esc(how)}</p></div><span></span></div>`).join("")}</div></section>
    <section class="panel"><h2>Hand-in</h2><div class="checks">
      <div class="chk"><span class="tier rule">File</span><div><p class="req-t">submission.csv</p><p class="how">Date, SKU_ID, Region, Units_Sold_Forecast for every row of test.csv, whole numbers ≥ 0, no blanks, same order (validated by celeris/submission.py on every run).</p></div><button class="go" data-v="download">Data download ${icon("arrow")}</button></div>
      <div class="chk"><span class="tier rule">Code</span><div><p class="req-t">Reproducible pipeline</p><p class="how">python run.py rebuilds every output from the raw files; python -m pytest runs the checks; README.md documents setup.</p></div><span></span></div>
    </div></section>${foot()}`;
  v.onclick = e => { const b = e.target.closest(".go[data-v]"); if (b) go(b.dataset.v); };
};

RENDER.health = (v) => {
  const s = Q.summary, issues = [...Q.issues].sort((a, b) => b.rows - a.rows);
  const cats = { error_fixed: ["Error fixed", "err"], error_dropped: ["Error removed / left missing", "err"], inferred: ["Filled in by inference", "loss"], real_event: ["Real event, kept", "real"], master_data: ["Master data conflict", "master"], info: ["Note", "ok"] };
  ST.hcat = ST.hcat || "all";
  const dc = Q.demand_checks, ba = Q.before_after;
  v.innerHTML = head("hist", "Data health",
    "Every problem found in the seven raw files, how many rows it touched and what was done. Each fix was proved with a second source before it was applied. Filters don't apply here.") +
    `<div class="kpis">
      ${kpi("Raw rows → clean rows", `${fmtK(ba[0].before)} → ${fmtK(ba[0].after)}`, "one row per month × product × region", "neutral")}
      ${kpi("Kinds of issue found", fmt(s.issues), "data errors, lost data, real events, master-data conflicts", "neutral", s.issues)}
      ${kpi("Rows touched", fmtK(s.rows_touched), "across all 7 files", "neutral", s.rows_touched)}
      ${kpi("Stockout answer key", `${fmt(Q.answer_key.match)} / ${fmt(Q.answer_key.rows)}`, "cleaned units that match the independent stockout file", "neutral")}
    </div>
    <div class="grid g2 mt">
      <div class="card"><h2>Before vs after cleaning</h2><p class="sub">Distinct values in the raw file and after cleaning (log scale). ${(r => `${fmt(r.before)} ways of writing a product code became ${fmt(r.after)} products.`)(ba.find(r => r.what === "SKU spellings"))}</p><div class="chart short" id="c-ba"></div></div>
      <div class="card"><h2>Issues by type: rows affected</h2><p class="sub">Errors are fixed; real events are kept and explained</p><div class="chart short" id="c-hc"></div></div>
      <div class="card"><h2>Issues by file: rows affected</h2><p class="sub">Most problems are in the sales history (train.csv)</p><div class="chart short" id="c-hf"></div></div>
      <div class="card"><h2>Months where demand had to be rebuilt</h2><p class="sub">Shipments understate demand when stock ran out; lost data had to be estimated. Shortage method tested on hidden months: ${pct(dc.shortage_method_test.wape_estimated, 0)} error vs ${pct(dc.shortage_method_test.wape_uncorrected, 0)} uncorrected.</p><div class="chart short" id="c-src"></div></div>
    </div>
    <div class="card mt"><h2>Every issue</h2><p class="sub">Open "examples" to see real rows before → after</p>
      <div class="chips" id="hcat" style="margin-bottom:10px">${[["all", "All"], ...Object.entries(cats).map(([k, x]) => [k, x[0]])].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${ST.hcat === k}">${l}</button>`).join("")}</div>
      <div class="tw"><table class="t"><tr><th>File</th><th>Column</th><th>Issue</th><th>Kind</th><th class="n">Rows</th><th>What was done · evidence</th></tr>
      ${issues.filter(i => ST.hcat === "all" || i.category === ST.hcat).map(i => `<tr><td class="mono" style="font-size:12px">${esc(i.file)}</td><td>${esc(i.column)}</td><td>${esc(i.issue)}</td>
        <td><span class="pill ${(cats[i.category] || ["", "ok"])[1]}">${(cats[i.category] || [i.category])[0]}</span></td><td class="n">${fmt(i.rows)}</td>
        <td>${esc(i.action)}${i.evidence ? `<div class="muted" style="font-size:12px">${esc(i.evidence)}</div>` : ""}${i.examples && i.examples.length ? `<details class="ex"><summary>examples</summary>${i.examples.map(e => `<code>${esc(JSON.stringify(e.before))}${e.after !== undefined ? "  →  " + esc(JSON.stringify(e.after)) : ""}</code>`).join("")}</details>` : ""}</td></tr>`).join("")}
      </table></div></div>${foot()}`;
  $("#hcat").onclick = e => { const b = e.target.closest(".chip"); if (b) { ST.hcat = b.dataset.k; render(); } };
  const sp = ba.filter(r => r.what !== "Rows");
  hbar("#c-ba", sp.map(r => r.what), [{ name: "Raw", data: sp.map(r => r.before), color: css("--muted") }, { name: "Clean", data: sp.map(r => r.after), color: css("--hist") }],
    { log: true, legend: true, labels: true, left: 130, right: 46, axisFmt: x => fmt(x) });
  const bc = Object.entries(s.by_category).sort((a, b) => b[1] - a[1]);
  const colorFor = k => ({ err: css("--bad"), loss: css("--violet"), real: css("--hist"), master: css("--amber"), ok: css("--muted") })[(cats[k] || ["", "ok"])[1]];
  hbar("#c-hc", bc.map(x => (cats[x[0]] || [x[0]])[0]), bc.map(x => x[1]), { colors: bc.map(x => colorFor(x[0])), labels: true, left: 190 });
  const bf = Object.entries(s.by_file).sort((a, b) => b[1] - a[1]);
  hbar("#c-hf", bf.map(x => x[0]), bf.map(x => x[1]), { labels: true, left: 170, colors: bf.map(() => css("--ink-2")) });
  const SRC = { stockout_requested: "Stockout: requested units", shortage_estimated: "Chip shortage, estimated", catchup_removed: "Catch-up order removed", imputed_missing: "Missing month, estimated", imputed_erp_loss: "Lost in ERP migration, estimated", imputed_unlisted_constraint: "Constrained, estimated" };
  const ds = Object.entries(dc.demand_sources).filter(x => x[0] !== "shipped").sort((a, b) => b[1] - a[1]);
  hbar("#c-src", ds.map(x => SRC[x[0]] || x[0]), ds.map(x => x[1]), { labels: true, left: 200, colors: ds.map(x => x[0].startsWith("imputed") ? css("--violet") : css("--fc")) });
};

RENDER.explorer = (v, idx) => {
  const f = ST.f;
  let level;
  if (f.fam === "" && !f.sku) level = "fam"; else if (!f.sku) level = "sku"; else if (f.reg === "") level = "reg"; else level = "one";
  const keyOf = { fam: i => D.fam[i], sku: i => D.sku[i], reg: i => D.reg[i], one: i => i }[level];
  const nameOf = { fam: k => D.families[k], sku: k => `${D.skus[k].id} · ${D.skus[k].desc}`, reg: k => D.regions[k], one: k => seriesName(k) + " · " + D.customers[D.cust[k]] }[level];
  const groups = {};
  for (const i of idx) (groups[keyOf(i)] = groups[keyOf(i)] || []).push(i);
  const rows = Object.entries(groups).map(([k, ii]) => { const d = sumHist(ii, "demand");
    let z = 0, c = 0; for (const i of ii) { const a = D.demand[i], o = D.h0[i]; if (o == null) continue; a.forEach((x, j) => { if (o + j >= NH - 12 && x != null) { c++; if (x === 0) z++; } }); }
    return { k, ii, last: sum(d.slice(-12)), prev: sum(d.slice(-24, -12)), spark: d.slice(-24).map(x => x || 0), zero: c ? z / c : null, n: ii.length }; })
    .sort((a, b) => b.last - a.last);
  const crumbs = [`<button data-c="all">All families</button>`];
  if (f.fam !== "") crumbs.push(f.sku ? `<button data-c="fam">${esc(D.families[+f.fam])}</button>` : `<span class="here">${esc(D.families[+f.fam])}</span>`);
  if (f.sku) crumbs.push(f.reg !== "" ? `<button data-c="sku">${f.sku}</button>` : `<span class="here">${f.sku}</span>`);
  if (f.reg !== "") crumbs.push(`<span class="here">${esc(D.regions[+f.reg])}</span>`);
  const spark = a => { const mx = Math.max(...a, 1), w = 110, h = 26; const p = a.map((x, j) => `${(j / (a.length - 1) * w).toFixed(1)},${(h - x / mx * (h - 2) - 1).toFixed(1)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${p}" fill="none" stroke="${css("--hist")}" stroke-width="1.5"/></svg>`; };
  const lvlName = { fam: "Family", sku: "Product", reg: "Region", one: "Series" }[level];
  // lifecycle: launches and end of life among the products in a row
  const life = ii => { const ks = [...new Set(ii.map(i => D.sku[i]))].map(k => D.skus[k]);
    if (ks.length === 1) { const k = ks[0]; return k.status === "Active" ? `<span class="muted">active since ${k.launch}</span>`
      : `<span class="pill ${k.status === "Planned Launch" ? "real" : k.status === "Discontinued" ? "err" : "warn"}">${k.status === "Planned Launch" ? "launch " + k.launch : k.status === "Discontinued" ? "ended " + k.eol : "ends " + k.eol}</span>`; }
    const n = st => ks.filter(k => k.status === st).length, out = [];
    if (n("Planned Launch")) out.push(`<span class="pill real">${n("Planned Launch")} launching</span>`);
    if (n("EOL Announced")) out.push(`<span class="pill warn">${n("EOL Announced")} ending</span>`);
    if (n("Discontinued")) out.push(`<span class="pill err">${n("Discontinued")} ended</span>`);
    return out.join("") || `<span class="muted">${ks.length} active</span>`; };
  const dem = sumHist(idx, "demand"), shp = sumHist(idx, "shipped");
  let skuCard = "";
  if (f.sku) { const s = D.skus[D.skuIndex[f.sku]];
    skuCard = `<div class="card"><h2>${s.id} · ${esc(s.desc)}</h2><p class="sub">${esc(s.family)} · ${esc(s.size)} · ${esc(s.powertrain)}</p>
      <div class="chips"><span class="pill ${s.status === "Active" ? "ok" : s.status === "Planned Launch" ? "real" : "warn"}">${s.status}</span>
      <span class="pill ok">Launched ${s.launch}</span>${s.eol ? `<span class="pill warn">End of life ${s.eol}</span>` : ""}${s.replaces ? `<span class="pill real">Replaces ${s.replaces}</span>` : ""}</div>
      ${level === "one" ? `<p class="note">Customer: <b>${esc(D.customers[D.cust[idx[0]]])}</b> · ${esc(D.channels[D.ch[idx[0]]])} · orders ${["", "every month", "every 2nd month", "every quarter"][D.cyc[idx[0]]] || "every " + D.cyc[idx[0]] + " months"} · demand class <b>${D.cls[idx[0]] == null ? "–" : D.classes[D.cls[idx[0]]]}</b></p>` : ""}</div>`; }
  v.innerHTML = head("hist", "Demand explorer",
    "Drill down family → product → region → customer by clicking a row. For any selection: demand against shipments, seasonality, zero months, ordering rhythm and lifecycle. Blue is what customers asked for; grey is what shipped.", "", idx) +
    `<div class="crumbs" id="crumbs">${crumbs.join('<span class="sep">›</span>')}</div>
    <div class="grid g2">
      <div class="card span2"><h2>${{ Family: "Families", Product: "Products", Region: "Regions", Series: "This series" }[lvlName]} in the selection</h2><p class="sub">Last 12 months of demand, change against the 12 before, share of months with no order</p>
        <div class="tw" style="max-height:340px"><table class="t" id="drill"><tr><th>${lvlName}</th><th class="n">Series</th><th class="n">Last 12 months</th><th class="n">vs prior 12</th><th class="n">Zero months</th><th>Lifecycle</th><th>Last 24 months</th></tr>
        ${rows.slice(0, 300).map(r => `<tr class="${level === "one" ? "" : "click"}" data-k="${r.k}"><td>${esc(nameOf(+r.k))}</td><td class="n">${r.n}</td><td class="n">${fmt(r.last)}</td>
          <td class="n ${r.last >= r.prev ? "up" : "down"}">${r.prev ? sgn(r.last / r.prev - 1, 0) : "new"}</td><td class="n">${pct(r.zero, 0)}</td><td>${life(r.ii)}</td><td>${spark(r.spark)}</td></tr>`).join("")}</table></div></div>
      ${skuCard}
      <div class="card ${skuCard ? "" : "span2"}"><h2>Asked for vs shipped</h2><p class="sub">The gap is stockouts and the chip shortage (rebuilt from the stockout file)</p><div class="chart" id="c-ds"></div></div>
      <div class="card"><h2>Seasonality</h2><p class="sub">Each month against the average month, last 3 years (100 = average)</p><div class="chart short" id="c-sea"></div></div>
      <div class="card"><h2>Ordering rhythm</h2><p class="sub">How often the customers in this selection order</p><div class="chart short" id="c-cyc"></div></div>
      <div class="card span2"><h2>Seasonality by region</h2><p class="sub">Each month against that region's average month, last 3 years (100 = average). Regional holidays and plant shutdowns show as the darker cells.</p><div class="chart short" id="c-sreg" style="height:260px"></div></div>
      <div class="card span2"><h2>How each series orders: the four demand classes</h2><p class="sub">Each dot is one series (size = last 12 months of demand). Across: average months between orders. Up: how much order sizes vary. The standard cut-offs (dashed, 1.32 and 0.49) split the four classes. Pick a class to highlight it; click a dot to open that series.</p>
        <div class="cls-chips" id="clsc"></div><div class="chart tall" id="c-cls"></div></div>
      <div class="card"><h2>Where the volume is (ABC)</h2><p class="sub">Series sorted by the last 12 months of demand. A = the few that make 80% of volume, B = the next 15%, C = the long tail.</p><div class="chart short" id="c-abc"></div></div>
      <div class="card"><h2>Volume class × ordering class</h2><p class="sub">Series (share of volume). A planner can forecast A-smooth tightly and buy C-lumpy to order.</p><div id="abcx"></div></div>
      <div class="card span2"><h2>Order calendar: zero, normal and corrected months</h2><p class="sub">Largest ${Math.min(30, idx.length)} series in the selection, last 36 months. Each square is one month.</p><div class="chart" id="c-zero" style="height:${Math.max(220, 110 + 17 * Math.min(30, idx.length))}px"></div></div>
    </div>${foot()}`;
  $("#crumbs").onclick = e => { const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.c === "all") setFilters({ fam: "", sku: "", reg: "" }); else if (b.dataset.c === "fam") setFilters({ sku: "", reg: "" }); else if (b.dataset.c === "sku") setFilters({ reg: "" }); };
  $("#drill").onclick = e => { const tr = e.target.closest("tr.click"); if (!tr) return; const k = +tr.dataset.k;
    if (level === "fam") setFilters({ fam: String(k) }); else if (level === "sku") setFilters({ sku: D.skus[k].id, fam: String(D.families.indexOf(D.skus[k].family)) }); else if (level === "reg") setFilters({ reg: String(k) }); };
  mount("#c-ds", base({ xAxis: yearAxis(D.months), legend: base().legend, dataZoom: zoomBar(undefined, false),
    series: [{ name: "Asked for (demand)", type: "line", data: dem, symbol: "none", lineStyle: { width: 2, color: css("--hist") }, itemStyle: { color: css("--hist") } },
      { name: "Shipped", type: "line", data: shp, symbol: "none", lineStyle: { width: 1.3, color: css("--muted") }, itemStyle: { color: css("--muted") } }] }));
  const last36 = dem.slice(-36), mo = D.months.slice(-36).map(m => +m.slice(5) - 1), byM = Array(12).fill(0), cnt = Array(12).fill(0);
  last36.forEach((x, j) => { byM[mo[j]] += x || 0; cnt[mo[j]]++; });
  const avg = byM.map((x, k) => cnt[k] ? x / cnt[k] : 0), mean = sum(avg) / 12 || 1;
  mount("#c-sea", base({ xAxis: { ...base().xAxis, data: MN }, tooltip: { ...base().tooltip, valueFormatter: x => x.toFixed(0) },
    yAxis: { ...base().yAxis, axisLabel: { color: css("--muted") } }, grid: { left: 40, right: 10, top: 16, bottom: 26 },
    series: [{ type: "bar", data: avg.map(x => +(100 * x / mean).toFixed(1)), barMaxWidth: 22, itemStyle: { borderRadius: [4, 4, 0, 0], color: p => p.value >= 100 ? css("--hist") : css("--axis") },
      markLine: { silent: true, symbol: "none", data: [{ yAxis: 100 }], lineStyle: { color: css("--muted"), type: "dashed" }, label: { show: false } } }] }));
  { const regs = [...new Set(idx.map(i => D.reg[i]))].sort((a, b) => a - b), cells = [];
    regs.forEach((r, ri) => { const d = sumHist(idx.filter(i => D.reg[i] === r), "demand").slice(-36), bm = Array(12).fill(0), cn = Array(12).fill(0);
      d.forEach((x, j) => { if (x == null) return; bm[mo[j]] += x; cn[mo[j]]++; });
      const av = bm.map((x, k) => cn[k] ? x / cn[k] : null), mean = sum(av) / av.filter(x => x != null).length || 1;
      av.forEach((x, k) => cells.push([k, ri, x == null ? null : Math.round(100 * x / mean)])); });
    mount("#c-sreg", base({ grid: { left: 140, right: 16, top: 6, bottom: 30 },
      tooltip: { trigger: "item", ...tip(), formatter: p => `${D.regions[regs[p.value[1]]]} · ${MN[p.value[0]]}: <b>${p.value[2]}</b> (100 = average month)` },
      xAxis: { type: "category", data: MN, axisTick: { show: false }, axisLabel: { color: css("--muted") } },
      yAxis: { type: "category", data: regs.map(r => D.regions[r]), inverse: true, axisTick: { show: false }, axisLabel: { color: css("--ink-2") } },
      visualMap: { show: false, dimension: 2, min: 60, max: 140, inRange: { color: [css("--bad"), css("--surface"), css("--hist")] } },
      series: [{ type: "heatmap", data: cells, label: { show: true, fontSize: 11, color: css("--ink") }, itemStyle: { borderColor: css("--bg"), borderWidth: 2 } }] })); }
  const cy = {}; for (const i of idx) cy[D.cyc[i]] = (cy[D.cyc[i]] || 0) + 1;
  const cyn = { 1: "Every month", 2: "Every 2nd month", 3: "Every quarter" };
  const cyk = Object.keys(cy).sort((a, b) => a - b);
  hbar("#c-cyc", cyk.map(k => cyn[k] || `Every ${k} months`), cyk.map(k => cy[k]), { labels: true, left: 130 });
  // demand classes
  ST.cls = ST.cls ?? -1;
  const pts = [], cc = [0, 0, 0, 0], cv = [0, 0, 0, 0];
  for (const i of idx) { if (D.adi[i] == null || D.h0[i] == null) continue; const l12 = lastN(i, "demand", 12), c = D.cls[i]; cc[c]++; cv[c] += l12;
    const jit = 1 + (((i * 2654435761) % 1000) / 1000 - 0.5) * 0.06;     // fixed small spread so equal values don't hide each other
    pts.push({ value: [Math.min(Math.max(D.adi[i], 1), 12) * jit, Math.min(D.cv2[i], 2), l12, i], symbolSize: 4 + Math.sqrt(l12) / 9,
      itemStyle: ST.cls >= 0 && c !== ST.cls ? { opacity: 0.08 } : undefined }); }
  const tv = sum(cv) || 1, cname = D.classes;
  $("#clsc").innerHTML = cname.map((n, k) => `<button class="cls-chip" data-k="${k}" aria-pressed="${ST.cls === k}"><b>${n}</b><span>${CLASS_TEXT[n]}</span><span class="n">${fmt(cc[k])} series · ${pct(cv[k] / tv, 0)} of volume</span></button>`).join("");
  $("#clsc").onclick = e => { const b = e.target.closest(".cls-chip"); if (b) { ST.cls = ST.cls === +b.dataset.k ? -1 : +b.dataset.k; render(); } };
  const cls = mount("#c-cls", base({ grid: { left: 54, right: 44, top: 34, bottom: 46 },
    tooltip: { trigger: "item", ...tip(), formatter: p => p.componentType !== "series" ? "" : `<b>${seriesName(p.value[3])}</b> · ${esc(D.customers[D.cust[p.value[3]]])}<br>${cname[D.cls[p.value[3]]]}: ${CLASS_TEXT[cname[D.cls[p.value[3]]]]}<br>an order every ${D.adi[p.value[3]].toFixed(1)} months · size variation ${D.cv2[p.value[3]].toFixed(2)}<br>last 12 months ${fmt(p.value[2])} units<br><i>click to open</i>` },
    xAxis: { type: "log", min: 0.95, max: 13, name: "average months between orders (log scale)", nameLocation: "middle", nameGap: 28, nameTextStyle: { color: css("--muted") }, axisLabel: { color: css("--muted"), formatter: x => x < 1 ? "" : fmt(x) }, splitLine: { lineStyle: { color: css("--grid") } }, axisLine: { lineStyle: { color: css("--axis") } } },
    yAxis: { type: "value", min: 0, max: 2.05, name: "order-size variation (CV²)", nameTextStyle: { color: css("--muted"), align: "left" }, axisLabel: { color: css("--muted"), formatter: x => x.toFixed(1) }, splitLine: { lineStyle: { color: css("--grid") } } },
    series: [{ type: "scatter", data: pts, itemStyle: { color: css("--hist"), opacity: 0.45, borderColor: css("--surface"), borderWidth: 0.5 }, emphasis: { itemStyle: { opacity: 1, color: css("--fc") } },
      markLine: { silent: true, symbol: "none", lineStyle: { color: css("--ink-2"), type: "dashed" }, label: { show: true, color: css("--ink-2"), fontSize: 11, formatter: p => p.value === 1.32 ? "1.32" : "0.49" }, data: [{ xAxis: 1.32 }, { yAxis: 0.49 }] } }] }));
  cls.on("click", p => { if (p.componentType === "series") { const i = p.value[3]; setFilters({ fam: String(D.fam[i]), sku: D.skuId[i], reg: String(D.reg[i]) }); } });
  // ABC: cumulative share of the last 12 months of demand
  const vol = idx.filter(i => D.h0[i] != null && D.cls[i] != null).map(i => [i, lastN(i, "demand", 12)]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
  const vt = sum(vol.map(x => x[1])) || 1; let run = 0;
  const curve = [[0, 0]], M3 = [0, 1, 2].map(() => [0, 1, 2, 3].map(() => [0, 0]));
  vol.forEach(([i, x], k) => { const before = run / vt; run += x; const g = before < 0.8 ? 0 : before < 0.95 ? 1 : 2;
    M3[g][D.cls[i]][0]++; M3[g][D.cls[i]][1] += x; curve.push([(k + 1) / vol.length * 100, run / vt * 100]); });
  const nA = sum(M3[0].map(c => c[0]));
  mount("#c-abc", base({ grid: { left: 46, right: 16, top: 14, bottom: 40 }, tooltip: { trigger: "axis", ...tip(), formatter: ps => `${ps[0].value[0].toFixed(0)}% of series make ${ps[0].value[1].toFixed(0)}% of volume` },
    xAxis: { type: "value", min: 0, max: 100, name: "share of series (largest first)", nameLocation: "middle", nameGap: 24, nameTextStyle: { color: css("--muted") }, axisLabel: { color: css("--muted"), formatter: x => x + "%" }, splitLine: { show: false } },
    yAxis: { type: "value", min: 0, max: 100, axisLabel: { color: css("--muted"), formatter: x => x + "%" }, splitLine: { lineStyle: { color: css("--grid") } } },
    series: [{ type: "line", data: curve, symbol: "none", lineStyle: { color: css("--hist"), width: 2.4 }, areaStyle: { color: css("--hist"), opacity: 0.08 },
      markLine: { silent: true, symbol: "none", lineStyle: { color: css("--muted"), type: "dashed" }, label: { position: "insideEndBottom", color: css("--ink-2"), fontSize: 11, formatter: p => p.value === 80 ? `80% of volume: ${fmt(nA)} A series (${pct(nA / (vol.length || 1), 0)} of all)` : "95%: end of B" },
        data: [{ yAxis: 80 }, { yAxis: 95 }] } }] }));
  $("#abcx").innerHTML = `<table class="t"><tr><th></th>${D.classes.map(c => `<th class="n" style="text-transform:capitalize">${c}</th>`).join("")}</tr>
    ${["A", "B", "C"].map((g, r) => `<tr><th>${g}</th>${M3[r].map(c => `<td class="n" style="background:color-mix(in srgb,var(--hist) ${Math.min(42, Math.round(c[1] / vt * 90))}%,transparent)">${fmt(c[0])}<div class="muted" style="font-size:11px">${pct(c[1] / vt, 0)}</div></td>`).join("")}</tr>`).join("")}</table>`;
  const top = [...idx].filter(i => D.h0[i] != null).sort((a, b) => lastN(b, "demand", 12) - lastN(a, "demand", 12)).slice(0, 30);
  const months36 = D.months.slice(-36), data = [];
  top.forEach((i, r) => { const a = D.demand[i], s = D.src[i], o = D.h0[i], mx = Math.max(...a.slice(-36).filter(x => x != null), 1);
    months36.forEach((m, c) => { const j = NH - 36 + c - o; if (j < 0 || j >= a.length) return;
      const code = s[j] >= 4 ? 4 : s[j] >= 1 ? 3 : a[j] === 0 ? 0 : (a[j] / mx > 0.5 ? 2 : 1); data.push([c, r, code, a[j]]); }); });
  mount("#c-zero", base({ grid: { left: 130, right: 10, top: 10, bottom: 70 },
    tooltip: { trigger: "item", ...tip(), formatter: p => `${seriesName(top[p.value[1]])}<br>${monthName(months36[p.value[0]])}: <b>${fmt(p.value[3])}</b> · ${["no order", "order", "big order", "corrected for a stockout", "estimated (missing data)"][p.value[2]]}` },
    xAxis: { type: "category", data: months36, axisLabel: { color: css("--muted"), interval: 5, formatter: x => x.slice(2) }, axisTick: { show: false } },
    yAxis: { type: "category", data: top.map(seriesName), inverse: true, axisLabel: { color: css("--ink-2"), fontSize: 10.5, interval: 0 }, axisTick: { show: false } },
    visualMap: { type: "piecewise", dimension: 2, orient: "horizontal", left: "center", bottom: 4, textStyle: { color: css("--ink-2") },
      pieces: [{ value: 0, label: "No order", color: css("--sunk") }, { value: 1, label: "Order", color: css("--hist-soft") }, { value: 2, label: "Big order", color: css("--hist") },
        { value: 3, label: "Corrected (stockout)", color: css("--fc") }, { value: 4, label: "Estimated (missing)", color: css("--violet") }] },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: css("--surface"), borderWidth: 1.5 } }] }));
};

RENDER.anomalies = (v, idx) => {
  const inSel = new Set(idx);
  ST.akind = ST.akind || "all"; ST.atype = ST.atype ?? ""; ST.apage = ST.apage || 0;
  let rows = A.rows.filter(r => inSel.has(r[0]));
  const typeKind = t => A.type_kind[A.types[t]];
  if (ST.akind !== "all") rows = rows.filter(r => typeKind(r[2]) === ST.akind);
  const typeCounts = {}; rows.forEach(r => typeCounts[r[2]] = (typeCounts[r[2]] || 0) + 1);
  if (ST.atype !== "") rows = rows.filter(r => r[2] === +ST.atype);
  rows.sort((a, b) => b[1] - a[1]);
  const per = 60, pages = Math.max(1, Math.ceil(rows.length / per)); ST.apage = Math.min(ST.apage, pages - 1);
  const kc = {}; A.rows.filter(r => inSel.has(r[0])).forEach(r => kc[typeKind(r[2])] = (kc[typeKind(r[2])] || 0) + 1);
  const KCLS = { data_error: "bad", data_loss: "violet", real_event: "hist", master_data: "amber" };
  v.innerHTML = head("hist", "Anomaly monitor",
    "Every flagged month, sorted into four kinds: data errors that were fixed, data that was lost, real events that were kept and explained, and master-data conflicts. Each has a reason and an action.", "", idx) +
    `<div class="kpis">${Object.entries(KIND).map(([k, x]) => `<div class="kpi"><span class="l"><span class="pill ${x[1]}">${x[0]}</span></span><span class="v" data-n="${kc[k] || 0}" data-f="k">${fmtK(kc[k] || 0)}</span><span class="d">${{ data_error: "fixed in cleaning", data_loss: "estimated, left out of training", real_event: "kept, explained, modelled", master_data: "reference data contradicts sales" }[k]}</span></div>`).join("")}</div>
    <div class="card mt">
      <div class="card-h"><div><h2>Flagged points over time</h2><p class="sub" id="an-sub"></p></div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><div class="chips" id="akind">${[["all", "All kinds"], ...Object.entries(KIND).map(([k, x]) => [k, x[0]])].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${ST.akind === k}">${l}</button>`).join("")}</div>
        <select id="atype" class="btn"><option value="">All types</option>${Object.entries(typeCounts).sort((a, b) => b[1] - a[1]).map(([t, n]) => `<option value="${t}" ${String(ST.atype) === t ? "selected" : ""}>${esc(A.types[t])} (${fmt(n)})</option>`).join("")}</select></div></div>
      <div class="chart tall" id="c-an"></div></div>
    <div class="card mt"><h2>Anomalies by type</h2><p class="sub">Flagged series-months in this selection, coloured by kind. Click a bar to show only that type.</p><div class="chart" id="c-atype" style="height:${Math.max(220, 24 * Object.keys(typeCounts).length + 40)}px"></div></div>
    <div class="card mt"><h2>Register: ${fmt(rows.length)} entries</h2><p class="sub">Newest first. Click a row to look at that series on its own.</p>
      <div class="tw"><table class="t" id="areg"><tr><th>Month</th><th>Series</th><th>Type</th><th class="n">Shipped</th><th class="n">Demand</th><th>Reason</th><th>Action</th></tr>
      ${rows.slice(ST.apage * per, ST.apage * per + per).map(r => `<tr class="click" data-i="${r[0]}"><td class="mono">${D.months[r[1]]}</td><td class="mono">${seriesName(r[0])}</td><td><span class="pill ${KIND[typeKind(r[2])][1]}">${esc(A.types[r[2]])}</span></td>
        <td class="n">${fmt(r[3])}</td><td class="n">${fmt(r[4])}</td><td>${esc(r[6])}</td><td class="muted">${esc(A.actions[r[7]])}</td></tr>`).join("")}</table></div>
      <div class="pager"><button id="ap">‹ Newer</button><span>Page ${ST.apage + 1} of ${pages}</span><button id="an">Older ›</button></div></div>
    <div class="card mt"><h2>Accounts that changed hands</h2><p class="sub">${M.accounts.facts.switches} product accounts moved to another customer. No customer-region lost more than ${pct(1 - M.accounts.facts.customer_region_yoy_min, 0)} of its volume year on year. After a switch, the new customer ordered a median ${sgn(M.accounts.facts.median_demand_after_vs_before - 1, 0)} more. (All series; filters don't apply.)</p>
      <div class="grid g2"><div class="chart short" id="c-acc"></div>
      <div class="tw" style="max-height:260px"><table class="t"><tr><th>Month</th><th>Account</th><th>From → to</th><th class="n">Demand after</th></tr>
      ${M.accounts.changes.map(r => `<tr><td class="mono">${r.Date}</td><td class="mono">${r.SKU_ID} · ${esc(r.Region)}</td><td>${esc(r.from_customer)} → ${esc(r.to_customer)}</td><td class="n">${r.demand_after_vs_before == null ? "–" : sgn(r.demand_after_vs_before - 1, 0)}</td></tr>`).join("")}</table></div></div></div>${foot()}`;
  $("#akind").onclick = e => { const b = e.target.closest(".chip"); if (b) { ST.akind = b.dataset.k; ST.atype = ""; ST.apage = 0; render(); } };
  $("#atype").onchange = e => { ST.atype = e.target.value; ST.apage = 0; render(); };
  $("#ap").onclick = () => { ST.apage = Math.max(0, ST.apage - 1); render(); };
  $("#an").onclick = () => { ST.apage = Math.min(pages - 1, ST.apage + 1); render(); };
  $("#areg").onclick = e => { const tr = e.target.closest("tr.click"); if (tr) { const i = +tr.dataset.i; ST.apage = 0; setFilters({ fam: String(D.fam[i]), sku: D.skuId[i], reg: String(D.reg[i]) }); } };
  const tc = Object.entries(typeCounts).sort((a, b) => b[1] - a[1]);
  const tch = hbar("#c-atype", tc.map(([t]) => A.types[t]), tc.map(([, n]) => n), { left: 290, labels: true,
    colors: tc.map(([t]) => ({ data_error: css("--bad"), data_loss: css("--violet"), real_event: css("--hist"), master_data: css("--amber") })[typeKind(+t)]) });
  tch.on("click", p => { ST.atype = String(tc[p.dataIndex][0]); ST.apage = 0; render(); });
  const ac = M.accounts.by_customer;
  hbar("#c-acc", ac.map(r => r.customer), [{ name: "Won", data: ac.map(r => r.accounts_won), color: css("--good") }, { name: "Lost", data: ac.map(r => -r.accounts_lost), color: css("--bad") }],
    { left: 160, legend: true, zero: true, stack: "acc", fmt: x => String(Math.abs(x)), axisFmt: x => String(Math.abs(x)) });
  const dem = sumHist(idx, "demand");
  const focused = idx.length <= 30 || ST.atype !== "";
  const col = { data_error: css("--bad"), data_loss: css("--violet"), real_event: css("--hist"), master_data: css("--amber") };
  const counts = {}; rows.forEach(r => { const k = typeKind(r[2]); (counts[k] = counts[k] || Array(NH).fill(0))[r[1]]++; });
  const series = [];
  if (focused) {
    series.push({ name: "Demand", type: "line", data: dem, symbol: "none", lineStyle: { width: 1.6, color: css("--muted") }, itemStyle: { color: css("--muted") }, z: 1 });
    const byK = {}; rows.forEach(r => { const k = typeKind(r[2]); const m = (byK[k] = byK[k] || {}); const c = (m[r[1]] = m[r[1]] || { n: 0, t: {}, r: r }); c.n++; c.t[r[2]] = (c.t[r[2]] || 0) + 1; });
    for (const [k, m] of Object.entries(byK)) {
      const mx = Math.max(...Object.values(m).map(c => c.n));
      series.push({ name: KIND[k][0], type: "scatter", z: 3, itemStyle: { color: col[k], opacity: 0.85, borderColor: css("--surface"), borderWidth: 1.5 },
        data: Object.entries(m).map(([mi, c]) => ({ value: [D.months[mi], dem[mi]], n: c.n, t: c.t, r: c.r, symbolSize: 8 + 16 * Math.sqrt(c.n / mx) })) });
    }
  } else {
    for (const [k, arr] of Object.entries(counts)) series.push({ name: KIND[k][0], type: "bar", stack: "k", data: arr, barMaxWidth: 10, itemStyle: { color: col[k] } });
  }
  $("#an-sub").textContent = focused ? "Each dot is a month with flagged points on this demand line; size = how many series. Hover for the reason."
    : "Flagged series per month, by kind. Pick a type, or narrow the filters to 30 series or fewer, to see the points on the demand line.";
  mount("#c-an", base({ xAxis: yearAxis(D.months), legend: base().legend, dataZoom: zoomBar(undefined), grid: { left: 60, right: 18, top: 36, bottom: 52 },
    tooltip: focused ? { trigger: "item", ...tip(), formatter: p => p.seriesType !== "scatter" ? "" : `<b>${monthName(p.value[0])}</b> · ${p.seriesName}: ${fmt(p.data.n)} series<br>` + (p.data.n === 1 ? `<div style="max-width:320px;white-space:normal">${esc(p.data.r[6])}</div>` :
        Object.entries(p.data.t).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([t, n]) => `${esc(A.types[t])}: ${fmt(n)}`).join("<br>")) } : base().tooltip,
    series }));
};

RENDER.stockouts = (v, idx) => {
  const inSel = new Set(idx);
  const rows = SO.rows.filter(r => inSel.has(r[0]));
  const req = sum(rows.map(r => r[2])), shp = sum(rows.map(r => r[3])), bo = sum(rows.map(r => r[4])), lost = sum(rows.map(r => r[5]));
  let hidden = 0; for (const i of idx) { const s = D.src[i]; for (let j = 0; j < s.length; j++) if (s[j] === 2) hidden += (D.demand[i][j] || 0) - (D.shipped[i][j] || 0); }
  const byM = SO.reasons.map(() => Array(NH).fill(0)); rows.forEach(r => byM[r[6]][r[1]] += r[5]);
  const byR = SO.reasons.map((_, k) => sum(byM[k]));
  // gap between asked for and shipped, only where both are recorded (months whose demand had to be estimated
  // because records were lost, e.g. the 2023 ERP migration, are not a supply shortfall)
  const gap = Array(NH).fill(null);
  for (const i of idx) { const o = D.h0[i]; if (o == null) continue; const s = D.src[i];
    for (let j = 0; j < s.length; j++) { if (s[j] >= 4) continue; const d = D.demand[i][j], sh = D.shipped[i][j]; if (d == null || sh == null) continue; gap[o + j] = (gap[o + j] || 0) + d - sh; } }
  const bySeries = {}; rows.forEach(r => { const b = (bySeries[r[0]] = bySeries[r[0]] || { lost: 0, n: 0, req: 0, shp: 0 }); b.lost += r[5]; b.n++; b.req += r[2]; b.shp += r[3]; });
  const top = Object.entries(bySeries).sort((a, b) => b[1].lost - a[1].lost).slice(0, 15);
  const pal = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s7", "--s8"].map(css);
  v.innerHTML = head("hist", "Stockouts & lost sales",
    "Months when Celeris could not ship everything customers asked for. Lost units never shipped; back-ordered units shipped the next month. The forecast is built on what customers asked for, not on what shipped.", "", idx) +
    `<div class="kpis">
      ${kpi("Listed stockout months", fmt(rows.length), "from inventory_stockouts.csv", "neutral", rows.length)}
      ${kpi("Units lost for good", fmtK(lost), `fill rate in those months ${pct(shp / (req || 1), 0)}`, "neutral", lost)}
      ${kpi("Units back-ordered", fmtK(bo), "shipped the following month", "neutral", bo)}
      ${kpi("Hidden shortage, estimated", fmtK(hidden), "chip-shortage months the file doesn't list", "neutral", hidden)}
    </div>
    <div class="grid g5 mt">
      <div class="card"><h2>Lost units by month and cause</h2><p class="sub">Bars: units lost (listed stockouts). Line: what was asked for minus what shipped, including the estimated hidden shortage (below zero = a catch-up month shipping last month's back-order).</p><div class="chart tall" id="c-so"></div></div>
      <div class="card"><h2>Lost units by cause</h2><p class="sub">This selection</p><div class="chart tall" id="c-sor"></div></div>
    </div>
    <div class="card mt"><h2>Series that lost the most</h2><p class="sub">Click a row to open the series in the demand explorer</p><div class="tw"><table class="t" id="sot"><tr><th>Series</th><th>Customer</th><th class="n">Stockout months</th><th class="n">Asked for</th><th class="n">Shipped</th><th class="n">Lost</th></tr>
      ${top.map(([i, b]) => `<tr class="click" data-i="${i}"><td class="mono">${seriesName(+i)}</td><td>${esc(D.customers[D.cust[i]])}</td><td class="n">${b.n}</td><td class="n">${fmt(b.req)}</td><td class="n">${fmt(b.shp)}</td><td class="n">${fmt(b.lost)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">No listed stockouts in this selection.</td></tr>`}</table></div></div>${foot()}`;
  mount("#c-so", base({ xAxis: yearAxis(D.months), legend: base().legend, grid: { left: 60, right: 18, top: 52, bottom: 30 }, dataZoom: zoomBar(undefined, false),
    series: [...SO.reasons.map((r, k) => ({ name: r, type: "bar", stack: "lost", data: byM[k], itemStyle: { color: pal[k % pal.length] }, barMaxWidth: 14 })),
      { name: "Gap: asked for − shipped", type: "line", data: gap, symbol: "none", lineStyle: { width: 1.6, color: css("--ink") }, itemStyle: { color: css("--ink") } }] }));
  const ro = SO.reasons.map((r, k) => [r, byR[k], pal[k % pal.length]]).sort((a, b) => b[1] - a[1]);
  const sor = hbar("#c-sor", ro.map(x => x[0]), ro.map(x => x[1]), { colors: ro.map(x => x[2]), labels: true, left: 150, right: 60 });
  sor.setOption({ xAxis: { splitNumber: 3 } });
  $("#sot").onclick = e => { const tr = e.target.closest("tr.click"); if (tr) { const i = +tr.dataset.i; ST.f = { fam: String(D.fam[i]), sku: D.skuId[i], reg: String(D.reg[i]), ch: "", cust: "" }; go("explorer"); } };
};

RENDER.forecast = (v, idx) => {
  const b = band(idx), dem = sumHist(idx, "demand"), mc = M.model, tot19 = rangeOver(idx);
  const aF = accuracy(idx, "fam"), aR = accuracy(idx, "reg"), aC = accuracy(idx, "cls"), acc = aF;
  const won = list => { const g = list.filter(x => x.a > 0); return `${g.filter(x => x.wm <= x.wb).length} of ${g.length}`; };
  v.innerHTML = head("fc", "Forecast & accuracy",
    "The forecast continues the history on one timeline, with its range. Accuracy comes from the backtest: forecasts made at four past dates (Mar 2022, 2023, 2024, 2025) using only the data known then, each scored on the 19 months that followed.", "", idx) +
    `<div class="kpis">
      ${kpi("19-month forecast", fmtK(tot19.e), `80% range ${fmtK(tot19.lo)} – ${fmtK(tot19.hi)} units`, "fc", tot19.e)}
      ${kpi("Backtest error per series (WAPE)", pct(acc.tot.wm), `best simple method ${pct(acc.tot.wb)}`, "neutral", acc.tot.wm, "p")}
      ${kpi("Backtest bias", sgn(acc.tot.bm), `typical forecast (P50); totals use expected = P50 × ${mc.bias_factor}`, "neutral")}
      ${kpi("Error on the monthly total", pct(acc.total.wm), `best simple method ${pct(acc.total.wb)}`, "neutral", acc.total.wm, "p")}
    </div>
    <section class="panel"><h2>Forecast vs history</h2><p class="sub">Blue: what customers asked for. Orange: expected forecast (solid) and typical month P50 (dashed). Shaded: 80% range (P10–P90) for this total.</p><div class="chart tall" id="c-fc"></div></section>
    <section class="panel"><h2>Backtest error (WAPE) by family, region and months ahead</h2>
      <p class="sub">WAPE = total absolute error ÷ total demand; lower is better. Orange: the model. Grey: the best simple method (last year's level × each month's usual share). The model is better in ${won(Object.values(aF.byG))} families, ${won(Object.values(aR.byG))} regions, ${won(acc.byH)} horizons and ${won(acc.byO)} forecast dates.</p>
      <div class="grid g3"><div><h3>By family</h3><div class="chart" id="c-wf"></div></div><div><h3>By region</h3><div class="chart" id="c-wr"></div></div><div><h3>By months ahead</h3><div class="chart" id="c-wh"></div></div></div></section>
    <section class="panel"><h2>Backtest bias by family, region and months ahead</h2>
      <p class="sub">Bias = (total forecast − total actual) ÷ total actual. Zero is best; below zero means the forecast was too low. The typical-month forecast runs a few percent low on purpose (it minimises WAPE); totals and plans use the bias-corrected expected value.</p>
      <div class="grid g3"><div><h3>By family</h3><div class="chart" id="c-bf"></div></div><div><h3>By region</h3><div class="chart" id="c-br"></div></div><div><h3>By months ahead</h3><div class="chart" id="c-bh"></div></div></div></section>
    <div class="grid g2">
      <section class="panel"><h2>Error at the level you plan at</h2><p class="sub">All series. One part in one month is hard to call; totals are easier because errors cancel out.</p>
        <div class="tw" style="max-height:none"><table class="t"><tr><th>Level</th><th class="n">Model, expected</th><th class="n">Model, typical</th><th class="n">Best simple</th></tr>
        ${M.accuracy_by_level.map(r => `<tr><td>${esc(r.level)}</td><td class="n"><b>${pct(r.wape_model_expected)}</b></td><td class="n">${pct(r.wape_model_p50)}</td><td class="n">${pct(r.wape_best_simple)}</td></tr>`).join("")}</table></div>
        <h2 style="margin-top:20px">More splits (this selection)</h2>
        <div class="tw" style="max-height:none"><table class="t"><tr><th>Split</th><th class="n">Model WAPE</th><th class="n">Simple WAPE</th><th class="n">Model bias</th></tr>
        ${acc.byO.map((x, k) => `<tr><td>Forecast made ${monthName(D.bt.origins[k])}</td><td class="n">${pct(x.wm)}</td><td class="n">${pct(x.wb)}</td><td class="n">${sgn(x.bm)}</td></tr>`).join("")}
        ${Object.entries(aC.byG).filter(([, g]) => g.a > 0).map(([k, x]) => `<tr><td>Demand class: ${D.classes[k]}</td><td class="n">${pct(x.wm)}</td><td class="n">${pct(x.wb)}</td><td class="n">${sgn(x.bm)}</td></tr>`).join("")}</table></div></section>
      <section class="panel"><h2>Every method tried</h2><p class="sub">Same backtest rows, all series: simple baselines, classic statistical models, deep learning, pretrained time-series models and other gradient-boosting libraries.</p><div class="chart" id="c-meth" style="height:${Math.max(240, 26 * ((M.methods || []).length + (M.comparison || []).length))}px"></div>
        <h2 style="margin-top:14px">What the model pays attention to</h2><p class="sub">Share of the model's total gain, top 15 inputs</p><div class="chart tall" id="c-imp"></div></section>
    </div>${foot()}`;
  timeline("#c-fc", { main: dem, label: "Asked for (history)" }, { main: b.E, lo: b.LO, hi: b.HI, p50: b.P50, label: "Forecast (expected)" }, { story: storyFor(), zoom: "recent" });
  const pf = x => pct(x), sf = x => sgn(x), af = x => (x * 100).toFixed(0) + "%";
  const bars = (el, G, names, W) => { const g = Object.entries(G).filter(([, x]) => x.a > 0).sort((a, c) => (W ? a[1].wm - c[1].wm : a[1].bm - c[1].bm));
    hbar(el, g.map(([k]) => names[k]), [{ name: "Model", data: g.map(([, x]) => W ? x.wm : x.bm), color: css("--fc") }, { name: "Best simple", data: g.map(([, x]) => W ? x.wb : x.bb), color: css("--axis") }],
      { legend: true, left: 132, right: 16, fmt: W ? pf : sf, axisFmt: af, zero: !W }); };
  const lines = (el, W) => mount(el, base({ grid: { left: 44, right: 12, top: 30, bottom: 40 },
    xAxis: { ...base().xAxis, data: acc.byH.map((_, k) => k + 1), name: "months ahead", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: css("--muted") } },
    yAxis: { ...base().yAxis, axisLabel: { color: css("--muted"), formatter: af } }, legend: base().legend, tooltip: { ...base().tooltip, valueFormatter: W ? pf : sf },
    series: [{ name: "Model", type: "line", data: acc.byH.map(h => W ? h.wm : h.bm), symbol: "circle", symbolSize: 5, lineStyle: { width: 2.2, color: css("--fc") }, itemStyle: { color: css("--fc") },
      markLine: W ? undefined : { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: css("--muted") }, label: { show: false } } },
      { name: "Best simple", type: "line", data: acc.byH.map(h => W ? h.wb : h.bb), symbol: "none", lineStyle: { width: 1.6, color: css("--muted"), type: "dashed" }, itemStyle: { color: css("--muted") } }] }));
  bars("#c-wf", aF.byG, D.families, true); bars("#c-wr", aR.byG, D.regions, true); lines("#c-wh", true);
  bars("#c-bf", aF.byG, D.families, false); bars("#c-br", aR.byG, D.regions, false); lines("#c-bh", false);
  const seen = new Set(M.methods.map(r => r.method));
  const meth = [...M.methods, ...(M.comparison || []).filter(r => !seen.has(r.method) && r.method !== "LightGBM (pipeline)")].sort((a, c) => a.wape - c.wape);
  hbar("#c-meth", meth.map(r => r.method), meth.map(r => r.wape), { left: 190, fmt: x => pct(x), axisFmt: x => (x * 100).toFixed(0) + "%", labels: true,
    colors: meth.map(r => r.method.startsWith("LightGBM") ? css("--fc") : css("--axis")) });
  const imp = Object.entries(M.importance);
  hbar("#c-imp", imp.map(x => featName(x[0])), imp.map(x => x[1]), { left: 230, fmt: x => pct(x), axisFmt: x => (x * 100).toFixed(0) + "%", labels: true, colors: imp.map(() => css("--ink-2")) });
};

/* Replay: pick a past date, see the forecast made then, reveal what really happened. */
function replayData(idx, o) {
  const bt = D.bt, f = M.model.bias_factor;
  const a = Array(19).fill(0), m = Array(19).fill(0), b = Array(19).fill(0), seen = Array(19).fill(false);
  let em = 0, eb = 0;
  for (const i of idx) { const A_ = bt.act[i]; if (!A_ || !A_.length) continue;
    for (let h = 0; h < 19; h++) { const x = A_[o][h]; if (x == null) continue; const mm = bt.model[i][o][h] ?? 0, bb = bt.base[i][o][h] ?? 0;
      a[h] += x; m[h] += mm; b[h] += bb; seen[h] = true; em += Math.abs(mm - x); eb += Math.abs(bb - x); } }
  const ta = sum(a), totErr = arr => ta ? arr.reduce((s, v, h) => s + Math.abs(v - a[h]), 0) / ta : null;
  const me = m.map(x => x * f);
  return { a: a.map((v, h) => seen[h] ? v : null), m: me.map((v, h) => seen[h] ? v : null), b: b.map((v, h) => seen[h] ? v : null),
    wm: ta ? em / ta : null, wb: ta ? eb / ta : null, tm: totErr(me), tb: totErr(b), ta };
}
RENDER.replay = (v, idx) => {
  const no = D.bt.origins.length; if (ST.ro == null) ST.ro = no - 1;
  const o = ST.ro, org = D.bt.origins[o], oi = D.months.indexOf(org), R = replayData(idx, o), acc = accuracy(idx);
  const shown = !!ST.revealed, anim = shown && ST.animate && !REDUCED; ST.animate = false;
  const card = (name, per, tot, win, cls) => `<div class="s ${shown && win ? "win" : ""}"><div class="l">${name}${shown && win ? " · closer" : ""}</div><div class="n" style="color:var(${cls})">${shown ? pct(tot) : "?"}</div><div class="l">error on the monthly total · per series ${shown ? pct(per) : "?"}</div></div>`;
  v.innerHTML = head("fc", "Backtest replay",
    "How the accuracy figures were measured. Pick one of the four backtest dates, see the forecast made with only what was known then, and press Reveal to compare it with what really happened over the next 19 months.", "", idx) +
    `<div class="grid g5">
      <div class="card"><div class="card-h"><div><h2>Forecast made in ${monthName(org)}</h2><p class="sub">Pick a forecast origin, then reveal the next 19 months</p></div>
        <button class="btn fc reveal" id="rv">${icon("play")} ${shown ? "Replay it" : "Reveal what happened"}</button></div>
        <div class="stops" id="stops">${D.bt.origins.map((x, k) => `<button class="stop" data-k="${k}" aria-pressed="${k === o}"><i></i>${monthName(x)}</button>`).join("")}</div>
        <div class="chart tall" id="c-rp"></div></div>
      <div class="card"><h2>Who was closer?</h2><p class="sub">${R.ta ? `${fmtK(R.ta)} units asked for in the 19 months after ${monthName(org)}` : "No tested history for this selection at this date"}</p>
        <div class="score">${card("Model", R.wm, R.tm, R.tm != null && R.tm <= R.tb, "--fc")}${card("Best simple method", R.wb, R.tb, R.tb != null && R.tb < R.tm, "--ink-2")}</div>
        <p class="note">The simple method is the best of six classic ones: last year's level × each month's usual share. It is the bar the model has to clear.</p>
        <h2 style="margin-top:16px">All four dates, this selection</h2>
        <div class="tw" style="max-height:none;margin-top:6px"><table class="t"><tr><th>Forecast made at</th><th class="n">Model</th><th class="n">Simple</th><th class="n">Model bias</th></tr>
        ${acc.byO.map((x, k) => `<tr class="${k === o ? "hl" : ""}"><td class="mono">${D.bt.origins[k]}</td><td class="n">${shown || k !== o ? pct(x.wm) : "?"}</td><td class="n">${shown || k !== o ? pct(x.wb) : "?"}</td><td class="n">${shown || k !== o ? sgn(x.bm) : "?"}</td></tr>`).join("")}</table></div>
        <p class="sub" style="margin-top:6px">Per-series WAPE (each part, each region, each month)</p></div>
    </div>${foot()}`;
  const from = Math.max(0, oi - 35), to = Math.min(NH - 1, oi + 19), xs = D.months.slice(from, to + 1);
  const dem = sumHist(idx, "demand"), cut = oi - from;
  const after = arr => xs.map((_, k) => k > cut && k - cut - 1 < 19 ? arr[k - cut - 1] : null);
  const actual = after(R.a);
  const ch = mount("#c-rp", base({ xAxis: { ...base().xAxis, data: xs, axisLabel: { color: css("--muted"), interval: (i, x) => x.endsWith("-01"), formatter: x => x.slice(0, 4) } },
    legend: base().legend, grid: { left: 60, right: 18, top: 40, bottom: 30 },
    tooltip: { ...base().tooltip, formatter: ps => `<b>${monthName(ps[0].axisValue)}</b><br>` + ps.filter(p => p.value != null).map(p => `${p.marker}${p.seriesName}: <b>${fmt(p.value)}</b>`).join("<br>") },
    series: [
      { name: "Known at the time", type: "line", data: xs.map((_, k) => k <= cut ? dem[from + k] : null), symbol: "none", lineStyle: { width: 2, color: css("--hist") }, itemStyle: { color: css("--hist") },
        markLine: { silent: true, symbol: "none", lineStyle: { color: css("--ink-2"), type: "dashed" }, label: { formatter: "Forecast origin", color: css("--ink-2"), fontSize: 11 }, data: [{ xAxis: org }] },
        markArea: { silent: true, itemStyle: { color: css("--fc"), opacity: 0.05 }, data: [[{ xAxis: xs[Math.min(cut + 1, xs.length - 1)] }, { xAxis: xs[xs.length - 1] }]] } },
      { name: "Model forecast", type: "line", data: after(R.m), symbol: "none", lineStyle: { width: 2.4, color: css("--fc") }, itemStyle: { color: css("--fc") } },
      { name: "Best simple method", type: "line", data: after(R.b), symbol: "none", lineStyle: { width: 1.6, color: css("--muted"), type: "dashed" }, itemStyle: { color: css("--muted") } },
      { id: "act", name: "What really happened", type: "line", data: shown && !anim ? actual : xs.map(() => null), symbol: "circle", symbolSize: 5, lineStyle: { width: 2.4, color: css("--ink") }, itemStyle: { color: css("--ink") } }] }));
  $("#stops").onclick = e => { const s = e.target.closest(".stop"); if (s) { ST.ro = +s.dataset.k; ST.revealed = false; render(); } };
  $("#rv").onclick = () => { ST.revealed = true; ST.animate = true; render(); };
  if (anim) {
    let k = cut; const data = xs.map(() => null);
    ST.timer = setInterval(() => { k++; if (k >= xs.length) { clearInterval(ST.timer); return; } data[k] = actual[k]; ch.setOption({ series: [{ id: "act", data: [...data] }] }); }, 70);
  }
};

RENDER.lifecycle = (v, idx) => {
  const skuSet = new Set(idx.map(i => D.sku[i]));
  const special = D.skus.map((s, k) => ({ ...s, k })).filter(s => skuSet.has(s.k) && s.status !== "Active");
  const pairs = D.skus.filter(s => s.replaces && s.status === "Planned Launch" && skuSet.has(D.skuIndex[s.id]));
  const tot = k => { let h = 0, f = 0; for (const i of idx) if (D.sku[i] === k) { h += lastN(i, "demand", 12); f += sum(D.exp[i] || []); } return [h, f]; };
  v.innerHTML = head("fc", "Lifecycle: EOL and launches",
    "Products being discontinued, and the new products replacing them. Forecasts are 0 after end of life; a new product ramps up from its predecessor's level over about 6 months (the median of the 6 replacements in the history).", "", idx) +
    `<div class="grid g3" id="pairs">${pairs.map(p => `<div class="card"><h2>${p.replaces} → ${p.id}</h2><p class="sub">${esc(p.desc)}. ${p.powertrain} replaces ${D.skus[D.skuIndex[p.replaces]].powertrain}. New from ${monthName(p.launch)}; old ends ${monthName(D.skus[D.skuIndex[p.replaces]].eol)}.</p><div class="chart short" id="c-p-${p.id}"></div></div>`).join("")}</div>
    <div class="card ${pairs.length ? "mt" : ""}"><h2>Products that are not simply "active"</h2><p class="sub">Click a row to open its forecast</p><div class="tw"><table class="t" id="lct"><tr><th>Product</th><th>Status</th><th>Launch</th><th>End of life</th><th>Replaces</th><th class="n">Last 12 months</th><th class="n">Next 19 months (expected)</th></tr>
      ${special.map(s => { const [h, f] = tot(s.k); return `<tr class="click" data-s="${s.id}"><td><b class="mono">${s.id}</b> ${esc(s.desc)}<div class="muted" style="font-size:12px">${esc(s.family)} · ${s.powertrain}</div></td>
        <td><span class="pill ${s.status === "Planned Launch" ? "real" : s.status === "Discontinued" ? "err" : "warn"}">${s.status}</span></td><td class="mono">${s.launch}</td><td class="mono">${s.eol || "–"}</td><td class="mono">${s.replaces || "–"}</td>
        <td class="n">${fmt(h)}</td><td class="n">${fmt(f)}</td></tr>`; }).join("") || `<tr><td colspan="7" class="empty">No discontinued or planned products in this selection.</td></tr>`}</table></div></div>
    <div class="card mt"><h2>The shift to electric</h2><p class="sub">Share of demand by powertrain, full years, all products. EV-only parts grew fast until 2024 and have been flat since.</p><div class="chart short" id="c-ev"></div></div>${foot()}`;
  $("#lct").onclick = e => { const tr = e.target.closest("tr.click"); if (tr) { setFilters({ sku: tr.dataset.s, fam: String(D.families.indexOf(D.skus[D.skuIndex[tr.dataset.s]].family)) }); go("forecast"); } };
  const regOk = i => ST.f.reg === "" || D.reg[i] === +ST.f.reg;
  for (const p of pairs) {
    const oldK = D.skuIndex[p.replaces], newK = D.skuIndex[p.id];
    const oi = [], ni = []; for (let i = 0; i < D.n; i++) { if (!regOk(i)) continue; if (D.sku[i] === oldK) oi.push(i); if (D.sku[i] === newK) ni.push(i); }
    const from = D.months.indexOf("2025-11"), months = ALLM.slice(from);
    const oh = sumHist(oi, "demand").slice(from), of = sumFc(oi, "exp"), nf = sumFc(ni, "exp");
    mount("#c-p-" + p.id, base({ xAxis: { ...base().xAxis, data: months, axisLabel: { color: css("--muted"), interval: 5, formatter: x => MN[+x.slice(5) - 1] + " " + x.slice(2, 4) } }, legend: base().legend, grid: { left: 44, right: 22, top: 30, bottom: 24 },
      series: [{ name: p.replaces + " (old)", type: "line", data: [...oh, ...of], symbol: "none", lineStyle: { color: css("--muted"), width: 2 }, itemStyle: { color: css("--muted") } },
        { name: p.id + " (new)", type: "line", data: [...Array(oh.length).fill(null), ...nf.map(x => x || null)], symbol: "none", lineStyle: { color: css("--fc"), width: 2.4 }, itemStyle: { color: css("--fc") },
          markLine: { silent: true, symbol: "none", lineStyle: { color: css("--axis"), type: "dashed" }, label: { show: false }, data: [{ xAxis: D.fc_months[0] }] } }] }));
  }
  const ev = DR.ev.filter(r => r.full_year);
  mount("#c-ev", base({ xAxis: { ...base().xAxis, data: ev.map(r => String(r.Year)) }, legend: base().legend, tooltip: { ...base().tooltip, valueFormatter: x => x + "%" },
    yAxis: { ...base().yAxis, axisLabel: { color: css("--muted"), formatter: x => x + "%" } },
    series: [{ name: "EV-only parts", type: "line", data: ev.map(r => r.EV), symbolSize: 6, lineStyle: { color: css("--s3"), width: 2.4 }, itemStyle: { color: css("--s3") }, label: { show: true, position: "top", formatter: p => p.value + "%", color: css("--ink-2"), fontSize: 11 } },
      { name: "Petrol-only (ICE) parts", type: "line", data: ev.map(r => r.ICE), symbolSize: 6, lineStyle: { color: css("--s7"), width: 2.4 }, itemStyle: { color: css("--s7") } }] }));
};

/* What-if: the saved model re-run with each indicator moved; sliders interpolate between tested points, several sliders multiply. */
const WI = { ISM_Manufacturing_PMI: { unit: "pts", scale: 1, min: -6, max: 6, step: 1 }, New_Vehicle_Orders_Index: { unit: "%", scale: 100, min: -10, max: 10, step: 1 },
  Auto_Production_YoY_Pct: { unit: "pts", scale: 1, min: -6, max: 6, step: 1 } };
const PRESETS = [["Current forecast", [0, 0, 0]], ["Mild slowdown", [-3, -5, -3]], ["Downturn", [-6, -10, -6]], ["Upswing", [3, 5, 3]], ["Boom", [6, 10, 6]]];
function whatifFactors(vals) {
  const G = M.scenarios.grid, inds = Object.keys(WI), R = new Float64Array(D.n).fill(1), F = D.families.map(() => Array(NF).fill(1));
  inds.forEach((ind, q) => {
    const x = vals[q] / WI[ind].scale; if (!x) return;
    const pts = [...G[ind].steps, 0].sort((a, b) => a - b);
    let k = 0; while (k < pts.length - 2 && x > pts[k + 1]) k++;
    const p0 = pts[k], p1 = pts[k + 1], w = Math.min(1, Math.max(0, (x - p0) / (p1 - p0)));
    const S = p => p === 0 ? null : D.whatif.series[`${ind}|${p}`], FM = p => p === 0 ? null : D.whatif.fam_month[`${ind}|${p}`];
    const s0 = S(p0), s1 = S(p1), f0 = FM(p0), f1 = FM(p1);
    for (let i = 0; i < D.n; i++) R[i] *= (1 - w) * (s0 ? s0[i] : 1) + w * (s1 ? s1[i] : 1);
    for (let g = 0; g < F.length; g++) for (let m = 0; m < NF; m++) F[g][m] *= (1 - w) * (f0 ? f0[g][m] : 1) + w * (f1 ? f1[g][m] : 1);
  });
  return { R, F };
}
RENDER.whatif = (v, idx) => {
  ST.wv = ST.wv || [...PRESETS[1][1]];
  const inds = Object.keys(WI), G = M.scenarios.grid;
  v.innerHTML = head("fc", "What-if scenarios",
    "How much would demand change if the economy moved? The trained model was re-run with each indicator shifted for the whole forecast window (for example PMI −3 points). Drag a slider or pick a preset; values between the tested points are interpolated.", "", idx) +
    `<div class="grid g5">
      <div class="card"><div class="card-h"><div><h2>Expected units per month</h2><p class="sub">Current forecast against this scenario, for the selection</p></div><div class="chips" id="pre">${PRESETS.map(([n], k) => `<button class="chip" data-k="${k}">${n}</button>`).join("")}</div></div>
        <div class="chart" id="c-wi"></div>
        <div class="kpis" style="margin-top:10px" id="wi-kpi"></div></div>
      <div class="card"><h2>Indicators</h2><p class="sub">The three inputs the model reads, as a change from the published path</p>
        <div class="sliders">${inds.map((ind, q) => `<div class="sl"><div class="row"><label for="s-${q}">${esc(G[ind].label)}</label><span class="val" id="sv-${q}"></span></div>
          <input type="range" id="s-${q}" min="${WI[ind].min}" max="${WI[ind].max}" step="${WI[ind].step}" value="${ST.wv[q]}" aria-label="${esc(G[ind].label)}">
          <div class="ticks"><span>${WI[ind].min}${WI[ind].unit === "%" ? "%" : ""}</span><span>0</span><span>+${WI[ind].max}${WI[ind].unit === "%" ? "%" : ""}</span></div></div>`).join("")}</div>
        <p class="note">Checked: moving all three by the "mild slowdown" amounts in one model run gives ${sgn(M.scenarios.combination_check.together_change)}; multiplying the three single effects gives ${sgn(M.scenarios.combination_check.multiplied_change)}.</p></div>
    </div>
    <div class="grid g2 mt">
      <div class="card"><h2>Impact by family</h2><p class="sub">Families whose demand follows the economy react most (see Economy lead / lag)</p><div class="chart" id="c-wf"></div></div>
      <div class="card"><h2>How sensitive is the whole company?</h2><p class="sub">19-month total change when one indicator moves on its own. A downturn hurts more than an upswing helps.</p><div class="chart" id="c-ws"></div></div>
    </div>${foot()}`;
  const baseM = Array(NF).fill(0), revBase = sum(sumFc(idx, "frev"));
  for (const i of idx) { const o = D.f0[i]; if (o == null) continue; D.exp[i].forEach((e, j) => baseM[o + j] += e); }
  const chart = mount("#c-wi", base({ xAxis: { ...base().xAxis, data: D.fc_months, axisLabel: { color: css("--muted"), formatter: x => MN[+x.slice(5) - 1] + " " + x.slice(2, 4) } }, legend: base().legend,
    yAxis: { ...base().yAxis, scale: true }, grid: { left: 56, right: 18, top: 30, bottom: 30 },
    series: [{ name: "Current forecast", type: "line", data: baseM, symbol: "none", lineStyle: { color: css("--fc"), width: 2.4 }, itemStyle: { color: css("--fc") } },
      { id: "sc", name: "This scenario", type: "line", data: baseM, symbol: "none", lineStyle: { color: css("--violet"), width: 2.4, type: "dashed" }, itemStyle: { color: css("--violet") },
        areaStyle: { color: css("--violet"), opacity: 0.06 } }] }));
  const fchart = hbar("#c-wf", D.families, D.families.map(() => 0), { left: 170, fmt: x => sgn(x, 2), axisFmt: x => (x * 100).toFixed(1) + "%", labels: true, zero: true });
  const update = () => {
    const vals = ST.wv, { R, F } = whatifFactors(vals);
    const scM = Array(NF).fill(0), byF = {}; let revS = 0;
    for (const i of idx) { const o = D.f0[i]; if (o == null) continue; const fam = D.fam[i];
      for (let j = 0; j < D.exp[i].length; j++) { const k = R[i] * F[fam][o + j], e = D.exp[i][j];
        scM[o + j] += e * k; revS += D.frev[i][j] * k; const g = (byF[fam] = byF[fam] || [0, 0]); g[0] += e; g[1] += e * k; } }
    const tb = sum(baseM), ts = sum(scM), chg = tb ? ts / tb - 1 : 0;
    chart.setOption({ series: [{ id: "sc", data: scM }] });
    const Fr = Object.entries(byF).map(([k, g]) => [D.families[k], g[0] ? g[1] / g[0] - 1 : 0]).sort((a, b) => a[1] - b[1]);
    const mx = Math.max(0.01, ...Fr.map(x => Math.abs(x[1]))) * 1.25;
    fchart.setOption({ xAxis: { min: -mx, max: mx }, yAxis: { data: Fr.map(x => x[0]) }, series: [{ data: Fr.map(x => ({ value: x[1], itemStyle: { color: x[1] < 0 ? css("--bad") : css("--good"), borderRadius: 4 } })) }] });
    document.querySelectorAll("#pre .chip").forEach(b => b.classList.toggle("on", PRESETS[+b.dataset.k][1].every((x, q) => x === vals[q])));
    $("#wi-kpi").innerHTML = kpi("19 months, current", fmtK(tb), "expected units", "fc") + kpi("With this scenario", fmtK(ts), `${chg >= 0 ? "+" : "−"}${fmtK(Math.abs(ts - tb))} units`, "neutral") +
      kpi("Change", `<span class="${chg > 0.0005 ? "up" : chg < -0.0005 ? "down" : ""}">${sgn(chg)}</span>`, `revenue ${revS - revBase >= 0 ? "+" : "−"}${money(Math.abs(revS - revBase))}`, "neutral");
    inds.forEach((ind, q) => { const x = vals[q], el = $("#sv-" + q); el.textContent = (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x) + (WI[ind].unit === "%" ? "%" : " pts");
      el.className = "val " + (x < 0 ? "neg" : x > 0 ? "pos" : ""); $("#s-" + q).value = x; });
  };
  inds.forEach((_, q) => { $("#s-" + q).oninput = e => { ST.wv[q] = +e.target.value; requestAnimationFrame(update); }; });
  $("#pre").onclick = e => { const b = e.target.closest(".chip"); if (b) { ST.wv = [...PRESETS[+b.dataset.k][1]]; update(); } };
  update();
  const gc = M.scenarios.grid_change;
  mount("#c-ws", base({ grid: { left: 50, right: 30, top: 30, bottom: 34 }, xAxis: { type: "value", min: -1, max: 1, interval: 0.5, axisLabel: { color: css("--muted"), alignMinLabel: "left", alignMaxLabel: "right", formatter: x => x === -1 ? "largest fall tested" : x === 1 ? "largest rise tested" : x === 0 ? "published path" : "" }, splitLine: { show: false }, axisLine: { lineStyle: { color: css("--axis") } } },
    yAxis: { ...base().yAxis, axisLabel: { color: css("--muted"), formatter: x => (x * 100).toFixed(0) + "%" } }, legend: base().legend,
    tooltip: { trigger: "item", ...tip(), formatter: p => `${p.seriesName}<br>${p.data.lab}: <b>${sgn(p.value[1])}</b>` },
    series: inds.map((ind, q) => { const steps = [...G[ind].steps, 0].sort((a, b) => a - b), mx = Math.max(...steps.map(Math.abs));
      return { name: G[ind].label, type: "line", symbolSize: 7, lineStyle: { width: 2, color: css(["--s1", "--s2", "--s3"][q]) }, itemStyle: { color: css(["--s1", "--s2", "--s3"][q]) },
        data: steps.map(s => ({ value: [s / mx, s === 0 ? 0 : gc[`${ind}|${s}`]], lab: s === 0 ? "no change" : `${s > 0 ? "+" : ""}${WI[ind].unit === "%" ? (s * 100).toFixed(0) + "%" : s + " pts"}` })) }; }) }));
};

function planRows(idx, by, period) {
  const names = by === "fam" ? D.families : D.regions;
  const per = m => period === "q" ? D.fc_months[m].slice(0, 4) + "-Q" + (Math.floor((+D.fc_months[m].slice(5) - 1) / 3) + 1) : D.fc_months[m];
  const groups = {}; for (const i of idx) (groups[D[by][i]] = groups[D[by][i]] || []).push(i);
  const periods = [...new Set(D.fc_months.map((_, m) => per(m)))];
  const span = p => { const ms = D.fc_months.map((_, m) => m).filter(m => per(m) === p); return [ms[0], ms[ms.length - 1], ms.length]; };
  const price = (ii, m0, m1) => { let e = 0, r = 0; for (const i of ii) { const o = D.f0[i]; if (o == null) continue; D.exp[i].forEach((x, j) => { if (o + j >= m0 && o + j <= m1) { e += x; r += D.frev[i][j]; } }); } return e ? r / e : 0; };
  const rows = [];
  for (const [g, ii] of Object.entries(groups)) for (const p of periods) {
    const [m0, m1] = span(p), r = rangeOver(ii, m0, m1);
    rows.push({ group: names[g], period: p, expected: r.e, sd: r.sd, price: price(ii, m0, m1) });
  }
  const tot = periods.map(p => { const [m0, m1, n] = span(p); const r = rangeOver(idx, m0, m1); return { p, n, e: r.e, sd: r.sd, price: price(idx, m0, m1) }; });
  return { rows, periods, tot };
}
RENDER.procure = (v, idx) => {
  ST.pby = ST.pby || "fam"; ST.pper = ST.pper || "q"; ST.sl = ST.sl || 90;
  v.innerHTML = head("plan", "Procurement plan from P90 forecasts",
    "How much to plan for so you rarely run short. Pick a service level: at 90% there is about a 1-in-10 chance that a period's demand goes above the plan. The buffer is the stock above the expected volume that buys that safety.",
    seg("pby", [["fam", "By family"], ["reg", "By region"]], ST.pby) + seg("pper", [["q", "Quarters"], ["m", "Months"]], ST.pper) + `<button class="btn" id="pdl">${icon("download")} Plan CSV</button>`, idx) +
    `<div class="grid g5">
      <div class="card"><div class="sl"><div class="row"><label for="slv">Service level: chance of having enough in each period</label><span class="val" id="slv-t"></span></div>
        <input type="range" class="plain" id="slv" min="50" max="99" step="1" value="${ST.sl}"><div class="ticks"><span>50% (plan = expected)</span><span>90% (P90)</span><span>99%</span></div></div>
        <div class="kpis" style="margin-top:12px" id="p-kpi"></div>
        <div class="chart" id="c-pr" style="margin-top:8px"></div></div>
      <div class="card"><h2>What safety costs</h2><p class="sub">Buffer stock for the whole selection and window as the service level rises. The last few points are the most expensive.</p><div class="chart" id="c-curve"></div></div>
    </div>
    <div class="card mt"><h2>Plan table</h2><p class="sub">P90 for a total is not the sum of each series' P90 (that would over-buy): totals use the calibrated method described in the report.</p><div class="tw" id="p-tab"></div></div>${foot()}`;
  onSeg("pby", k => { ST.pby = k; render(); }); onSeg("pper", k => { ST.pper = k; render(); });
  const plabel = (p, n) => ST.pper === "q" && n < 3 ? `${p} (${n} mo)` : p;
  const pr = mount("#c-pr", base({ xAxis: { ...base().xAxis, data: [] }, legend: base().legend, tooltip: { ...base().tooltip },
    series: [{ id: "e", name: "Expected", type: "bar", stack: "plan", data: [], itemStyle: { color: css("--fc") }, barMaxWidth: 26 },
      { id: "b", name: "Buffer", type: "bar", stack: "plan", data: [], itemStyle: { color: css("--fc-soft"), borderColor: css("--fc"), borderWidth: 1, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 26 }] }));
  const sls = Array.from({ length: 50 }, (_, k) => 50 + k);
  const curve = mount("#c-curve", base({ xAxis: { ...base().xAxis, data: sls.map(x => x + "%"), axisLabel: { color: css("--muted"), interval: 9 } }, grid: { left: 60, right: 18, top: 20, bottom: 30 },
    tooltip: { ...base().tooltip, formatter: ps => `Service level <b>${ps[0].axisValue}</b><br>buffer <b>${fmt(ps[0].value)}</b> units` },
    series: [{ id: "c", type: "line", data: [], symbol: "none", lineStyle: { color: css("--fc"), width: 2.4 }, areaStyle: { color: css("--fc"), opacity: 0.08 },
      markPoint: { symbol: "circle", symbolSize: 12, itemStyle: { color: css("--fc"), borderColor: css("--surface"), borderWidth: 2 }, label: { show: false }, data: [] } }] }));
  const P = planRows(idx, ST.pby, ST.pper);
  const update = () => {
    const z = probit(ST.sl / 100);
    P.rows.forEach(r => { r.plan = Math.max(0, r.expected + z * r.sd); r.buffer = r.plan - r.expected; r.value = r.buffer * r.price; });
    const E = sum(P.tot.map(t => t.e)), B = sum(P.tot.map(t => Math.max(0, z * t.sd))), V = sum(P.tot.map(t => Math.max(0, z * t.sd) * t.price));
    $("#slv-t").textContent = ST.sl + "%";
    $("#p-kpi").innerHTML = kpi("Plan for", fmtK(E + B), `expected ${fmtK(E)} units`, "fc") + kpi("Buffer above expected", fmtK(B), `${pct(B / (E || 1))} on top · worth ${money(V)}`, "neutral") +
      kpi("Chance a period runs short", ST.sl >= 99 ? "1 in 100" : `1 in ${Math.round(100 / (100 - ST.sl))}`, `${100 - ST.sl}% per ${ST.pper === "q" ? "quarter" : "month"}`, "neutral");
    pr.setOption({ xAxis: { data: P.tot.map(t => plabel(t.p, t.n)), axisLabel: { interval: ST.pper === "q" ? 0 : "auto", fontSize: 11, color: css("--muted") } }, series: [{ id: "e", data: P.tot.map(t => Math.round(t.e)) }, { id: "b", data: P.tot.map(t => Math.round(Math.max(0, z * t.sd))) }] });
    const sds = sum(P.tot.map(t => t.sd));
    curve.setOption({ series: [{ id: "c", data: sls.map(s => Math.round(Math.max(0, probit(s / 100)) * sds)), markPoint: { data: [{ coord: [ST.sl - 50, Math.round(Math.max(0, z) * sds)] }] } }] });
    $("#p-tab").innerHTML = `<table class="t"><tr><th>${ST.pby === "fam" ? "Family" : "Region"}</th><th>Period</th><th class="n">Expected</th><th class="n">Plan for (${ST.sl}%)</th><th class="n">Buffer</th><th class="n">Buffer value</th></tr>
      ${P.rows.map(r => `<tr><td>${esc(r.group)}</td><td class="mono">${r.period}</td><td class="n">${fmt(r.expected)}</td><td class="n"><b>${fmt(r.plan)}</b></td><td class="n">${fmt(r.buffer)}</td><td class="n">${money(r.value)}</td></tr>`).join("")}</table>`;
  };
  $("#slv").oninput = e => { ST.sl = +e.target.value; requestAnimationFrame(update); };
  update();
  $("#pdl").onclick = () => download(`procurement_plan_${ST.sl}pct.csv`, ["group", "period", "expected_units", `plan_units_${ST.sl}pct`, "buffer_units", "buffer_value_usd"],
    P.rows.map(r => [r.group, r.period, r.expected.toFixed(0), r.plan.toFixed(0), r.buffer.toFixed(0), r.value.toFixed(0)]));
};

RENDER.exceptions = (v, idx) => {
  const out = [], kinds = {};
  for (const i of idx) {
    if (D.f0[i] == null) continue;
    const last = lastN(i, "demand", 12), next = sum(D.exp[i].slice(0, Math.max(0, 12 - D.f0[i])));
    const p50 = sum(D.p50[i].slice(0, 12)), width = p50 > 0 ? (sum(D.p90[i].slice(0, 12)) - sum(D.p10[i].slice(0, 12))) / p50 : 0;
    let ba = 0, be = 0; const bt = D.bt.act[i]; if (bt && bt.length) for (let o = 0; o < bt.length; o++) for (let h = 0; h < 19; h++) { const a = bt[o][h]; if (a == null) continue; ba += a; be += Math.abs((D.bt.model[i][o][h] || 0) - a); }
    const s = D.skus[D.sku[i]], flags = [];
    const ch = last > 0 ? next / last - 1 : null;
    if (D.h0[i] == null) flags.push(["New product", "real", "launch"]);
    if (s.eol && s.eol <= D.fc_months[NF - 1]) flags.push(["Ends " + s.eol, "warn", "eol"]);
    if (ch != null && Math.abs(ch) > 0.4 && last > 100) flags.push([(ch > 0 ? "Jump " : "Drop ") + sgn(ch, 0), ch > 0 ? "ok" : "err", "change"]);
    if (width > 2.2) flags.push(["Wide range", "warn", "range"]);
    if (ba > 200 && be / ba > 0.6) flags.push(["Hard to forecast " + pct(be / ba, 0), "err", "hard"]);
    const src = D.src[i] || []; if (src.slice(-6).some(x => x >= 1)) flags.push(["Recent stockout / estimate", "loss", "stock"]);
    flags.forEach(f => kinds[f[2]] = (kinds[f[2]] || 0) + 1);
    if (flags.length) out.push({ i, flags, last, next, ch, width, score: flags.length * 1e6 + next });
  }
  ST.xk = ST.xk || "all";
  const KN = { launch: "New product", eol: "Ending", change: "Big change", range: "Wide range", hard: "Hard to forecast", stock: "Recent stockout" };
  const shown = out.filter(r => ST.xk === "all" || r.flags.some(f => f[2] === ST.xk)).sort((a, b) => b.score - a.score);
  v.innerHTML = head("plan", "Exception list",
    "The series a planner should look at first: big jumps or drops, wide ranges, poor past accuracy, recent stockouts, launches and end of life. Click a row to open its forecast.", "", idx) +
    `<div class="card"><div class="card-h"><div><h2>${fmt(shown.length)} series need a look</h2><p class="sub">Sorted by number of flags, then volume</p></div>
      <div class="chips" id="xk">${[["all", `All (${fmt(out.length)})`], ...Object.entries(KN).filter(([k]) => kinds[k]).map(([k, n]) => [k, `${n} (${fmt(kinds[k])})`])].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${ST.xk === k}">${l}</button>`).join("")}</div></div>
      <div class="tw" style="max-height:660px"><table class="t" id="ex"><tr><th>Series</th><th>Customer</th><th>Flags</th><th class="n">Last 12 months</th><th class="n">Next 12 (expected)</th><th class="n">Change</th></tr>
      ${shown.slice(0, 400).map(r => `<tr class="click" data-i="${r.i}"><td class="mono">${seriesName(r.i)}</td><td>${esc(D.customers[D.cust[r.i]])}</td><td>${r.flags.map(f => `<span class="pill ${f[1]}">${esc(f[0])}</span>`).join("")}</td>
        <td class="n">${fmt(r.last)}</td><td class="n">${fmt(r.next)}</td><td class="n ${r.ch >= 0 ? "up" : "down"}">${r.ch == null ? "–" : sgn(r.ch, 0)}</td></tr>`).join("")}</table></div></div>${foot()}`;
  $("#xk").onclick = e => { const b = e.target.closest(".chip"); if (b) { ST.xk = b.dataset.k; render(); } };
  $("#ex").onclick = e => { const tr = e.target.closest("tr.click"); if (tr) { const i = +tr.dataset.i; ST.f = { fam: String(D.fam[i]), sku: D.skuId[i], reg: String(D.reg[i]), ch: "", cust: "" }; go("forecast"); } };
};

RENDER.drivers = (v) => {
  const fams = [...new Set(DR.summary.map(r => r.family))].sort();
  const IND = ["New_Vehicle_Orders_Index", "ISM_Manufacturing_PMI", "Auto_Production_YoY_Pct", "Vehicle_Inventory_Days", "Industrial_Util_Rate_Pct", "Raw_Material_Cost_Index", "Fuel_Price_Index", "EV_Adoption_Index"];
  const IN = { New_Vehicle_Orders_Index: "New vehicle orders", ISM_Manufacturing_PMI: "Factory index (PMI)", Auto_Production_YoY_Pct: "Auto production growth", Vehicle_Inventory_Days: "Vehicle stock days", Industrial_Util_Rate_Pct: "Factory utilisation", Raw_Material_Cost_Index: "Raw material cost", Fuel_Price_Index: "Fuel price", EV_Adoption_Index: "EV adoption" };
  ST.dsel = ST.dsel || ["Hydraulic Systems", "New_Vehicle_Orders_Index"]; ST.dind = ST.dind || "New_Vehicle_Orders_Index";
  const ev = [...DR.events].filter(e => e.effect_demand_pct != null);
  v.innerHTML = head("data", "Macro lead / lag heatmap",
    "Does an economic indicator move before demand does? Each cell is the strongest correlation between the indicator's yearly change and the family's yearly demand growth, and how many months the indicator comes first. ● = the lead holds in both 2021–23 and 2024–26. Click a cell. Filters don't apply here.") +
    `<div class="card"><div class="chart tall" id="c-hm"></div></div>
    <div class="grid g2 mt">
      <div class="card"><h2 id="pt"></h2><p class="sub">Correlation when the indicator is moved 0–8 months earlier, in two periods</p><div class="chart" id="c-pf"></div><p class="note" id="pn"></p></div>
      <div class="card"><div class="card-h"><div><h2>Indicator over time</h2><p class="sub">Shaded: the forecast window (values given in the data)</p></div><select id="dind" class="btn">${IND.map(i => `<option value="${i}" ${i === ST.dind ? "selected" : ""}>${IN[i]}</option>`).join("")}</select></div><div class="chart" id="c-mi"></div></div>
    </div>
    <div class="card mt"><h2>Did calendar events move demand?</h2><p class="sub">Measured effect on demand against a fair comparison. Grey = inside the range that happens by chance (placebo test on 300 event-free months).</p><div class="tw"><table class="t"><tr><th>Event</th><th>When</th><th>Scope</th><th class="n">Effect</th><th>Verdict</th></tr>
      ${ev.map(e => `<tr><td>${esc(e.Description)}</td><td class="mono">${e.Start}${e.End && e.End !== e.Start ? " → " + e.End : ""}</td><td>${esc(e.Scope)}</td><td class="n ${Math.abs(e.effect_demand_pct) < (e.noise_band_pct || 11.7) ? "muted" : e.effect_demand_pct > 0 ? "up" : "down"}">${e.effect_demand_pct > 0 ? "+" : ""}${e.effect_demand_pct.toFixed(0)}%</td><td class="muted">${esc(e.verdict)}</td></tr>`).join("")}</table></div></div>${foot()}`;
  const data = [];
  fams.forEach((f, r) => IND.forEach((ind, c) => { const s = DR.summary.find(x => x.family === f && x.indicator === ind);
    if (s) data.push({ value: [c, r, +s.corr.toFixed(2), s.best_lag, s.stable ? 1 : 0], label: { color: s.corr > 0.6 ? "#ffffff" : css("--ink") } }); }));
  const hm = mount("#c-hm", base({ grid: { left: 180, right: 20, top: 10, bottom: 70 },
    tooltip: { trigger: "item", ...tip(), formatter: p => `${fams[p.value[1]]} vs ${IN[IND[p.value[0]]]}<br>correlation <b>${p.value[2]}</b> when the indicator comes ${p.value[3]} month(s) first<br>${p.value[4] ? "stable in both periods" : "not stable"}<br><i>click for detail</i>` },
    xAxis: { type: "category", data: IND.map(i => IN[i]), axisLabel: { color: css("--ink-2"), interval: 0, rotate: 20, fontSize: 11 }, axisTick: { show: false } },
    yAxis: { type: "category", data: fams, inverse: true, axisLabel: { color: css("--ink-2") }, axisTick: { show: false } },
    visualMap: { min: 0, max: 1, dimension: 2, show: false, inRange: { color: [css("--sunk"), css("--hist")] } },
    series: [{ type: "heatmap", data, label: { show: true, fontSize: 11, formatter: p => `${p.value[2].toFixed(2)}${p.value[4] ? " ●" : ""}\n${p.value[3]} mo ahead` },
      itemStyle: { borderColor: css("--surface"), borderWidth: 3, borderRadius: 6 } }] }));
  hm.on("click", p => { ST.dsel = [fams[p.value[1]], IND[p.value[0]]]; prof(); });
  const prof = () => {
    const [f, ind] = ST.dsel, rows = DR.leadlag.filter(r => r.family === f && r.indicator === ind).sort((a, b) => a.lag - b.lag), s = DR.summary.find(x => x.family === f && x.indicator === ind);
    $("#pt").textContent = `${f} vs ${IN[ind]}`;
    $("#pn").innerHTML = s.stable ? `Stable: both periods peak around ${s.early_lag}–${s.late_lag} months ahead.` : `Not stable: best lead ${s.early_lag} vs ${s.late_lag} months, correlation ${s.early_corr.toFixed(2)} vs ${s.late_corr.toFixed(2)}.`;
    mount("#c-pf", base({ xAxis: { ...base().xAxis, data: rows.map(r => r.lag + " mo") }, legend: base().legend, tooltip: { ...base().tooltip, valueFormatter: x => x.toFixed(2) },
      yAxis: { ...base().yAxis, min: -1, max: 1, axisLabel: { color: css("--muted"), formatter: x => x.toFixed(1) } },
      series: [{ name: "2021–2023", type: "line", data: rows.map(r => r.corr_early), lineStyle: { color: css("--muted"), width: 2 }, itemStyle: { color: css("--muted") } },
        { name: "2024–2026", type: "line", data: rows.map(r => r.corr_late), lineStyle: { color: css("--hist"), width: 2.4 }, itemStyle: { color: css("--hist") } }] }));
  };
  prof();
  const mi = () => mount("#c-mi", base({ xAxis: yearAxis(DR.macro.Date),
    yAxis: { ...base().yAxis, scale: true, axisLabel: { color: css("--muted") } }, tooltip: { ...base().tooltip, valueFormatter: x => x == null ? "–" : (+x).toFixed(1) },
    series: [{ name: IN[ST.dind], type: "line", data: DR.macro[ST.dind], symbol: "none", lineStyle: { color: css("--hist"), width: 2 }, itemStyle: { color: css("--hist") },
      markArea: { silent: true, itemStyle: { color: css("--fc"), opacity: 0.08 }, data: [[{ xAxis: D.fc_months[0] }, { xAxis: D.fc_months[NF - 1] }]] } }] }));
  mi(); $("#dind").onchange = e => { ST.dind = e.target.value; mi(); };
};

RENDER.download = (v, idx) => {
  v.innerHTML = head("data", "Data download",
    "Download what you see, for the current filters, as CSV. With no filters you get everything.", "", idx) +
    `<div class="grid g4">
      <div class="card"><h2>Forecast</h2><p class="sub">Every month × series: P10, P50, P90, expected units and revenue</p><button class="btn primary" id="d1">${icon("download")} forecast.csv</button></div>
      <div class="card"><h2>Submission format</h2><p class="sub">Date, SKU_ID, Region, Units_Sold_Forecast (P50, whole units)</p><button class="btn" id="d4">${icon("download")} forecast_submission_format.csv</button></div>
      <div class="card"><h2>History</h2><p class="sub">Every month × series: asked for, shipped, revenue</p><button class="btn" id="d2">${icon("download")} history.csv</button></div>
      <div class="card"><h2>Anomalies</h2><p class="sub">The anomaly register for this selection</p><button class="btn" id="d3">${icon("download")} anomalies.csv</button></div>
    </div>
    <div class="card mt"><h2>Documents</h2><div class="chips" style="margin-top:6px"><a class="btn" href="docs/report.pdf" target="_blank" rel="noopener">${icon("doc")} Report (PDF)</a></div></div>
    <div class="card mt"><h2>Files written by the pipeline</h2><p class="sub">In the code package, after <span class="mono">python run.py</span></p><div class="tw" style="max-height:none"><table class="t"><tr><th>File</th><th>What</th></tr>
      ${[["outputs/submission/submission.csv", "The competition submission (P50 for every test row)"], ["outputs/forecast/forecast.csv", "Every test row with P10 / P50 / P90, expected, price, revenue"],
        ["outputs/clean/sales_clean.csv", "Cleaned history, with what was changed on each row"], ["outputs/reports/anomaly_register.csv", "All flagged points"],
        ["outputs/reports/data_quality.json", "Every data issue with evidence and examples"], ["outputs/reports/backtest_all_methods.csv", "Replay-test scores of all methods"],
        ["outputs/reports/event_effects.csv", "Measured effect of every calendar event"], ["outputs/reports/macro_leadlag.csv", "Indicator vs family correlations at every lag"]]
        .map(r => `<tr><td class="mono">${r[0]}</td><td>${r[1]}</td></tr>`).join("")}</table></div></div>${foot()}`;
  $("#d1").onclick = () => downloadForecast(idx);
  $("#d4").onclick = () => { const rows = []; for (const i of idx) { const o = D.f0[i]; if (o == null) continue; D.p50[i].forEach((p, j) => rows.push([D.fc_months[o + j], D.skuId[i], D.regions[D.reg[i]], Math.max(0, Math.round(p))])); }
    rows.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0); download("forecast_submission_format.csv", ["Date", "SKU_ID", "Region", "Units_Sold_Forecast"], rows); };
  $("#d2").onclick = () => { const rows = []; for (const i of idx) { const o = D.h0[i]; if (o == null) continue; D.demand[i].forEach((x, j) => rows.push([D.months[o + j], D.skuId[i], D.regions[D.reg[i]], D.families[D.fam[i]], D.channels[D.ch[i]], D.customers[D.cust[i]], x ?? "", D.shipped[i][j] ?? "", D.revenue[i][j] ?? ""])); }
    download("history.csv", ["Date", "SKU_ID", "Region", "Product_Family", "Sales_Channel", "Customer_Name", "demand", "shipped", "revenue_usd"], rows); };
  $("#d3").onclick = () => { const s = new Set(idx); download("anomalies.csv", ["Date", "SKU_ID", "Region", "type", "kind", "shipped", "demand", "reason", "action"],
    A.rows.filter(r => s.has(r[0])).map(r => [D.months[r[1]], D.skuId[r[0]], D.regions[D.reg[r[0]]], A.types[r[2]], A.type_kind[A.types[r[2]]], r[3] ?? "", r[4] ?? "", r[6], A.actions[r[7]]])); };
};

/* ------------------------------------------------------------------ downloads */
function download(name, header, rows) {
  const q = x => { const s = String(x ?? ""); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const text = [header, ...rows].map(r => r.map(q).join(",")).join("\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = name;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  toast(`${name}: ${fmt(rows.length)} rows`);
}
function downloadForecast(idx) {
  const rows = [];
  for (const i of idx) { const o = D.f0[i]; if (o == null) continue;
    D.exp[i].forEach((e, j) => rows.push([D.fc_months[o + j], D.skuId[i], D.regions[D.reg[i]], D.families[D.fam[i]], D.channels[D.ch[i]], D.customers[D.cust[i]], D.p10[i][j], D.p50[i][j], D.p90[i][j], e, D.frev[i][j]])); }
  download("forecast.csv", ["Date", "SKU_ID", "Region", "Product_Family", "Sales_Channel", "Customer_Name", "p10", "p50", "p90", "expected", "revenue_expected_usd"], rows);
}

/* ------------------------------------------------------------------ finder and help */
function paletteItems(q) {
  q = q.trim().toLowerCase();
  const hit = s => !q || s.toLowerCase().includes(q);
  const out = [];
  VIEWS.forEach(([, id, name]) => { if (hit(name)) out.push(["View", name, "", () => go(id)]); });
  D.families.forEach((x, i) => { if (hit(x)) out.push(["Family", x, "filter", () => setFilters({ fam: String(i), sku: "" })]); });
  D.regions.forEach((x, i) => { if (hit(x)) out.push(["Region", x, "filter", () => setFilters({ reg: String(i) })]); });
  D.customers.forEach((x, i) => { if (hit(x)) out.push(["Customer", x, "filter", () => setFilters({ cust: String(i) })]); });
  D.channels.forEach((x, i) => { if (hit(x)) out.push(["Channel", x, "filter", () => setFilters({ ch: String(i) })]); });
  if (q) D.skus.forEach(s => { if (hit(s.id) || hit(s.desc)) out.push(["Product", `${s.id} · ${s.desc}`, s.family, () => setFilters({ sku: s.id, fam: String(D.families.indexOf(s.family)) })]); });
  return out.slice(0, 60);
}
function openPalette() {
  const box = $("#palette"), inp = $("#pal-q"), list = $("#pal-list"); let items = [], k = 0;
  const draw = () => { items = paletteItems(inp.value); k = Math.min(k, Math.max(0, items.length - 1)); let g = "";
    list.innerHTML = items.map((it, j) => { const h = it[0] !== g ? `<div class="hd">${it[0]}</div>` : ""; g = it[0];
      return `${h}<button data-j="${j}" class="${j === k ? "sel" : ""}">${esc(it[1])}<span class="k">${esc(it[2])}</span></button>`; }).join("") || `<div class="empty">Nothing matches “${esc(inp.value)}”.</div>`;
    const s = list.querySelector(".sel"); if (s) s.scrollIntoView({ block: "nearest" }); };
  const pick = j => { const it = items[j]; if (!it) return; close(); it[3](); };
  const close = () => { box.hidden = true; document.removeEventListener("keydown", keys, true); };
  const keys = e => { if (e.key === "Escape") { e.preventDefault(); close(); } else if (e.key === "ArrowDown") { e.preventDefault(); k = Math.min(items.length - 1, k + 1); draw(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); k = Math.max(0, k - 1); draw(); } else if (e.key === "Enter") { e.preventDefault(); pick(k); } };
  box.hidden = false; inp.value = ""; k = 0; draw(); inp.focus();
  inp.oninput = () => { k = 0; draw(); };
  list.onclick = e => { const b = e.target.closest("button"); if (b) pick(+b.dataset.j); };
  box.onclick = e => { if (e.target === box) close(); };
  document.addEventListener("keydown", keys, true);
}
function openHelp() {
  const box = $("#helpbox");
  $("#help-body").innerHTML = `<button class="x" id="hx" aria-label="Close">×</button><h2>How to read this</h2>
    <div class="legend-key"><span class="k"><i style="background:var(--hist)"></i>What customers asked for (history)</span><span class="k"><i style="background:var(--muted)"></i>What shipped</span>
      <span class="k"><i style="background:var(--fc)"></i>Forecast, expected</span><span class="k"><i class="dash" style="background:repeating-linear-gradient(90deg,var(--fc) 0 4px,transparent 4px 7px)"></i>Typical month (P50)</span><span class="k"><i style="background:var(--fc);opacity:.3;height:10px"></i>80% range (P10–P90)</span></div>
    <h3>How the app is organised</h3><ul>
      <li><b>Part 1 · Training data</b>: what happened, from the cleaned history 2019–2026 (with demand rebuilt where stock ran out).</li>
      <li><b>Part 2 · Prediction</b>: what will happen, from the forecast Nov 2026 – May 2028, with P10/P50/P90 ranges.</li>
      <li>The five filters at the top apply to every view. The address bar keeps them, so <b>Copy link</b> shares exactly what you see.</li>
      <li><b>Brief checklist</b> lists every requirement of the problem statement and links to where it is shown.</li></ul>
    <h3>Words used here</h3><ul>
      <li><b>Demand</b> = what customers asked for. In stockout months it is more than what shipped; the history was rebuilt so the forecast learns from demand, not shipments.</li>
      <li><b>P10 / P50 / P90</b>: 10% of months should fall below P10 and 10% above P90. P50 is the typical month; <b>expected</b> is the average, used for totals.</li>
      <li><b>WAPE</b>: total absolute error ÷ total demand. <b>Bias</b>: how much the forecast is too high (+) or too low (−) overall.</li>
      <li><b>Backtest</b>: forecasts made at four past dates using only what was known then, scored on the next 19 months.</li></ul>
    <h3>Charts</h3><ul><li>Hover for values. To zoom a timeline, drag the ends of the bar under it, or pinch / <kbd>Ctrl</kbd> + scroll on the chart; <b>Reset zoom</b> puts it back.</li></ul>
    <h3>Keys</h3><ul><li><kbd>/</kbd> find a product, customer, region or view · <kbd>?</kbd> this panel · <kbd>t</kbd> light / dark · <kbd>esc</kbd> close</li></ul>`;
  box.hidden = false;
  const close = () => { box.hidden = true; };
  $("#hx").onclick = close; box.onclick = e => { if (e.target === box) close(); };
}
function toggleTheme() {
  const r = document.documentElement;
  r.dataset.theme = r.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("celeris-theme", r.dataset.theme); } catch (e) { /* storage blocked */ }
  render();
}

/* ------------------------------------------------------------------ start */
(async function start() {
  // light by default; the light / dark switch is remembered in this browser
  try { const t = localStorage.getItem("celeris-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* storage blocked */ }
  try { const p = localStorage.getItem("celeris-part"); if (HIDDEN[p] !== undefined) ST.part = p; } catch (e) { /* storage blocked */ }
  try { await load(); } catch (e) {
    $("#view").innerHTML = `<div class="empty">Could not load the data files (${esc(e.message)}).<br>Run <code>python run.py export</code>, then serve this folder with <code>python -m http.server 8000 -d dashboard</code> and open <code>http://localhost:8000</code>.</div>`;
    return;
  }
  readHash();
  buildNav(); buildFilters(); render();
  $("#nav").onclick = e => { const b = e.target.closest("button"); if (b) go(b.dataset.v); };
  $("#parts").onclick = e => { const b = e.target.closest("button"); if (b) showPart(b.dataset.p); };
  $("#download").onclick = () => downloadForecast(sel());
  $("#theme").onclick = toggleTheme;
  $("#find").onclick = openPalette; $("#find2").onclick = openPalette;
  $("#help").onclick = openHelp;
  $("#menu").onclick = () => $("#rail").classList.toggle("open");
  $("#share").onclick = async () => { writeHash(); try { await navigator.clipboard.writeText(location.href); toast("Link copied: it opens this view with these filters"); } catch (e) { toast("Copy the address bar to share this view"); } };
  addEventListener("keydown", e => {
    if (e.target.closest("input,select,textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); openPalette(); } else if (e.key === "?") { e.preventDefault(); openHelp(); } else if (e.key === "t") toggleTheme();
    else if (e.key === "Escape") $("#helpbox").hidden = true;
  });
  addEventListener("hashchange", () => { const before = ST.view + JSON.stringify(ST.f); readHash(); if (ST.view + JSON.stringify(ST.f) !== before) { buildNav(); render(); } });
  if (!window.ResizeObserver) { let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => ST.charts.forEach(c => c.resize()), 120); }); }
})();
