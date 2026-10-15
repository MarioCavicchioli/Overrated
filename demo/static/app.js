/*
 * Overrated customer app. Plain ES2020, no framework, no build step.
 *
 * Screens (hash routes): #/ search, #/p/{uarn} property, #/job/{job_id} live pipeline,
 * #/case/{case_id} case tracker, #/area Hackney sweep.
 * Every number comes from the local API (/api/...), which reads the data service and Supabase.
 * Development only: add ?mock=1 to the URL to use the synthetic fixtures at the bottom of this file.
 */
'use strict';

(function () {
  const MOCK = new URLSearchParams(location.search).get('mock') === '1';
  const POLL_MS = 1500;
  const CASE_POLL_MS = 5000;
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const UARN_RE = /^\d{1,15}$/;
  const TILE_BASE = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}';
  const TILE_LABELS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}';
  const TILE_ATTRIBUTION = 'Esri, HERE, Garmin, OpenStreetMap contributors';

  // ---------------------------------------------------------------------------
  // HTML escaping. html`...` escapes every interpolated value unless it is raw().
  // ---------------------------------------------------------------------------
  const RAW = Symbol('raw');
  const raw = (s) => ({ [RAW]: String(s) });
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
  function part(v) {
    if (v === null || v === undefined || v === false || v === true) return '';
    if (Array.isArray(v)) return v.map(part).join('');
    if (typeof v === 'object' && RAW in v) return v[RAW];
    return esc(v);
  }
  function html(strings, ...vals) {
    let out = strings[0];
    for (let i = 0; i < vals.length; i++) out += part(vals[i]) + strings[i + 1];
    return raw(out);
  }
  function setHTML(el, tpl) { if (el) el.innerHTML = part(tpl); }

  // ---------------------------------------------------------------------------
  // Formatting
  // ---------------------------------------------------------------------------
  const NF0 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
  const NF1 = new Intl.NumberFormat('en-GB', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const NF2 = new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const DF = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  const TF = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const MINUS = '−';
  const isNum = (v) => v !== null && v !== undefined && v !== '' && !Array.isArray(v) && Number.isFinite(Number(v));
  const int = (v) => (isNum(v) ? NF0.format(Math.round(Number(v))) : '–');
  const f1 = (v) => (isNum(v) ? NF1.format(Number(v)) : '–');
  const f2 = (v) => (isNum(v) ? NF2.format(Number(v)) : '–');
  function money(v) {
    if (!isNum(v)) return '–';
    const n = Math.round(Number(v));
    return (n < 0 ? MINUS : '') + '£' + NF0.format(Math.abs(n));
  }
  const signedMoney = (v) => (isNum(v) && Math.round(v) > 0 ? '+' : '') + money(v);
  function ppm(v) { // £ per m2: no decimals unless needed
    if (!isNum(v)) return '–';
    const n = Number(v), a = Math.abs(n);
    const s = Math.abs(a - Math.round(a)) < 0.005 ? NF0.format(Math.round(a)) : NF2.format(a);
    return (n < 0 ? MINUS : '') + '£' + s;
  }
  const signedPpm = (v) => (isNum(v) && Number(v) > 0 ? '+' : '') + ppm(v);
  const pct = (v) => (isNum(v) ? NF0.format(Math.round(Number(v))) + '%' : '–');
  const signedPct = (v) => (isNum(v) ? (Math.round(v) > 0 ? '+' : Math.round(v) < 0 ? MINUS : '') + NF0.format(Math.abs(Math.round(v))) + '%' : '–');
  function mult(m) { // 0.382 -> 38.2p
    if (!isNum(m)) return '–';
    const p = Math.round(Number(m) * 1000) / 10;
    return (Number.isInteger(p) ? String(p) : p.toFixed(1)) + 'p';
  }
  function toDate(v) { if (!v) return null; const d = new Date(v); return isNaN(d.getTime()) ? null : d; }
  const fdate = (v) => { const d = toDate(v); return d ? DF.format(d) : '–'; };
  const ftime = (v) => { const d = toDate(v); return d ? TF.format(d) : ''; };
  function ftimeSec(v) { // 11:42 plus muted seconds
    const d = toDate(v);
    if (!d) return '';
    return html`${TF.format(d)}<span class="sec">:${String(d.getSeconds()).padStart(2, '0')}</span>`;
  }
  function fdur(ms) {
    if (!isNum(ms) || ms < 0) return '';
    const s = Math.round(ms / 1000);
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60), r = s % 60;
    if (m < 60) return m + 'm ' + String(r).padStart(2, '0') + 's';
    return Math.floor(m / 60) + 'h ' + (m % 60) + 'm';
  }
  const SMALL_WORDS = new Set(['and', 'of', 'the', 'on', 'at', 'to', 'in', 'for', 'by', 'with']);
  function tc(s) { // VOA text is upper case; title-case it for reading. Tokens with digits stay as they are.
    if (!s) return '';
    s = String(s);
    if (/[a-z]/.test(s)) return s;
    let first = true;
    return s.split(/(\s+|,|\/|-)/).map((w) => {
      if (!w || /^(\s+|,|\/|-)$/.test(w)) { if (w === ',') first = true; return w; }
      if (/\d/.test(w)) { first = false; return w; }
      const lw = w.toLowerCase();
      const out = !first && SMALL_WORDS.has(lw) ? lw : lw.charAt(0).toUpperCase() + lw.slice(1);
      first = false;
      return out;
    }).join('');
  }
  function sentence(s) { if (!s) return ''; s = String(s); if (/[a-z]/.test(s)) return s; const l = s.toLowerCase(); return l.charAt(0).toUpperCase() + l.slice(1); }
  function shortAddr(a) { // "Bst and Grd Flr 223, Shoreditch High Street, London" -> first two parts
    const parts = String(a || '').replace(/^\([^)]*\)\s*/, '').split(',').map((x) => x.trim()).filter(Boolean);
    if (parts.length > 1 && /^london$/i.test(parts[parts.length - 1])) parts.pop();
    return parts.slice(0, 2).join(', ');
  }
  function parseSegKey(k) { // 'SHOREDITCH HIGH STREET|E1 6#694626'
    const m = String(k || '').match(/^([^|#]*)\|?([^#]*)#?(.*)$/);
    return m ? { street: m[1].trim(), sector: m[2].trim(), scheme: m[3].trim() } : { street: '', sector: '', scheme: '' };
  }
  function streetName(seg) {
    if (!seg) return 'your street';
    return tc(seg.street_name || parseSegKey(seg.segment_key).street) || 'your street';
  }

  // ---------------------------------------------------------------------------
  // Labels shared by every screen
  // ---------------------------------------------------------------------------
  const STAGES = [
    { key: 'scout', agent: 'scout', label: 'Scout scores the shop', short: 'Scout', doing: 'Scout is scoring the shop against its street' },
    { key: 'builder', agent: 'builder', label: 'Case builder writes the evidence pack', short: 'Case builder', doing: 'Case builder is writing the evidence pack' },
    { key: 'advocate', agent: 'advocate', label: 'VOA advocate tries to kill the case', short: 'VOA advocate', doing: 'VOA advocate is arguing the VOA’s side' },
    { key: 'owner', agent: 'owner', label: 'You approve the filing', short: 'Your approval', doing: 'Waiting for your approval' },
    { key: 'check', agent: 'orchestrator', label: 'Check filed with the VOA (mock)', short: 'Check', doing: 'Check filed, waiting for the VOA decision (mock)' },
    { key: 'challenge', agent: 'caseworker', label: 'Challenge', short: 'Challenge', doing: 'Caseworker is preparing the Challenge' },
    { key: 'decision', agent: 'caseworker', label: 'Decision and refund', short: 'Decision', doing: 'Waiting for the VOA decision (mock)' },
  ];
  const AGENT_NAMES = {
    scout: 'Scout', builder: 'Case builder', advocate: 'VOA advocate', owner: 'You', orchestrator: 'Orchestrator',
    caseworker: 'Caseworker', playbook: 'Playbook keeper', eval: 'Eval agent',
  };
  const agentName = (a) => AGENT_NAMES[a] || sentence(a || '');
  const CASE_STAGE_TEXT = {
    waiting_owner: 'Waiting for your approval', check_draft: 'Check being drafted', check_submitted: 'Check submitted',
    waiting_check_decision: 'Waiting for the Check decision', challenge_draft: 'Challenge being drafted',
    challenge_submitted: 'Challenge submitted', waiting_challenge_decision: 'Waiting for the Challenge decision',
    appeal_draft: 'Appeal being drafted', appeal_submitted: 'Appeal submitted', waiting_tribunal: 'Waiting for the tribunal',
    resolved: 'Resolved', closed: 'Closed', needs_human: 'Needs a person',
  };
  const stageText = (s) => CASE_STAGE_TEXT[s] || sentence(String(s || '').replace(/_/g, ' '));
  const CAND_STATUS = {
    flagged: 'Flagged', building_pack: 'Building pack', pack_ready: 'Pack ready', killed: 'Killed by advocate',
    waiting_owner: 'Waiting for owner', approved: 'Approved', in_case: 'In case', dismissed: 'Dismissed',
  };
  const FLAG_TEXT = {
    zone_a_above_tone: 'Zone A above street tone', just_over_51k: 'Just over £51,000', just_over_500k: 'Just over £500,000',
    epc_area_gap: 'Floor area far from EPC', segment_cluster: 'Priced run (3+ shops at this price)',
  };
  function flagText(f) {
    const k = typeof f === 'string' ? f : f && (f.code || f.flag || f.kind || f.name);
    if (!k) return '';
    return FLAG_TEXT[k] || sentence(String(k).replace(/_/g, ' '));
  }

  // Diverging scale for "how far above or below the street's median" (z = spreads from the median).
  const ZC = [
    { test: (z) => z <= -2, color: '#3d6fe0', label: '2+ spreads below' },
    { test: (z) => z < -0.5, color: '#9fb4ec', label: 'Below the median' },
    { test: (z) => z <= 0.5, color: '#9aa0a9', label: 'Near the median' },
    { test: (z) => z < 2, color: '#e3a59d', label: 'Above the median' },
    { test: () => true, color: '#b42318', label: '2+ spreads above (flagged)' },
  ];
  const zColor = (z) => (isNum(z) ? ZC.find((c) => c.test(Number(z))).color : '#9aa0a9');
  function zText(z) {
    if (!isNum(z)) return '';
    const n = Number(z);
    if (Math.abs(n) < 0.05) return 'at the median';
    return f1(Math.abs(n)) + (Math.abs(n) === 1 ? ' spread ' : ' spreads ') + (n > 0 ? 'above' : 'below');
  }

  const ICON_SEARCH = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const ICON_EXT = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M4.5 2.5h5v5M9.5 2.5L3 9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_CHECK = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_CROSS = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

  // ---------------------------------------------------------------------------
  // API
  // ---------------------------------------------------------------------------
  class ApiError extends Error {
    constructor(status, detail) { super(detail); this.name = 'ApiError'; this.status = status; }
  }
  function detailText(d) {
    if (!d) return '';
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x) => (x && (x.msg || JSON.stringify(x))) || '').join('; ');
    return JSON.stringify(d);
  }
  async function apiRaw(path, opts = {}) {
    if (MOCK) return Mock.handle(path, opts);
    const init = { method: opts.method || 'GET', headers: { Accept: 'application/json' }, signal: opts.signal, cache: 'no-store' };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new ApiError(0, 'Cannot reach the Overrated server. Check that it is running.');
    }
    const text = await res.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch (_) { data = null; } }
    if (!res.ok) throw new ApiError(res.status, detailText(data && data.detail) || `The server answered ${res.status}.`);
    return { status: res.status, data };
  }
  const api = (path, opts) => apiRaw(path, opts).then((r) => r.data);
  const isAbort = (e) => e && e.name === 'AbortError';

  // Job metadata (candidate_id from POST /api/appeal) so the log can load before a case exists.
  const jobMeta = new Map();
  function rememberJob(id, meta) {
    jobMeta.set(id, meta);
    try { sessionStorage.setItem('overrated:job:' + id, JSON.stringify(meta)); } catch (_) { /* storage unavailable */ }
  }
  function recallJob(id) {
    if (jobMeta.has(id)) return Object.assign({}, jobMeta.get(id));
    try {
      const s = sessionStorage.getItem('overrated:job:' + id);
      if (s) return JSON.parse(s) || {};
    } catch (_) { /* storage unavailable */ }
    return {};
  }

  // ---------------------------------------------------------------------------
  // View context: everything a screen starts (timers, fetches, maps) stops when you leave it.
  // ---------------------------------------------------------------------------
  function makeCtx() {
    const ctrl = new AbortController();
    const cleanups = [];
    const timers = new Set();
    const ctx = {
      alive: true,
      signal: ctrl.signal,
      onCleanup(fn) { cleanups.push(fn); },
      later(fn, ms) {
        const id = setTimeout(() => { timers.delete(id); if (ctx.alive) fn(); }, ms);
        timers.add(id);
        return id;
      },
      every(fn, ms) { const id = setInterval(() => { if (ctx.alive) fn(); }, ms); cleanups.push(() => clearInterval(id)); },
      dispose() {
        ctx.alive = false;
        ctrl.abort();
        timers.forEach(clearTimeout);
        cleanups.splice(0).forEach((fn) => { try { fn(); } catch (_) { /* ignore */ } });
      },
    };
    return ctx;
  }

  function renderState(main, { title, text, actions }) {
    document.title = title + ' · Overrated';
    setHTML(main, html`<div class="state">
      <h1 tabindex="-1">${title}</h1>
      <p>${text}</p>
      ${actions ? html`<div class="btn-row">${actions}</div>` : ''}
    </div>`);
    focusHeading(main);
  }
  function focusHeading(root) {
    const h = root.querySelector('h1');
    if (h && (document.activeElement === document.body || document.activeElement === root || !document.activeElement)) {
      h.focus({ preventScroll: true });
    }
  }
  const backToSearch = html`<a class="btn btn-secondary" href="#/">Search for a property</a>`;

  // ---------------------------------------------------------------------------
  // Typeahead search (combobox + listbox, keyboard navigable)
  // ---------------------------------------------------------------------------
  let searchSeq = 0;
  function mountSearch(host, { large = false, autofocus = false } = {}) {
    const id = 'q' + (++searchSeq);
    setHTML(host, html`<div class="search ${large ? 'search-lg' : 'search-sm'}">
      <label class="${large ? 'search-label' : 'sr-only'}" for="${id}">Business address or postcode</label>
      <div class="search-field">
        ${raw(ICON_SEARCH)}
        <input id="${id}" type="text" inputmode="search" enterkeyhint="search" autocomplete="off" autocapitalize="off" spellcheck="false"
          role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-list" aria-describedby="${id}-status"
          placeholder="${large ? 'e.g. 223 Shoreditch High Street or E1 6' : 'Search an address or postcode'}">
      </div>
      <div class="search-pop" hidden>
        <div class="search-msg" hidden></div>
        <ul id="${id}-list" class="search-list" role="listbox" aria-label="Matching properties"></ul>
      </div>
      <div class="sr-only" aria-live="polite" id="${id}-status"></div>
    </div>`);
    const input = host.querySelector('input');
    const pop = host.querySelector('.search-pop');
    const list = host.querySelector('ul');
    const msg = host.querySelector('.search-msg');
    const status = host.querySelector('#' + id + '-status');
    let results = [], corrected = null, active = -1, timer = 0, ctrl = null, shownFor = '', pendingEnter = false, blurTimer = 0;

    function setOpen(open) {
      pop.hidden = !open;
      input.setAttribute('aria-expanded', open && results.length ? 'true' : 'false');
      if (!open) setActive(-1);
    }
    function setActive(i) {
      active = i;
      [...list.children].forEach((li, j) => li.setAttribute('aria-selected', j === i ? 'true' : 'false'));
      if (i >= 0 && list.children[i]) {
        input.setAttribute('aria-activedescendant', list.children[i].id);
        list.children[i].scrollIntoView({ block: 'nearest' });
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }
    function showMsg(text, isErr) {
      msg.hidden = !text;
      msg.textContent = text || '';
      msg.classList.toggle('err', !!isErr);
    }
    function highlight(text, q) {
      const words = q.split(/\s+/).filter((w) => w.length >= 2).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
      if (!words.length) return esc(text);
      const re = new RegExp('(' + words.join('|') + ')', 'ig');
      return text.split(re).map((p, i) => (i % 2 ? '<mark>' + esc(p) + '</mark>' : esc(p))).join('');
    }
    function renderResults(q) {
      shownFor = q;
      const hl = corrected || q;
      setHTML(list, results.map((r, i) => html`<li class="opt" role="option" id="${id}-o${i}" aria-selected="false" data-i="${i}">
        <div class="opt-main">${raw(highlight(tc(r.address), hl))}</div>
        <div class="opt-meta">
          ${r.use ? html`<span>${sentence(r.use)}</span>` : ''}
          ${r.postcode ? html`<span>${r.postcode}</span>` : ''}
          <span>RV ${money(r.rv)}</span>
          ${isNum(r.zone_a_price) ? html`<span>Zone A ${ppm(r.zone_a_price)}/m²</span>` : html`<span class="nz">no zoned valuation</span>`}
        </div>
      </li>`));
      active = -1;
      if (!results.length) showMsg(`No properties on the 2026 list match “${q}”. Check the spelling, or try the house number and street, or a postcode.`);
      else if (corrected) showMsg(`No exact match for “${q}”. Showing results for “${tc(corrected)}”.`);
      else showMsg('');
      status.textContent = results.length
        ? `${corrected ? 'Showing results for ' + tc(corrected) + '. ' : ''}${results.length} result${results.length === 1 ? '' : 's'}. Use the up and down arrows to choose, Enter to open.`
        : 'No matching properties.';
      setOpen(document.activeElement === input);
    }
    async function run(q) {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      const mine = ctrl;
      if (!results.length || shownFor !== q) { if (!results.length) showMsg('Searching...'); setOpen(true); }
      try {
        const data = await api('/api/search?q=' + encodeURIComponent(q), { signal: mine.signal });
        if (mine !== ctrl || q !== input.value.trim()) return;
        results = (data && Array.isArray(data.results) ? data.results : []).slice(0, 12);
        corrected = data && typeof data.corrected === 'string' && data.corrected ? data.corrected : null;
        renderResults(q);
        if (pendingEnter) { pendingEnter = false; if (results.length) choose(results[0]); }
      } catch (e) {
        if (isAbort(e)) return;
        results = []; corrected = null;
        setHTML(list, '');
        showMsg('Search is unavailable: ' + e.message, true);
        setOpen(true);
        pendingEnter = false;
      }
    }
    function choose(r) {
      if (!r || !UARN_RE.test(String(r.uarn))) return;
      clearTimeout(timer);
      setOpen(false);
      if (!large) { input.value = ''; results = []; setHTML(list, ''); input.blur(); }
      location.hash = '#/p/' + String(r.uarn);
    }
    function onInput() {
      const q = input.value.trim();
      clearTimeout(timer);
      pendingEnter = false;
      if (q.length < 3) {
        if (ctrl) ctrl.abort();
        results = []; corrected = null;
        setHTML(list, '');
        if (q.length) { showMsg('Keep typing: at least 3 characters.'); setOpen(true); } else setOpen(false);
        status.textContent = '';
        return;
      }
      timer = setTimeout(() => run(q), 180);
    }
    input.addEventListener('input', onInput);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') {
        if (!results.length) return;
        e.preventDefault();
        if (pop.hidden) setOpen(true);
        setActive(active < results.length - 1 ? active + 1 : 0);
      } else if (e.key === 'ArrowUp') {
        if (!results.length) return;
        e.preventDefault();
        if (pop.hidden) setOpen(true);
        setActive(active > 0 ? active - 1 : results.length - 1);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const q = input.value.trim();
        if (active >= 0 && results[active]) choose(results[active]);
        else if (q.length >= 3 && shownFor === q && results.length) choose(results[0]);
        else if (q.length >= 3) { pendingEnter = true; clearTimeout(timer); run(q); }
      } else if (e.key === 'Escape') {
        if (!pop.hidden) { e.preventDefault(); setOpen(false); } else if (input.value) { input.value = ''; onInput(); }
      }
    });
    input.addEventListener('focus', () => {
      clearTimeout(blurTimer);
      if (input.value.trim().length >= 3 && (results.length || !msg.hidden)) setOpen(true);
    });
    input.addEventListener('blur', () => { blurTimer = setTimeout(() => setOpen(false), 150); });
    list.addEventListener('mousedown', (e) => e.preventDefault());
    list.addEventListener('click', (e) => {
      const li = e.target.closest('li[data-i]');
      if (li) choose(results[Number(li.dataset.i)]);
    });
    if (autofocus) setTimeout(() => input.focus({ preventScroll: true }), 0);
    return {
      setQuery(q) { input.value = q; input.focus(); clearTimeout(timer); run(q.trim()); },
      destroy() { clearTimeout(timer); clearTimeout(blurTimer); if (ctrl) ctrl.abort(); },
    };
  }

  // ---------------------------------------------------------------------------
  // Map (Leaflet). Points sharing a postcode centroid are fanned out so each stays visible.
  // ---------------------------------------------------------------------------
  function fanOut(points) {
    const groups = new Map();
    points.forEach((p) => {
      const k = p.lat.toFixed(5) + ',' + p.lng.toFixed(5);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(p);
    });
    groups.forEach((g) => {
      if (g.length < 2) return;
      g.sort((a, b) => (b.order || 0) - (a.order || 0)); // subject keeps the centre
      const lat0 = g[0].lat, lng0 = g[0].lng;
      const mPerLng = 111320 * Math.cos((lat0 * Math.PI) / 180);
      g.forEach((p, i) => {
        if (i === 0) return;
        const r = 7 * Math.sqrt(i); // metres, sunflower spiral
        const a = i * 2.39996;
        p.lat = lat0 + (r * Math.sin(a)) / 111320;
        p.lng = lng0 + (r * Math.cos(a)) / mPerLng;
      });
    });
    return points;
  }
  function mountMap(el, points, { emptyText = 'No locations to show.' } = {}) {
    const noop = { destroy() {}, highlight() {} };
    if (!window.L) {
      setHTML(el, html`<div class="map-msg">The map library did not load, so the map is not available. The same shops are listed below.</div>`);
      return noop;
    }
    const pts = fanOut(points.filter((p) => isNum(p.lat) && isNum(p.lng)).map((p) => Object.assign({}, p, { lat: +p.lat, lng: +p.lng })));
    if (!pts.length) { setHTML(el, html`<div class="map-msg">${emptyText}</div>`); return noop; }
    const L = window.L;
    const map = L.map(el, { scrollWheelZoom: false, maxZoom: 19, zoomSnap: 0.5, attributionControl: true });
    map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    L.tileLayer(TILE_BASE, { maxNativeZoom: 16, maxZoom: 19, attribution: TILE_ATTRIBUTION }).addTo(map);
    const pane = map.createPane('labels');
    pane.style.zIndex = '450';
    pane.style.pointerEvents = 'none';
    L.tileLayer(TILE_LABELS, { pane: 'labels', maxNativeZoom: 16, maxZoom: 19 }).addTo(map);
    const markers = new Map();
    pts.sort((a, b) => (a.order || 0) - (b.order || 0)).forEach((p) => {
      const style = { radius: p.r || 5, color: p.stroke || '#ffffff', weight: p.weight || 1, fillColor: p.fill, fillOpacity: 0.9, opacity: 1 };
      // One tooltip per marker (Leaflet keeps one), bound before the marker is added so a permanent
      // label opens once the map has its view.
      const m = L.circleMarker([p.lat, p.lng], style);
      if (p.label) m.bindTooltip(esc(p.label), { permanent: true, direction: 'top', offset: [0, -(p.r || 5) - 4], className: 'subject-label' });
      else if (p.tip) m.bindTooltip(p.tip, { className: 'tip', direction: 'top', offset: [0, -(p.r || 5) - 2] });
      m.addTo(map);
      if (p.href) m.on('click', () => { location.hash = p.href; });
      markers.set(p.id, { m, style, p });
    });
    let fitted = false, removed = false;
    function fit() {
      if (removed) return; // the view was left before the first frame
      map.invalidateSize();
      const latlngs = pts.map((p) => [p.lat, p.lng]);
      if (latlngs.length === 1) map.setView(latlngs[0], 17);
      else map.fitBounds(L.latLngBounds(latlngs), { padding: [28, 28], maxZoom: 18 });
      fitted = true;
    }
    const raf = requestAnimationFrame(() => requestAnimationFrame(fit));
    let ro = null;
    if ('ResizeObserver' in window) {
      ro = new ResizeObserver(() => { if (fitted && !removed) map.invalidateSize(); });
      ro.observe(el);
    }
    let hl = null;
    return {
      highlight(id) {
        if (hl) { hl.m.setStyle(hl.style); hl.m.setRadius(hl.style.radius); if (!hl.p.label) hl.m.closeTooltip(); hl = null; }
        const x = id != null ? markers.get(String(id)) : null;
        if (!x) return;
        x.m.setStyle({ color: '#16181d', weight: 2 });
        x.m.setRadius((x.style.radius || 5) + 3);
        x.m.bringToFront();
        if (!x.p.label) x.m.openTooltip();
        hl = x;
      },
      destroy() { removed = true; cancelAnimationFrame(raf); if (ro) ro.disconnect(); map.remove(); },
    };
  }

  function legendHTML({ strip = false, subject = true, comps = true, seg = null } = {}) {
    const thr = seg && isNum(seg.median) && isNum(seg.spread) ? Number(seg.median) + 2 * Number(seg.spread) : null;
    return html`<div class="legend">
      ${ZC.map((c) => html`<span><i style="background:${c.color}"></i>${c.label}</span>`)}
      ${subject ? html`<span><i class="subj"></i>Your shop</span>` : ''}
      ${comps ? html`<span><i class="ring"></i>Comparable</span>` : ''}
      ${strip ? html`<span><i class="line"></i>Median</span>
        <span><i class="line flag"></i>Flag line: median + 2 spreads${thr != null ? ' (' + ppm(thr) + ')' : ''}</span>
        <span><i class="band"></i>Middle half of shops</span>` : ''}
    </div>`;
  }

  // ---------------------------------------------------------------------------
  // Price strip: every segment shop's Zone A price on one axis (dot plot, stacked where prices tie)
  // ---------------------------------------------------------------------------
  function niceTicks(lo, hi, count) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const rawStep = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const norm = rawStep / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
    return out;
  }
  function swarmOffsets(rows, cols) {
    const seq = (n) => { const a = [0]; for (let i = 1; i <= n; i++) a.push(i, -i); return a; };
    // Fill the centre column first, then the columns either side alternately so overflow stays centred on the price.
    const out = [];
    for (let c = 0; c <= cols; c++) {
      seq(rows).forEach((r) => { out.push([c, r]); if (c > 0) out.push([-c, r]); });
    }
    return out;
  }
  function mountStrip(wrap, seg, compSet) {
    const host = wrap.querySelector('.strip');
    const tip = wrap.querySelector('.chart-tip');
    const shops = (seg.shops || []).filter((s) => isNum(s.zone_a_price));
    if (!shops.length) { setHTML(host, html`<p class="secondary">No Zone A prices to plot.</p>`); return { destroy() {} }; }
    let placed = [], scaleW = 1, lastW = 0;
    function draw() {
      const W = Math.max(280, Math.floor(host.clientWidth || 600));
      if (W === lastW) return;
      lastW = W;
      const n = shops.length;
      const r = n > 80 ? 3 : n > 40 ? 3.5 : 4.5;
      const gap = 1.5, step = r * 2 + gap;
      const rows = n > 80 ? 7 : 5;
      const M = { l: 14, r: 14, top: 44, bottom: 40 };
      const median = Number(seg.median);
      const thr = isNum(seg.spread) ? median + 2 * Number(seg.spread) : null;
      const vals = shops.map((s) => Number(s.zone_a_price)).concat([median]);
      if (thr != null) vals.push(thr);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      if (hi - lo < 1) { lo -= 50; hi += 50; }
      const pad = (hi - lo) * 0.06;
      lo -= pad; hi += pad;
      const X = (v) => M.l + ((v - lo) / (hi - lo)) * (W - M.l - M.r);
      const order = shops.map((s) => ({ s, subj: !!s.is_subject, comp: compSet.has(String(s.uarn)) }))
        .sort((a, b) => (Number(b.subj) - Number(a.subj)) || (a.s.zone_a_price - b.s.zone_a_price));
      const offs = swarmOffsets(rows, 60);
      placed = [];
      for (const o of order) {
        const rr = o.subj ? 7 : r;
        const cx = X(Number(o.s.zone_a_price));
        let pos = null;
        for (const [c, rw] of offs) {
          const px = cx + c * step * 0.92, py = rw * step;
          if (px < M.l - 4 || px > W - M.r + 4) continue;
          let ok = true;
          for (const p of placed) {
            const dx = p.x - px, dy = p.y - py, min = p.rr + rr + gap - 0.01;
            if (dx * dx + dy * dy < min * min) { ok = false; break; }
          }
          if (ok) { pos = [px, py]; break; }
        }
        placed.push(Object.assign({ x: pos ? pos[0] : cx, y: pos ? pos[1] : 0, rr }, o));
      }
      const ext = Math.max(10, ...placed.map((p) => Math.abs(p.y) + p.rr));
      const plotH = ext * 2 + 8;
      const cy = M.top + plotH / 2;
      const axisY = M.top + plotH + 4;
      const H = axisY + M.bottom;
      placed.forEach((p) => { p.cy = cy + p.y; });
      const ticks = niceTicks(lo, hi, W < 480 ? 4 : 6);
      const subj = placed.find((p) => p.subj);
      const mx = X(median);
      const bandOk = isNum(seg.p25) && isNum(seg.p75) && X(Number(seg.p75)) - X(Number(seg.p25)) >= 2;
      const estW = (t) => t.length * 6.3;
      // labels: subject on the top row, median on the second, each anchored away from the other
      let subjLabel = '';
      if (subj) {
        const t = 'Your shop ' + ppm(subj.s.zone_a_price);
        let anchor = 'middle', lx = subj.x;
        if (subj.x - estW(t) / 2 < 0) { anchor = 'start'; lx = Math.max(2, subj.x - 6); } else if (subj.x + estW(t) / 2 > W) { anchor = 'end'; lx = Math.min(W - 2, subj.x + 6); }
        subjLabel = html`<line x1="${subj.x}" x2="${subj.x}" y1="17" y2="${subj.cy - subj.rr - 1}" stroke="#16181d" stroke-width="1"/>
          <text x="${lx}" y="12" text-anchor="${anchor}" font-size="12" font-weight="600" fill="#16181d">${t}</text>`;
      }
      const mt = 'Median ' + ppm(median);
      let manchor = subj && subj.x < mx ? 'start' : 'end';
      let mlx = manchor === 'start' ? mx + 4 : mx - 4;
      if (manchor === 'end' && mlx - estW(mt) < 0) { manchor = 'start'; mlx = mx + 4; }
      if (manchor === 'start' && mlx + estW(mt) > W) { manchor = 'end'; mlx = mx - 4; }
      const dots = placed.slice().sort((a, b) => Number(a.subj) - Number(b.subj) || Number(a.comp) - Number(b.comp));
      const svg = html`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img"
          aria-label="${`Zone A prices of ${n} shops in this segment. Median ${ppm(median)} per square metre.` + (subj ? ` Your shop ${ppm(subj.s.zone_a_price)}.` : '')}">
        ${bandOk ? html`<rect x="${X(Number(seg.p25))}" y="${M.top - 4}" width="${X(Number(seg.p75)) - X(Number(seg.p25))}" height="${plotH + 8}" fill="#ecece7"/>` : ''}
        <line x1="${M.l}" x2="${W - M.r}" y1="${axisY}" y2="${axisY}" stroke="#cfcfc8"/>
        ${ticks.map((t) => html`<line x1="${X(t)}" x2="${X(t)}" y1="${axisY}" y2="${axisY + 4}" stroke="#cfcfc8"/>
          <text x="${X(t)}" y="${axisY + 17}" text-anchor="middle" font-size="11" fill="#8a909c">${ppm(t)}</text>`)}
        <text x="${W - M.r}" y="${axisY + 34}" text-anchor="end" font-size="11" fill="#5b616e">Zone A price, £ per m²</text>
        ${thr != null ? html`<line x1="${X(thr)}" x2="${X(thr)}" y1="${M.top - 4}" y2="${axisY}" stroke="#b42318" stroke-opacity="0.55" stroke-width="1"/>` : ''}
        <line x1="${mx}" x2="${mx}" y1="${M.top - 14}" y2="${axisY}" stroke="#5b616e" stroke-width="1"/>
        <text x="${mlx}" y="${M.top - 17}" text-anchor="${manchor}" font-size="11" fill="#5b616e">${mt}</text>
        ${dots.map((p) => p.subj
          ? html`<circle cx="${p.x}" cy="${p.cy}" r="${p.rr}" fill="#16181d" stroke="#ffffff" stroke-width="2" data-i="${placed.indexOf(p)}"/>`
          : html`<circle cx="${p.x}" cy="${p.cy}" r="${p.rr}" fill="${zColor(p.s.z)}" fill-opacity="0.9" stroke="${p.comp ? '#16181d' : '#ffffff'}" stroke-width="${p.comp ? 1.5 : 1}" data-i="${placed.indexOf(p)}"/>`)}
        ${subjLabel}
      </svg>`;
      setHTML(host, svg);
      scaleW = W;
    }
    function nearest(ev) {
      const svg = host.querySelector('svg');
      if (!svg) return null;
      const rect = svg.getBoundingClientRect();
      const k = scaleW / rect.width;
      const x = (ev.clientX - rect.left) * k, y = (ev.clientY - rect.top) * k;
      let best = null, bd = 14 * 14;
      for (const p of placed) {
        const d = (p.x - x) ** 2 + (p.cy - y) ** 2;
        if (d < bd) { bd = d; best = p; }
      }
      return best;
    }
    let hov = null;
    function onMove(ev) {
      const p = nearest(ev);
      if (hov && hov !== p) { const c = host.querySelector(`circle[data-i="${placed.indexOf(hov)}"]`); if (c) c.removeAttribute('class'); }
      hov = p;
      if (!p) { tip.hidden = true; return; }
      const c = host.querySelector(`circle[data-i="${placed.indexOf(p)}"]`);
      if (c) c.setAttribute('class', 'hov');
      setHTML(tip, html`<b>${shortAddr(tc(p.s.address))}</b>${p.subj ? ' (your shop)' : p.comp ? ' (comparable)' : ''}<br>
        <span class="secondary">Zone A ${ppm(p.s.zone_a_price)}/m²${isNum(p.s.itza) ? ' · ITZA ' + f1(p.s.itza) + ' m²' : ''}${isNum(p.s.z) ? ' · ' + zText(p.s.z) : ''}</span>`);
      tip.hidden = false;
      const wr = wrap.getBoundingClientRect();
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let left = ev.clientX - wr.left + 12, top = ev.clientY - wr.top - th - 10;
      if (left + tw > wr.width) left = Math.max(0, ev.clientX - wr.left - tw - 12);
      if (top < 0) top = ev.clientY - wr.top + 14;
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }
    function onLeave() { tip.hidden = true; if (hov) { const c = host.querySelector(`circle[data-i="${placed.indexOf(hov)}"]`); if (c) c.removeAttribute('class'); hov = null; } }
    host.addEventListener('pointermove', onMove);
    host.addEventListener('pointerdown', onMove);
    host.addEventListener('pointerleave', onLeave);
    draw();
    let ro = null, raf = 0;
    if ('ResizeObserver' in window) {
      ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); });
      ro.observe(host);
    }
    return { destroy() { if (ro) ro.disconnect(); cancelAnimationFrame(raf); } };
  }

  // ---------------------------------------------------------------------------
  // Home
  // ---------------------------------------------------------------------------
  function viewHome(ctx, main) {
    document.title = 'Overrated: check your business rates valuation';
    setHTML(main, html`<section class="home">
      <h1 class="home-title" tabindex="-1">Check your business rates valuation</h1>
      <p class="lede">Find your property on the 2026 rating list and see how its value compares with similar shops on the same street.</p>
      <div class="home-search" id="home-search"></div>
      <p class="examples">Try <button type="button" class="linkbtn" data-q="223 Shoreditch High Street">223 Shoreditch High Street</button>
        or <button type="button" class="linkbtn" data-q="E1 6">E1 6</button></p>
      <div class="how">
        <p>We compare your Zone A price (the value per m² of the first 6.1 m behind the shop window) with similar shops on your street segment. If yours is out of line, we build the evidence, you approve it, and we file a Check and Challenge with the VOA as your agent. No win, no fee.</p>
        <ol class="steps-preview" aria-label="How a case runs">
          <li><b>1. Scout</b><span>scores your shop against the street</span></li>
          <li><b>2. Case builder</b><span>writes the evidence pack</span></li>
          <li><b>3. VOA advocate</b><span>argues the VOA’s side</span></li>
          <li><b>4. You</b><span>approve the filing</span></li>
          <li><b>5. Check</b><span>filed with the VOA</span></li>
          <li><b>6. Challenge</b><span>if the VOA says no</span></li>
          <li><b>7. Decision</b><span>refund from the council</span></li>
        </ol>
      </div>
      <section class="sweep" id="home-sweep" aria-labelledby="sweep-h">
        <h2 class="label" id="sweep-h">Latest area sweep</h2>
        <p class="small secondary">Loading...</p>
      </section>
    </section>`);
    const search = mountSearch(main.querySelector('#home-search'), { large: true, autofocus: true });
    ctx.onCleanup(search.destroy);
    main.querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => search.setQuery(b.dataset.q)));
    const sweep = main.querySelector('#home-sweep');
    api('/api/area', { signal: ctx.signal }).then((d) => {
      if (!ctx.alive) return;
      const run = (d && d.run) || {};
      const cands = (d && d.candidates) || [];
      const total = cands.reduce((a, c) => a + (isNum(c.est_saving) ? Number(c.est_saving) : 0), 0);
      const flagged = isNum(run.candidates_flagged) && Number(run.candidates_flagged) > 0 ? run.candidates_flagged : cands.length;
      setHTML(sweep, html`<h2 class="label" id="sweep-h">Latest area sweep: ${run.area_name || 'Hackney'}</h2>
        <div class="sweep-stats">
          <a href="#/area"><span class="v">${int(run.properties_checked)}</span><span class="l">shops checked</span></a>
          <a href="#/area"><span class="v">${int(flagged)}</span><span class="l">candidates flagged</span></a>
          <a href="#/area"><span class="v">${money(total)}</span><span class="l">est. savings to March 2029</span></a>
        </div>
        <p class="sweep-foot secondary">${run.finished_at ? 'Finished ' + fdate(run.finished_at) + '. ' : (run.status ? 'Status: ' + run.status + '. ' : '')}<a href="#/area">See every candidate on the map</a></p>`);
    }).catch((e) => {
      if (isAbort(e) || !ctx.alive) return;
      setHTML(sweep, html`<h2 class="label" id="sweep-h">Latest area sweep</h2><p class="small secondary">Sweep figures are unavailable: ${e.message}</p>`);
    });
  }

  // ---------------------------------------------------------------------------
  // Property
  // ---------------------------------------------------------------------------
  const VERDICT_BADGE = {
    overpaying: { text: 'Likely overpaying', cls: 'badge-red' },
    in_line: { text: 'In line with the street', cls: '' },
    no_zoning: { text: 'No zoned valuation', cls: '' },
  };
  function bandMult(rv, m1) { // same bands as the data service; retail unless the current multiplier says otherwise
    const m = isNum(m1) ? Math.round(Number(m1) * 1000) / 1000 : null;
    const retail = !(m === 0.432 || m === 0.48);
    if (rv >= 500000) return 0.508;
    if (rv >= 51000) return retail ? 0.43 : 0.48;
    return retail ? 0.382 : 0.432;
  }
  function verdictSentence(p) {
    const z = p.zoning, s = p.segment;
    if (p.verdict === 'no_zoning' || !z || !isNum(z.zone_a_price)) return 'This property has no zoned shop valuation';
    if (!s || !isNum(s.median) || Number(s.median) <= 0) return 'There are no similar shops to compare with on this street';
    const street = streetName(s);
    const d = ((Number(z.zone_a_price) - Number(s.median)) / Number(s.median)) * 100;
    const r = Math.round(Math.abs(d));
    if (p.verdict === 'overpaying') return `Your Zone A price is ${r}% above similar shops on ${street}`;
    if (isNum(s.n) && Number(s.n) < 5) return `Only ${s.n} similar shops on this part of ${street}, too few to compare`;
    if (r === 0) return `Your Zone A price matches similar shops on ${street}`;
    if (d > 0) {
      return isNum(p.z_score) && Number(p.z_score) < 2
        ? `Your Zone A price is ${r}% above the median on ${street}, within the normal spread`
        : `Your Zone A price is ${r}% above the median on ${street}`;
    }
    return `Your Zone A price is ${r}% below similar shops on ${street}`;
  }
  // One factual line under the headline. The server's verdict_reason restates the headline for ordinary
  // cases, so we show the numbers instead and keep verdict_reason for the unusual ones (no zoning, no ITZA).
  function verdictDetail(p) {
    const z = p.zoning, s = p.segment;
    if (p.verdict === 'no_zoning' || !z || !s || !isNum(z.zone_a_price) || !isNum(s.median)) return p.verdict_reason || '';
    if (p.verdict === 'in_line' && !isNum(z.itza)) return p.verdict_reason || '';
    let t = `${ppm(z.zone_a_price)} per m² against a median of ${ppm(s.median)} for ${int(s.n)} similar shop${Number(s.n) === 1 ? '' : 's'} on this stretch.`;
    if (isNum(p.z_score) && Number(p.z_score) > 0.05) t += ` That is ${zText(p.z_score)} the median; we take cases from 2.`;
    return t;
  }
  // An earlier run the VOA advocate killed, on the same list entry and street tone as today (so a new run would see
  // the same facts). The property page then leads with that review instead of promising a saving.
  function killedReview(p) {
    const ex = p.existing || {};
    if (ex.case_id || ex.job_id || p.verdict !== 'overpaying' || !p.can_appeal) return null;
    const lr = ex.last_review;
    return lr && lr.verdict === 'kill' && lr.same_inputs === true && UUID_RE.test(String(lr.case_id || '')) ? lr : null;
  }
  // The money is shown only when we would take the case on today's facts.
  const showMoney = (p) => p.verdict === 'overpaying' && !!p.proposal && !!p.can_appeal && !killedReview(p);
  function firstSentence(t) {
    const s = String(t || '').trim();
    const m = s.match(/^.{40,}?[.!?](?=\s+[A-Z(£0-9])/);
    return m ? m[0] : s;
  }
  function appealAction(p) {
    const ex = p.existing || {};
    if (ex.case_id && UUID_RE.test(ex.case_id)) {
      return html`<a class="btn btn-primary btn-block" href="#/case/${ex.case_id}">Open your case</a>
        <p class="note">A case is already open for this property${ex.stage ? ': ' + stageText(ex.stage).toLowerCase() : ''}.</p>`;
    }
    if (ex.job_id && UUID_RE.test(ex.job_id)) {
      return html`<a class="btn btn-primary btn-block" href="#/job/${ex.job_id}">See the agents working</a>
        <p class="note">Our agents are already building the case for this property.</p>`;
    }
    const err = html`<p class="form-error" id="appeal-error" role="alert" hidden></p>`;
    if (killedReview(p)) {
      return html`<button type="button" class="btn btn-secondary btn-block" id="appeal-btn" data-label="Run the review again">Run the review again</button>
        <p class="note">The agents start again from today’s list. Expect the same answer unless you have evidence the data does not show. Nothing is filed without your approval.</p>
        ${err}`;
    }
    const lr = ex.last_review && UUID_RE.test(String(ex.last_review.case_id || '')) ? ex.last_review : null;
    const lastNote = lr ? html`<p class="note">An earlier review${lr.case_ref ? ' (' + lr.case_ref + ')' : ''}${lr.created_at ? ' on ' + fdate(lr.created_at) : ''} stopped the case${lr.same_inputs === false ? ', but the valuation or the street has changed since' : ''}. <a href="#/case/${lr.case_id}">See why</a></p>` : '';
    if (p.can_appeal) {
      return html`<button type="button" class="btn btn-primary btn-block" id="appeal-btn" data-label="Challenge this valuation">Challenge this valuation</button>
        <p class="note">Our agents build and test the evidence first, which takes a minute or two. Nothing is filed until you approve it. No win, no fee.</p>
        ${lastNote}
        ${err}`;
    }
    return html`<button type="button" class="btn btn-primary btn-block" disabled aria-describedby="appeal-blocked">Challenge this valuation</button>
      <p class="blocked" id="appeal-blocked">${p.appeal_blocked_reason || 'There is no case to make for this property.'}</p>`;
  }
  function verdictCard(p) {
    const kr = killedReview(p);
    const b = kr ? { text: 'Reviewed: the VOA would likely defend this', cls: '' } : (VERDICT_BADGE[p.verdict] || VERDICT_BADGE.in_line);
    const pr = p.proposal;
    return html`<section class="card verdict o-verdict" aria-labelledby="verdict-h">
      <h2 class="sr-only" id="verdict-h">Our view</h2>
      <span class="badge ${b.cls}">${b.text}</span>
      <p class="verdict-sentence">${verdictSentence(p)}.</p>
      ${verdictDetail(p) ? html`<p class="verdict-reason">${verdictDetail(p)}</p>` : ''}
      ${kr ? html`<div class="review">
        <p class="label">Independent review <span class="ref">${[kr.case_ref, kr.created_at ? fdate(kr.created_at) : null].filter(Boolean).join(', ')}</span></p>
        ${kr.reason ? html`<p>${firstSentence(kr.reason)}</p>` : html`<p>The VOA advocate found reasons the VOA would win, so nothing was filed.</p>`}
        <p class="small"><a href="#/case/${kr.case_id}">${isNum(kr.n_reasons) && Number(kr.n_reasons) > 1 ? 'All ' + int(kr.n_reasons) + ' reasons and the evidence pack' : 'The full review and the evidence pack'}</a></p>
        ${pr && isNum(pr.annual_saving) ? html`<p class="small muted">On paper the gap is worth ${money(pr.annual_saving)} a year, but the VOA would likely keep the value, so we do not count it as a saving.</p>` : ''}
      </div>` : ''}
      ${showMoney(p) ? html`<div class="saving">
        <div><div class="v pos">${money(pr.annual_saving)}</div><div class="l">likely saving a year</div></div>
        <div><div class="v">${money(pr.est_saving)}</div><div class="l">to March 2029</div></div>
      </div>` : ''}
      ${appealAction(p)}
    </section>`;
  }
  function calcRow(label, value, { sub = '', cls = '', op = '' } = {}) {
    return html`<tr class="${cls}"><th scope="row">${op ? html`<span class="op">${op} </span>` : ''}${label}${sub ? html`<span class="sub">${sub}</span>` : ''}</th><td>${value}</td></tr>`;
  }
  function calcCard(p) {
    const z = p.zoning, s = p.segment, pr = p.proposal;
    if (!z || !s || !isNum(z.zone_a_price) || !isNum(s.median)) return '';
    const zap = Number(z.zone_a_price), med = Number(s.median);
    const diff = zap - med;
    const zs = isNum(p.z_score) ? Number(p.z_score) : (isNum(s.spread) && Number(s.spread) > 0 ? diff / Number(s.spread) : null);
    const withMoney = showMoney(p) && isNum(pr.proposed_rv);
    const groupHead = (t) => html`<tr class="grp"><th colspan="2" scope="colgroup">${t}</th></tr>`;
    // Group 1: is the price out of line with the street?
    const rows = [
      groupHead('Is it out of line?'),
      calcRow('Your Zone A price', ppm(zap) + '/m²'),
      calcRow(`Median of ${int(s.n)} similar shops`, ppm(med) + '/m²', { sub: 'same street and VOA scheme' }),
      calcRow('Your price less the median', signedPpm(diff) + '/m²', { op: '=' }),
    ];
    if (isNum(s.spread)) rows.push(calcRow('Normal spread on this segment', ppm(s.spread) + '/m²', { op: '÷' }));
    if (zs != null && isNum(s.spread)) rows.push(calcRow('Spreads from the median', (zs > 0 ? '+' : zs < 0 ? MINUS : '') + f1(Math.abs(zs)), { cls: 'strong', op: '=', sub: 'we take cases at 2 or more' }));
    let note = '';
    if (withMoney) {
      // Group 2: what the gap is worth. Every figure is the data service's; only the display products are computed here.
      const rv = Number(p.rv);
      const m1 = p.bill && isNum(p.bill.multiplier) ? Number(p.bill.multiplier) : null;
      const m2 = m1 != null ? bandMult(Number(pr.proposed_rv), m1) : null;
      const bill = p.bill && isNum(p.bill.annual_bill) ? Number(p.bill.annual_bill) : null;
      rows.push(
        groupHead('What it is worth'),
        calcRow('Your price less the median', signedPpm(diff) + '/m²'),
        calcRow('ITZA (floor area in Zone A terms)', f1(z.itza) + ' m²', { op: '×' }),
        calcRow('Reduction in rateable value', money(diff * Number(z.itza)), { op: '=' }),
        calcRow('Current rateable value', money(rv)),
        calcRow('Proposed rateable value', money(pr.proposed_rv), { cls: 'strong', sub: 'current value less the reduction, rounded down to £50' }),
      );
      if (bill != null) {
        rows.push(
          calcRow('Bill now', money(bill), { sub: `${money(rv)} × ${mult(m1)}` }),
          calcRow('Bill after', money(bill - Number(pr.annual_saving)), { sub: `${money(pr.proposed_rv)} × ${mult(m2)}` }),
        );
      }
      rows.push(
        calcRow('Saving a year', html`<span class="pos">${money(pr.annual_saving)}</span>`, { cls: 'strong' }),
        calcRow('Years left on the 2026 list', f2(pr.years_left), { op: '×', sub: 'to 31 March 2029' }),
        calcRow('Likely saving', money(pr.est_saving), { cls: 'total', op: '=' }),
      );
      note = 'Before reliefs. The total uses unrounded figures, so it can differ from the rows above by a few pounds.';
      if (m1 != null && m2 != null && Math.round(m1 * 1000) !== Math.round(m2 * 1000)) {
        note += ` The lower value also moves the bill from the ${mult(m1)} to the ${mult(m2)} multiplier.`;
      }
    } else if (killedReview(p)) {
      note = 'The price is out of line on paper, but the independent review found the VOA would likely defend it, so we do not work out a saving.';
    } else if (p.verdict === 'overpaying' && !p.can_appeal) {
      note = p.bill && p.bill.basis === 'standard_assumed'
        ? 'No saving is worked out here: the bill for this kind of property may use a different multiplier from a shop’s.'
        : 'We do not take this case, so we do not work out a saving.';
    } else if (diff <= 0) {
      note = 'Your Zone A price is at or below the median, so there is no reduction to claim.';
    } else if (isNum(s.n) && Number(s.n) < 5) {
      note = 'Too few shops on this stretch are valued the same way to show that your price is out of line.';
    } else if (!isNum(z.itza)) {
      note = 'The valuation has no zoned area, so there is no saving to work out.';
    } else if (zs != null && zs < 2) {
      note = 'The gap is inside the street\u2019s normal spread. The VOA would likely defend it, so we do not take the case.';
    }
    return html`<section class="card o-calc" aria-labelledby="calc-h">
      <div class="card-head"><h2 class="label" id="calc-h">How we worked this out</h2></div>
      <table class="calc">${rows}</table>
      ${note ? html`<p class="calc-note">${note}</p>` : ''}
    </section>`;
  }
  function segmentCard(p, compSet) {
    const s = p.segment;
    const k = parseSegKey(s.segment_key);
    const shops = (s.shops || []).slice().sort((a, b) => (Number(b.zone_a_price) || 0) - (Number(a.zone_a_price) || 0));
    return html`<section class="card o-seg" aria-labelledby="seg-h">
      <div class="card-head">
        <h2 class="label" id="seg-h">Your street segment</h2>
        <p class="card-sub">${streetName(s)}${k.sector ? ', ' + k.sector : ''} · VOA scheme ${k.scheme || '–'} · ${int(s.n)} shops valued on the same basis</p>
      </div>
      <div class="map" id="seg-map" role="region" aria-label="Map of the shops in this street segment"></div>
      <p class="map-note small muted">Shops are placed at their postcode centre and fanned out where they share one. Select a shop to open it.</p>
      <div class="card-section">
        <h3 class="label">Zone A price of every shop in the segment</h3>
        <div class="strip-wrap" id="strip-wrap"><div class="strip"></div><div class="chart-tip" hidden></div></div>
        ${legendHTML({ strip: true, seg: s })}
        <details class="table-twin">
          <summary>All ${shops.length} shops as a table</summary>
          <div class="table-wrap"><table class="tbl">
            <thead><tr><th>Address</th><th class="col-opt">Use</th><th class="n">Zone A £/m²</th><th class="n col-opt2">ITZA m²</th><th class="n">From median</th></tr></thead>
            <tbody>${shops.map((sh) => html`<tr class="${sh.is_subject ? 'is-subject' : ''}">
              <td class="addr">${sh.is_subject ? html`${shortAddr(tc(sh.address))} (your shop)` : html`<a href="#/p/${String(sh.uarn)}">${shortAddr(tc(sh.address))}</a>`}${!sh.is_subject && compSet.has(String(sh.uarn)) ? html` <span class="small muted">comparable</span>` : ''}</td>
              <td class="col-opt">${sentence(sh.use)}</td>
              <td class="n">${ppm(sh.zone_a_price)}</td>
              <td class="n col-opt2">${f1(sh.itza)}</td>
              <td class="n">${isNum(sh.z) ? zText(sh.z) : '–'}</td>
            </tr>`)}</tbody>
          </table></div>
        </details>
      </div>
    </section>`;
  }
  function compsCard(p) {
    const subj = p.zoning && isNum(p.zoning.zone_a_price) ? Number(p.zoning.zone_a_price) : null;
    // Cheaper shops lead (nearest first); shops priced at or above yours follow and are labelled, never hidden.
    const atOrAbove = (c) => subj != null && isNum(c.zone_a_price) && Number(c.zone_a_price) >= subj - 0.005;
    const comps = (p.comparables || []).map((c, i) => ({ c, i }))
      .sort((a, b) => (atOrAbove(a.c) - atOrAbove(b.c)) || (a.i - b.i)).map((x) => x.c);
    const nHigh = comps.filter(atOrAbove).length;
    return html`<section class="card o-comps" aria-labelledby="comps-h">
      <div class="card-head">
        <h2 class="label" id="comps-h">Similar shops nearby</h2>
        <p class="card-sub">Same segment first, nearest first${nHigh ? html`; the ${nHigh === 1 ? 'shop' : int(nHigh) + ' shops'} priced at or above yours ${nHigh === 1 ? 'is' : 'are'} listed last` : ''}. The Case builder picks its comparables from these.</p>
      </div>
      ${comps.length ? html`<div class="table-wrap"><table class="tbl">
        <thead><tr><th>Address</th><th class="col-opt">Use</th><th class="n">Zone A £/m²</th><th class="n">vs yours</th><th class="n">Distance</th><th class="col-opt2">Segment</th></tr></thead>
        <tbody>${comps.map((c) => {
          const d = subj != null && isNum(c.zone_a_price) ? Number(c.zone_a_price) - subj : null;
          return html`<tr>
            <td class="addr">${UARN_RE.test(String(c.uarn)) ? html`<a href="#/p/${String(c.uarn)}">${shortAddr(tc(c.address))}</a>` : shortAddr(tc(c.address))}</td>
            <td class="col-opt">${sentence(c.use)}</td>
            <td class="n">${ppm(c.zone_a_price)}</td>
            <td class="n">${d == null ? '–' : Math.abs(d) < 0.005 ? html`<span class="secondary">same price as yours</span>` : html`${signedPpm(d)} <span class="muted">(${signedPct((d / subj) * 100)})</span>`}</td>
            <td class="n">${!isNum(c.distance_m) ? '–' : Number(c.distance_m) === 0 ? 'same postcode' : int(c.distance_m) + ' m'}</td>
            <td class="col-opt2">${c.same_segment ? 'Same' : 'Same street'}</td>
          </tr>`;
        })}</tbody>
      </table></div>` : html`<p class="secondary">No comparable shops were found on this street.</p>`}
    </section>`;
  }
  function zoningCard(p) {
    const z = p.zoning;
    const lines = (z && z.lines) || [];
    if (!lines.length) return '';
    const sumVal = lines.reduce((a, l) => a + (isNum(l.value) ? Number(l.value) : 0), 0);
    const sumArea = lines.reduce((a, l) => a + (isNum(l.area) ? Number(l.area) : 0), 0);
    return html`<section class="card o-zoning" aria-labelledby="zone-h">
      <div class="card-head">
        <h2 class="label" id="zone-h">The VOA’s valuation</h2>
        <p class="card-sub">Each part of the property with its area and price per m². Zone A is the first 6.1 m from the window; the next zones count at a half, a quarter and an eighth.</p>
      </div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th class="col-opt2">Floor</th><th>Description</th><th class="n">Area m²</th><th class="n">£/m²</th><th class="n">Value</th></tr></thead>
        <tbody>${lines.map((l) => html`<tr>
          <td class="col-opt2">${sentence(l.floor)}</td><td>${sentence(l.description)}</td>
          <td class="n">${f1(l.area)}</td><td class="n">${ppm(l.price)}</td><td class="n">${money(l.value)}</td>
        </tr>`)}</tbody>
        <tfoot><tr><td class="col-opt2"></td><td>Total</td><td class="n">${f1(sumArea)}</td><td></td><td class="n">${money(sumVal)}</td></tr></tfoot>
      </table></div>
      <p class="small muted" style="margin-top:8px">Rateable value ${money(p.rv)}${isNum(z.itza) ? ' · ITZA ' + f1(z.itza) + ' m²' : ''}${isNum(z.zone_a_area) ? ' · Zone A area ' + f1(z.zone_a_area) + ' m²' : ''}${isNum(z.total_area) ? ' · total area ' + f1(z.total_area) + ' m²' : ''}</p>
    </section>`;
  }
  function noZoningCard(p) {
    return html`<section class="card o-seg" aria-labelledby="nz-h">
      <div class="card-head"><h2 class="label" id="nz-h">No street comparison</h2></div>
      <p class="prose">The VOA did not value this property on a Zone A basis, so there is no price per m² to compare with shops on the street. Overrated only takes cases where the comparison is clear.</p>
      <dl class="kv" style="margin-top:16px">
        <div><dt>Use</dt><dd>${sentence(p.use) || '–'}</dd></div>
        <div><dt>Rateable value</dt><dd>${money(p.rv)}</dd></div>
        <div><dt>Postcode</dt><dd>${p.postcode || '–'}</dd></div>
      </dl>
    </section>`;
  }

  async function viewProperty(ctx, main, uarn) {
    if (!UARN_RE.test(uarn)) {
      renderState(main, { title: 'Property not found', text: 'That is not a valid property reference.', actions: backToSearch });
      return;
    }
    document.title = 'Loading... · Overrated';
    setHTML(main, html`<div class="page-head"><div class="skel h1"></div><div class="skel w40"></div></div>
      <p class="loading-line" role="status">Loading the valuation from the 2026 list...</p>
      <div class="cols" style="margin-top:16px">
        <div class="side"><div class="card"><div class="skel w40"></div><div class="skel h1"></div><div class="skel"></div><div class="skel w70"></div></div></div>
        <div class="main"><div class="card"><div class="skel block"></div></div><div class="card"><div class="skel"></div><div class="skel"></div><div class="skel w70"></div></div></div>
      </div>`);
    let p;
    try {
      p = await api('/api/property/' + uarn, { signal: ctx.signal });
    } catch (e) {
      if (isAbort(e) || !ctx.alive) return;
      if (e.status === 404) renderState(main, { title: 'Not on the 2026 list', text: e.message || 'We could not find this property on the 2026 rating list.', actions: backToSearch });
      else renderState(main, { title: 'Could not load this property', text: e.message, actions: html`<button type="button" class="btn btn-primary" id="retry">Try again</button>${backToSearch}` });
      const r = main.querySelector('#retry');
      if (r) r.addEventListener('click', () => viewProperty(ctx, main, uarn));
      return;
    }
    if (!ctx.alive) return;
    renderProperty(ctx, main, p);
  }

  function renderProperty(ctx, main, p) {
    const addr = tc(p.address);
    document.title = shortAddr(addr) + ' · Overrated';
    const compSet = new Set((p.comparables || []).map((c) => String(c.uarn)));
    const hasSeg = p.segment && Array.isArray(p.segment.shops) && p.segment.shops.length;
    const street = p.segment ? streetName(p.segment) : '';
    setHTML(main, html`<div class="page-head">
        <nav class="crumbs" aria-label="Breadcrumb"><a href="#/">Search</a><span class="sep">/</span><span>${street || shortAddr(addr)}</span></nav>
        <div class="head-row">
          <div class="head-main">
            <h1 tabindex="-1">${addr}</h1>
            <p class="meta">${[sentence(p.use), p.postcode, 'UARN ' + p.uarn].filter(Boolean).join(' · ')}</p>
          </div>
          <dl class="facts">
            <div><dt>Rateable value</dt><dd>${money(p.rv)}</dd></div>
            <div><dt>Est. bill a year</dt><dd>${p.bill ? money(p.bill.annual_bill) : '–'}</dd>${p.bill && isNum(p.bill.multiplier) ? (p.bill.basis === 'standard_assumed'
              ? html`<dd class="sub" title="Retail, hospitality and leisure properties pay a lower multiplier; we have not checked whether this one qualifies.">${mult(p.bill.multiplier)} standard multiplier assumed</dd>`
              : html`<dd class="sub">${mult(p.bill.multiplier)} multiplier, before reliefs</dd>`) : ''}</div>
            ${p.zoning && isNum(p.zoning.zone_a_price) ? html`<div><dt>Zone A price</dt><dd>${ppm(p.zoning.zone_a_price)}/m²</dd>${p.segment ? html`<dd class="sub">street median ${ppm(p.segment.median)}</dd>` : ''}</div>` : ''}
          </dl>
        </div>
      </div>
      <div class="cols prop-cols">
        <div class="side">
          ${verdictCard(p)}
          ${calcCard(p)}
        </div>
        <div class="main">
          ${hasSeg ? segmentCard(p, compSet) : noZoningCard(p)}
          ${(p.comparables || []).length || hasSeg ? compsCard(p) : ''}
          ${zoningCard(p)}
        </div>
      </div>`);
    focusHeading(main);

    const btn = main.querySelector('#appeal-btn');
    if (btn) {
      btn.addEventListener('click', async () => {
        const errEl = main.querySelector('#appeal-error');
        btn.disabled = true;
        btn.textContent = 'Starting the agents...';
        errEl.hidden = true;
        try {
          const r = await apiRaw('/api/appeal', { method: 'POST', body: { uarn: String(p.uarn) }, signal: ctx.signal });
          const d = r.data || {};
          if (d.job_id && UUID_RE.test(d.job_id)) {
            rememberJob(d.job_id, { candidate_id: d.candidate_id || null, uarn: String(p.uarn), address: p.address });
            location.hash = '#/job/' + d.job_id;
          } else if (d.case_id && UUID_RE.test(d.case_id)) {
            location.hash = '#/case/' + d.case_id;
          } else {
            throw new ApiError(r.status, 'The server did not return a job or a case.');
          }
        } catch (e) {
          if (isAbort(e) || !ctx.alive) return;
          btn.disabled = false;
          btn.textContent = btn.dataset.label || 'Challenge this valuation';
          errEl.textContent = e.message;
          errEl.hidden = false;
        }
      });
    }

    if (hasSeg) {
      const s = p.segment;
      const shops = s.shops.slice();
      if (!shops.some((x) => x.is_subject) && p.latlng && p.zoning) {
        shops.push({ uarn: p.uarn, address: p.address, use: p.use, zone_a_price: p.zoning.zone_a_price, itza: p.zoning.itza, z: p.z_score, lat: p.latlng[0], lng: p.latlng[1], is_subject: true });
      }
      const pts = shops.map((sh) => {
        const id = String(sh.uarn);
        const comp = compSet.has(id);
        return {
          id, lat: sh.lat, lng: sh.lng,
          fill: sh.is_subject ? '#16181d' : zColor(sh.z),
          r: sh.is_subject ? 7 : 5,
          stroke: sh.is_subject ? '#ffffff' : comp ? '#16181d' : '#ffffff',
          weight: sh.is_subject || comp ? 2 : 1,
          order: sh.is_subject ? 2 : comp ? 1 : 0,
          label: sh.is_subject ? 'Your shop' : null,
          href: sh.is_subject || !UARN_RE.test(id) ? null : '#/p/' + id,
          tip: part(html`<b>${shortAddr(tc(sh.address))}</b>${sh.is_subject ? ' (your shop)' : comp ? ' (comparable)' : ''}<br><span class="t2">Zone A ${ppm(sh.zone_a_price)}/m²${isNum(sh.z) ? ' · ' + zText(sh.z) : ''}</span>`),
        };
      });
      const m = mountMap(main.querySelector('#seg-map'), pts, { emptyText: 'No locations are available for this segment.' });
      ctx.onCleanup(m.destroy);
      const strip = mountStrip(main.querySelector('#strip-wrap'), Object.assign({}, s, { shops }), compSet);
      ctx.onCleanup(strip.destroy);
    }
  }

  // ---------------------------------------------------------------------------
  // Job and case: live process tracker, approval, case timeline, agent log
  // ---------------------------------------------------------------------------
  const STATUS_TEXT = { pending: 'Pending', running: 'Running', done: 'Done', failed: 'Failed', skipped: 'Skipped', waiting: 'Waiting' };
  const STATUS_BADGE = { running: 'badge-blue', done: 'badge-green', failed: 'badge-red', waiting: 'badge-blue', pending: '', skipped: '' };
  function statusText(s) {
    if (s.status === 'waiting') return s.key === 'owner' ? 'Waiting for you' : s.key === 'check' || s.key === 'decision' ? 'Waiting for the VOA' : 'In progress';
    return STATUS_TEXT[s.status] || sentence(s.status || 'pending');
  }
  function normStages(list) {
    const byKey = {};
    (Array.isArray(list) ? list : []).forEach((s) => { if (s && s.key) byKey[s.key] = s; });
    return STAGES.map((d) => {
      const s = byKey[d.key] || {};
      return {
        key: d.key, short: d.short, doing: d.doing,
        agent: s.agent || d.agent,
        label: s.label || d.label,
        status: STATUS_TEXT[s.status] ? s.status : 'pending',
        started_at: s.started_at || null,
        finished_at: s.finished_at || null,
        summary: s.summary || null,
        steps: Array.isArray(s.steps) ? s.steps.filter((x) => x && (x.text || x.message)) : [],
      };
    });
  }
  function nodeIcon(status, n) {
    if (status === 'done') return raw(ICON_CHECK);
    if (status === 'failed') return raw(ICON_CROSS);
    if (status === 'skipped') return '–';
    return String(n);
  }

  function viewFlow(ctx, main, ids) {
    const id = ids.jobId || ids.caseId;
    if (!UUID_RE.test(id)) {
      renderState(main, { title: 'Case not found', text: 'That link does not point to a case or a running job.', actions: backToSearch });
      return;
    }
    document.title = 'Case · Overrated';
    const st = {
      mode: ids.jobId ? 'job' : 'case',
      jobId: ids.jobId || null,
      caseId: ids.caseId || null,
      meta: ids.jobId ? recallJob(ids.jobId) : {},
      job: null, kase: null, prop: null, propLoading: false,
      events: [], eventKeys: new Set(), eventsLoaded: false, eventsError: null,
      stepKeys: new Set(), stepsLoaded: false,
      open: new Map(), sigs: {}, lastStatus: {},
      busy: '', actionError: '', lastAction: '', loadError: null, timer: 0, headFocused: false,
    };
    setHTML(main, html`<div class="page-head" id="f-head"><div class="skel h1"></div><div class="skel w40"></div></div>
      <section class="progress" id="f-progress" aria-label="Overall progress"></section>
      <div class="cols">
        <div class="main" id="f-main">
          <div class="card"><p class="loading-line" role="status">Loading the case...</p><div class="skel w70" style="margin-top:12px"></div><div class="skel"></div><div class="skel w40"></div></div>
        </div>
        <div class="side">
          <section class="card log-card" aria-labelledby="log-h">
            <div class="card-head"><h2 class="label" id="log-h">Agent log</h2><span class="small muted" id="f-logmeta"></span></div>
            <ol class="log" id="f-log"><li class="log-empty">Loading...</li></ol>
          </section>
        </div>
      </div>
      <div class="sr-only" aria-live="polite" id="f-live"></div>`);
    const elHead = main.querySelector('#f-head');
    const elProg = main.querySelector('#f-progress');
    const elMain = main.querySelector('#f-main');
    const elLog = main.querySelector('#f-log');
    const elLogMeta = main.querySelector('#f-logmeta');
    const elLive = main.querySelector('#f-live');

    elMain.addEventListener('toggle', (e) => {
      const d = e.target;
      if (!d || !d.matches || !d.matches('details[data-stage]')) return;
      if (d.open === (d.dataset.default === '1')) st.open.delete(d.dataset.stage);
      else st.open.set(d.dataset.stage, d.open);
    }, true);
    // "Review and approve" in the progress line scrolls to the approval box (a plain #approve link would change the route)
    elProg.addEventListener('click', (e) => {
      const a = e.target.closest('[data-jump]');
      if (!a) return;
      e.preventDefault();
      const t = elMain.querySelector('#' + a.dataset.jump);
      if (t) { t.scrollIntoView({ behavior: 'smooth', block: 'center' }); const b = t.querySelector('button'); if (b) b.focus({ preventScroll: true }); }
    });
    elMain.addEventListener('click', (e) => {
      const b = e.target.closest('[data-action]');
      if (!b || b.disabled) return;
      if (b.dataset.action === 'approve') approve();
      else if (b.dataset.action === 'simulate') simulate(b.dataset.reply);
      else if (b.dataset.action === 'retry') { st.loadError = null; tick(); }
    });

    function model() {
      const j = st.job, c = st.kase;
      return {
        uarn: (c && c.uarn) || (j && j.uarn) || st.meta.uarn || null,
        address: (c && c.address) || (st.prop && st.prop.address) || st.meta.address || null,
        caseId: [c && c.case_id, j && j.case_id].find((x) => x && UUID_RE.test(x)) || null,
        logCaseId: [c && c.case_id, j && j.case_id, j && j.events_case_id].find((x) => x && UUID_RE.test(x)) || null,
        stages: normStages((c && c.stages) || (j && j.stages) || []),
        verdict: (c && c.verdict) || (j && j.verdict) || null,
        jobStatus: j ? j.status : null,
        jobError: j ? j.error : null,
        kase: c,
      };
    }
    const killed = (v) => !!(v.verdict && v.verdict.verdict === 'kill');

    function overall(v) {
      const by = (k) => v.stages.find((x) => x.key === k);
      if (killed(v)) return { text: 'Stopped by the advocate', cls: 'badge-red' };
      if (v.jobStatus === 'failed' || v.stages.some((x) => x.status === 'failed')) return { text: 'Failed', cls: 'badge-red' };
      if (v.kase && v.kase.stage === 'resolved') return { text: 'Resolved', cls: 'badge-green' };
      if (v.kase && v.kase.stage === 'closed') return { text: 'Closed', cls: '' };
      if (by('owner').status === 'waiting') return { text: 'Waiting for you', cls: 'badge-blue' };
      if (v.kase && v.kase.owner_approved) return { text: stageText(v.kase.stage), cls: 'badge-blue' };
      if (v.jobStatus === 'running') return { text: 'Agents working', cls: 'badge-blue' };
      if (!st.job && !st.kase) return { text: 'Loading', cls: '' };
      return { text: 'In progress', cls: '' };
    }

    function renderHead(v) {
      const addr = v.address ? tc(v.address) : v.uarn ? 'UARN ' + v.uarn : 'your property';
      const o = overall(v);
      const ref = v.kase && v.kase.pack && v.kase.pack.case_ref;
      const meta = [ref ? 'Case ' + ref : null, v.uarn ? 'UARN ' + v.uarn : null, v.kase && v.kase.pack ? 'Current RV ' + money(v.kase.pack.current_rv) : (st.prop ? 'Current RV ' + money(st.prop.rv) : null), 'VOA filing is mocked'].filter(Boolean);
      patch('head', [addr, o, meta], () => {
        setHTML(elHead, html`<nav class="crumbs" aria-label="Breadcrumb"><a href="#/">Search</a><span class="sep">/</span>
            ${v.uarn ? html`<a href="#/p/${v.uarn}">${shortAddr(addr)}</a><span class="sep">/</span>` : ''}<span>Challenge</span></nav>
          <div class="head-row">
            <div class="head-main"><h1 tabindex="-1">Challenge: ${addr}</h1><p class="meta">${meta.join(' · ')}</p></div>
            <span class="badge ${o.cls}">${o.text}</span>
          </div>`);
        document.title = 'Challenge: ' + shortAddr(addr) + ' · Overrated';
        if (!st.headFocused && (st.job || st.kase)) { st.headFocused = true; focusHeading(main); }
      });
    }

    // The step the case is on: the first one not yet done or skipped (the last done one once all are finished).
    function currentStage(stages) {
      return stages.find((s) => s.status !== 'done' && s.status !== 'skipped')
        || stages.slice().reverse().find((s) => s.status === 'done')
        || stages[0];
    }
    function renderProgress(v) {
      const cur = currentStage(v.stages);
      const i = v.stages.indexOf(cur) + 1;
      let text;
      if (!st.job && !st.kase) text = html`Loading...`;
      else if (killed(v)) text = html`<b>Stopped at step 3 of 7.</b> <span>The VOA advocate found reasons the VOA would win, so nothing will be filed.</span>`;
      else if (cur.status === 'failed') text = html`<b>Stopped at step ${i} of 7.</b> <span>${cur.label} failed.</span>`;
      else if (v.kase && v.kase.stage === 'resolved') text = html`<b>Complete.</b> <span>${v.kase.outcome === 'reduced' || v.kase.outcome === 'partially_reduced' ? 'The VOA reduced the value (mock).' : 'The case is resolved.'}</span>`;
      else if (cur.status === 'running') text = html`<b>Step ${i} of 7.</b> <span>${cur.doing}</span>${cur.started_at ? html` <span class="muted small" data-since="${cur.started_at}">${fdur(Date.now() - toDate(cur.started_at))}</span>` : ''}`;
      else if (cur.status === 'waiting' && cur.key === 'owner' && v.caseId) text = html`<b>Step ${i} of 7.</b> <span>Nothing is filed until you approve.</span> <a href="#approve" data-jump="approve">Review and approve</a>`;
      else if (cur.status === 'waiting') text = html`<b>Step ${i} of 7.</b> <span>${cur.doing}</span>`;
      else text = html`<b>Step ${i} of 7.</b> <span>${cur.label}</span>`;
      patch('progress', [v.stages.map((s) => s.status), cur.key, cur.started_at, killed(v), v.kase && v.kase.stage, !!(st.job || st.kase), !!v.caseId], () => {
        setHTML(elProg, html`<p class="progress-text" aria-live="polite">${text}</p>
          <ol class="segbar">${v.stages.map((s) => html`<li class="seg seg-${s.status}" title="${s.label}: ${statusText(s)}">
            <span class="seg-bar"></span><span class="seg-label" aria-hidden="true">${s.short}</span><span class="sr-only">${s.label}: ${statusText(s)}</span></li>`)}</ol>`);
      });
    }

    function stepHTML(stageKey, step) {
      const ts = step.ts || step.at || null;
      const text = step.text || step.message || '';
      const k = stageKey + '|' + ts + '|' + text;
      const fresh = st.stepsLoaded && !st.stepKeys.has(k);
      st.stepKeys.add(k);
      return html`<li class="${fresh ? 'new' : ''}"><time datetime="${ts || ''}">${ftimeSec(ts)}</time><span>${text}</span></li>`;
    }
    function timeRange(s) {
      if (s.status === 'running' && s.started_at) {
        return html`<span class="stage-time">since ${ftime(s.started_at)} · <span data-since="${s.started_at}">${fdur(Date.now() - toDate(s.started_at))}</span></span>`;
      }
      if (s.started_at && s.finished_at) {
        const d = toDate(s.finished_at) - toDate(s.started_at);
        return html`<span class="stage-time">${ftime(s.started_at)}${ftime(s.finished_at) !== ftime(s.started_at) ? '–' + ftime(s.finished_at) : ''}${isNum(d) && d >= 0 && d < 36e5 ? ' · ' + fdur(d) : ''}</span>`;
      }
      if (s.finished_at) return html`<span class="stage-time">${fdate(s.finished_at)}, ${ftime(s.finished_at)}</span>`;
      if (s.started_at) return html`<span class="stage-time">since ${ftime(s.started_at)}</span>`;
      return '';
    }
    const defaultOpen = (s) => s.status === 'running' || s.status === 'failed';
    function verdictBox(vd) {
      const kind = vd.verdict === 'kill' ? 'kill' : vd.verdict === 'survive' ? 'survive' : '';
      const head = kind === 'kill' ? 'Killed: the advocate found reasons the VOA would win'
        : kind === 'survive' ? 'Survived: the advocate could not knock the case down' : 'The advocate wants more evidence';
      const reasons = Array.isArray(vd.reasons) ? vd.reasons : [];
      return html`<div class="stage-extra verdict-box ${kind}">
        <p class="vb-head">${head}</p>
        ${reasons.length ? html`<ol class="reasons">${reasons.map((r) => {
          const text = typeof r === 'string' ? r : (r && (r.reason || r.text)) || '';
          const rec = r && typeof r === 'object' && r.voa_record != null ? String(r.voa_record) : '';
          return html`<li><span>${text}</span>${rec ? html`<span class="rec">VOA record: ${UARN_RE.test(rec) ? html`<a href="#/p/${rec}">UARN ${rec}</a>` : rec}</span>` : ''}</li>`;
        })}</ol>` : ''}
        ${vd.advocate_version ? html`<p class="small muted" style="margin-top:8px">${vd.advocate_version}</p>` : ''}
      </div>`;
    }
    function approveBox(v) {
      const busy = st.busy === 'approve';
      return html`<div class="stage-extra approve-box" id="approve">
        <p>Read the evidence pack, then approve to file a Check with the VOA for this property.</p>
        <div class="btn-row">
          <button type="button" class="btn btn-primary" data-action="approve" ${st.busy ? raw('disabled') : ''}>${busy ? 'Filing the Check...' : 'Approve and file the Check'}</button>
          <a class="btn btn-secondary" href="/report/${v.caseId}" target="_blank" rel="noopener">Review the evidence pack ${raw(ICON_EXT)}</a>
        </div>
        <p class="consent">By approving you appoint Overrated as your agent for this Check. Mock filing: nothing is sent to the VOA.</p>
        ${st.actionError && st.lastAction === 'approve' ? html`<p class="form-error" role="alert">${st.actionError}</p>` : ''}
      </div>`;
    }
    // Blocks that say more than the stage summary; when one is shown, the summary is left out so nothing is said twice.
    const showsVerdict = (s, v) => s.key === 'advocate' && !!v.verdict && s.status !== 'pending' && s.status !== 'running';
    const showsApprove = (s, v) => s.key === 'owner' && s.status === 'waiting' && !!v.caseId;
    function stageExtra(s, v) {
      const out = [];
      if (s.key === 'builder' && s.status === 'done' && v.caseId) {
        out.push(html`<div class="stage-extra btn-row">
          <a class="btn btn-secondary btn-sm" href="/report/${v.caseId}" target="_blank" rel="noopener">View evidence pack ${raw(ICON_EXT)}</a>
        </div>`);
      }
      if (showsVerdict(s, v)) out.push(verdictBox(v.verdict));
      if (showsApprove(s, v)) out.push(approveBox(v));
      if (s.status === 'failed' && v.jobError && v.jobError !== s.summary) out.push(html`<p class="stage-error">${v.jobError}</p>`);
      return out;
    }
    function stageHTML(s, i, v) {
      const open = st.open.has(s.key) ? st.open.get(s.key) : defaultOpen(s);
      const summary = showsVerdict(s, v) || showsApprove(s, v) ? null : s.summary;
      return html`<li class="stage st-${s.status}" id="stage-${s.key}">
        <div class="node" aria-hidden="true">${nodeIcon(s.status, i + 1)}</div>
        <div class="stage-body">
          <div class="stage-head">
            <div><div class="stage-title">${s.label}</div><div class="stage-agent">${agentName(s.agent)}</div></div>
            <div class="stage-meta">${timeRange(s)}<span class="badge ${STATUS_BADGE[s.status] || ''}">${statusText(s)}</span></div>
          </div>
          ${summary ? html`<p class="stage-summary">${summary}</p>` : ''}
          ${stageExtra(s, v)}
          ${s.steps.length ? html`<details class="steps-d" data-stage="${s.key}" data-default="${defaultOpen(s) ? '1' : '0'}" ${open ? raw('open') : ''}>
            <summary>${s.steps.length} step${s.steps.length === 1 ? '' : 's'}</summary>
            <ol class="steps">${s.steps.map((x) => stepHTML(s.key, x))}</ol>
          </details>` : ''}
        </div>
      </li>`;
    }
    function normHist(h) {
      if (!h) return null;
      if (typeof h === 'string') return { ts: null, text: h };
      const ts = h.ts || h.at || h.date || h.time || h.created_at || null;
      const text = h.message || h.note || h.text || h.event || h.action || (h.stage ? stageText(h.stage) : '') || h.kind || '';
      return text ? { ts, text: String(text) } : null;
    }
    function outcomeBlock(c) {
      const pk = c.pack || {};
      if (c.outcome === 'reduced' || c.outcome === 'partially_reduced') {
        return html`<div class="outcome">
          <p class="o-head">${c.outcome === 'reduced' ? 'The VOA reduced the value (mock reply)' : 'The VOA partly reduced the value (mock reply)'}</p>
          <dl class="figs">
            <div><dt>Was</dt><dd>${money(pk.current_rv)}</dd></div>
            <div><dt>Now</dt><dd>${money(c.final_rv)}</dd></div>
            <div><dt>Confirmed saving a year</dt><dd class="pos">${money(c.confirmed_annual_saving)}</dd></div>
          </dl>
          <p class="small secondary" style="margin-top:8px">The council refunds what was overpaid since the list started; our fee is a share of that refund.</p>
        </div>`;
      }
      if (c.outcome === 'unchanged' && (c.stage === 'closed' || c.stage === 'resolved')) {
        return html`<div class="outcome neutral"><p class="o-head">The VOA kept the value</p><p class="small secondary">No fee is due.</p></div>`;
      }
      if (c.outcome === 'withdrawn') return html`<div class="outcome neutral"><p class="o-head">The case was withdrawn</p></div>`;
      return '';
    }
    function caseCard(v) {
      const c = v.kase;
      const done = c.stage === 'resolved' || c.stage === 'closed';
      const hist = (Array.isArray(c.history) ? c.history : []).map(normHist).filter(Boolean)
        .sort((a, b) => (toDate(b.ts) || 0) - (toDate(a.ts) || 0));
      const simBusy = st.busy === 'simulate';
      const atCheck = ['check_draft', 'check_submitted', 'waiting_check_decision'].includes(c.stage);
      return html`<section class="card" aria-labelledby="case-h">
        <div class="card-head"><h2 class="label" id="case-h" tabindex="-1">Case tracker</h2><span class="badge ${done ? 'badge-green' : 'badge-blue'}">${stageText(c.stage)}</span></div>
        <dl class="kv">
          <div><dt>VOA reference</dt><dd>${c.external_case_ref || '–'}<span class="tag">mock</span></dd></div>
          <div><dt>Filed</dt><dd>${c.submitted_at ? fdate(c.submitted_at) + ', ' + ftime(c.submitted_at) : '–'}</dd></div>
          ${!done ? html`<div><dt>Next deadline</dt><dd>${fdate(c.next_deadline)}</dd><dd class="sub">mock VOA target date</dd></div>` : ''}
          ${c.next_action ? html`<div><dt>Next action</dt><dd>${c.next_action}</dd></div>` : ''}
          ${done && c.resolved_at ? html`<div><dt>Resolved</dt><dd>${fdate(c.resolved_at)}</dd></div>` : ''}
        </dl>
        ${outcomeBlock(c)}
        <ol class="timeline" aria-label="Case history, newest first">
          ${c.next_action ? html`<li class="next"><span class="when">${c.next_deadline ? 'by ' + fdate(c.next_deadline) : ''}</span><span class="pin"></span><span class="what">Next: ${c.next_action}</span></li>` : ''}
          ${hist.map((h, i) => html`<li class="${i === 0 ? 'latest' : ''}"><span class="when">${h.ts ? fdate(h.ts) + ' ' + ftime(h.ts) : ''}</span><span class="pin"></span><span class="what">${h.text}</span></li>`)}
        </ol>
        ${!done ? html`<div class="demo" role="group" aria-labelledby="demo-h">
          <p class="demo-label" id="demo-h">Demo: simulate VOA reply</p>
          <p>Mock only. In a real case this reply comes from the VOA; here you pick it to see what happens next.${atCheck ? '' : ' The Check came back unchanged, so this is the reply to the Challenge.'}</p>
          <div class="btn-row">
            <button type="button" class="btn btn-secondary btn-sm" data-action="simulate" data-reply="reduced" ${st.busy ? raw('disabled') : ''}>Reduced</button>
            ${atCheck ? html`<button type="button" class="btn btn-secondary btn-sm" data-action="simulate" data-reply="unchanged" ${st.busy ? raw('disabled') : ''}>Unchanged</button>` : ''}
            ${simBusy ? html`<span class="small secondary">Sending...</span>` : ''}
          </div>
          ${st.actionError && st.lastAction === 'simulate' ? html`<p class="form-error" role="alert">${st.actionError}</p>` : ''}
        </div>` : ''}
      </section>`;
    }
    function packCard(v) {
      const pk = v.kase.pack;
      const calc = pk.calc || {};
      const comps = Array.isArray(pk.comparables) ? pk.comparables : [];
      return html`<section class="card" aria-labelledby="pack-h">
        <div class="card-head">
          <h2 class="label" id="pack-h">Evidence pack${pk.case_ref ? ' ' + pk.case_ref : ''}</h2>
          <a class="btn btn-secondary btn-sm" href="/report/${v.caseId}" target="_blank" rel="noopener">Open the printable pack ${raw(ICON_EXT)}</a>
        </div>
        <dl class="figs">
          <div><dt>Current RV</dt><dd>${money(pk.current_rv)}</dd></div>
          <div><dt>Proposed RV</dt><dd>${money(pk.proposed_rv)}</dd></div>
          <div><dt>Saving a year</dt><dd class="pos">${money(pk.annual_saving)}</dd></div>
          <div><dt>Zone A / median</dt><dd>${ppm(calc.zone_a_price)} / ${ppm(calc.street_median)}</dd></div>
        </dl>
        ${pk.argument ? html`<div class="card-section"><h3 class="label">Argument</h3><p class="argument">${pk.argument}</p></div>` : ''}
        ${comps.length ? html`<div class="card-section"><h3 class="label">Comparables in the pack (${comps.length})</h3>
          <div class="table-wrap"><table class="tbl">
            <thead><tr><th>Property</th><th class="col-opt">Use</th><th class="n">Zone A £/m²</th><th class="n">Distance</th></tr></thead>
            <tbody>${comps.map((c) => {
              const u = String(c.uarn || '');
              const label = c.address ? shortAddr(tc(c.address)) : 'UARN ' + u;
              return html`<tr><td>${UARN_RE.test(u) ? html`<a href="#/p/${u}">${label}</a>` : label}</td><td class="col-opt">${sentence(c.use)}</td>
                <td class="n">${ppm(c.zone_a_price)}</td><td class="n">${!isNum(c.distance_m) ? '–' : Number(c.distance_m) === 0 ? 'same postcode' : int(c.distance_m) + ' m'}</td></tr>`;
            })}</tbody>
          </table></div></div>` : ''}
        <p class="small muted" style="margin-top:12px">${[pk.builder_version, pk.status ? 'pack status: ' + String(pk.status).replace(/_/g, ' ') : null].filter(Boolean).join(' · ')}</p>
      </section>`;
    }
    function renderMain(v) {
      const sig = [v.stages, v.verdict, v.caseId, v.jobStatus, v.jobError, v.kase, st.busy, st.actionError, st.lastAction, st.loadError && st.loadError.message, !!(st.job || st.kase)];
      patch('main', sig, () => {
        if (!st.job && !st.kase) {
          if (st.loadError) {
            setHTML(elMain, html`<div class="notice err" role="alert">${st.loadError.message}
              ${st.loadError.status === 404 ? '' : html` <button type="button" class="linkbtn" data-action="retry">Try again</button>`}</div>`);
          }
          return;
        }
        const failedNoStage = v.jobStatus === 'failed' && !v.stages.some((s) => s.status === 'failed');
        setHTML(elMain, html`
          ${st.loadError ? html`<div class="notice err" role="alert">Lost contact with the server: ${st.loadError.message}. Retrying...</div>` : ''}
          ${failedNoStage ? html`<div class="notice err" role="alert">The pipeline failed${v.jobError ? ': ' + v.jobError : '.'}</div>` : ''}
          ${v.kase && v.kase.owner_approved ? caseCard(v) : ''}
          <section class="card" aria-labelledby="proc-h">
            <div class="card-head"><h2 class="label" id="proc-h">Process</h2><span class="small muted">One agent per step; every step is written to the agent log.</span></div>
            <ol class="tracker" aria-busy="${v.jobStatus === 'running' ? 'true' : 'false'}">${v.stages.map((s, i) => stageHTML(s, i, v))}</ol>
          </section>
          ${v.kase && v.kase.pack ? packCard(v) : ''}`);
        st.stepsLoaded = true;
      });
    }

    function renderLog() {
      patch('log', [st.events.length, st.events[0] && st.events[0].ts, st.eventsError && st.eventsError.message, st.eventsLoaded, st.events.map((e) => e._new)], () => {
        if (!st.events.length) {
          setHTML(elLog, html`<li class="log-empty">${st.eventsError ? 'The log is unavailable: ' + st.eventsError.message : st.eventsLoaded ? 'No agent activity yet.' : 'Waiting for the first agent...'}</li>`);
        } else {
          setHTML(elLog, st.events.map((e) => html`<li class="${e._new ? 'new' : ''}">
            <div class="lh"><time datetime="${e.ts || ''}">${ftimeSec(e.ts)}</time><span class="ag">${agentName(e.agent)}</span>${isNum(e.tokens) && Number(e.tokens) > 0 ? html`<span class="tok">${int(e.tokens)} tokens</span>` : ''}</div>
            <div class="msg">${e.message || ''}</div>
          </li>`));
        }
        const tokens = st.events.reduce((a, e) => a + (isNum(e.tokens) ? Number(e.tokens) : 0), 0);
        const live = (st.mode === 'job' && st.job && st.job.status === 'running');
        setHTML(elLogMeta, html`${live ? html`<span class="live-dot" aria-hidden="true"></span>live · ` : ''}${st.events.length} entr${st.events.length === 1 ? 'y' : 'ies'}${tokens ? ' · ' + int(tokens) + ' tokens' : ''}`);
      });
    }
    function announce(v) {
      const changes = [];
      v.stages.forEach((s) => {
        const prev = st.lastStatus[s.key];
        if (prev && prev !== s.status) changes.push(`${s.short}: ${statusText(s).toLowerCase()}.`);
        st.lastStatus[s.key] = s.status;
      });
      if (changes.length) elLive.textContent = changes.join(' ');
    }
    function patch(key, sigObj, fn) {
      const s = JSON.stringify(sigObj);
      if (st.sigs[key] === s) return;
      st.sigs[key] = s;
      fn();
    }
    function renderAll() {
      if (!st.job && !st.kase && st.loadError && st.loadError.status === 404) {
        renderState(main, {
          title: st.mode === 'job' ? 'This job is no longer running' : 'Case not found',
          text: st.loadError.message,
          actions: html`${st.meta.uarn && UARN_RE.test(st.meta.uarn) ? html`<a class="btn btn-primary" href="#/p/${st.meta.uarn}">Open the property</a>` : ''}${backToSearch}`,
        });
        return;
      }
      const v = model();
      renderHead(v);
      renderProgress(v);
      renderMain(v);
      renderLog();
      announce(v);
    }

    async function loadProp(uarn) {
      if (!UARN_RE.test(String(uarn)) || st.propLoading || st.prop) return;
      st.propLoading = true;
      try { st.prop = await api('/api/property/' + uarn, { signal: ctx.signal }); } catch (e) { if (isAbort(e)) return; }
      st.propLoading = false;
      if (ctx.alive) renderAll();
    }
    async function loadEvents() {
      const v = model();
      const candId = (st.kase && st.kase.candidate && st.kase.candidate.candidate_id) || (st.job && st.job.candidate_id) || st.meta.candidate_id;
      // Every events row of a run carries its case id from the first step, so the log follows this run only.
      // The candidate id (which also covers earlier runs for the same shop) is a fallback before the job answers.
      const qs = [];
      if (v.logCaseId) qs.push('case_id=' + encodeURIComponent(v.logCaseId));
      else if (candId && UUID_RE.test(candId)) qs.push('candidate_id=' + encodeURIComponent(candId));
      if (!qs.length) return;
      let err = null;
      const lists = await Promise.all(qs.map((q) => api('/api/events?' + q + '&limit=100', { signal: ctx.signal })
        .then((d) => (d && Array.isArray(d.events) ? d.events : []))
        .catch((e) => { if (isAbort(e)) throw e; err = e; return []; })));
      const merged = [];
      const seen = new Set();
      lists.flat().forEach((e) => {
        const k = (e.ts || '') + '|' + (e.agent || '') + '|' + (e.message || '');
        if (seen.has(k)) return;
        seen.add(k);
        merged.push(Object.assign({}, e, { _k: k, _new: st.eventsLoaded && !st.eventKeys.has(k) }));
      });
      merged.sort((a, b) => (toDate(b.ts) || 0) - (toDate(a.ts) || 0));
      merged.forEach((e) => st.eventKeys.add(e._k));
      st.events = merged.slice(0, 100);
      st.eventsError = merged.length ? null : err;
      st.eventsLoaded = true;
    }
    async function tryCase(caseId) {
      try { return await api('/api/case/' + caseId, { signal: ctx.signal }); } catch (e) { if (isAbort(e)) throw e; return null; }
    }
    function switchToCase(c) {
      st.kase = c;
      st.mode = 'case';
      st.caseId = c.case_id;
      if (c.case_id && location.hash !== '#/case/' + c.case_id) history.replaceState(null, '', '#/case/' + c.case_id);
    }
    function schedule() {
      clearTimeout(st.timer);
      if (!ctx.alive) return;
      if (st.loadError && st.loadError.status === 404) return;
      if (st.loadError) { st.timer = ctx.later(tick, 4000); return; }
      if (st.mode === 'job' && (!st.job || st.job.status === 'running')) st.timer = ctx.later(tick, POLL_MS);
      else if (st.mode === 'case' && st.kase) {
        const v = model();
        if (v.stages.some((s) => s.status === 'running')) st.timer = ctx.later(tick, POLL_MS);
        else if (killed(v) || ['resolved', 'closed'].includes(st.kase.stage)) return;
        else st.timer = ctx.later(tick, CASE_POLL_MS);
      }
    }
    async function tick() {
      if (!ctx.alive) return;
      clearTimeout(st.timer);
      try {
        if (st.mode === 'job') {
          const job = await api('/api/job/' + st.jobId, { signal: ctx.signal });
          if (!ctx.alive) return;
          st.job = job;
          st.loadError = null;
          if (job.uarn && !st.meta.uarn) st.meta.uarn = String(job.uarn);
          if (job.uarn && !st.prop && !st.meta.address) loadProp(String(job.uarn));
          if (job.status !== 'running' && job.case_id && UUID_RE.test(job.case_id)) {
            const c = await tryCase(job.case_id);
            if (c && ctx.alive) switchToCase(c);
          }
        } else {
          const c = await api('/api/case/' + st.caseId, { signal: ctx.signal });
          if (!ctx.alive) return;
          st.kase = c;
          st.loadError = null;
        }
      } catch (e) {
        if (isAbort(e) || !ctx.alive) return;
        st.loadError = e.status === 404
          ? new ApiError(404, st.mode === 'job'
            ? 'This job is no longer running on the server (it may have restarted). Open the property again to see its case.'
            : 'There is no case with this reference.')
          : e;
      }
      try { await loadEvents(); } catch (e) { if (isAbort(e)) return; }
      if (!ctx.alive) return;
      renderAll();
      schedule();
    }
    async function approve() {
      const v = model();
      if (!v.caseId || st.busy) return;
      st.busy = 'approve'; st.actionError = ''; st.lastAction = 'approve';
      renderAll();
      try {
        const c = await api('/api/case/' + v.caseId + '/approve', { method: 'POST', signal: ctx.signal });
        if (!ctx.alive) return;
        switchToCase(c);
      } catch (e) {
        if (isAbort(e) || !ctx.alive) return;
        st.actionError = e.message;
      }
      st.busy = '';
      try { await loadEvents(); } catch (e) { if (isAbort(e)) return; }
      if (!ctx.alive) return;
      renderAll();
      if (!st.actionError) { const h = elMain.querySelector('#case-h'); if (h) { h.focus({ preventScroll: true }); h.scrollIntoView({ block: 'nearest' }); } }
      schedule();
    }
    async function simulate(reply) {
      const v = model();
      if (!v.caseId || st.busy || (reply !== 'reduced' && reply !== 'unchanged')) return;
      st.busy = 'simulate'; st.actionError = ''; st.lastAction = 'simulate';
      renderAll();
      try {
        const c = await api('/api/case/' + v.caseId + '/simulate', { method: 'POST', body: { reply }, signal: ctx.signal });
        if (!ctx.alive) return;
        switchToCase(c);
      } catch (e) {
        if (isAbort(e) || !ctx.alive) return;
        st.actionError = e.message;
      }
      st.busy = '';
      try { await loadEvents(); } catch (e) { if (isAbort(e)) return; }
      if (!ctx.alive) return;
      renderAll();
      schedule();
    }
    // live elapsed counters on running stages
    ctx.every(() => {
      main.querySelectorAll('[data-since]').forEach((el) => {
        const d = toDate(el.getAttribute('data-since'));
        if (d) el.textContent = fdur(Date.now() - d);
      });
    }, 1000);
    tick();
  }

  // ---------------------------------------------------------------------------
  // Area sweep
  // ---------------------------------------------------------------------------
  async function viewArea(ctx, main) {
    document.title = 'Area sweep: Hackney · Overrated';
    setHTML(main, html`<div class="page-head"><div class="skel h1"></div><div class="skel w40"></div></div>
      <p class="loading-line" role="status">Loading the latest Scout sweep...</p>
      <div class="card" style="margin-top:16px"><div class="skel block"></div></div>`);
    let d;
    try {
      d = await api('/api/area', { signal: ctx.signal });
    } catch (e) {
      if (isAbort(e) || !ctx.alive) return;
      renderState(main, { title: 'Area sweep unavailable', text: e.message, actions: html`<button type="button" class="btn btn-primary" id="retry">Try again</button>` });
      const r = main.querySelector('#retry');
      if (r) r.addEventListener('click', () => { if (ctx.alive) viewArea(ctx, main); });
      return;
    }
    if (!ctx.alive) return;
    const run = (d && d.run) || {};
    const cands = ((d && d.candidates) || []).map((c) => Object.assign({}, c, {
      uarn: String(c.uarn),
      above: isNum(c.zone_a_price) && isNum(c.street_median) && Number(c.street_median) > 0
        ? ((Number(c.zone_a_price) - Number(c.street_median)) / Number(c.street_median)) * 100 : null,
    }));
    const total = cands.reduce((a, c) => a + (isNum(c.est_saving) ? Number(c.est_saving) : 0), 0);
    const name = run.area_name || 'Hackney';
    document.title = 'Area sweep: ' + name + ' · Overrated';
    setHTML(main, html`<div class="page-head">
        <nav class="crumbs" aria-label="Breadcrumb"><a href="#/">Search</a><span class="sep">/</span><span>Area sweep</span></nav>
        <div class="head-row">
          <div class="head-main">
            <h1 tabindex="-1">Area sweep: ${name}</h1>
            <p class="meta">The Scout scored every shop on the 2026 list in ${name} against its street segment.${run.finished_at ? ' Finished ' + fdate(run.finished_at) + ', ' + ftime(run.finished_at) + '.' : run.status ? ' Status: ' + run.status + '.' : ''}</p>
          </div>
          <dl class="facts">
            <div><dt>Shops checked</dt><dd>${int(run.properties_checked)}</dd></div>
            <div><dt>Candidates</dt><dd>${int(cands.length)}</dd></div>
            <div><dt>Est. saving to 2029</dt><dd>${money(total)}</dd></div>
          </dl>
        </div>
      </div>
      ${cands.length ? html`<div class="main" style="display:grid;gap:16px">
        <section class="card" aria-labelledby="amap-h">
          <div class="card-head"><h2 class="label" id="amap-h">Candidates on the map</h2>
            <p class="card-sub">Colour shows how far each shop’s Zone A price sits from its street’s median. Select a shop to open it.</p></div>
          <div class="map map-area" id="area-map" role="region" aria-label="Map of candidate shops"></div>
          ${legendHTML({ subject: false, comps: false })}
        </section>
        <section class="card" aria-labelledby="atable-h">
          <div class="card-head"><h2 class="label" id="atable-h">All candidates</h2><p class="card-sub">Largest estimated saving first. Select a column heading to sort by it.</p></div>
          <div class="table-wrap"><table class="tbl" id="area-table">
            <thead><tr>
              <th scope="col">Address</th>
              <th scope="col" class="n" data-sort="zone_a_price"><button type="button" class="sort">Zone A £/m²</button></th>
              <th scope="col" class="n col-opt" data-sort="street_median"><button type="button" class="sort">Median</button></th>
              <th scope="col" class="n" data-sort="above"><button type="button" class="sort">Above median</button></th>
              <th scope="col" class="n col-opt2" data-sort="z_score"><button type="button" class="sort">Spreads</button></th>
              <th scope="col" class="n" data-sort="est_saving"><button type="button" class="sort">Est. saving</button></th>
              <th scope="col" class="col-opt">Flags</th>
              <th scope="col" class="col-opt">Status</th>
            </tr></thead>
            <tbody></tbody>
          </table></div>
        </section>
      </div>` : html`<div class="card"><p class="secondary">The Scout has not flagged any candidates in this run yet.</p></div>`}`);
    focusHeading(main);
    if (!cands.length) return;

    const mapApi = mountMap(main.querySelector('#area-map'), cands.map((c) => ({
      id: c.uarn, lat: c.latitude, lng: c.longitude,
      fill: zColor(c.z_score), r: 6, stroke: '#ffffff', weight: 1, order: isNum(c.z_score) ? Number(c.z_score) : 0,
      href: UARN_RE.test(c.uarn) ? '#/p/' + c.uarn : null,
      tip: part(html`<b>${shortAddr(tc(c.address))}</b><br><span class="t2">Zone A ${ppm(c.zone_a_price)}/m² vs ${ppm(c.street_median)} · est. ${money(c.est_saving)}</span>`),
    })), { emptyText: 'None of the candidates has a location yet.' });
    ctx.onCleanup(mapApi.destroy);

    const table = main.querySelector('#area-table');
    const tbody = table.querySelector('tbody');
    let sortKey = 'est_saving', sortDir = -1;
    function renderRows() {
      const rows = cands.slice().sort((a, b) => {
        const x = isNum(a[sortKey]) ? Number(a[sortKey]) : -Infinity, y = isNum(b[sortKey]) ? Number(b[sortKey]) : -Infinity;
        return (x - y) * sortDir || String(a.address).localeCompare(String(b.address));
      });
      setHTML(tbody, rows.map((c) => html`<tr class="row-link" data-uarn="${c.uarn}">
        <td class="addr">${UARN_RE.test(c.uarn) ? html`<a href="#/p/${c.uarn}">${shortAddr(tc(c.address))}</a>` : shortAddr(tc(c.address))}</td>
        <td class="n">${ppm(c.zone_a_price)}</td>
        <td class="n col-opt">${ppm(c.street_median)}</td>
        <td class="n">${c.above != null ? signedPct(c.above) : '–'}</td>
        <td class="n col-opt2">${isNum(c.z_score) ? f1(c.z_score) : '–'}</td>
        <td class="n">${money(c.est_saving)}</td>
        <td class="col-opt small">${(Array.isArray(c.flags) ? c.flags : []).map(flagText).filter(Boolean).join(', ') || '–'}</td>
        <td class="col-opt small">${CAND_STATUS[c.status] || sentence(String(c.status || '').replace(/_/g, ' ')) || '–'}</td>
      </tr>`));
      table.querySelectorAll('th[data-sort]').forEach((th) => {
        if (th.dataset.sort === sortKey) th.setAttribute('aria-sort', sortDir < 0 ? 'descending' : 'ascending');
        else th.removeAttribute('aria-sort');
        const b = th.querySelector('button');
        b.textContent = b.textContent.replace(/ [↑↓]$/, '') + (th.dataset.sort === sortKey ? (sortDir < 0 ? ' ↓' : ' ↑') : '');
      });
    }
    table.querySelector('thead').addEventListener('click', (e) => {
      const th = e.target.closest('th[data-sort]');
      if (!th) return;
      if (sortKey === th.dataset.sort) sortDir = -sortDir; else { sortKey = th.dataset.sort; sortDir = -1; }
      renderRows();
    });
    tbody.addEventListener('mouseover', (e) => {
      const tr = e.target.closest('tr[data-uarn]');
      if (tr) mapApi.highlight(tr.dataset.uarn);
    });
    tbody.addEventListener('mouseleave', () => mapApi.highlight(null));
    tbody.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      const tr = e.target.closest('tr[data-uarn]');
      if (tr && UARN_RE.test(tr.dataset.uarn)) location.hash = '#/p/' + tr.dataset.uarn;
    });
    renderRows();
  }

  // ---------------------------------------------------------------------------
  // Router
  // ---------------------------------------------------------------------------
  let current = null;
  let topSearch = null;
  function route() {
    const hash = location.hash || '#/';
    const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map((x) => { try { return decodeURIComponent(x); } catch (_) { return x; } });
    if (current) current.dispose();
    const ctx = makeCtx();
    current = ctx;
    const main = document.getElementById('main');
    const isHome = parts.length === 0;
    document.getElementById('topbar-search').hidden = isHome;
    document.querySelectorAll('.nav a').forEach((a) => {
      const on = (a.dataset.nav === 'search' && (isHome || parts[0] === 'p')) || (a.dataset.nav === 'area' && parts[0] === 'area');
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    window.scrollTo(0, 0);
    if (isHome) viewHome(ctx, main);
    else if (parts[0] === 'p' && parts[1]) viewProperty(ctx, main, parts[1]);
    else if (parts[0] === 'job' && parts[1]) viewFlow(ctx, main, { jobId: parts[1] });
    else if (parts[0] === 'case' && parts[1]) viewFlow(ctx, main, { caseId: parts[1] });
    else if (parts[0] === 'area') viewArea(ctx, main);
    else renderState(main, { title: 'Page not found', text: 'There is nothing at this address.', actions: backToSearch });
  }

  function start() {
    if (MOCK) document.getElementById('mock-banner').hidden = false;
    topSearch = mountSearch(document.getElementById('topbar-search'), { large: false });
    document.getElementById('skip-link').addEventListener('click', (e) => {
      e.preventDefault();
      const main = document.getElementById('main');
      const h = main.querySelector('h1');
      (h || main).focus();
    });
    window.addEventListener('hashchange', route);
    route();
  }

  // ---------------------------------------------------------------------------
  // Development fixtures (?mock=1 only). Synthetic shops on a made-up street; not VOA data.
  // ---------------------------------------------------------------------------
  const Mock = (() => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const uuid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => ((Math.random() * 16) | 0).toString(16)));
    const iso = (ms) => new Date(ms).toISOString();
    const PC = {
      'E0 1AA': [51.53001, -0.08004], 'E0 1AB': [51.52962, -0.07958], 'E0 1AD': [51.52925, -0.07912],
      'E0 2AA': [51.5309, -0.0772], 'E0 2AB': [51.53055, -0.07688], 'E0 3AA': [51.5341, -0.0745], 'E0 4AA': [51.5268, -0.0721],
    };
    // [uarn, address, use, zone_a_price, itza, rv, postcode]
    const SEGS = {
      'EXAMPLE PARADE|E0 1#900001': {
        street: 'EXAMPLE PARADE', median: 1050, p25: 1050, p75: 1050, spread: 52.5, shops: [
          ['900000001', 'UNIT 4, 12 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1200, 26.0, 38400, 'E0 1AA'],
          ['900000003', '15 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 22.4, 27500, 'E0 1AA'],
          ['900000011', '2 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 30.1, 35000, 'E0 1AA'],
          ['900000012', '4 EXAMPLE PARADE, LONDON', 'CAFE AND PREMISES', 1050, 24.0, 28500, 'E0 1AA'],
          ['900000013', '6 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 19.5, 23250, 'E0 1AB'],
          ['900000014', '8 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1000, 21.0, 23500, 'E0 1AB'],
          ['900000015', '10 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 27.2, 31750, 'E0 1AB'],
          ['900000016', '14 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1100, 25.0, 30750, 'E0 1AB'],
          ['900000017', '16 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 18.0, 21250, 'E0 1AB'],
          ['900000018', '18 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 23.3, 27250, 'E0 1AD'],
          ['900000019', '20 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 975, 20.0, 21750, 'E0 1AD'],
          ['900000020', '22 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 26.4, 30500, 'E0 1AD'],
          ['900000021', '24 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1150, 22.0, 28250, 'E0 1AD'],
          ['900000022', '26 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 24.8, 29000, 'E0 1AD'],
          ['900000023', '28 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1000, 21.6, 24000, 'E0 1AD'],
          ['900000024', '30 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1100, 23.0, 28000, 'E0 1AD'],
          ['900000025', '32 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 25.5, 29750, 'E0 1AD'],
          ['900000026', '34 EXAMPLE PARADE, LONDON', 'SHOP AND PREMISES', 1050, 20.9, 24500, 'E0 1AD'],
        ],
      },
      'CORNER ROW|E0 2#900002': {
        street: 'CORNER ROW', median: 1250, p25: 1250, p75: 1262.5, spread: 62.5, shops: [
          ['900000002', '1 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1400, 30.0, 52500, 'E0 2AA'],
          ['900000031', '3 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1250, 26.0, 36500, 'E0 2AA'],
          ['900000032', '5 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1250, 24.0, 33500, 'E0 2AA'],
          ['900000033', '7 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1250, 22.5, 31500, 'E0 2AB'],
          ['900000034', '9 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1200, 25.0, 33750, 'E0 2AB'],
          ['900000035', '11 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1300, 21.0, 30500, 'E0 2AB'],
          ['900000036', '13 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1250, 27.0, 37750, 'E0 2AB'],
          ['900000037', '15 CORNER ROW, LONDON', 'SHOP AND PREMISES', 1250, 23.5, 32750, 'E0 2AB'],
        ],
      },
    };
    const PROPS = {
      '900000001': {
        seg: 'EXAMPLE PARADE|E0 1#900001', mult: 0.382, total_area: 101, zone_a_area: 16, flags: ['zone_a_above_tone'],
        proposal: { proposed_rv: 34500, annual_saving: 1490, est_saving: 3663, years_left: 2.46 },
        lines: [['Ground', 'Retail Zone A', 16, 1200, 19200], ['Ground', 'Retail Zone B', 15, 600, 9000], ['Ground', 'Retail Zone C', 10, 300, 3000], ['First', 'Internal Storage', 60, 120, 7200]],
        reasons: [
          { reason: 'All five comparables are shops of the same use within 90 m, and four are priced at £1,050; nothing about size or frontage explains £1,200.', voa_record: '900000011' },
          { reason: 'The arithmetic holds: £150 × 26.0 m² ITZA = £3,900, giving £34,500 after rounding down to £50.', voa_record: '900000001' },
        ],
      },
      '900000002': {
        seg: 'CORNER ROW|E0 2#900002', mult: 0.43, total_area: 118, zone_a_area: 18, flags: ['zone_a_above_tone', 'just_over_51k'],
        proposal: { proposed_rv: 48000, annual_saving: 4239, est_saving: 10421, years_left: 2.46 }, kill: true,
        lines: [['Ground', 'Retail Zone A', 18, 1400, 25200], ['Ground', 'Retail Zone B', 18, 700, 12600], ['Ground', 'Retail Zone C', 12, 350, 4200], ['Basement', 'Internal Storage', 70, 150, 10500]],
        reasons: [
          { reason: 'The subject is a corner unit with a return frontage; the VOA would say the higher Zone A price reflects that, as with the £1,300 at 11 Corner Row.', voa_record: '900000035' },
          { reason: 'Only 7 other shops in the segment and two are priced off the median, so the tone is not settled enough to win on.', voa_record: '900000034' },
        ],
      },
      '900000003': {
        seg: 'EXAMPLE PARADE|E0 1#900001', mult: 0.382, total_area: 64, zone_a_area: 14, flags: [], proposal: null,
        lines: [['Ground', 'Retail Zone A', 14, 1050, 14700], ['Ground', 'Retail Zone B', 16.8, 525, 8820], ['Ground', 'Internal Storage', 33.2, 120, 3984]],
      },
      '900000004': { noZoning: true, address: 'FIRST FLOOR OFFICES, 20 EXAMPLE PARADE, LONDON', use: 'OFFICES AND PREMISES', rv: 21000, postcode: 'E0 1AD', mult: 0.432 },
    };
    const JOBS = new Map();
    const CASES = new Map();
    const EXTRA_EVENTS = [];
    let packCount = 6;

    function shopRow(uarn) {
      for (const k of Object.keys(SEGS)) { const r = SEGS[k].shops.find((s) => s[0] === uarn); if (r) return { seg: SEGS[k], key: k, row: r }; }
      return null;
    }
    function dist(a, b) {
      const R = 6371000, toR = Math.PI / 180;
      const dLat = (b[0] - a[0]) * toR, dLng = (b[1] - a[1]) * toR;
      const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * toR) * Math.cos(b[0] * toR) * Math.sin(dLng / 2) ** 2;
      return Math.round(2 * R * Math.asin(Math.sqrt(h)));
    }
    function existingFor(uarn) {
      for (const c of CASES.values()) if (c.uarn === uarn) return { case_id: c.case_id, job_id: null, stage: c.stage };
      for (const j of JOBS.values()) if (j.uarn === uarn && !jobDone(j)) return { case_id: null, job_id: j.id, stage: null };
      return { case_id: null, job_id: null, stage: null };
    }
    function property(uarn) {
      const P = PROPS[uarn];
      if (!P) {
        const found = shopRow(uarn);
        if (!found) throw new ApiError(404, 'This property is not on the 2026 list.');
        throw new ApiError(404, 'The mock fixtures only cover a few shops. Try 12 Example Parade, 1 Corner Row, 15 Example Parade or the offices at 20 Example Parade.');
      }
      if (P.noZoning) {
        return {
          uarn, address: P.address, postcode: P.postcode, use: P.use, rv: P.rv, latlng: PC[P.postcode],
          bill: { multiplier: P.mult, annual_bill: Math.round(P.rv * P.mult) }, zoning: null, segment: null, z_score: null, flags: [],
          verdict: 'no_zoning', verdict_reason: 'The VOA valued this property as a whole rather than by zones, so there is no Zone A price to compare.',
          proposal: null, comparables: [], can_appeal: false,
          appeal_blocked_reason: 'No zoned valuation: there is no street comparison to challenge.', existing: existingFor(uarn),
        };
      }
      const seg = SEGS[P.seg];
      const row = seg.shops.find((s) => s[0] === uarn);
      const [, address, use, zap, itza, rv, pc] = row;
      const z = (zap - seg.median) / seg.spread;
      const overpaying = !!P.proposal && z >= 2 && seg.shops.length >= 5;
      const ex = existingFor(uarn);
      const comps = seg.shops.filter((s) => s[0] !== uarn)
        .map((s) => ({ uarn: s[0], address: s[1], use: s[2], zone_a_price: s[3], distance_m: s[6] === pc ? 0 : dist(PC[pc], PC[s[6]]), same_segment: true, rv: s[5] }))
        .sort((a, b) => a.distance_m - b.distance_m).slice(0, 6);
      return {
        uarn, address, postcode: pc, use, rv, latlng: PC[pc],
        bill: { multiplier: P.mult, annual_bill: Math.round(rv * P.mult) },
        zoning: { zone_a_price: zap, itza, zone_a_area: P.zone_a_area, total_area: P.total_area, lines: P.lines.map(([floor, description, area, price, value]) => ({ floor, description, area, price, value })) },
        segment: {
          segment_key: P.seg, street_name: seg.street, n: seg.shops.length, median: seg.median, p25: seg.p25, p75: seg.p75, spread: seg.spread,
          shops: seg.shops.map((s) => ({ uarn: s[0], address: s[1], use: s[2], zone_a_price: s[3], itza: s[4], z: Math.round(((s[3] - seg.median) / seg.spread) * 100) / 100, lat: PC[s[6]][0], lng: PC[s[6]][1], is_subject: s[0] === uarn })),
        },
        z_score: z, flags: P.flags,
        verdict: overpaying ? 'overpaying' : 'in_line',
        verdict_reason: overpaying
          ? `Zone A ${ppm(zap)} against a median of ${ppm(seg.median)} across ${seg.shops.length} shops on the same segment: ${f1(z)} spreads above, and we flag anything over 2.`
          : 'Your Zone A price is at the median of the shops on your segment.',
        proposal: P.proposal, comparables: comps,
        can_appeal: overpaying && !ex.case_id && !ex.job_id,
        appeal_blocked_reason: overpaying ? null : 'Your valuation is in line with similar shops, so there is no case to make.',
        existing: ex,
      };
    }
    function search(q) {
      q = q.trim();
      if (q.length < 3) return { results: [] };
      const words = q.toUpperCase().split(/\s+/);
      const pool = [];
      Object.values(SEGS).forEach((sg) => sg.shops.forEach((s) => pool.push({ uarn: s[0], address: s[1], postcode: s[6], use: s[2], rv: s[5], zone_a_price: s[3], street_key: sg.street + '|' + s[6].slice(0, 4) })));
      const o = PROPS['900000004'];
      pool.push({ uarn: '900000004', address: o.address, postcode: o.postcode, use: o.use, rv: o.rv, zone_a_price: null, street_key: 'EXAMPLE PARADE|E0 1' });
      const hits = pool.filter((r) => words.every((w) => r.address.includes(w) || r.postcode.replace(' ', '').startsWith(w.replace(' ', '')) || r.postcode.startsWith(q.toUpperCase())));
      hits.sort((a, b) => (a.zone_a_price == null) - (b.zone_a_price == null) || a.address.localeCompare(b.address));
      return { results: hits.slice(0, 12) };
    }
    function plan(j) {
      const P = PROPS[j.uarn];
      const { seg, row } = shopRow(j.uarn);
      const [, address, , zap, itza, rv] = row;
      const short = shortAddr(tc(address));
      const z = (zap - seg.median) / seg.spread;
      const above = Math.round(((zap - seg.median) / seg.median) * 100);
      const c1 = shortAddr(tc(seg.shops[1][1]));
      const kill = !!P.kill;
      return {
        scout: { start: 0, end: 1.6, summary: `Flagged: Zone A ${above}% above the segment median (${f1(z)} spreads)`, steps: [
          [0.2, `Read the 2026 valuation for ${short}`],
          [0.8, `Zone A ${ppm(zap)}/m² against a segment median of ${ppm(seg.median)}/m² across ${seg.shops.length} shops`],
          [1.3, 'Saved the candidate and started the Case builder'],
        ] },
        builder: { start: 1.6, end: 7.5, summary: `Pack ${j.ref}: proposed RV ${money(P.proposal.proposed_rv)} (from ${money(rv)}), saving ${money(P.proposal.annual_saving)} a year`, steps: [
          [2.1, `Picked ${Math.min(5, seg.shops.length - 1)} comparables from the same segment, nearest first`],
          [3.3, `Recomputed the zoning: ITZA ${f1(itza)} m², reduction ${money((zap - seg.median) * itza)}`],
          [5.9, 'Wrote the argument from the pack facts', 1184],
          [7.0, 'Validated the pack against pack.schema.json'],
        ] },
        advocate: { start: 7.5, end: 15, summary: kill ? 'Killed: the VOA would likely defend this value' : 'Survived: no reason found for the VOA to keep the value', steps: [
          [8.0, 'Read the evidence pack'],
          [9.4, `Checked the valuation of ${c1}`],
          [10.9, `Checked the comparables of ${short}`],
          [12.3, `Checked the street tone for ${tc(seg.street)}`],
          [14.4, kill ? 'Verdict: kill, with 2 reasons' : 'Verdict: survive, with 2 reasons', 2310],
        ] },
        kill,
      };
    }
    const jobDone = (j) => (Date.now() - j.t0) / 1000 >= 15;
    function stagesFor(j, kase) {
      const t = (Date.now() - j.t0) / 1000;
      const pl = plan(j);
      const out = ['scout', 'builder', 'advocate'].map((key) => {
        const p = pl[key];
        const status = t < p.start ? 'pending' : t < p.end ? 'running' : 'done';
        const def = STAGES.find((s) => s.key === key);
        return {
          key, agent: def.agent, label: def.label, status,
          started_at: t >= p.start ? iso(j.t0 + p.start * 1000) : null,
          finished_at: t >= p.end ? iso(j.t0 + p.end * 1000) : null,
          summary: status === 'done' ? p.summary : null,
          steps: p.steps.filter((s) => s[0] <= t).map((s) => ({ ts: iso(j.t0 + s[0] * 1000), text: s[1] })),
        };
      });
      const advDone = t >= pl.advocate.end;
      const st = (key, status, extra) => Object.assign({ key, agent: STAGES.find((s) => s.key === key).agent, label: STAGES.find((s) => s.key === key).label, status, started_at: null, finished_at: null, summary: null, steps: [] }, extra || {});
      if (!advDone) { ['owner', 'check', 'challenge', 'decision'].forEach((k) => out.push(st(k, 'pending'))); return out; }
      if (pl.kill) {
        ['owner', 'check', 'challenge', 'decision'].forEach((k) => out.push(st(k, 'skipped', { summary: k === 'owner' ? 'Not needed: the advocate killed the case, so nothing is filed.' : null })));
        return out;
      }
      const c = kase;
      const advEnd = iso(j.t0 + pl.advocate.end * 1000);
      if (!c || !c.owner_approved) {
        out.push(st('owner', 'waiting', { started_at: advEnd, summary: 'Read the pack, then approve to file the Check.' }));
        ['check', 'challenge', 'decision'].forEach((k) => out.push(st(k, 'pending')));
        return out;
      }
      out.push(st('owner', 'done', { started_at: advEnd, finished_at: c.owner_approved_at, summary: `Approved on ${fdate(c.owner_approved_at)} at ${ftime(c.owner_approved_at)}`, steps: [{ ts: c.owner_approved_at, text: 'You approved filing the Check' }] }));
      const checkSteps = [{ ts: c.submitted_at, text: `Check filed with the VOA (mock) as ${c.external_case_ref}` }];
      let checkSummary = `Filed as ${c.external_case_ref} on ${fdate(c.submitted_at)}`;
      if (c.checkReply === 'unchanged') { checkSummary += '; VOA decision: no change'; checkSteps.push({ ts: c.checkReplyAt, text: 'VOA replied to the Check (simulated): no change to the value' }); }
      if (c.checkReply === 'reduced') { checkSummary += '; VOA decision: value reduced'; checkSteps.push({ ts: c.resolved_at, text: 'VOA replied to the Check (simulated): rateable value reduced' }); }
      if (c.checkReply) out.push(st('check', 'done', { started_at: c.submitted_at, finished_at: c.checkReply === 'reduced' ? c.resolved_at : c.checkReplyAt, summary: checkSummary, steps: checkSteps }));
      else out.push(st('check', 'waiting', { started_at: c.submitted_at, summary: `${checkSummary}; waiting for the VOA's decision, mock target ${fdate(c.next_deadline)}`, steps: checkSteps }));
      const inChallenge = ['challenge_draft', 'challenge_submitted', 'waiting_challenge_decision'].includes(c.stage);
      if (inChallenge) out.push(st('challenge', 'waiting', { started_at: c.checkReplyAt, summary: `${c.next_action}, deadline ${fdate(c.next_deadline)}`, steps: [{ ts: c.checkReplyAt, text: `Drafting the Challenge: it must reach the VOA within 4 months, by ${fdate(c.next_deadline)}` }] }));
      else if (c.stage === 'resolved' && c.checkReply === 'reduced') out.push(st('challenge', 'skipped', { summary: 'Not needed: the value was reduced at Check' }));
      else if (c.stage === 'resolved') out.push(st('challenge', 'done', { started_at: c.checkReplyAt, finished_at: c.resolved_at, summary: 'Challenge decided: reduced' }));
      else out.push(st('challenge', 'pending'));
      if (c.stage === 'resolved') {
        out.push(st('decision', 'done', { started_at: c.resolved_at, finished_at: c.resolved_at, summary: `Rateable value reduced from ${money(PROPS[c.uarn] && shopRow(c.uarn).row[5])} to ${money(c.final_rv)}; saving ${money(c.confirmed_annual_saving)} a year, refunded by the council (mock)` }));
      } else if (inChallenge) {
        out.push(st('decision', 'pending', { summary: 'After the VOA decides the Challenge' }));
      } else {
        out.push(st('decision', 'pending'));
      }
      return out;
    }
    function verdictFor(j) {
      const P = PROPS[j.uarn];
      return { verdict: P.kill ? 'kill' : 'survive', reasons: P.reasons || [], advocate_version: 'advocate-local-v1' };
    }
    function job(id) {
      const j = JOBS.get(id);
      if (!j) throw new ApiError(404, 'No such job.');
      const t = (Date.now() - j.t0) / 1000;
      const done = jobDone(j);
      if (done && !PROPS[j.uarn].kill && !CASES.has(j.case_id)) createCase(j);
      return {
        job_id: id, uarn: j.uarn, candidate_id: j.candidate_id, status: done ? 'done' : 'running',
        case_id: t >= 7.0 ? j.case_id : null, pack_id: t >= 7.0 ? j.pack_id : null,
        stages: stagesFor(j, CASES.get(j.case_id)), verdict: done ? verdictFor(j) : null, error: null,
      };
    }
    function createCase(j) {
      const t = iso(j.t0 + 15000);
      CASES.set(j.case_id, {
        case_id: j.case_id, uarn: j.uarn, job: j, stage: 'waiting_owner', owner_approved: false, owner_approved_at: null,
        external_case_ref: null, submitted_at: null, next_deadline: null, next_action: 'Owner to approve filing the Check',
        outcome: null, final_rv: null, confirmed_annual_saving: null, resolved_at: null,
        history: [{ ts: t, stage: 'waiting_owner', note: 'Advocate verdict: survive. Waiting for the owner to approve.' }],
      });
    }
    function caseObj(id) {
      const c = CASES.get(id);
      if (!c) throw new ApiError(404, 'There is no case with this reference.');
      const P = PROPS[c.uarn];
      const { seg, row } = shopRow(c.uarn);
      const comps = seg.shops.filter((s) => s[0] !== c.uarn).slice(0, 5).map((s) => ({ uarn: s[0], zone_a_price: s[3], distance_m: s[6] === row[6] ? 0 : dist(PC[row[6]], PC[s[6]]), use: s[2].toLowerCase() }));
      return {
        case_id: c.case_id, uarn: c.uarn, address: row[1], stage: c.stage, owner_approved: c.owner_approved,
        external_case_ref: c.external_case_ref, submitted_at: c.submitted_at, next_deadline: c.next_deadline, next_action: c.next_action,
        outcome: c.outcome, final_rv: c.final_rv, confirmed_annual_saving: c.confirmed_annual_saving, resolved_at: c.resolved_at,
        history: c.history.slice(),
        pack: {
          pack_id: c.job.pack_id, case_ref: c.job.ref, current_rv: row[5], proposed_rv: P.proposal.proposed_rv, annual_saving: P.proposal.annual_saving,
          comparables: comps, calc: { zone_a_price: row[3], street_median: seg.median, street_spread: seg.spread },
          argument: `The subject's Zone A price of ${ppm(row[3])}/m² is ${ppm(row[3] - seg.median)} above the ${ppm(seg.median)}/m² adopted for ${seg.shops.length - 1} other shops on the same segment of ${tc(seg.street)}, valued under the same scheme. The nearest comparables are shops of the same use and similar size, all within 90 m. Applying the segment tone to the subject's ITZA of ${f1(row[4])} m² gives a rateable value of ${money(P.proposal.proposed_rv)}.`,
          status: c.owner_approved ? 'approved' : 'waiting_owner', builder_version: 'builder-local-v1',
        },
        verdict: verdictFor(c.job),
        candidate: { candidate_id: c.job.candidate_id, z_score: (row[3] - seg.median) / seg.spread, est_saving: P.proposal.est_saving, street_median: seg.median },
        stages: stagesFor(c.job, c), report_url: '/report/' + c.case_id,
      };
    }
    function approve(id) {
      const c = CASES.get(id);
      if (!c) throw new ApiError(404, 'There is no case with this reference.');
      if (c.owner_approved) return caseObj(id);
      const now = Date.now();
      c.owner_approved = true;
      c.owner_approved_at = iso(now);
      c.stage = 'check_submitted';
      c.external_case_ref = 'MOCK-CHK-' + Math.random().toString(36).slice(2, 8).toUpperCase().padEnd(6, '0');
      c.submitted_at = iso(now);
      c.next_deadline = iso(now + 365 * 864e5);
      c.next_action = 'Wait for the VOA Check decision';
      c.history.push({ ts: iso(now), stage: 'check_submitted', note: `Owner approved. Check filed with the VOA (mock), reference ${c.external_case_ref}.` });
      EXTRA_EVENTS.push({ ts: iso(now), agent: 'orchestrator', kind: 'case.approved', message: `Owner approved. Check filed with the VOA (mock), reference ${c.external_case_ref}`, tokens: null, case_id: id, candidate_id: c.job.candidate_id });
      return caseObj(id);
    }
    function simulate(id, reply) {
      const c = CASES.get(id);
      if (!c) throw new ApiError(404, 'There is no case with this reference.');
      if (!c.owner_approved) throw new ApiError(409, 'Approve the filing first.');
      if (c.stage === 'resolved' || c.stage === 'closed') throw new ApiError(409, 'This case is already resolved.');
      const now = Date.now();
      const P = PROPS[c.uarn];
      const atCheck = ['check_draft', 'check_submitted', 'waiting_check_decision'].includes(c.stage);
      if (reply === 'reduced') {
        if (atCheck) Object.assign(c, { checkReply: 'reduced' });
        Object.assign(c, { stage: 'resolved', outcome: 'reduced', final_rv: P.proposal.proposed_rv, confirmed_annual_saving: P.proposal.annual_saving, resolved_at: iso(now), next_action: 'Council to refund the overpayment', next_deadline: null });
        c.history.push({ ts: iso(now), event: 'voa_decision', at: atCheck ? 'check' : 'challenge', reply: 'reduced', stage: 'resolved', note: `Mock VOA reply: value reduced to ${money(P.proposal.proposed_rv)}` });
        EXTRA_EVENTS.push({ ts: iso(now), agent: 'caseworker', kind: 'voa_reply', message: `VOA replied to the ${atCheck ? 'Check' : 'Challenge'} (simulated): rateable value reduced`, tokens: null, case_id: id, candidate_id: c.job.candidate_id });
        EXTRA_EVENTS.push({ ts: iso(now + 1), agent: 'caseworker', kind: 'decision', message: `Rateable value reduced to ${money(P.proposal.proposed_rv)}: saving ${money(P.proposal.annual_saving)} a year; the council refunds the overpayment (mock)`, tokens: 640, case_id: id, candidate_id: c.job.candidate_id });
      } else if (reply === 'unchanged') {
        if (!atCheck) throw new ApiError(409, 'An unchanged reply can only be simulated while the Check is with the VOA.');
        Object.assign(c, { stage: 'challenge_draft', checkReply: 'unchanged', checkReplyAt: iso(now), next_action: 'Draft the Challenge', next_deadline: iso(now + 122 * 864e5) });
        c.history.push({ ts: iso(now), event: 'voa_decision', at: 'check', reply: 'unchanged', stage: 'challenge_draft', note: 'Mock VOA reply: no change at Check' });
        EXTRA_EVENTS.push({ ts: iso(now), agent: 'caseworker', kind: 'voa_reply', message: 'VOA replied to the Check (simulated): no change to the value', tokens: null, case_id: id, candidate_id: c.job.candidate_id });
        EXTRA_EVENTS.push({ ts: iso(now + 1), agent: 'caseworker', kind: 'challenge_next', message: `Drafting the Challenge: it must reach the VOA within 4 months, by ${fdate(c.next_deadline)}`, tokens: 820, case_id: id, candidate_id: c.job.candidate_id });
      } else {
        throw new ApiError(400, "reply must be 'reduced' or 'unchanged'");
      }
      return caseObj(id);
    }
    function events(q) {
      const caseId = q.get('case_id'), candId = q.get('candidate_id');
      const limit = Math.min(Number(q.get('limit')) || 50, 200);
      const out = [];
      JOBS.forEach((j) => {
        if ((caseId && j.case_id !== caseId) && (candId && j.candidate_id !== candId)) return;
        if (!caseId && !candId) return;
        if (caseId && !candId && j.case_id !== caseId) return;
        if (candId && !caseId && j.candidate_id !== candId) return;
        const t = (Date.now() - j.t0) / 1000;
        const pl = plan(j);
        ['scout', 'builder', 'advocate'].forEach((key) => pl[key].steps.forEach((s) => {
          if (s[0] <= t) out.push({ ts: iso(j.t0 + s[0] * 1000), agent: key, kind: key + '.step', message: s[1], tokens: s[2] || null });
        }));
        if (t >= 15) {
          out.push({ ts: iso(j.t0 + 15000), agent: 'orchestrator', kind: 'gate', message: pl.kill ? 'Case killed by the advocate. Nothing will be filed.' : 'Case opened. Waiting for the owner to approve the filing.', tokens: null });
        }
      });
      EXTRA_EVENTS.forEach((e) => { if ((caseId && e.case_id === caseId) || (candId && e.candidate_id === candId)) out.push(e); });
      out.sort((a, b) => (a.ts < b.ts ? 1 : -1));
      return { events: out.slice(0, limit).map(({ ts, agent, kind, message, tokens }) => ({ ts, agent, kind, message, tokens })) };
    }
    function area() {
      const c = (uarn, address, pc, zap, med, z, est, flags, status, dy, dx) => ({
        uarn, address, latitude: PC[pc][0] + (dy || 0), longitude: PC[pc][1] + (dx || 0), zone_a_price: zap, street_median: med, z_score: z, est_saving: est, flags, status,
      });
      return {
        run: { run_id: '00000000-0000-4000-8000-000000000001', area_name: 'Hackney', status: 'completed', properties_checked: 2140, candidates_flagged: 6, finished_at: iso(Date.now() - 3 * 3600e3) },
        candidates: [
          c('900000001', 'UNIT 4, 12 EXAMPLE PARADE, LONDON', 'E0 1AA', 1200, 1050, 2.86, 3663, ['zone_a_above_tone'], 'flagged'),
          c('900000002', '1 CORNER ROW, LONDON', 'E0 2AA', 1400, 1250, 2.4, 10421, ['zone_a_above_tone', 'just_over_51k'], 'flagged'),
          c('900000041', '7 SAMPLE STREET, LONDON', 'E0 3AA', 980, 850, 3.06, 5120, ['zone_a_above_tone'], 'flagged', 0.0003, -0.0004),
          c('900000042', '19 SAMPLE STREET, LONDON', 'E0 3AA', 940, 850, 2.12, 2210, ['zone_a_above_tone'], 'flagged', -0.0002, 0.0005),
          c('900000043', '3 TEST MARKET, LONDON', 'E0 4AA', 1500, 1300, 3.08, 8954, ['zone_a_above_tone'], 'flagged'),
          c('900000044', '41 TEST MARKET, LONDON', 'E0 4AA', 1420, 1300, 2.05, 1960, ['zone_a_above_tone'], 'killed', 0.0004, 0.0006),
        ],
      };
    }
    function appeal(body) {
      const uarn = String(body.uarn || '');
      if (!UARN_RE.test(uarn)) throw new ApiError(422, 'uarn must be digits');
      const p = property(uarn);
      if (p.existing.case_id) return { status: 200, data: { case_id: p.existing.case_id } };
      if (p.existing.job_id) { const j = JOBS.get(p.existing.job_id); return { status: 202, data: { job_id: j.id, candidate_id: j.candidate_id } }; }
      if (!p.can_appeal) throw new ApiError(409, p.appeal_blocked_reason || 'There is no case for this property.');
      const j = { id: uuid(), uarn, t0: Date.now(), candidate_id: uuid(), case_id: uuid(), pack_id: uuid(), ref: 'c_' + String(++packCount).padStart(4, '0') };
      JOBS.set(j.id, j);
      return { status: 202, data: { job_id: j.id, candidate_id: j.candidate_id } };
    }
    async function handle(path, opts = {}) {
      await wait(120 + Math.random() * 180);
      if (opts.signal && opts.signal.aborted) { const e = new Error('Aborted'); e.name = 'AbortError'; throw e; }
      const u = new URL(path, location.origin);
      const p = u.pathname, q = u.searchParams, m = (opts.method || 'GET').toUpperCase();
      const ok = (data) => ({ status: 200, data });
      let mm;
      if (p === '/api/search') return ok(search(q.get('q') || ''));
      if ((mm = p.match(/^\/api\/property\/(\d+)$/))) return ok(property(mm[1]));
      if (p === '/api/appeal' && m === 'POST') return appeal(opts.body || {});
      if ((mm = p.match(/^\/api\/job\/([0-9a-f-]{36})$/i))) return ok(job(mm[1]));
      if ((mm = p.match(/^\/api\/case\/([0-9a-f-]{36})$/i))) return ok(caseObj(mm[1]));
      if ((mm = p.match(/^\/api\/case\/([0-9a-f-]{36})\/approve$/i)) && m === 'POST') return ok(approve(mm[1]));
      if ((mm = p.match(/^\/api\/case\/([0-9a-f-]{36})\/simulate$/i)) && m === 'POST') return ok(simulate(mm[1], (opts.body || {}).reply));
      if (p === '/api/events') return ok(events(q));
      if (p === '/api/area') return ok(area());
      throw new ApiError(404, 'Not found (mock).');
    }
    return { handle };
  })();

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
