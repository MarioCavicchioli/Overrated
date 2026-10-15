"""Local map of the Scout's candidates and live feed, read from Supabase.

Run: .venv/bin/uvicorn data.viewer:app --host 127.0.0.1 --port 8100   (then open http://127.0.0.1:8100)

For the team to see real rows before the Lovable app is wired up. It binds to 127.0.0.1 only and is
not behind the ngrok tunnel: the rows are VOA-derived and must not be published. The service keys
stay in this process; the page only calls the /api routes below.
"""
import os

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for line in open(os.path.join(ROOT, ".env")):
    k, _, v = line.strip().partition("=")
    if k and not k.startswith("#") and v:
        os.environ.setdefault(k, v)
BASE = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
HEADERS = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
DATA = "http://127.0.0.1:8000"  # the data service, called locally (not through the tunnel)

app = FastAPI(title="Overrated local viewer")
_shops = {}


def get(table, **params):
    r = httpx.get(f"{BASE}/{table}", headers=HEADERS, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


@app.get("/api/run")
def latest_run():
    rows = get("scout_runs", select="*", order="created_at.desc", limit="1")
    return rows[0] if rows else {}


@app.get("/api/candidates")
def candidates(run_id: str):
    return get("candidates", run_id=f"eq.{run_id}", order="score.desc", select=(
        "uarn,address,postcode,latitude,longitude,property_use,rv,zone_a_price,street_median,"
        "street_spread,z_score,est_saving,score,flags,status,street_key,evidence_refs"))


@app.get("/api/shops")
def shops(area_code: str):
    """Every shop the Scout scored in the area, as faint context dots (cached; the list doesn't change)."""
    if area_code not in _shops:
        r = httpx.get(f"{DATA}/scan", timeout=60, headers={"X-API-Key": os.environ["DATA_SERVICE_KEY"]},
                      params={"ba_code": area_code.removeprefix("ba"), "limit": 5000})
        r.raise_for_status()
        _shops[area_code] = [[s["latitude"], s["longitude"]] for s in r.json()["shops"] if s.get("latitude")]
    return _shops[area_code]


@app.get("/api/events")
def events(limit: int = 30):
    return get("events", select="ts,agent,kind,message,tokens,cost_usd", order="ts.desc", limit=str(limit))


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Overrated · Scout</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    color-scheme: dark;
    --bg: #0b0c0f; --panel: rgba(18, 20, 25, .86); --panel-solid: #121419; --raise: #1a1d24;
    --line: rgba(255, 255, 255, .07); --line-strong: rgba(255, 255, 255, .14);
    --ink: #f2f3f5; --ink-2: #b4b9c4; --ink-3: #747b88;
    /* categorical slots 1-3 (dark steps), in fixed order: clean outlier, priced run, threshold */
    --s1: #3987e5; --s2: #d95926; --s3: #199e70;
    --radius: 14px;
  }
  * { box-sizing: border-box }
  html, body { height: 100%; margin: 0; background: var(--bg); color: var(--ink);
    font: 13px/1.45 Inter, system-ui, sans-serif; -webkit-font-smoothing: antialiased }
  .num { font-family: "JetBrains Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; letter-spacing: -.01em }
  .app { display: grid; grid-template-columns: 360px 1fr; height: 100vh }

  /* sidebar */
  aside { background: var(--panel-solid); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0 }
  .brand { padding: 18px 20px 14px; border-bottom: 1px solid var(--line) }
  .brand .logo { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15px; letter-spacing: -.01em }
  .brand .logo i { width: 22px; height: 22px; border-radius: 7px; background: linear-gradient(135deg, var(--s1), #7c5cff);
    box-shadow: 0 0 18px rgba(57, 135, 229, .45) }
  .run { margin-top: 10px; display: flex; align-items: center; gap: 8px; color: var(--ink-2) }
  .run b { color: var(--ink); font-weight: 600 }
  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 99px; font-size: 11px; font-weight: 600;
    background: rgba(255, 255, 255, .06); color: var(--ink-2); border: 1px solid var(--line) }
  .pill .d { width: 6px; height: 6px; border-radius: 50% }
  .pill.done .d { background: #0ca30c } .pill.live .d { background: #fab219; animation: blink 1.2s infinite }
  @keyframes blink { 50% { opacity: .25 } }

  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line); border-bottom: 1px solid var(--line) }
  .stat { background: var(--panel-solid); padding: 14px 20px }
  .stat .v { font-size: 22px; font-weight: 600 }
  .stat .l { color: var(--ink-3); font-size: 11px; text-transform: uppercase; letter-spacing: .07em; margin-top: 2px }
  .stat.hero { grid-column: span 2 }
  .stat.hero .v { font-size: 30px; letter-spacing: -.02em }

  .filters { display: flex; gap: 6px; padding: 14px 20px 10px; flex-wrap: wrap }
  .chip { display: inline-flex; align-items: center; gap: 7px; padding: 6px 11px; border-radius: 99px; cursor: pointer; user-select: none;
    border: 1px solid var(--line-strong); color: var(--ink-2); background: transparent; font: inherit; font-weight: 500 }
  .chip .d { width: 8px; height: 8px; border-radius: 50% }
  .chip .c { color: var(--ink-3) }
  .chip[aria-pressed="false"] { opacity: .45 }
  .chip:hover { border-color: rgba(255, 255, 255, .3) }

  .list { overflow: auto; flex: 1; min-height: 0; padding: 0 10px 14px }
  .item { display: grid; grid-template-columns: 10px 1fr auto; gap: 4px 12px; align-items: center; padding: 10px; border-radius: 10px; cursor: pointer }
  .item:hover { background: var(--raise) } .item.on { background: rgba(57, 135, 229, .12); box-shadow: inset 0 0 0 1px rgba(57, 135, 229, .35) }
  .item .d { width: 8px; height: 8px; border-radius: 50%; grid-row: span 2 }
  .item .a { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
  .item .s { color: var(--ink); font-weight: 600; text-align: right }
  .item .m { color: var(--ink-3); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
  .item .z { color: var(--ink-3); font-size: 12px; text-align: right }

  /* map */
  .stage { position: relative; min-width: 0 }
  #map { position: absolute; inset: 0; background: #0b0c0f }
  .leaflet-container { font: inherit }
  .leaflet-control-attribution { background: rgba(11, 12, 15, .7) !important; color: var(--ink-3) !important }
  .leaflet-control-attribution a { color: var(--ink-2) !important }
  .leaflet-bar a { background: var(--panel-solid) !important; color: var(--ink) !important; border-color: var(--line) !important }

  .base { filter: brightness(.62) contrast(1.15) saturate(.6) }
  .ctx { fill: #8a93a6; fill-opacity: .28; stroke: none }
  .mk { position: relative; border-radius: 50%; background: var(--c); box-shadow: 0 0 0 2px rgba(11, 12, 15, .9), 0 0 12px 1px color-mix(in srgb, var(--c) 55%, transparent);
    transition: transform .15s ease }
  .mk:hover, .mk.on { transform: scale(1.25) }
  .mk.on { box-shadow: 0 0 0 2px #fff, 0 0 22px 4px color-mix(in srgb, var(--c) 75%, transparent) }
  .mk.pulse::after { content: ""; position: absolute; inset: -6px; border-radius: 50%; border: 2px solid var(--c); animation: ring 2.4s ease-out infinite }
  @keyframes ring { from { transform: scale(.6); opacity: .9 } to { transform: scale(1.8); opacity: 0 } }

  .leaflet-tooltip.tip { background: var(--panel-solid); color: var(--ink); border: 1px solid var(--line-strong); border-radius: 10px;
    padding: 8px 11px; box-shadow: 0 10px 30px rgba(0, 0, 0, .5); font: inherit }
  .leaflet-tooltip.tip::before { display: none }
  .tip b { display: block; font-weight: 600 } .tip span { color: var(--ink-2) }

  /* floating cards */
  .card { position: absolute; z-index: 500; background: var(--panel); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border: 1px solid var(--line-strong); border-radius: var(--radius); box-shadow: 0 18px 50px rgba(0, 0, 0, .45) }
  .detail { top: 16px; right: 16px; width: 320px; padding: 18px; display: none }
  .detail.show { display: block; animation: rise .2s ease }
  @keyframes rise { from { opacity: 0; transform: translateY(6px) } }
  .detail .x { position: absolute; top: 12px; right: 12px; background: none; border: 0; color: var(--ink-3); font-size: 18px; cursor: pointer }
  .detail h3 { margin: 0 24px 2px 0; font-size: 16px; letter-spacing: -.01em }
  .detail .sub { color: var(--ink-3); font-size: 12px }
  .detail .save { margin: 16px 0 4px; font-size: 28px; font-weight: 600; letter-spacing: -.02em }
  .detail .save + .l { color: var(--ink-3); font-size: 11px; text-transform: uppercase; letter-spacing: .07em }
  .bars { margin: 16px 0 12px; display: grid; gap: 8px }
  .bar .t { display: flex; justify-content: space-between; color: var(--ink-2); font-size: 12px; margin-bottom: 4px }
  .bar .track { height: 8px; border-radius: 4px; background: rgba(255, 255, 255, .05) }
  .bar .fill { height: 100%; border-radius: 4px }
  .facts { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-top: 12px }
  .facts > div { background: rgba(255, 255, 255, .04); border-radius: 9px; padding: 8px 10px }
  .facts .v { font-weight: 600 } .facts .l { color: var(--ink-3); font-size: 11px }
  .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px }

  .feed { left: 16px; bottom: 16px; width: 380px; max-height: 42%; display: flex; flex-direction: column }
  .feed header { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px 8px }
  .feed header b { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-2) }
  .feed .rows { overflow: auto; padding: 0 6px 8px }
  .ev { display: grid; grid-template-columns: 52px 1fr; gap: 10px; padding: 7px 8px; border-radius: 8px }
  .ev.new { animation: rise .35s ease, flash 1.6s ease }
  @keyframes flash { from { background: rgba(57, 135, 229, .18) } to { background: transparent } }
  .ev .t { color: var(--ink-3); font-size: 11px; padding-top: 1px }
  .ev .k { font-weight: 600; font-size: 12px } .ev .k em { font-style: normal; color: var(--ink-3); font-weight: 500 }
  .ev .msg { color: var(--ink-2); font-size: 12px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical }

  @media (max-width: 1100px) {
    .app { grid-template-columns: 300px 1fr }
    .feed { width: 300px; max-height: 34% } .detail { width: 290px }
  }
  @media (max-width: 760px) {
    .app { grid-template-columns: 1fr; grid-template-rows: 58vh auto; height: auto }
    .stage { order: -1; height: 58vh } aside { border-right: 0; border-top: 1px solid var(--line) }
    .detail { left: 10px; right: 10px; top: auto; bottom: 10px; width: auto; padding: 14px; max-height: 70%; overflow: auto }
    .detail .save { margin-top: 10px; font-size: 24px } .bars { margin: 10px 0 8px }
    .feed { display: none }
  }
</style></head><body>
<div class="app">
  <aside>
    <div class="brand">
      <div class="logo"><i></i>Overrated <span style="color:var(--ink-3);font-weight:500">· Scout</span></div>
      <div class="run"><b id="area">Loading…</b><span class="pill" id="status"><span class="d"></span><span></span></span></div>
    </div>
    <div class="stats">
      <div class="stat hero"><div class="v num" id="saving">£0</div><div class="l">Estimated saving to March 2029</div></div>
      <div class="stat"><div class="v num" id="checked">0</div><div class="l">Shops checked</div></div>
      <div class="stat"><div class="v num" id="flagged">0</div><div class="l">Candidates</div></div>
    </div>
    <div class="filters" id="filters"></div>
    <div class="list" id="list"></div>
  </aside>
  <div class="stage">
    <div id="map"></div>
    <div class="card detail" id="detail"></div>
    <div class="card feed"><header><b>Live agent feed</b><span class="pill live"><span class="d"></span>live</span></header><div class="rows" id="feed"></div></div>
  </div>
</div>
<script>
const KINDS = {
  clean: { label: 'Clean outlier', c: 'var(--s1)', hex: '#3987e5' },
  run:   { label: 'Priced run',    c: 'var(--s2)', hex: '#d95926' },
  thr:   { label: 'Just over £51k', c: 'var(--s3)', hex: '#199e70' },
};
const shown = { clean: true, run: true, thr: true };
const map = L.map('map', { zoomControl: false, maxZoom: 18 }).setView([51.55, -0.07], 13);
L.control.zoom({ position: 'bottomright' }).addTo(map);
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/';
L.tileLayer(ESRI + 'World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', { maxNativeZoom: 16, maxZoom: 18, className: 'base',
  attribution: 'Basemap &copy; Esri, HERE, Garmin, OpenStreetMap contributors' }).addTo(map);
map.createPane('labels'); map.getPane('labels').style.zIndex = 450; map.getPane('labels').style.pointerEvents = 'none';
L.tileLayer(ESRI + 'World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', { maxNativeZoom: 16, maxZoom: 18, pane: 'labels', opacity: .7 }).addTo(map);
const ctxLayer = L.layerGroup().addTo(map), mkLayer = L.layerGroup().addTo(map);
let cands = [], markers = {}, selected = null, seen = new Set(), fitted = false, fitPts = [], touched = false;
// Fit to the candidates once the map has a real size, and again on resize until the user moves the map.
function fit() {
  map.invalidateSize();
  if (!fitPts.length || document.getElementById('map').clientHeight < 80) return;
  map.fitBounds(fitPts, { padding: [36, 36], maxZoom: 16, animate: false });
}
new ResizeObserver(() => { if (!touched) fit(); else map.invalidateSize(); }).observe(document.getElementById('map'));
['mousedown', 'wheel', 'touchstart'].forEach(ev => document.getElementById('map').addEventListener(ev, () => { touched = true; }, { passive: true }));

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
const gbp = n => '£' + Math.round(Number(n) || 0).toLocaleString('en-GB');
const title = s => String(s || '').toLowerCase().replace(/\b([a-z])/g, m => m.toUpperCase()).replace(/\b(Bst|Gnd|Grd|Flr|Flrs)\b/g, m => m.toUpperCase()).replace(/\b(And|At|Of|Within)\b/g, m => m.toLowerCase());
const use = s => { const t = String(s || '').toLowerCase(); return t.charAt(0).toUpperCase() + t.slice(1); };
const kindOf = c => !c.flags.includes('zone_a_above_tone') ? 'thr' : c.flags.includes('segment_cluster') ? 'run' : 'clean';
function nice(addr) {
  // "BST & GRD FLR 223, SHOREDITCH HIGH STREET, LONDON" -> "223 Shoreditch High Street", sub "BST & GRD FLR 223"
  const parts = String(addr).split(',').map(s => s.trim()).filter(p => p && p !== 'LONDON');
  if (parts.length < 2) return { main: title(parts[0]), sub: '' };
  const first = parts[0], tokens = first.replace(/[()]/g, ' ').split(/\s+/).filter(t => /\d/.test(t));
  const num = tokens.length ? tokens[tokens.length - 1] : '';
  const main = (num ? num + ' ' : '') + title(parts[1]);
  return { main, sub: first === num ? '' : title(first) };
}
const ago = ts => { const s = (Date.now() - new Date(ts)) / 1000;
  return s < 60 ? 'now' : s < 3600 ? Math.floor(s / 60) + 'm ago' : s < 86400 ? Math.floor(s / 3600) + 'h ago' : Math.floor(s / 86400) + 'd ago'; };
const human = k => ({ street_scored: 'Street scored', checkpoint: 'Checkpoint', run_started: 'Run started', run_finished: 'Run finished',
  fanout_skipped: 'Fan-out skipped', playbook_default: 'Default playbook', session_started: 'Session started',
  session_finished: 'Session finished', batch_started: 'Batch started' }[k] || title(String(k).replace(/_/g, ' ')));

async function j(u) { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); }

function filters() {
  $('filters').innerHTML = Object.entries(KINDS).map(([k, v]) => {
    const n = cands.filter(c => kindOf(c) === k).length;
    return `<button class="chip" data-k="${k}" aria-pressed="${shown[k]}"><span class="d" style="background:${v.c}"></span>${v.label} <span class="c num">${n}</span></button>`;
  }).join('');
  $('filters').querySelectorAll('.chip').forEach(b => b.onclick = () => { shown[b.dataset.k] = !shown[b.dataset.k]; render(); });
}

function size(c) { return Math.round(9 + Math.min(11, Math.sqrt(Number(c.est_saving) || 0) / 10)); }

function render() {
  filters();
  mkLayer.clearLayers(); markers = {};
  const vis = cands.filter(c => shown[kindOf(c)]);
  const cleanTop = new Set(cands.filter(c => kindOf(c) === 'clean').slice(0, 3).map(c => c.uarn));
  vis.forEach((c, i) => {
    if (!c.latitude) return;
    const k = kindOf(c), s = size(c);
    // shops that share a postcode centroid get a small deterministic offset so each stays clickable
    const lat = Number(c.latitude) + ((i * 7) % 5 - 2) * 0.00007, lng = Number(c.longitude) + ((i * 3) % 5 - 2) * 0.0001;
    const icon = L.divIcon({ className: '', iconSize: [s, s],
      html: `<div class="mk ${cleanTop.has(c.uarn) ? 'pulse' : ''} ${selected === c.uarn ? 'on' : ''}" style="--c:${KINDS[k].hex};width:${s}px;height:${s}px"></div>` });
    const n = nice(c.address);
    const m = L.marker([lat, lng], { icon, riseOnHover: true })
      .bindTooltip(`<b>${esc(n.main)}</b><span class="num">${gbp(c.zone_a_price)}/m² vs ${gbp(c.street_median)}</span> · <span class="num">${gbp(c.est_saving)}</span>`,
        { className: 'tip', direction: 'top', offset: [0, -s / 2 - 4] })
      .on('click', () => select(c.uarn, true)).addTo(mkLayer);
    markers[c.uarn] = m;
  });
  $('list').innerHTML = vis.map(c => {
    const n = nice(c.address), k = kindOf(c);
    return `<div class="item ${selected === c.uarn ? 'on' : ''}" data-u="${c.uarn}">
      <span class="d" style="background:${KINDS[k].c}"></span>
      <div class="a">${esc(n.main)}</div><div class="s num">${gbp(c.est_saving)}</div>
      <div class="m">${esc(use(c.property_use))}</div><div class="z num">z ${Number(c.z_score).toFixed(1)}</div></div>`;
  }).join('');
  $('list').querySelectorAll('.item').forEach(el => el.onclick = () => select(el.dataset.u, true));
}

function select(uarn, fly) {
  selected = uarn; const c = cands.find(x => x.uarn === uarn); if (!c) return;
  render();
  const k = kindOf(c), n = nice(c.address), hi = Math.max(Number(c.zone_a_price), Number(c.street_median));
  const pct = v => (100 * Number(v) / hi).toFixed(1) + '%';
  const over = ((Number(c.zone_a_price) / Number(c.street_median) - 1) * 100).toFixed(0);
  $('detail').innerHTML = `<button class="x" aria-label="Close" onclick="closeDetail()">×</button>
    <h3>${esc(n.main)}</h3><div class="sub">${esc(n.sub ? n.sub + ' · ' : '')}${esc(use(c.property_use))} · ${esc(c.postcode)}</div>
    <div class="save num">${gbp(c.est_saving)}</div><div class="l">Estimated saving to March 2029</div>
    <div class="bars">
      <div class="bar"><div class="t"><span>This shop, Zone A</span><span class="num">${gbp(c.zone_a_price)}/m²</span></div>
        <div class="track"><div class="fill" style="width:${pct(c.zone_a_price)};background:${KINDS[k].c}"></div></div></div>
      <div class="bar"><div class="t"><span>Similar shops on its street</span><span class="num">${gbp(c.street_median)}/m²</span></div>
        <div class="track"><div class="fill" style="width:${pct(c.street_median)};background:#5b6270"></div></div></div>
    </div>
    <div class="facts"><div><div class="v num">+${over}%</div><div class="l">above street</div></div>
      <div><div class="v num">${Number(c.z_score).toFixed(1)}</div><div class="l">spreads above</div></div>
      <div><div class="v num">${gbp(c.rv)}</div><div class="l">rateable value</div></div></div>
    <div class="tags"><span class="pill"><span class="d" style="background:${KINDS[k].c}"></span>${KINDS[k].label}</span>
      <span class="pill">${esc(title(c.status))}</span></div>`;
  $('detail').classList.add('show');
  const m = markers[uarn];
  if (m && fly) map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 16), { duration: .8 });
  // scroll only the list, never the page, so the detail card stays in view
  const row = document.querySelector(`.item[data-u="${uarn}"]`), list = $('list');
  if (row && list.scrollHeight > list.clientHeight) list.scrollTop = row.offsetTop - list.offsetTop - list.clientHeight / 3;
}
function closeDetail() { selected = null; $('detail').classList.remove('show'); render(); }

