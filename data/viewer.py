"""Local map of the Scout's candidates and live feed, read from Supabase.

Run: .venv/bin/uvicorn data.viewer:app --host 127.0.0.1 --port 8100   (then open http://127.0.0.1:8100)

For the team to see real rows before the Lovable app is wired up. It binds to 127.0.0.1 only and is
not behind the ngrok tunnel: the rows are VOA-derived and must not be published. The service key
stays in this process; the page only calls the three /api routes below.
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

app = FastAPI(title="Overrated local viewer")


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
        "street_spread,z_score,est_saving,score,flags,status,street_key"))


@app.get("/api/events")
def events(limit: int = 25):
    return get("events", select="ts,agent,kind,message,tokens,cost_usd", order="ts.desc", limit=str(limit))


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Overrated: Scout map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --text:#e7e9ee; --muted:#9aa3b2;
          --clean:#e5484d; --run:#f5a524; --thr:#3e8ef7; }
  * { box-sizing:border-box } body { margin:0; font:14px/1.4 system-ui, sans-serif; background:var(--bg); color:var(--text) }
  header { display:flex; gap:24px; align-items:baseline; padding:12px 16px; border-bottom:1px solid var(--line); flex-wrap:wrap }
  header h1 { font-size:16px; margin:0 } .stat b { font-size:18px } .stat span { color:var(--muted); margin-left:4px }
  main { display:grid; grid-template-columns: 1fr 380px; height: calc(100vh - 54px) }
  #map { height:100% } aside { overflow:auto; border-left:1px solid var(--line); background:var(--panel) }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:14px 14px 6px }
  .row { padding:8px 14px; border-bottom:1px solid var(--line); cursor:pointer } .row:hover { background:#1e222b }
  .row .a { font-weight:600 } .row .m { color:var(--muted); font-size:12px }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px }
  .legend span { margin-right:12px; color:var(--muted); font-size:12px }
  .ev { padding:6px 14px; border-bottom:1px solid var(--line); font-size:12px } .ev .k { color:var(--run) } .ev .t { color:var(--muted) }
  @media (max-width: 800px) { main { grid-template-columns:1fr; grid-template-rows: 55vh auto; height:auto } }
</style></head><body>
<header><h1>Overrated · Scout map</h1>
  <div class="stat"><b id="area">…</b><span id="status"></span></div>
  <div class="stat"><b id="checked">0</b><span>shops checked</span></div>
  <div class="stat"><b id="flagged">0</b><span>candidates</span></div>
  <div class="stat"><b id="saving">£0</b><span>est. saving to Mar 2029</span></div>
  <div class="legend"><span><i class="dot" style="background:var(--clean)"></i>clean outlier</span>
    <span><i class="dot" style="background:var(--run)"></i>priced run</span>
    <span><i class="dot" style="background:var(--thr)"></i>just over £51k</span></div>
</header>
<main><div id="map"></div>
<aside><h2>Candidates, best first</h2><div id="list"></div><h2>Live agent feed</h2><div id="feed"></div></aside></main>
<script>
const map = L.map('map').setView([51.55, -0.075], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
const layer = L.layerGroup().addTo(map); const markers = {};
const gbp = n => '£' + Math.round(n || 0).toLocaleString('en-GB');
const kind = c => c.flags.includes('segment_cluster') ? 'run' : c.flags.includes('zone_a_above_tone') ? 'clean' : 'thr';
const colour = k => getComputedStyle(document.documentElement).getPropertyValue('--' + k);
const esc = s => String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
async function j(u) { const r = await fetch(u); return r.json(); }
async function load() {
  const run = await j('/api/run'); if (!run.run_id) return;
  document.getElementById('area').textContent = run.area_name;
  document.getElementById('status').textContent = run.status;
  document.getElementById('checked').textContent = (run.properties_checked || 0).toLocaleString('en-GB');
  const cs = await j('/api/candidates?run_id=' + run.run_id);
  document.getElementById('flagged').textContent = cs.length;
  document.getElementById('saving').textContent = gbp(cs.reduce((a, c) => a + Number(c.est_saving || 0), 0));
  layer.clearLayers(); const list = document.getElementById('list'); list.innerHTML = ''; const pts = [];
  cs.forEach((c, i) => {
    const k = kind(c);
    const pop = `<b>${esc(c.address)}</b><br>${esc(c.property_use)}<br>Zone A £${c.zone_a_price}/m² vs segment median £${c.street_median}` +
      `<br>z = ${Number(c.z_score).toFixed(2)} · RV ${gbp(c.rv)} · est. saving ${gbp(c.est_saving)}<br>${esc(c.flags.join(', '))} · ${esc(c.status)}`;
    if (c.latitude) {
      // jitter shops that share a postcode centroid so they don't sit on one point
      const lat = Number(c.latitude) + (i % 5 - 2) * 0.00008, lng = Number(c.longitude) + (Math.floor(i / 5) % 5 - 2) * 0.00012;
      markers[c.uarn] = L.circleMarker([lat, lng], {radius: 6 + Math.min(10, Math.sqrt(c.est_saving || 0) / 12),
        color: colour(k), fillColor: colour(k), fillOpacity: .75, weight: 1}).bindPopup(pop).addTo(layer);
      pts.push([lat, lng]);
    }
    const row = document.createElement('div'); row.className = 'row';
    row.innerHTML = `<div class="a"><i class="dot" style="background:${colour(k)}"></i>${esc(c.address)}</div>` +
      `<div class="m">£${c.zone_a_price} vs £${c.street_median} · z ${Number(c.z_score).toFixed(2)} · ${gbp(c.est_saving)}</div>`;
    row.onclick = () => { const m = markers[c.uarn]; if (m) { map.setView(m.getLatLng(), 17); m.openPopup(); } };
    list.appendChild(row);
  });
  if (pts.length && !load.fitted) { map.fitBounds(pts, {padding: [30, 30]}); load.fitted = true; }
}
async function feed() {
  const ev = await j('/api/events?limit=25');
  document.getElementById('feed').innerHTML = ev.map(e => `<div class="ev"><span class="t">${new Date(e.ts).toLocaleTimeString('en-GB')}</span> ` +
    `<span class="k">${esc(e.agent)} · ${esc(e.kind)}</span><br>${esc(e.message)}</div>`).join('');
}
load(); feed(); setInterval(feed, 5000); setInterval(load, 15000);
</script></body></html>"""