async function load() {
  const run = await j('/api/run'); if (!run.run_id) { $('area').textContent = 'No runs yet'; return; }
  $('area').textContent = run.area_name;
  const st = $('status'); st.className = 'pill ' + (run.status === 'completed' ? 'done' : 'live');
  st.lastElementChild.textContent = run.status;
  $('checked').textContent = (run.properties_checked || 0).toLocaleString('en-GB');
  cands = await j('/api/candidates?run_id=' + run.run_id);
  $('flagged').textContent = cands.length;
  $('saving').textContent = gbp(cands.reduce((a, c) => a + Number(c.est_saving || 0), 0));
  render();
  if (!fitted) {
    fitPts = cands.filter(c => c.latitude).map(c => [Number(c.latitude), Number(c.longitude)]);
    fit(); fitted = true;
    j('/api/shops?area_code=' + encodeURIComponent(run.area_code)).then(pts => {
      const r = L.svg({ padding: .5 });
      pts.forEach(p => L.circleMarker(p, { renderer: r, radius: 2.2, className: 'ctx', interactive: false }).addTo(ctxLayer));
    }).catch(() => {});
  }
}

async function feed() {
  const ev = await j('/api/events?limit=30');
  $('feed').innerHTML = ev.filter(e => e.kind !== 'checkpoint').map(e => {
    const key = e.ts + e.kind + e.message, fresh = seen.size && !seen.has(key); seen.add(key);
    return `<div class="ev ${fresh ? 'new' : ''}"><div class="t">${ago(e.ts)}</div>
      <div><div class="k">${esc(human(e.kind))} <em>· ${esc(e.agent)}</em></div><div class="msg">${esc(e.message)}</div></div></div>`;
  }).join('');
}

const quiet = f => () => f().catch(() => {});  // a missed poll (e.g. server restarting) just waits for the next one
quiet(load)(); quiet(feed)(); setInterval(quiet(feed), 5000); setInterval(quiet(load), 20000);
</script></body></html>"""
