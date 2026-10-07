'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = (n) => String(n).padStart(2, '0');
const icon = (name, size, extra = '') => `<img src="icons/${name}.svg" width="${size}" height="${size}" alt=""${extra}>`;

const S = {
  state: null, batches: [], detail: null, settings: null,
  view: 'create', focusId: null, pinned: false, renaming: null,
  rows: [newRow()], mode: 'list', perPrompt: 1, model: null,
  galleryAll: false, galleryList: false, sort: 'new', lastBatchesSig: '',
};
function newRow(text = '', o = {}) {
  return { text, ref: null, size: o.size || '', per: o.per || '', seed: o.seed ?? '', open: false };
}
const hasOptions = (r) => Boolean(r.size || r.per || r.seed !== '');
const SORTS = { new: 'Newest first', name: 'Name A–Z', most: 'Most images' };
const COLLAPSED_TILES = 6;

// ---------- helpers ----------

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { 'X-Studio': '1' } };
  if (opts.json !== undefined) { init.body = JSON.stringify(opts.json); init.headers['Content-Type'] = 'application/json'; }
  if (opts.form) init.body = opts.form;
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : (j.detail?.[0]?.msg || msg); } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

let toastTimer;
function toast(msg, ok = false) {
  const t = $('toast');
  const host = [...document.querySelectorAll('dialog[open]')].pop() || document.body;  // stay above modals
  if (t.parentElement !== host) host.append(t);
  t.textContent = msg; t.className = 'toast' + (ok ? ' ok' : ''); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ok ? 2500 : 6000);
}

// Polling re-renders constantly; only touch the DOM when the markup really changed,
// otherwise buttons get replaced between mousedown and mouseup and clicks are lost.
let pointerHeld = false;
window.addEventListener('pointerdown', () => { pointerHeld = true; }, true);
window.addEventListener('pointerup', () => { pointerHeld = false; }, true);
function setHtml(el, html) {
  if (el._html === html || pointerHeld) return;  // a held press redraws on the next poll
  el._html = html;
  el.innerHTML = html;
}

const STOP = new Set(['a', 'an', 'the', 'of', 'in', 'on', 'at', 'with', 'and', 'to', 'for', 'by', 'from', 'into']);
function slug(text) {
  const words = (text.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => !STOP.has(w));
  return words.slice(0, 4).join('-') || 'batch';
}
function autoName() {
  const d = new Date();
  const first = promptTexts()[0] || '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}_${slug(first)}`;
}
function fmtDuration(sec) {
  sec = Math.round(sec);
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return s ? `${m} min ${s} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
function fmtWhen(iso, sep = ' ') {
  const d = new Date(iso), now = new Date();
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((day - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  const date = diff === 0 ? 'Today' : diff === 1 ? 'Yesterday' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return `${date}${sep}${time}`;
}
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const modelShort = (key) => (key || '').toUpperCase();
const modelLabel = (key) => S.state?.models.find((m) => m.key === key)?.label || modelShort(key);
const fileUrl = (batchId, rel, thumb) => `/api/batches/${batchId}/files/${rel}${thumb ? (rel.includes('?') ? '&' : '?') + 'thumb=1' : ''}`;
const isActive = (status) => status === 'running' || status === 'queued';

function statusChip(b) {
  if (b.status === 'running') return '<span class="chip red">Running</span>';
  if (b.status === 'queued') return '<span class="chip ghost">Queued</span>';
  if (b.status === 'paused') return '<span class="chip ghost">Paused</span>';
  if (b.status === 'cancelled') return '<span class="chip ghost">Cancelled</span>';
  if (b.failed) return `<span class="chip warn">${b.failed} failed</span>`;
  if (b.cancelled) return `<span class="chip ghost">${b.cancelled} cancelled</span>`;
  return `<span class="chip lime">${icon('dot-ink', 5)}Done</span>`;
}

// ---------- routing ----------

function showView() {
  const v = (location.hash || '#create').slice(1);
  const VIEWS = ['create', 'batches', 'models', 'setup', 'connect', 'settings'];
  S.view = S.locked ? 'setup' : VIEWS.includes(v) ? v : 'create';  // fresh install: Setup only
  if (S.locked && v !== 'setup') history.replaceState(null, '', '#setup');
  for (const name of VIEWS) $(`view-${name}`).hidden = name !== S.view;
  // Setup is a section of Settings, so Settings stays highlighted there (except while setup is locked in).
  const navView = S.view === 'setup' && !S.locked ? 'settings' : S.view;
  document.querySelectorAll('.nav a').forEach((a) =>
    a.dataset.view === navView ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'));
  if (S.view === 'batches') { S.lastBatchesSig = ''; renderBatches(); }
  if (['settings', 'connect', 'models'].includes(S.view)) loadSettings();
  if (S.view === 'models') loadLoras();
  if (S.view === 'setup') loadSetup();
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', showView);

// ---------- create: prompts ----------

const promptList = $('prompt-list');

function renderRows() {
  promptList.innerHTML = S.rows.map((r, i) => `
    <li class="prompt" data-i="${i}">
      <div class="prompt-head">
        <span class="micro">Prompt ${pad(i + 1)} <span class="opt-summary">${esc(optSummary(r))}</span></span>
        <div class="prompt-actions">
          ${r.ref
            ? `<span class="ref-set"><img class="thumb" src="${r.ref.url}" alt="Reference for prompt ${i + 1}" title="${esc(r.ref.file.name)} — click to replace" data-act="ref">Ref
                 <button type="button" class="icon-btn" data-act="unref" aria-label="Remove reference from prompt ${i + 1}" style="width:20px;height:20px">${icon('x', 13)}</button></span>`
            : `<button type="button" class="ref-btn" data-act="ref" aria-label="Add reference image to prompt ${i + 1}">${icon('image-plus-sm', 13)}Ref</button>`}
          <button type="button" class="opt-btn" data-act="opts" aria-expanded="${r.open}" aria-label="Size, images and seed for prompt ${i + 1}">Options</button>
          <button type="button" class="icon-btn" data-act="remove" aria-label="Remove prompt ${i + 1}" style="width:20px;height:20px">${icon('x', 13)}</button>
        </div>
      </div>
      <textarea rows="1" aria-label="Prompt ${i + 1}" placeholder="${i === 0 ? 'Describe the image…' : ''}">${esc(r.text)}</textarea>
      ${r.open ? `<div class="prompt-opts">
        <label>Size<select data-opt="size"><option value="">Batch size</option>${SIZE_OPTIONS.map(([v, t]) =>
          `<option value="${v}"${r.size === v ? ' selected' : ''}>${t}</option>`).join('')}</select></label>
        <label>Images<select data-opt="per"><option value="">Batch</option>${[1, 2, 3, 4, 5, 6, 7, 8].map((n) =>
          `<option value="${n}"${String(r.per) === String(n) ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
        <label>Seed<input data-opt="seed" inputmode="numeric" placeholder="Batch" value="${esc(r.seed)}" autocomplete="off"></label>
      </div>` : ''}
    </li>`).join('');
  promptList.querySelectorAll('textarea').forEach(grow);
  updateSummary();
}
const SIZE_OPTIONS = ['1024x1024', '768x768', '512x512', '1360x768', '768x1360', '1184x880', '880x1184', '1248x832', '832x1248',
  '1568x672', '1536x1024', '1024x1536', '1920x1088', '1088x1920'].map((v) => [v, v.replace('x', ' × ')]);
function optSummary(r) {
  return [r.size && r.size.replace('x', ' × '), r.per && `× ${r.per}`, r.seed !== '' && `seed ${r.seed}`].filter(Boolean).join(' · ');
}
promptList.addEventListener('change', (e) => {
  const opt = e.target.dataset.opt;
  if (!opt) return;
  const li = e.target.closest('li');
  const r = S.rows[+li.dataset.i];
  r[opt] = e.target.value.trim();
  li.querySelector('.opt-summary').textContent = optSummary(r);
  updateSummary();
});
function grow(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }
function focusRow(i) {
  const ta = promptList.querySelector(`li[data-i="${i}"] textarea`);
  if (!ta) return;
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  ta.closest('li').scrollIntoView({ block: 'nearest' });
}
function setRowRef(i, file) {
  if (!file || !file.type.startsWith('image/')) { toast('That file is not an image.'); return; }
  if (S.rows[i].ref) URL.revokeObjectURL(S.rows[i].ref.url);
  S.rows[i].ref = { file, url: URL.createObjectURL(file) };
  renderRows();
}

promptList.addEventListener('input', (e) => {
  if (e.target.tagName !== 'TEXTAREA') return;
  const i = +e.target.closest('li').dataset.i;
  S.rows[i].text = e.target.value.replace(/\n/g, ' ');
  grow(e.target);
  updateSummary();
});
promptList.addEventListener('keydown', (e) => {
  if (e.target.tagName !== 'TEXTAREA') return;
  const i = +e.target.closest('li').dataset.i;
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    S.rows.splice(i + 1, 0, newRow());
    renderRows(); focusRow(i + 1);
  } else if (e.key === 'Backspace' && e.target.value === '' && S.rows.length > 1) {
    e.preventDefault();
    removeRow(i); focusRow(Math.max(0, i - 1));
  }
});
promptList.addEventListener('paste', (e) => {
  if (e.target.tagName !== 'TEXTAREA') return;
  const lines = (e.clipboardData.getData('text') || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return;
  e.preventDefault();
  const i = +e.target.closest('li').dataset.i;
  const ta = e.target;
  S.rows[i].text = (ta.value.slice(0, ta.selectionStart) + lines[0] + ta.value.slice(ta.selectionEnd)).trim();
  S.rows.splice(i + 1, 0, ...lines.slice(1).map((text) => newRow(text)));
  renderRows(); focusRow(i + lines.length - 1);
  toast(`Added ${lines.length} prompts`, true);
});
promptList.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const i = +btn.closest('li').dataset.i;
  if (btn.dataset.act === 'remove') removeRow(i);
  else if (btn.dataset.act === 'opts') { S.rows[i].open = !S.rows[i].open; renderRows(); }
  else if (btn.dataset.act === 'unref') { URL.revokeObjectURL(S.rows[i].ref.url); S.rows[i].ref = null; renderRows(); }
  else if (btn.dataset.act === 'ref') pickFile((f) => setRowRef(i, f));
});
for (const ev of ['dragover', 'dragleave', 'drop']) {
  promptList.addEventListener(ev, (e) => {
    const li = e.target.closest('li');
    if (!li || !e.dataTransfer?.types.includes('Files')) return;
    e.preventDefault();
    promptList.querySelectorAll('li.drop').forEach((x) => x.classList.remove('drop'));
    if (ev === 'dragover') li.classList.add('drop');
    if (ev === 'drop' && e.dataTransfer.files[0]) setRowRef(+li.dataset.i, e.dataTransfer.files[0]);
  });
}
function removeRow(i) {
  if (S.rows[i].ref) URL.revokeObjectURL(S.rows[i].ref.url);
  S.rows.splice(i, 1);
  if (!S.rows.length) S.rows.push(newRow());
  renderRows();
}
$('add-prompt').addEventListener('click', () => {
  if (S.mode !== 'list') setMode('list');
  S.rows.push(newRow());
  renderRows(); focusRow(S.rows.length - 1);
});

let filePicker;
function pickFile(cb, multiple = false) {
  filePicker?.remove();
  filePicker = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', hidden: true, multiple });
  filePicker.addEventListener('change', () => filePicker.files[0] && cb(multiple ? [...filePicker.files] : filePicker.files[0]));
  document.body.appendChild(filePicker);
  filePicker.click();
}

function setMode(mode) {
  if (mode === S.mode) return;
  if (S.mode === 'paste') syncFromPaste();
  if (mode === 'paste') $('prompt-paste').value = S.rows.map((r) => r.text).filter(Boolean).join('\n');
  S.mode = mode;
  for (const m of ['list', 'paste', 'template']) $(`mode-${m}`).setAttribute('aria-pressed', mode === m);
  promptList.hidden = mode !== 'list';
  $('prompt-paste').hidden = mode !== 'paste';
  $('template-box').hidden = mode !== 'template';
  if (mode === 'list') renderRows();
  else if (mode === 'paste') $('prompt-paste').focus();
  else { renderTemplate(); $('tpl-text').focus(); }
}

// ---------- create: templates ----------
// "A {animal} in {place}" + a list per {word} → many prompts, as every combination or line by line.

const TPL = { values: {}, zip: false };
const MAX_EXPANDED = 2000;
const tplNames = () => [...new Set([...$('tpl-text').value.matchAll(/\{([^{}\n]{1,40})\}/g)].map((m) => m[1].trim()))].filter(Boolean);
const tplList = (name) => (TPL.values[name] || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);

function expandTemplate() {
  const text = $('tpl-text').value.trim();
  const names = tplNames();
  if (!text) return { prompts: [], note: '' };
  if (!names.length) return { prompts: [text], note: 'No {words} yet — this adds the template as one prompt.' };
  const lists = names.map(tplList);
  const missing = names.filter((_, i) => !lists[i].length);
  if (missing.length) return { prompts: [], note: `Add values for {${missing.join('}, {')}}.` };
  const fill = (vals) => names.reduce((t, n, i) => t.split(`{${n}}`).join(vals[i]), text);
  if (TPL.zip) {
    const len = Math.min(...lists.map((l) => l.length));
    const uneven = lists.some((l) => l.length !== len);
    return { prompts: Array.from({ length: len }, (_, r) => fill(lists.map((l) => l[r]))),
             note: uneven ? `Lists have different lengths — using the first ${len} line${len === 1 ? '' : 's'} of each.` : '' };
  }
  const total = lists.reduce((n, l) => n * l.length, 1);
  if (total > MAX_EXPANDED) return { prompts: [], note: `That makes ${total} prompts — the limit is ${MAX_EXPANDED}. Shorten a list or use Line by line.` };
  let combos = [[]];
  for (const l of lists) combos = combos.flatMap((c) => l.map((v) => [...c, v]));
  return { prompts: combos.map(fill), note: '' };
}

function renderTemplate() {
  const names = tplNames();
  const box = $('tpl-vars');
  const have = [...box.querySelectorAll('[data-var]')].map((t) => t.dataset.var);
  if (have.join('\n') !== names.join('\n')) {  // rebuild only when the set of {words} changes, so typing isn't interrupted
    box.innerHTML = names.map((n) => `<label class="tpl-var"><span class="label" style="text-transform:none">{${esc(n)}} — one value per line</span>
      <textarea data-var="${esc(n)}" placeholder="value 1&#10;value 2">${esc(TPL.values[n] || '')}</textarea></label>`).join('');
  }
  const { prompts, note } = expandTemplate();
  $('tpl-count').textContent = prompts.length ? plural(prompts.length, 'prompt') : '';
  $('tpl-preview').innerHTML = note ? `<li class="more warn-text">${esc(note)}</li>` : '';
  $('tpl-preview').innerHTML += prompts.slice(0, 3).map((t) => `<li>${esc(t)}</li>`).join('') +
    (prompts.length > 3 ? `<li class="more">+ ${prompts.length - 3} more</li>` : '');
  $('tpl-add').disabled = !prompts.length;
  $('tpl-add').textContent = prompts.length ? `Add ${plural(prompts.length, 'prompt')} to the list` : 'Add prompts to the list';
}
$('tpl-text').addEventListener('input', renderTemplate);
$('tpl-vars').addEventListener('input', (e) => {
  if (!e.target.dataset.var) return;
  TPL.values[e.target.dataset.var] = e.target.value;
  renderTemplate();
});
for (const [id, zip] of [['tpl-all', false], ['tpl-zip', true]]) {
  $(id).addEventListener('click', () => {
    TPL.zip = zip;
    $('tpl-all').setAttribute('aria-pressed', !zip);
    $('tpl-zip').setAttribute('aria-pressed', zip);
    renderTemplate();
  });
}
$('tpl-add').addEventListener('click', () => {
  const { prompts } = expandTemplate();
  if (!prompts.length) return;
  S.rows = S.rows.filter((r) => r.text.trim() || r.ref).concat(prompts.map((t) => newRow(t)));
  setMode('list');
  toast(`Added ${plural(prompts.length, 'prompt')} from the template`, true);
});
// Paste mode edits only the text; references and options stay with the prompt in the same position.
function pastedRows() {
  const old = S.rows.filter((r) => r.text);
  return $('prompt-paste').value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    .map((text, i) => (old[i] ? { ...old[i], text } : newRow(text)));
}
function syncFromPaste() {
  S.rows = pastedRows();
  if (!S.rows.length) S.rows.push(newRow());
}
$('mode-list').addEventListener('click', () => setMode('list'));
$('mode-paste').addEventListener('click', () => setMode('paste'));
$('mode-template').addEventListener('click', () => setMode('template'));
$('prompt-paste').addEventListener('input', updateSummary);

$('import-btn').addEventListener('click', () => $('import-file').click());
$('import-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const text = await file.text();
  const prompts = (/\.csv$/i.test(file.name) ? csvRows(text) : text.split(/\r?\n/).map((l) => newRow(l.trim())))
    .filter((r) => r.text);
  if (!prompts.length) { toast('No prompts found in that file.'); return; }
  if (S.mode === 'paste') syncFromPaste();
  S.rows = S.rows.filter((r) => r.text.trim() || r.ref).concat(prompts);
  if (S.mode === 'paste') $('prompt-paste').value = S.rows.map((r) => r.text).join('\n');
  else if (S.mode === 'template') setMode('list');
  else renderRows();
  updateSummary();
  const custom = prompts.filter(hasOptions).length;
  toast(`Imported ${plural(prompts.length, 'prompt')} from ${file.name}` + (custom ? ` (${custom} with their own size, images or seed)` : ''), true);
});
// CSV: a "prompt" column (or the first column), plus optional size ("1344x768") or width/height,
// images (also "count" / "per_prompt") and seed columns.
function csvRows(text) {
  const rows = parseCSV(text).filter((r) => r.some((c) => c.trim()));
  const header = rows[0]?.map((c) => c.trim().toLowerCase()) || [];
  const col = (...names) => header.findIndex((h) => names.includes(h));
  const c = { prompt: col('prompt', 'text'), size: col('size'), w: col('width'), h: col('height'),
              per: col('images', 'count', 'per_prompt', 'images_per_prompt'), seed: col('seed') };
  const hasHeader = Object.values(c).some((i) => i >= 0);
  const cell = (r, i) => (i >= 0 ? (r[i] || '').trim() : '');
  return (hasHeader ? rows.slice(1) : rows).map((r) => {
    let size = cell(r, c.size).toLowerCase().replace(/\s/g, '').replace('×', 'x');
    if (!/^\d+x\d+$/.test(size)) size = cell(r, c.w) && cell(r, c.h) ? `${cell(r, c.w)}x${cell(r, c.h)}` : '';
    const per = /^[1-8]$/.test(cell(r, c.per)) ? cell(r, c.per) : '';
    const seed = /^\d{1,10}$/.test(cell(r, c.seed)) ? cell(r, c.seed) : '';
    return newRow(cell(r, c.prompt >= 0 ? c.prompt : 0), { size, per, seed });
  });
}
function parseCSV(text) {
  const rows = []; let row = [], f = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(f); f = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
    else f += c;
  }
  if (f || row.length) { row.push(f); rows.push(row); }
  return rows;
}

// ---------- create: size (aspect ratio + resolution, or a custom width × height) ----------

const RATIOS = [['1:1', 1, 1], ['16:9', 16, 9], ['9:16', 9, 16], ['4:3', 4, 3], ['3:4', 3, 4],
                ['3:2', 3, 2], ['2:3', 2, 3], ['21:9', 21, 9]];
const LEVELS = { s: 0.5, m: 1, l: 1.5 };  // megapixels
const snap16 = (v) => Math.min(2048, Math.max(256, Math.round(v / 16) * 16));
const clampSide = (v) => Math.min(2048, Math.max(256, Math.round(v)));  // typed sizes: any value 256–2048
function sizeFor(rw, rh, mp) {
  const area = mp * 1024 * 1024;
  let w = Math.sqrt(area * rw / rh), h = w * rh / rw;
  const k = Math.min(1, 2048 / Math.max(w, h));  // keep the long side within the engine's limit
  return [snap16(w * k), snap16(h * k)];
}
S.size = { ratio: '1:1', level: 'm', auto: null };  // auto: [w, h] of the first reference image, when known
function getSize() {
  return [clampSide(Number($('width').value) || 1024), clampSide(Number($('height').value) || 1024)];
}
function applySize() {
  const r = S.size.ratio;
  if (r === 'auto' && S.size.auto) [$('width').value, $('height').value] = sizeFor(S.size.auto[0], S.size.auto[1], LEVELS[S.size.level]);
  else if (r !== 'custom' && r !== 'auto') {
    const [, rw, rh] = RATIOS.find((x) => x[0] === r);
    [$('width').value, $('height').value] = sizeFor(rw, rh, LEVELS[S.size.level]);
  }
  renderSize();
  updateSummary();
}
function renderSize() {
  const hasRef = S.pins.length > 0;
  if (S.size.ratio === 'auto' && !hasRef) S.size.ratio = '1:1';
  const chips = RATIOS.map(([k]) => k).concat(hasRef ? ['auto'] : []);
  setHtml($('ratios'), chips.map((k) => `<button type="button" data-ratio="${k}" aria-pressed="${S.size.ratio === k}"
    ${k === 'auto' ? 'title="Keep the first reference image\'s shape"' : ''}>${k === 'auto' ? 'Auto' : k}</button>`).join(''));
  $('size-level').querySelectorAll('[data-level]').forEach((b) =>
    b.setAttribute('aria-pressed', S.size.ratio !== 'custom' && b.dataset.level === S.size.level));
  const [w, h] = getSize();
  $('size-mp').textContent = `${w} × ${h} · ${(w * h / 1e6).toFixed(1)} MP`;
  // Show the standard size this matches (the first entry with these numbers), else the placeholder.
  const std = $('size-standard');
  if (std.selectedOptions[0]?.value !== `${w}x${h}`) std.value = [...std.options].some((o) => o.value === `${w}x${h}`) ? `${w}x${h}` : '';
  // Above ~3.3 MP an 8 GB GPU runs out of memory unless Low-memory mode moves the weights to RAM.
  const vram = SETUP?.gpu?.vram_gb, mp = (w * h) / (1024 * 1024);
  const limit = !vram || vram >= 12 ? Infinity : vram >= 8 ? 3.3 : 2;
  const tooBig = mp > limit && !SETUP?.low_vram_active;
  $('size-help').classList.toggle('warn-text', tooBig);
  if (tooBig) {
    $('size-help').textContent = `Large for your ${vram} GB GPU — it may run out of memory. Turn on Low-memory mode in Settings → Setup (slower), or pick a smaller size.`;
    return;
  }
  $('size-help').textContent = S.size.ratio === 'auto' ? 'Auto keeps the first reference image\'s shape.'
    : S.size.ratio === 'custom' ? 'Custom size, 256–2048 on each side. Sizes like 1920 × 1080 are made slightly larger and trimmed to fit exactly.'
    : 'Pick a shape and a resolution, or type an exact width and height.';
}
$('ratios').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-ratio]');
  if (!b) return;
  S.size.ratio = b.dataset.ratio;
  if (S.size.ratio === 'auto') S.size.auto = await imageSize(S.pins[0].file);
  applySize();
});
$('size-level').addEventListener('click', (e) => {
  const b = e.target.closest('[data-level]');
  if (!b) return;
  S.size.level = b.dataset.level;
  if (S.size.ratio === 'custom') {  // keep the custom shape, change its resolution
    const [w, h] = getSize();
    [$('width').value, $('height').value] = sizeFor(w, h, LEVELS[S.size.level]);
    renderSize(); updateSummary(); return;
  }
  applySize();
});
$('size-standard').addEventListener('change', (e) => {
  const [w, h] = e.target.value.split('x').map(Number);
  if (!w) return;
  $('width').value = w; $('height').value = h;
  S.size.ratio = 'custom';
  for (const [k, rw, rh] of RATIOS) for (const [lv, mp] of Object.entries(LEVELS)) {  // light up a matching button
    const [sw, sh] = sizeFor(rw, rh, mp);
    if (sw === w && sh === h) { S.size.ratio = k; S.size.level = lv; }
  }
  renderSize();
  updateSummary();
});
for (const id of ['width', 'height']) {
  $(id).addEventListener('input', () => { S.size.ratio = 'custom'; renderSize(); updateSummary(); });
  $(id).addEventListener('change', () => { $(id).value = clampSide(Number($(id).value) || 1024); renderSize(); updateSummary(); });
}
function imageSize(file) {
  return new Promise((resolve) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => { resolve([img.naturalWidth, img.naturalHeight]); URL.revokeObjectURL(url); };
    img.onerror = () => { resolve([1, 1]); URL.revokeObjectURL(url); };
    img.src = url;
  });
}
function setSizeFromText(text, ratio, level) {  // presets: "1360x768" (+ the ratio and level they were made with)
  const [w, h] = String(text || '').split('x').map(Number);
  if (!w || !h) return;
  $('width').value = clampSide(w); $('height').value = clampSide(h);
  S.size.level = LEVELS[level] ? level : S.size.level;
  S.size.ratio = ratio && (ratio === 'custom' || RATIOS.some((x) => x[0] === ratio)) ? ratio : 'custom';
  if (!ratio) {  // older presets only stored the size: recognise a shape + resolution that makes it
    for (const [k, rw, rh] of RATIOS) for (const [lv, mp] of Object.entries(LEVELS)) {
      const [sw, sh] = sizeFor(rw, rh, mp);
      if (sw === clampSide(w) && sh === clampSide(h)) { S.size.ratio = k; S.size.level = lv; }
    }
  }
  renderSize();
}

// ---------- create: batch reference, settings, submit ----------

// Pinned references: up to 4 images every prompt in the batch uses (character, setting, style…).
const MAX_PINS = 4;
S.pins = [];
function addPins(files) {
  for (const file of files) {
    if (!file.type.startsWith('image/')) { toast(`${file.name} is not an image.`); continue; }
    if (S.pins.length >= MAX_PINS) { toast(`Up to ${MAX_PINS} pinned references.`); break; }
    S.pins.push({ file, url: URL.createObjectURL(file) });
  }
  renderPins();
}
function clearPins() {
  S.pins.forEach((x) => URL.revokeObjectURL(x.url));
  S.pins = [];
  renderPins();
}
function renderPins() {
  $('pins').innerHTML = S.pins.map((x, i) => `<div class="pin"><img src="${x.url}" alt="Pinned reference ${i + 1}" title="${esc(x.file.name)}">
    <span class="n">${i + 1}</span><button type="button" data-pin="${i}" aria-label="Remove pinned reference ${i + 1}">${icon('x', 13)}</button></div>`).join('');
  $('pins-count').textContent = `${S.pins.length} / ${MAX_PINS}`;
  $('batch-ref').hidden = S.pins.length >= MAX_PINS;
  $('batch-ref-title').textContent = S.pins.length ? 'Add another image' : 'Drop images or browse';
  if (S.size.ratio === 'auto' && S.pins.length) imageSize(S.pins[0].file).then((d) => { S.size.auto = d; applySize(); });
  else if (S.size.ratio === 'auto') { S.size.ratio = '1:1'; applySize(); }  // its reference is gone
  else renderSize();
}
$('pins').addEventListener('click', (e) => {
  const b = e.target.closest('[data-pin]');
  if (!b) return;
  URL.revokeObjectURL(S.pins[+b.dataset.pin].url);
  S.pins.splice(+b.dataset.pin, 1);
  renderPins();
});
const dz = $('batch-ref');
dz.addEventListener('click', () => pickFile(addPins, true));
dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickFile(addPins, true); } });
dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drop'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drop'));
dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drop'); addPins([...e.dataTransfer.files]); });

$('per-minus').addEventListener('click', () => { S.perPrompt = Math.max(1, S.perPrompt - 1); $('per-prompt').textContent = S.perPrompt; updateSummary(); });
$('per-plus').addEventListener('click', () => { S.perPrompt = Math.min(8, S.perPrompt + 1); $('per-prompt').textContent = S.perPrompt; updateSummary(); });

function renderModels() {
  const models = (S.state?.models || []).filter((m) => m.installed);
  if (!models.some((m) => m.key === S.model)) S.model = S.state?.default_model;
  $('model-select').innerHTML = models.map((m) =>
    `<option value="${m.key}"${m.key === S.model ? ' selected' : ''}>${esc(m.short)}${m.noncommercial ? ' · non-commercial' : ''}</option>`).join('');
  S.modelsSig = models.map((m) => m.key).join();
}
const modelInfo = (key) => S.state?.models.find((m) => m.key === key) || {};
// Upscale choices are "factor:model", e.g. "2:illustration".
const upscaleChoice = (v) => (v ? { factor: Number(v.split(':')[0]), model: v.split(':')[1] } : null);
const upscalerLabel = (key) => S.state?.upscalers.find((u) => u.key === key)?.label || key;
function renderUpscalers() {
  const ups = (S.state?.upscalers || []).filter((u) => u.installed);
  const sel = $('upscale'), keep = sel.value;
  sel.innerHTML = `<option value="">${ups.length ? 'Off — keep the generated size' : 'Off — download an upscaler on the Models page'}</option>` + ups.flatMap((u) =>
    [2, 4].map((f) => `<option value="${f}:${u.key}">${f}× · ${esc(u.label)} — ${esc(u.hint)}</option>`)).join('');
  sel.value = keep;
  const opts = ups.map((u) => `<option value="${u.key}">${esc(u.label)} — ${esc(u.hint)}</option>`).join('');
  for (const id of ['up-model', 'v-up-model']) { const k = $(id).value; $(id).innerHTML = opts; if (k) $(id).value = k; }
}
$('upscale').addEventListener('change', updateSummary);

S.stylePos = 'before';
for (const [id, pos] of [['style-before', 'before'], ['style-after', 'after']]) {
  $(id).addEventListener('click', () => {
    S.stylePos = pos;
    $('style-before').setAttribute('aria-pressed', pos === 'before');
    $('style-after').setAttribute('aria-pressed', pos === 'after');
  });
}

// ---------- create: LoRAs ----------

S.loraLib = [];
S.loraJobs = {};
S.loraPicks = [];  // [{id, strength, use_triggers}]
const MAX_LORA_PICKS = 3;
const familyName = (f) => ({ 'flux2-klein-4b': 'FLUX.2 klein 4B', 'flux2-klein-9b': 'FLUX.2 klein 9B', 'z-image': 'Z-Image' }[f] || 'Unknown model');

async function loadLoras() {
  try {
    const r = await api('/api/loras');
    S.loraLib = r.items;
    S.loraJobs = r.jobs;
  } catch { return; }
  S.loraPicks = S.loraPicks.filter((x) => S.loraLib.some((l) => l.id === x.id));
  renderLoraPicker();
  if (S.view === 'models') renderLoraLibrary();
}
function renderLoraPicker() {
  const family = modelInfo(S.model).family;
  setHtml($('lora-picks'), S.loraPicks.map((x, i) => {
    const l = S.loraLib.find((y) => y.id === x.id) || {};
    const ok = l.family === family;
    return `<div class="lora-pick${ok ? '' : ' bad'}" data-i="${i}">
      <strong title="${esc(l.name)}">${esc(l.name)}</strong>
      <button type="button" class="icon-btn" data-lora-act="remove" aria-label="Remove ${esc(l.name)}" style="width:22px;height:22px">${icon('x', 13)}</button>
      <div class="str"><label class="micro" for="lora-str-${i}" style="font-weight:400">Strength</label>
        <input type="range" id="lora-str-${i}" min="0" max="1.5" step="0.05" value="${x.strength}" data-lora-act="strength">
        <span class="mono">${Number(x.strength).toFixed(2)}</span></div>
      ${l.triggers ? `<label class="trig"><input type="checkbox" data-lora-act="triggers"${x.use_triggers ? ' checked' : ''}> Add trigger words <code>${esc(l.triggers)}</code></label>` : ''}
      ${ok ? '' : `<span class="trig warn-text">Made for ${esc(familyName(l.family))} — it won't be used with this model.</span>`}
    </div>`;
  }).join(''));
  const usable = S.loraLib.filter((l) => l.family === family && !S.loraPicks.some((x) => x.id === l.id));
  $('lora-add').innerHTML = `<option value="">${S.loraLib.length ? '+ Add a LoRA…' : 'No LoRAs yet — add some on the Models page'}</option>` +
    usable.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join('');
  $('lora-add-wrap').hidden = S.loraPicks.length >= MAX_LORA_PICKS;
  $('lora-add').disabled = !usable.length;
  $('lora-help').textContent = !S.loraLib.length
    ? 'Style or character add-ons. Add LoRAs on the Models page, then pick them here.'
    : !S.loraLib.some((l) => l.family === family)
      ? `None of your LoRAs are made for ${familyName(family)}.`
      : `Style or character add-ons for ${familyName(family)}, up to ${MAX_LORA_PICKS}. Trigger words go in front of every prompt.`;
}
$('lora-add').addEventListener('change', () => {
  const l = S.loraLib.find((y) => y.id === $('lora-add').value);
  if (l) S.loraPicks.push({ id: l.id, strength: l.strength, use_triggers: true });
  renderLoraPicker();
});
$('lora-picks').addEventListener('input', (e) => {
  const act = e.target.dataset.loraAct;
  if (act !== 'strength') return;
  const i = +e.target.closest('[data-i]').dataset.i;
  S.loraPicks[i].strength = Number(e.target.value);
  e.target.nextElementSibling.textContent = Number(e.target.value).toFixed(2);
});
$('lora-picks').addEventListener('change', (e) => {
  if (e.target.dataset.loraAct !== 'triggers') return;
  S.loraPicks[+e.target.closest('[data-i]').dataset.i].use_triggers = e.target.checked;
});
$('lora-picks').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-lora-act="remove"]');
  if (!btn) return;
  S.loraPicks.splice(+btn.closest('[data-i]').dataset.i, 1);
  $('lora-picks')._html = null;
  renderLoraPicker();
});
$('lora-manage').addEventListener('click', () => setTimeout(() => $('lora-library').scrollIntoView({ behavior: 'smooth' }), 150));

// ---------- settings: LoRA library ----------

function renderLoraLibrary() {
  const jobs = Object.entries(S.loraJobs).filter(([, j]) => j.status !== 'done');
  const jobHtml = jobs.map(([, j]) => `<div class="model-row"><div class="model-info"><strong>${esc(j.name)}</strong>
      <p>${j.status === 'failed' ? `<span class="warn-text">Import failed: ${esc(j.error)}</span>` : j.status === 'converting' ? 'Checking and adjusting the layers…' : 'Downloading…'}</p></div>
      <div class="model-act">${j.status === 'downloading' ? `<div class="dl-track"><div style="width:${j.total ? (j.done / j.total) * 100 : 0}%"></div></div>
      <span class="micro" style="font-weight:400">${gb(j.done)} of ${gb(j.total)}</span>` : ''}</div></div>`).join('');
  const rows = S.loraLib.map((l) => `<div class="lora-row" data-id="${l.id}">
      <label class="field"><span class="label">Name</span><input class="input" data-f="name" value="${esc(l.name)}"></label>
      <label class="field"><span class="label">Base model</span><div class="select-wrap"><select class="select" data-f="family">
        ${l.family ? '' : '<option value="" selected>Unknown — pick one</option>'}
        ${['flux2-klein-4b', 'flux2-klein-9b', 'z-image'].map((f) => `<option value="${f}"${l.family === f ? ' selected' : ''}>${familyName(f)}</option>`).join('')}
      </select><img src="icons/chevron-down.svg" width="16" height="16" alt=""></div></label>
      <label class="field"><span class="label">Trigger words</span><input class="input mono" data-f="triggers" value="${esc(l.triggers)}" placeholder="none"></label>
      <label class="field"><span class="label">Strength</span><input class="input mono" data-f="strength" type="number" min="0" max="2" step="0.05" value="${l.strength}"></label>
      <div class="acts"><button type="button" class="btn sm" data-lora-lib="save">Save</button>
        <button type="button" class="text-btn red" data-lora-lib="delete">Remove</button></div>
      <div class="meta-line">
        <span class="tag">${gb(l.size)}</span>
        ${l.license ? `<span class="tag">${esc(l.license)}</span>` : ''}
        ${l.detected ? `<span class="tag ok">Detected: ${familyName(l.detected)}</span>` : '<span class="tag bad">Base model not detected</span>'}
        ${l.fixed_layers ? `<span class="tag">Adjusted ${l.fixed_layers} layers on import — all apply</span>` : ''}
        ${l.source ? `<a class="text-btn red" href="${esc(l.source)}" target="_blank" rel="noopener" style="font-size:11px">Source ↗</a>` : ''}
      </div>
    </div>`).join('');
  setHtml($('lora-list'), jobHtml + (rows || (jobs.length ? '' : '<p class="help" style="font-size:12px">No LoRAs yet. Paste a Hugging Face link above — for example a style LoRA made for FLUX.2 klein 4B.</p>')));
}
let loraFound = null;
$('lora-find').addEventListener('click', async () => {
  const url = $('lora-url').value.trim();
  if (!url) { toast('Paste a Hugging Face link first.'); return; }
  $('lora-find').disabled = true;
  try {
    loraFound = await api('/api/loras/resolve', { method: 'POST', json: { url } });
    const f = loraFound;
    $('lora-preview').innerHTML = `<div class="lora-preview">
      <div class="row between" style="flex-wrap:wrap"><strong>${esc(f.repo)}</strong>
        <span class="tag ${f.family ? 'ok' : 'bad'}">${f.family ? `For ${familyName(f.family)}` : 'Base model unknown — checked after download'}</span></div>
      <span class="micro" style="font-weight:400;text-transform:none">File: ${esc(f.file)}${f.license ? ` · licence: ${esc(f.license)}` : ''}</span>
      <div class="pair"><label class="field"><span class="label">Name</span><input class="input" id="lora-new-name" value="${esc(f.name)}"></label>
        <label class="field"><span class="label">Trigger words</span><input class="input mono" id="lora-new-triggers" value="${esc(f.triggers)}" placeholder="none"></label></div>
      <div class="row" style="justify-content:flex-end;gap:8px"><button type="button" class="btn" id="lora-cancel">Cancel</button>
        <button type="button" class="btn red" id="lora-import">Download &amp; add</button></div></div>`;
  } catch (err) { toast(err.message); }
  $('lora-find').disabled = false;
});
$('lora-preview').addEventListener('click', async (e) => {
  if (e.target.id === 'lora-cancel') { $('lora-preview').innerHTML = ''; loraFound = null; return; }
  if (e.target.id !== 'lora-import' || !loraFound) return;
  try {
    await api('/api/loras/import', { method: 'POST', json: { url: $('lora-url').value.trim(), name: $('lora-new-name').value.trim(),
                                                              triggers: $('lora-new-triggers').value.trim() } });
    $('lora-preview').innerHTML = ''; $('lora-url').value = ''; loraFound = null;
    toast('Downloading the LoRA — it appears in the list when ready', true);
    await loadLoras(); tick(true);
  } catch (err) { toast(err.message); }
});
$('lora-upload-btn').addEventListener('click', () => $('lora-file').click());
$('lora-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  form.append('name', file.name.replace(/\.safetensors$/i, '').replace(/[_-]+/g, ' '));
  toast(`Adding ${file.name}…`, true);
  try {
    const l = await api('/api/loras/upload', { method: 'POST', form });
    toast(l.family ? `Added “${l.name}” for ${familyName(l.family)}` : `Added “${l.name}” — pick its base model below`, true);
    await loadLoras();
  } catch (err) { toast(err.message); }
});
$('lora-list').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-lora-lib]');
  if (!btn) return;
  const row = btn.closest('[data-id]'), id = row.dataset.id;
  const l = S.loraLib.find((x) => x.id === id);
  try {
    if (btn.dataset.loraLib === 'save') {
      const val = (f) => row.querySelector(`[data-f="${f}"]`).value;
      await api(`/api/loras/${id}`, { method: 'PATCH', json: { name: val('name'), family: val('family'), triggers: val('triggers'), strength: Number(val('strength')) } });
      toast('Saved', true);
    } else {
      if (!await confirmDialog(`Remove “${l.name}”?`, 'The LoRA file is deleted from the library folder. Batches already made with it keep their images.', 'Remove')) return;
      await api(`/api/loras/${id}`, { method: 'DELETE' });
      toast('LoRA removed', true);
    }
    $('lora-list')._html = null;
    await loadLoras();
  } catch (err) { toast(err.message); }
});

// ---------- create: presets ----------

let PRESETS = [];
async function loadPresets(selectId) {
  try { PRESETS = await api('/api/presets'); } catch { return; }
  const sel = $('preset'), keep = selectId ?? sel.value;
  sel.innerHTML = '<option value="">No preset</option>' + PRESETS.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  sel.value = PRESETS.some((p) => p.id === keep) ? keep : '';
  $('preset-delete').hidden = !sel.value;
}
function currentValues() {
  return { size: getSize().join('x'), size_ratio: S.size.ratio === 'auto' ? 'custom' : S.size.ratio, size_level: S.size.level,
           per_prompt: S.perPrompt, model: S.model, seed: $('seed').value.trim(),
           upscale: $('upscale').value, style_text: $('style').value.trim(), style_position: S.stylePos,
           loras: S.loraPicks.map((x) => ({ ...x })) };
}
async function applyPreset(p) {
  const v = p.values || {};
  if (v.size) setSizeFromText(v.size, v.size_ratio, v.size_level);
  if (v.per_prompt) { S.perPrompt = Number(v.per_prompt); $('per-prompt').textContent = S.perPrompt; }
  if (v.model && S.state?.models.some((m) => m.key === v.model && m.installed)) { S.model = v.model; renderModels(); renderHeader(); }
  $('seed').value = v.seed ?? '';
  $('upscale').value = [...$('upscale').options].some((o) => o.value === v.upscale) ? v.upscale : '';
  $('style').value = v.style_text || '';
  S.loraPicks = (v.loras || []).filter((x) => S.loraLib.some((l) => l.id === x.id)).map((x) => ({ ...x }));
  $('lora-picks')._html = null;
  renderLoraPicker();
  $(v.style_position === 'after' ? 'style-after' : 'style-before').click();
  if (p.refs) {
    clearPins();
    for (let i = 0; i < p.refs; i++) {
      const blob = await (await fetch(`/api/presets/${p.id}/ref/${i}`)).blob();
      addPins([new File([blob], `${p.name} reference ${i + 1}.png`, { type: 'image/png' })]);
    }
  }
  updateSummary();
  toast(`Applied preset “${p.name}”`, true);
}
$('preset').addEventListener('change', () => {
  const p = PRESETS.find((x) => x.id === $('preset').value);
  $('preset-delete').hidden = !p;
  if (p) applyPreset(p);
});
$('preset-save').addEventListener('click', () => {
  const dlg = $('preset-dlg');
  $('preset-name').value = PRESETS.find((x) => x.id === $('preset').value)?.name || '';
  $('preset-ref-row').hidden = !S.pins.length;
  $('preset-ref').checked = S.pins.length > 0;
  $('preset-ref-row').querySelector('span:last-child').textContent =
    `Include the ${S.pins.length === 1 ? 'pinned reference image' : `${S.pins.length} pinned reference images`}`;
  dlg.returnValue = '';
  dlg.showModal();
  $('preset-name').focus();
});
$('preset-dlg').addEventListener('close', async () => {
  if ($('preset-dlg').returnValue !== 'ok') return;
  const name = $('preset-name').value.trim();
  if (!name) { toast('Give the preset a name.'); return; }
  const form = new FormData();
  form.append('spec', JSON.stringify({ name, values: currentValues() }));
  if ($('preset-ref').checked) S.pins.forEach((x, k) => form.append(`ref_${k}`, x.file));
  try {
    const p = await api('/api/presets', { method: 'POST', form });
    await loadPresets(p.id);
    toast(`Saved preset “${p.name}”`, true);
  } catch (err) { toast(err.message); }
});
$('preset-delete').addEventListener('click', async () => {
  const p = PRESETS.find((x) => x.id === $('preset').value);
  if (!p || !await confirmDialog('Delete this preset?', `“${p.name}” will be removed. Batches made with it aren't affected.`)) return;
  try { await api(`/api/presets/${p.id}`, { method: 'DELETE' }); await loadPresets(''); toast('Preset deleted', true); }
  catch (err) { toast(err.message); }
});

$('model-select').addEventListener('change', () => {
  S.model = $('model-select').value;
  updateSummary(); renderHeader();
  const family = modelInfo(S.model).family;
  const dropped = S.loraPicks.filter((x) => S.loraLib.find((l) => l.id === x.id)?.family !== family);
  if (dropped.length) {
    S.loraPicks = S.loraPicks.filter((x) => !dropped.includes(x));
    toast(`Removed ${plural(dropped.length, 'LoRA')} made for a different model.`);
  }
  renderLoraPicker();
  if (S.pins.length && !modelInfo(S.model).refs) toast(`${modelInfo(S.model).label} doesn't use reference images — remove the pinned ones or pick a FLUX.2 model.`);
});

$('batch-name').addEventListener('input', updateNameHelp);
function updateNameHelp() {
  const typed = $('batch-name').value.trim();
  $('name-help').innerHTML = typed
    ? 'Type to rename. Saved as a folder on disk.'
    : `Type to rename. Saved as a folder on disk — <span class="mono">${esc(autoName())}</span>`;
}
setInterval(updateNameHelp, 20000);

function currentRows() {
  return S.mode === 'paste' ? pastedRows() : S.rows.filter((r) => r.text.trim());
}
function promptTexts() {
  return currentRows().map((r) => r.text.trim());
}
function secondsPerImage(w, h, model) {
  const seen = S.batches.find((b) => b.avg_seconds && b.settings.width === w && b.settings.height === h && b.settings.model === model);
  if (seen) return seen.avg_seconds;
  const speed = S.state?.models.find((m) => m.key === model)?.speed || 1;  // relative to FLUX.2 klein 4B Q4
  return (0.6 + 6.4 * (w * h) / (1024 * 1024)) * speed;
}
function updateSummary() {
  if (S.cmode === 'single') {
    const text = $('single-prompt').value.trim();
    const [w, h] = getSize();
    $('start-label').textContent = `Generate · ${plural(S.perPrompt, 'image')}`;
    $('start').disabled = !text;
    const busy = (S.state?.queue || []).length > 0;
    $('sum-eta').textContent = !text ? 'Describe the image to generate.'
      : `About ${fmtDuration(S.perPrompt * secondsPerImage(w, h, S.model))} on this GPU` + (busy ? ' · made next, ahead of queued batches' : '');
    return;
  }
  const rows = currentRows();
  $('prompt-count').textContent = plural(rows.length, 'prompt');
  updateNameHelp();
  if (!rows.length) { $('start-label').textContent = 'Start batch'; $('sum-eta').textContent = 'Add a prompt to start.'; $('start').disabled = true; return; }
  let total = 0, seconds = 0;
  for (const r of rows) {
    const count = r.per ? Number(r.per) : S.perPrompt;
    const [w, h] = r.size ? r.size.split('x').map(Number) : getSize();
    total += count;
    seconds += count * secondsPerImage(w, h, S.model);
  }
  $('start-label').textContent = `Start batch · ${plural(total, 'image')}`;
  const queued = (S.state?.queue || []).length;
  $('sum-eta').textContent = `About ${fmtDuration(seconds)} on this GPU` +
    (queued ? ` · starts after ${plural(queued, 'queued batch').replace('batchs', 'batches')}` : '');
  $('start').disabled = false;
}

$('start').addEventListener('click', async () => {
  if (S.cmode === 'single') return startSingle();
  if (S.mode === 'paste') syncFromPaste();
  const rows = S.rows.filter((r) => r.text.trim());
  if (!rows.length) return;
  const seedText = $('seed').value.trim();
  if (seedText && !/^\d{1,10}$/.test(seedText)) { toast('Seed must be a whole number (or leave it empty for random).'); return; }
  const badSeed = rows.findIndex((r) => r.seed !== '' && !/^\d{1,10}$/.test(String(r.seed)));
  if (badSeed >= 0) { toast(`Prompt ${badSeed + 1}: seed must be a whole number (or leave it empty).`); return; }
  const [width, height] = getSize();
  const form = new FormData();
  form.append('spec', JSON.stringify({
    name: $('batch-name').value.trim() || null,
    prompts: rows.map((r) => {
      const p = { text: r.text.trim() };
      if (r.size) [p.width, p.height] = r.size.split('x').map(Number);
      if (r.per) p.per_prompt = Number(r.per);
      if (r.seed !== '') p.seed = Number(r.seed);
      return p;
    }),
    settings: { model: S.model, width, height, per_prompt: S.perPrompt, seed: seedText ? Number(seedText) : null,
                style: { text: $('style').value.trim(), position: S.stylePos }, upscale: upscaleChoice($('upscale').value),
                loras: S.loraPicks },
  }));
  S.pins.forEach((x, k) => form.append(`ref_batch_${k}`, x.file));
  rows.forEach((r, i) => r.ref && form.append(`ref_${i}`, r.ref.file));
  $('start').disabled = true;
  try {
    const b = await api('/api/batches', { method: 'POST', form });
    const waiting = (S.state?.queue || []).length > 0;
    toast(waiting ? `Queued “${b.name}” — it starts after the current batch.` : `Started “${b.name}”`, true);
    S.rows.forEach((r) => r.ref && URL.revokeObjectURL(r.ref.url));
    S.rows = [newRow()];
    $('prompt-paste').value = '';
    $('batch-name').value = '';  // pinned references, style and settings stay for the next batch
    S.focusId = b.id; S.pinned = waiting;  // queued behind another batch: keep the new one in view
    S.galleryAll = false;
    renderRows();
    await tick(true);
  } catch (err) {
    toast(err.message);
  } finally {
    updateSummary();
  }
});

// ---------- create: current batch ----------

function pickFocus() {
  const ids = new Set(S.batches.map((b) => b.id));
  if (S.pinned && ids.has(S.focusId)) return S.focusId;
  S.pinned = false;
  if (S.cmode === 'single') {
    const singles = S.batches.filter((b) => b.kind === 'singles').sort((a, b) => b.created.localeCompare(a.created))[0];
    if (singles) return singles.id;
  }
  return S.state?.current?.batch || S.state?.queue?.[0] || S.batches[0]?.id || null;
}

async function refreshDetail(force) {
  const id = pickFocus();
  if (!id) { S.detail = null; return; }
  const summary = S.batches.find((b) => b.id === id);
  const sig = summary && `${summary.status}|${summary.done}|${summary.failed}|${summary.cancelled}|${summary.name}|${summary.remaining}`;
  if (force || !S.detail || S.detail.id !== id || S.detail._sig !== sig || isActive(summary.status)) {
    if (S.detail?.id !== id) S.galleryAll = false;
    S.detail = await api(`/api/batches/${id}`);
    S.detail._sig = sig;
  }
}

function renderFocus() {
  const d = S.detail;
  renderSingleHero(d);
  if (!d) {
    setHtml($('focus'), S.cmode === 'single'
      ? `<div class="card empty">Describe an image on the left and press <b>Generate</b>. It appears here.</div>`
      : `<div class="card empty">No batches yet. Add prompts on the left and press <b>Start batch</b>.</div>`);
    renderSingleHero(null);
    setHtml($('next'), '');
    $('gallery-bar').hidden = $('gallery-foot').hidden = true;
    $('tiles').innerHTML = ''; delete $('tiles').dataset.batch; tileCache.clear();
    return;
  }
  if (S.renaming !== d.id) {
    const label = { running: 'Now running', queued: 'Queued', paused: 'Paused', done: 'Latest run', cancelled: 'Cancelled run' }[d.status];
    const processed = d.done + d.failed + d.cancelled;
    const pct = d.total ? (processed / d.total) * 100 : 0;
    const s = d.settings;
    const live = S.state?.current?.batch;
    const params = d.kind === 'singles' ? `Single images · ${plural(d.prompt_count, 'prompt')} · each with its own settings` : [`${s.width} × ${s.height}${d.mixed ? ' (default)' : ''}`, modelShort(s.model),
      d.mixed ? `${d.prompt_count} prompts · ${plural(d.total, 'image')} · per-prompt settings` : `${d.prompt_count} prompts × ${s.per_prompt}`,
      d.has_refs ? 'with reference images' : null, s.style ? 'with style' : null,
      s.upscale ? `upscale ${s.upscale.factor}× ${upscalerLabel(s.upscale.model).toLowerCase()}` : null,
      s.loras?.length ? `LoRA ${s.loras.map((l) => `${l.name} ${l.strength}`).join(' + ')}` : null,
      s.seed != null ? `seed ${s.seed}${s.seed_random ? ' (random)' : ''}` : null]
      .filter(Boolean).join('   /   ');
    const chip = d.status === 'done' && !d.failed && !d.cancelled ? `<span class="chip lime">${icon('dot-ink', 5)}Finished</span>` : statusChip(d);
    setHtml($('focus'), `
      <div class="summary">
        <div class="row between" style="flex-wrap:wrap">
          <span class="micro">${label} / ${fmtWhen(d.created).toUpperCase()}</span>
          <div class="summary-actions">
            ${isActive(d.status) ? '<button type="button" class="btn dark sm" data-act="pause">Pause</button>' : ''}
            ${d.status === 'paused' ? '<button type="button" class="btn dark sm" data-act="resume">Resume</button>' : ''}
            ${isActive(d.status) || d.status === 'paused' ? '<button type="button" class="btn dark sm" data-act="cancel">Cancel</button>' : ''}
            ${!isActive(d.status) && d.failed + d.cancelled ? `<button type="button" class="btn dark sm" data-act="retry">Retry ${d.failed + d.cancelled}</button>` : ''}
            ${d.done && !d.upscale_pending ? `<button type="button" class="btn dark sm" data-act="upscale">${d.upscaled ? 'Upscale again' : 'Upscale all'}</button>` : ''}
            ${d.done ? '<button type="button" class="btn dark sm" data-act="export">Export</button>' : ''}
            ${!isActive(d.status) ? '<button type="button" class="btn dark sm danger" data-act="delete">Delete</button>' : ''}
            ${chip}
          </div>
        </div>
        <div class="row" style="gap:10px;min-width:0" data-name>
          <h2 class="bname">${esc(d.name)}</h2>
          <button type="button" class="icon-btn" data-act="rename" aria-label="Rename batch">${icon('pencil-light', 14)}</button>
        </div>
        <p class="params">${esc(params)}</p>
        <div class="track" role="progressbar" aria-label="Batch progress" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100"><div style="width:${pct}%"></div></div>
        <div class="progress-row">
          <span class="done-count">${processed} / ${d.total} processed · ${d.done} done${d.failed ? ` · ${d.failed} failed` : ''}${d.cancelled ? ` · ${d.cancelled} cancelled` : ''}${d.upscaled || d.upscale_pending ? ` · ${d.upscaled} / ${d.upscaled + d.upscale_pending + d.upscale_failed} upscaled` : ''}${d.upscale_failed ? ` (${d.upscale_failed} failed)` : ''}</span>
          <span class="timing" id="timing"></span>
        </div>
        ${S.pinned && live && live !== d.id ? '<button type="button" class="text-btn lime" data-act="live" style="align-self:flex-start">← Show the running batch</button>' : ''}
      </div>`);
  }
  // Kept out of the markup above: it ticks every poll and would force a full redraw.
  const timing = $('timing');
  if (timing) {
    const parts = [];
    if (d.eta_seconds) parts.push(`≈ ${fmtDuration(d.eta_seconds)} left`);
    if (d.avg_seconds) parts.push(`${d.avg_seconds.toFixed(1)} s / image`);
    if (d.elapsed_seconds != null) parts.push(fmtDuration(d.elapsed_seconds));
    timing.textContent = parts.join(' · ');
  }
  $('gallery-bar').hidden = false;
  $('gallery-count').textContent = plural(d.total, 'image');
  renderTiles(d);
  renderNext(d.id);
}

function startRename(container, current, onSave) {
  container.innerHTML = `<div class="rename"><label class="sr-only" for="rename-input">Batch name</label>
    <input id="rename-input" value="${esc(current)}" spellcheck="false">
    <button type="button" class="btn red sm" data-r="save">Save</button>
    <button type="button" class="btn sm" data-r="cancel">Cancel</button></div>`;
  const input = container.querySelector('input');
  input.focus(); input.select();
  const done = async (save) => {
    if (save && input.value.trim() && input.value.trim() !== current) {
      try { await onSave(input.value.trim()); toast('Renamed — the folder was renamed too.', true); }
      catch (err) { toast(err.message); input.focus(); return; }
    }
    S.renaming = null;
    $('focus')._html = null;  // the rename box replaced rendered markup; force a redraw
    S.lastBatchesSig = '';
    await tick(true);
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(true); if (e.key === 'Escape') done(false); });
  container.querySelector('[data-r="save"]').addEventListener('click', () => done(true));
  container.querySelector('[data-r="cancel"]').addEventListener('click', () => done(false));
}
const renameBatch = (id, name) => api(`/api/batches/${id}`, { method: 'PATCH', json: { name } });

async function batchAction(id, act) {
  if (act === 'export') { openExportDialog(id); return; }
  try {
    if (act === 'delete') {
      const b = S.batches.find((x) => x.id === id);
      if (!await confirmDialog('Delete this batch?',
        `“${b.name}” and its ${plural(b.done, 'image')} will be moved to the Recycle Bin. You can restore the folder from there.`)) return;
      await api(`/api/batches/${id}`, { method: 'DELETE' });
      toast('Batch moved to the Recycle Bin', true);
      if (S.focusId === id) { S.focusId = null; S.pinned = false; }
      if (V.batchId === id && $('viewer').open) $('viewer').close();
      S.lastBatchesSig = '';
      await tick(true);
      return;
    }
    if (act === 'copy') {
      const d = await api(`/api/batches/${id}`);
      await navigator.clipboard.writeText(d.prompts.map((p) => p.text).join('\n'));
      toast(`Copied ${d.prompts.length} prompts`, true);
      return;
    }
    const r = await api(`/api/batches/${id}/${act}`, { method: 'POST' });
    if (act === 'rerun') { S.focusId = r.id; S.pinned = true; toast(`Re-running as “${r.name}”`, true); location.hash = '#create'; }
    await tick(true);
  } catch (err) { toast(err.message); }
}

$('focus').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn || !S.detail) return;
  const act = btn.dataset.act, d = S.detail;
  if (act === 'rename') {
    S.renaming = d.id;
    startRename(btn.closest('[data-name]'), d.name, (name) => renameBatch(d.id, name));
  } else if (act === 'live') { S.pinned = false; tick(true); }
  else if (act === 'upscale') openUpscaleDialog(d);
  else batchAction(d.id, act);
});
$('open-folder').addEventListener('click', () => S.detail && batchAction(S.detail.id, 'open'));
$('view-all').addEventListener('click', () => { S.galleryAll = !S.galleryAll; renderFocus(); });
for (const [id, list] of [['view-grid', false], ['view-list', true]]) {
  $(id).addEventListener('click', () => {
    S.galleryList = list;
    $('view-grid').setAttribute('aria-pressed', !list);
    $('view-list').setAttribute('aria-pressed', list);
    $('tiles').classList.toggle('list', list);
  });
}

const tileCache = new Map();
function phaseLabel() {
  const e = S.state?.engine || {};
  if (e.state === 'starting') return ['Loading model…', 0.03];
  if (e.phase === 'sampling' && e.steps) return [`Generating · step ${e.step} / ${e.steps}`, 0.1 + 0.8 * (e.step / e.steps)];
  if (e.phase === 'decoding') return ['Finishing…', 0.95];
  return ['Reading prompt…', 0.08];
}
function tileHtml(d, it, promptText) {
  let inner;
  if (it.status === 'done') {
    const v = encodeURIComponent(it.finished || '');
    inner = `<a href="${fileUrl(d.id, it.file)}?v=${v}" target="_blank" rel="noopener" title="Open in viewer">
      <img src="${fileUrl(d.id, it.file)}?v=${v}&thumb=1" alt="${esc(promptText)}" loading="lazy"></a>${upBadge(it)}`;
  } else if (it.status === 'running') {
    const [label, frac] = phaseLabel();
    const c = 2 * Math.PI * 17;
    inner = `<div class="state running"><svg class="ring" width="42" height="42" viewBox="0 0 42 42" aria-hidden="true">
      <circle class="bg" cx="21" cy="21" r="17"/><circle class="fg" cx="21" cy="21" r="17" stroke-dasharray="${(c * frac).toFixed(1)} ${c.toFixed(1)}"/></svg>
      <span>${label}</span></div>`;
  } else if (it.status === 'queued') {
    inner = '<div class="state queued">Queued</div>';
  } else {
    const failed = it.status === 'failed';
    inner = `<div class="state ${it.status}"><span>${failed ? 'Failed' : 'Cancelled'}</span>
      ${failed && it.error ? `<span class="err" title="${esc(it.error)}">${esc(it.error)}</span>` : ''}
      <button type="button" class="btn sm ${failed ? 'red' : ''}" data-retry="${it.id}">Retry</button></div>`;
  }
  return `<div class="frame">${inner}</div>
    <div class="tile-meta"><span class="file">${esc(it.file)}</span><span class="seed">${it.seed != null ? 'Seed ' + it.seed : '—'}</span></div>
    <p title="${esc(promptText)}">${esc(promptText)}</p>`;
}
function visibleItems(d) {
  if (S.galleryAll || d.items.length <= COLLAPSED_TILES) return d.items;
  // While running, show the images around the one in progress; otherwise the first few.
  const running = d.items.findIndex((it) => it.status === 'running');
  const anchor = running >= 0 ? running : d.items.findIndex((it) => it.status === 'queued');
  const start = anchor > 0 ? Math.min(Math.max(0, anchor - (COLLAPSED_TILES - 2)), d.items.length - COLLAPSED_TILES) : 0;
  return d.items.slice(start, start + COLLAPSED_TILES);
}
function renderTiles(d) {
  const grid = $('tiles');
  if (grid.dataset.batch !== d.id) { grid.innerHTML = ''; tileCache.clear(); grid.dataset.batch = d.id; }
  const prompts = new Map(d.prompts.map((p) => [p.n, p.text]));
  const e = S.state?.engine || {};
  const items = visibleItems(d);
  let prev = null;
  for (const it of items) {
    const sig = `${it.status}|${it.finished}|${it.seed}|${it.error}|${d.name}|${it.upscale?.status}|${it.upscale?.factor}` + (it.status === 'running' ? `|${e.state}|${e.phase}|${e.step}` : '');
    let entry = tileCache.get(it.id);
    if (!entry) {
      const fig = document.createElement('figure');
      fig.className = 'tile';
      entry = { fig, sig: null };
      tileCache.set(it.id, entry);
    }
    if (entry.sig !== sig) { entry.fig.innerHTML = tileHtml(d, it, itemText(d, it, prompts.get(it.prompt))); entry.sig = sig; }
    const want = prev ? prev.nextSibling : grid.firstChild;
    if (want !== entry.fig) grid.insertBefore(entry.fig, want);
    prev = entry.fig;
  }
  while (prev ? prev.nextSibling : grid.firstChild) (prev ? prev.nextSibling : grid.firstChild).remove();

  const collapsible = d.items.length > COLLAPSED_TILES;
  $('gallery-foot').hidden = !collapsible;
  $('showing').textContent = `Showing ${items.length} of ${d.items.length} · PNG`;
  $('view-all').textContent = S.galleryAll ? 'Show fewer ↑' : 'View all images →';
}
$('tiles').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-retry]');
  const fig = e.target.closest('.tile');
  if (!btn && fig && S.detail && e.target.closest('.frame') && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();  // ctrl/cmd-click still opens the file in a new tab
    const id = [...tileCache].find(([, entry]) => entry.fig === fig)?.[0];
    if (id) openViewer(S.detail.id, { id });
    return;
  }
  if (!btn || !S.detail) return;
  try { await api(`/api/batches/${S.detail.id}/items/${btn.dataset.retry}/retry`, { method: 'POST' }); await tick(true); }
  catch (err) { toast(err.message); }
});

function renderNext(focusId) {
  const order = S.state?.queue || [];
  const queue = order.filter((id) => id !== focusId).map((id) => S.batches.find((b) => b.id === id)).filter(Boolean);
  setHtml($('next'), queue.length ? `
    <div class="card next">
      <div class="row between"><span class="micro">Up next</span><span class="help tiny">Drag to reorder — the top batch runs first.</span></div>
      ${queue.map((b) => `
        <div class="next-row" draggable="true" data-id="${b.id}">
          <span class="grip" aria-hidden="true">⋮⋮</span>
          <span class="next-pos">#${order.indexOf(b.id) + 1}</span>
          <span class="mono" style="font-weight:500;overflow-wrap:anywhere">${esc(b.name)}</span>
          <span class="micro" style="font-weight:400">${b.prompt_count} prompts / ${plural(b.total, 'image')} / ${b.status === 'paused' ? 'paused' : 'waiting'}</span>
          <div class="bactions" style="margin-left:auto">
            <button type="button" class="btn sm" data-id="${b.id}" data-act="up" aria-label="Move ${esc(b.name)} up"${order.indexOf(b.id) === 0 ? ' disabled' : ''}>↑</button>
            <button type="button" class="btn sm" data-id="${b.id}" data-act="down" aria-label="Move ${esc(b.name)} down"${order.indexOf(b.id) === order.length - 1 ? ' disabled' : ''}>↓</button>
            <button type="button" class="btn sm" data-id="${b.id}" data-act="first">Run next</button>
            <button type="button" class="btn sm" data-id="${b.id}" data-act="view">View</button>
            <button type="button" class="btn sm" data-id="${b.id}" data-act="${b.status === 'paused' ? 'resume' : 'pause'}">${b.status === 'paused' ? 'Resume' : 'Pause'}</button>
            <button type="button" class="btn sm" data-id="${b.id}" data-act="cancel">Cancel</button>
          </div>
        </div>`).join('')}
    </div>` : '');
}
$('next').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const id = btn.dataset.id, act = btn.dataset.act, order = S.state.queue;
  if (act === 'view') { S.focusId = id; S.pinned = true; tick(true); }
  else if (act === 'up') moveInQueue(id, order.indexOf(id) - 1);
  else if (act === 'down') moveInQueue(id, order.indexOf(id) + 1);
  else if (act === 'first') {
    // straight after the batch that's generating now (that one keeps its current image)
    const running = S.state.current?.batch;
    moveInQueue(id, running && running !== id ? 1 : 0, running);
  } else batchAction(id, act);
});
async function moveInQueue(id, to, keepFirst) {
  const ids = S.state.queue.filter((x) => x !== id);
  if (keepFirst) { ids.splice(ids.indexOf(keepFirst), 1); ids.unshift(keepFirst); }
  ids.splice(Math.max(0, Math.min(to, ids.length)), 0, id);
  try { await api('/api/queue/order', { method: 'POST', json: { ids } }); $('next')._html = null; await tick(true); }
  catch (err) { toast(err.message); }
}
let dragId = null;
$('next').addEventListener('dragstart', (e) => { dragId = e.target.closest('.next-row')?.dataset.id; e.dataTransfer.effectAllowed = 'move'; });
$('next').addEventListener('dragend', () => { dragId = null; pointerHeld = false; });
$('next').addEventListener('dragover', (e) => {
  const row = e.target.closest('.next-row');
  if (!dragId || !row) return;
  e.preventDefault();
  $('next').querySelectorAll('.drag-over').forEach((r) => r.classList.remove('drag-over'));
  row.classList.add('drag-over');
});
$('next').addEventListener('drop', (e) => {
  const row = e.target.closest('.next-row');
  if (!dragId || !row) return;
  e.preventDefault();
  const to = S.state.queue.filter((x) => x !== dragId).indexOf(row.dataset.id);
  if (row.dataset.id !== dragId) moveInQueue(dragId, to);
});

function upBadge(it) {
  const up = it.upscale;
  if (!up) return '';
  if (up.status === 'done') return `<span class="up-badge">${up.factor}×</span>`;
  if (up.status === 'failed') return '<span class="up-badge failed">Upscale failed</span>';
  return `<span class="up-badge pending">${up.status === 'running' ? 'Upscaling…' : `${up.factor}× queued`}</span>`;
}

function openUpscaleDialog(d) {
  const dlg = $('upscale-dlg');
  const auto = d.settings.upscale;
  if (auto) { $('up-factor').value = String(auto.factor); $('up-model').value = auto.model; }
  const secs = { illustration: 15, detailed: 36 };  // measured at 4× on 1344×768 with this GPU
  const estimate = () => {
    const per = (secs[$('up-model').value] || 20) * (d.settings.width * d.settings.height) / (1344 * 768);
    $('upscale-note').textContent = `Makes a larger copy of each of the ${plural(d.done, 'finished image')} in upscaled\\. ` +
      `About ${fmtDuration(per * d.done)} in total. The originals stay as they are.`;
  };
  $('up-model').onchange = estimate;
  estimate();
  dlg.returnValue = '';
  dlg.onclose = async () => {
    if (dlg.returnValue !== 'ok') return;
    try {
      const r = await api(`/api/batches/${d.id}/upscale`, { method: 'POST', json: { factor: Number($('up-factor').value), model: $('up-model').value } });
      toast(r.queued ? `Queued ${plural(r.queued, 'image')} to upscale` : 'Every image is already upscaled that way', true);
      tick(true);
    } catch (err) { toast(err.message); }
  };
  dlg.showModal();
}

// ---------- image viewer ----------

const V = { batchId: null, detail: null, index: 0, sig: '', tab: 'edit', painted: false };
const KIND = { edit: 'Edit', vary: 'Variation', inpaint: 'Inpaint' };
function itemText(d, it, promptText) {
  if (!it.kind) return promptText;
  const src = d.items.find((x) => x.id === it.source);
  return `${KIND[it.kind]} of ${src ? src.file : it.source}: ${it.edit_prompt || promptText}`;
}
const TABS = {
  edit: { help: 'Describe a change — the layout, characters and style stay as they are.',
          placeholder: 'Make it night, keep everything else the same', button: 'Apply edit' },
  vary: { help: 'Repaints the same shot with new details. More change = further from the original.',
          placeholder: "Prompt for the new version (leave empty to reuse this image's prompt)", button: 'Make variation' },
  inpaint: { help: 'Paint over the area to change on the image, then describe what should be there.',
             placeholder: 'What should be in the painted area?', button: 'Inpaint painted area' },
};

async function openViewer(batchId, match) {
  try {
    V.batchId = batchId;
    V.detail = S.detail?.id === batchId ? S.detail : await api(`/api/batches/${batchId}`);
    V.index = Math.max(0, V.detail.items.findIndex((it) => it.id === match.id || it.file === match.file));
    V.sig = '';
    if (!$('viewer').open) $('viewer').showModal();
    renderViewer();
  } catch (err) { toast(err.message); }
}
async function reloadViewer() {
  V.detail = await api(`/api/batches/${V.batchId}`);
  V.sig = '';
  renderViewer();
}

function renderViewer() {
  const d = V.detail;
  if (!d.items.length) { $('viewer').close(); return; }
  V.index = Math.min(V.index, d.items.length - 1);
  const it = d.items[V.index];
  const e = S.state?.engine || {};
  const sig = `${d.id}|${V.index}|${it.id}|${it.status}|${it.finished}|${it.seed}|${d.items.length}|${d.name}|${it.upscale?.status}|${it.upscale?.factor}|${V.tab}` +
    (it.status === 'running' ? `|${e.phase}|${e.step}` : '');
  if (sig === V.sig) return;
  V.sig = sig;

  const p = d.prompts.find((x) => x.n === it.prompt) || {};
  const s = d.settings;
  const url = `${fileUrl(d.id, it.file)}?v=${encodeURIComponent(it.finished || '')}`;
  const stateHtml = it.status === 'running' ? `<div class="state running"><span>${phaseLabel()[0]}</span></div>`
    : it.status === 'queued' ? '<div class="state queued">Queued</div>'
    : it.status === 'failed' ? `<div class="state failed"><span>Failed</span><span class="err">${esc(it.error || '')}</span></div>`
    : '<div class="state cancelled">Cancelled</div>';
  const stage = $('v-img');
  V.painted = false;
  if (V.tab === 'inpaint' && it.status === 'done') {
    stage.innerHTML = `<div class="paint-wrap"><img src="${url}" alt="${esc(p.text || it.file)}"><canvas id="v-canvas" aria-label="Paint the area to change"></canvas></div>`;
    const img = stage.querySelector('img');
    img.complete ? setupCanvas(img) : img.addEventListener('load', () => setupCanvas(img), { once: true });
  } else if (it.status === 'done' || it.finished) {
    // While an image is queued to regenerate, keep showing the current file underneath.
    stage.innerHTML = `<img src="${url}" alt="${esc(p.text || it.file)}">` +
      (it.status === 'done' ? '' : `<span class="badge">${it.status === 'running' ? '<span class="chip red">Regenerating</span>' : statusChip({ status: it.status === 'queued' ? 'queued' : it.status, failed: 0 })}</span>`);
    stage.querySelector('img').addEventListener('error', () => { stage.innerHTML = stateHtml; }, { once: true });
  } else {
    stage.innerHTML = stateHtml;
  }

  $('v-pos').textContent = `${V.index + 1} / ${d.items.length}`;
  $('v-file').textContent = it.file;
  $('v-batch').textContent = d.name;
  $('v-status').innerHTML = it.status === 'done' ? `<span class="chip lime">${icon('dot-ink', 5)}Done</span>`
    : it.status === 'failed' ? '<span class="chip warn">Failed</span>' : statusChip({ status: it.status, failed: 0 });
  const pins = (d.batch_refs || []).length;
  const ref = [pins && plural(pins, 'pinned image'), p.ref && 'own image'].filter(Boolean).join(' + ') || 'None';
  const w = it.width || p.width || s.width, h = it.height || p.height || s.height;
  const facts = [['Seed', it.seed ?? '—'], ['Size', `${w} × ${h}`], ['Model', modelLabel(s.model)],
    ['Reference', it.kind ? '—' : ref], ['Time', it.status === 'done' && it.duration ? `${it.duration.toFixed(1)} s` : '—']];
  if (it.kind) {
    const src = d.items.find((x) => x.id === it.source);
    facts.unshift(['Made from', `${src ? src.file : it.source} · ${KIND[it.kind].toLowerCase()}${it.kind === 'vary' ? ` ${it.strength}` : ''}`]);
  }
  const up = it.upscale;
  if (up?.status === 'done') facts.push(['Upscaled', `${w * up.factor} × ${h * up.factor}`]);
  if (s.loras?.length) facts.push(['LoRA', s.loras.map((l) => `${l.name} · ${l.strength}`).join(', ')]);
  $('v-facts').innerHTML = facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
  $('v-prompt').textContent = it.kind ? `${KIND[it.kind]}: ${it.edit_prompt || '(same prompt)'}\n\nOriginal prompt: ${p.text || ''}` : (p.text || '');
  renderMakePanel(d, it);
  $('v-open').href = url;
  $('v-open').hidden = it.status !== 'done';
  $('v-prev').disabled = V.index === 0;
  $('v-next').disabled = V.index === d.items.length - 1;
  const upUrl = up?.file ? `${fileUrl(d.id, up.file)}?v=${encodeURIComponent(up.status + (it.finished || ''))}` : '';
  $('v-up-status').innerHTML = !up ? (it.status === 'done' ? 'Makes a larger copy in upscaled\\ — the original stays.' : 'Available once the image is generated.')
    : up.status === 'done' ? `${up.factor}× with ${esc(upscalerLabel(up.model))} · <a class="text-btn lime" href="${upUrl}" target="_blank" rel="noopener">Open upscaled ↗</a>`
    : up.status === 'running' ? 'Upscaling now…'
    : up.status === 'queued' ? `Queued for ${up.factor}× — runs after the images still generating.`
    : `Upscale failed: ${esc(up.error || '')}`;
  const upBusy = it.status !== 'done' || up?.status === 'queued' || up?.status === 'running';
  $('viewer').querySelectorAll('[data-v="up2"],[data-v="up4"]').forEach((b) => { b.disabled = upBusy; });
  const busy = it.status === 'running';
  $('viewer').querySelectorAll('[data-v="regen-same"],[data-v="regen-new"],[data-v="delete"]').forEach((b) => { b.disabled = busy; });
}

function renderMakePanel(d, it) {
  const canEdit = modelInfo(d.settings.model).refs !== false;
  if (V.tab === 'edit' && !canEdit) V.tab = 'vary';
  $('viewer').querySelectorAll('[data-tab]').forEach((b) => {
    b.setAttribute('aria-selected', b.dataset.tab === V.tab);
    b.disabled = b.dataset.tab === 'edit' && !canEdit;
    b.title = b.disabled ? `${modelLabel(d.settings.model)} can't edit by instruction` : '';
  });
  const t = TABS[V.tab];
  $('v-tab-help').textContent = t.help;
  $('v-edit-text').placeholder = t.placeholder;
  $('v-make-btn').textContent = t.button;
  $('v-make-btn').disabled = it.status !== 'done';
  $('v-brush').hidden = V.tab !== 'inpaint';
  $('v-strength').hidden = V.tab !== 'vary';
}
$('viewer').addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (!tab || tab.disabled) return;
  V.tab = tab.dataset.tab;
  V.sig = '';
  renderViewer();
});
$('v-strength-range').addEventListener('input', () => { $('v-strength-value').textContent = Number($('v-strength-range').value).toFixed(2); });

function setupCanvas(img) {
  const c = $('v-canvas');
  if (!c) return;
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext('2d');
  ctx.strokeStyle = ctx.fillStyle = '#de462b';
  ctx.lineCap = ctx.lineJoin = 'round';
  let last = null;
  const pos = (e) => {
    const r = c.getBoundingClientRect();
    return { x: (e.clientX - r.left) * c.width / r.width, y: (e.clientY - r.top) * c.height / r.height, k: c.width / r.width };
  };
  const draw = (a, b) => {
    ctx.lineWidth = Number($('v-brush-size').value) * a.k;  // brush size is in screen pixels
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    V.painted = true;
  };
  c.onpointerdown = (e) => { c.setPointerCapture(e.pointerId); last = pos(e); draw(last, last); };
  c.onpointermove = (e) => { if (last) { const q = pos(e); draw(last, q); last = q; } };
  c.onpointerup = c.onpointercancel = () => { last = null; };
}
$('v-mask-clear').addEventListener('click', () => {
  const c = $('v-canvas');
  if (c) c.getContext('2d').clearRect(0, 0, c.width, c.height);
  V.painted = false;
});
function maskBlob() {
  // White where painted, black elsewhere, at the image's full resolution.
  const c = $('v-canvas');
  const out = document.createElement('canvas');
  out.width = c.width; out.height = c.height;
  const ctx = out.getContext('2d');
  const src = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  const dst = ctx.createImageData(c.width, c.height);
  for (let i = 0; i < src.length; i += 4) {
    const v = src[i + 3] > 0 ? 255 : 0;
    dst.data[i] = dst.data[i + 1] = dst.data[i + 2] = v; dst.data[i + 3] = 255;
  }
  ctx.putImageData(dst, 0, 0);
  return new Promise((resolve) => out.toBlob(resolve, 'image/png'));
}

function stepViewer(delta) {
  const n = V.index + delta;
  if (n < 0 || n >= V.detail.items.length) return;
  V.index = n;
  renderViewer();
}
$('v-prev').addEventListener('click', () => stepViewer(-1));
$('v-next').addEventListener('click', () => stepViewer(1));
$('v-close').addEventListener('click', () => $('viewer').close());
document.addEventListener('keydown', (e) => {  // on document: focus isn't always inside the dialog
  if (!$('viewer').open || $('confirm').open) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); stepViewer(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); stepViewer(1); }
});
$('viewer').addEventListener('click', async (e) => {
  if (e.target === $('viewer')) { $('viewer').close(); return; }
  const btn = e.target.closest('[data-v]');
  if (!btn) return;
  const d = V.detail, it = d.items[V.index];
  try {
    if (btn.dataset.v === 'make') {
      const text = $('v-edit-text').value.trim();
      if (V.tab !== 'vary' && !text) { toast(V.tab === 'edit' ? 'Describe the change first.' : 'Describe what should be in the painted area.'); return; }
      if (V.tab === 'inpaint' && !V.painted) { toast('Paint over the area to change first.'); return; }
      const form = new FormData();
      form.append('kind', V.tab);
      form.append('prompt', text);
      form.append('strength', $('v-strength-range').value);
      if (V.tab === 'inpaint') form.append('mask', await maskBlob(), 'mask.png');
      const r = await api(`/api/batches/${d.id}/items/${it.id}/edit`, { method: 'POST', form });
      const made = (await api(`/api/batches/${d.id}`)).items.find((x) => x.id === r.item);
      toast(`Queued — ${made ? made.file : 'the new image'} will appear right after this one`, true);
      $('v-edit-text').value = '';
      await reloadViewer(); tick(true);
    } else if (btn.dataset.v === 'copy') {
      await navigator.clipboard.writeText(it.edit_prompt || d.prompts.find((p) => p.n === it.prompt)?.text || '');
      toast('Prompt copied', true);
    } else if (btn.dataset.v === 'regen-same' || btn.dataset.v === 'regen-new') {
      await api(`/api/batches/${d.id}/items/${it.id}/regenerate`, { method: 'POST', json: { new_seed: btn.dataset.v === 'regen-new' } });
      toast(btn.dataset.v === 'regen-new' ? 'Queued with a new seed — it replaces this image when done' : 'Queued to regenerate with the same seed', true);
      await reloadViewer(); tick(true);
    } else if (btn.dataset.v === 'up2' || btn.dataset.v === 'up4') {
      const factor = btn.dataset.v === 'up2' ? 2 : 4;
      await api(`/api/batches/${d.id}/upscale`, { method: 'POST', json: { factor, model: $('v-up-model').value, items: [it.id] } });
      toast(`Queued a ${factor}× upscale`, true);
      await reloadViewer(); tick(true);
    } else if (btn.dataset.v === 'delete') {
      if (!await confirmDialog('Delete this image?', `${it.file} will be moved to the Recycle Bin.`)) return;
      await api(`/api/batches/${d.id}/items/${it.id}`, { method: 'DELETE' });
      toast('Image moved to the Recycle Bin', true);
      await reloadViewer(); tick(true);
    }
  } catch (err) { toast(err.message); }
});

function download(url) {
  const a = Object.assign(document.createElement('a'), { href: url, download: '' });
  document.body.append(a);
  a.click();
  a.remove();
}
function openExportDialog(id) {
  const b = S.batches.find((x) => x.id === id);
  const content = $('ex-content');
  [...content.options].forEach((o) => { o.disabled = o.value !== 'originals' && !b.upscaled; });
  if (!b.upscaled) content.value = 'originals';
  $('export-note').textContent = `${plural(b.done, 'image')}${b.upscaled ? `, ${b.upscaled} upscaled` : ''}` +
    `${b.edits ? ` (including ${plural(b.edits, 'edited version')})` : ''}. Files are saved to your browser's downloads folder.`;
  const query = () => `content=${content.value}&names=${$('ex-names').value}&extras=${$('ex-extras').checked ? 1 : 0}`;
  $('ex-zip').onclick = () => { download(`/api/batches/${id}/export.zip?${query()}`); toast('Preparing the ZIP — your browser will save it when it\'s ready', true); };
  $('ex-sheet-pdf').onclick = () => download(`/api/batches/${id}/contact-sheet.pdf`);
  $('ex-sheet-png').onclick = () => download(`/api/batches/${id}/contact-sheet.png`);
  $('export-dlg').showModal();
}

function confirmDialog(title, body, okLabel = 'Delete') {
  return new Promise((resolve) => {
    const dlg = $('confirm');
    $('confirm-title').textContent = title;
    $('confirm-body').textContent = body;
    $('confirm-ok').textContent = okLabel;
    dlg.returnValue = '';
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true });
    dlg.showModal();
  });
}

// ---------- batches ----------

// ---------- batches: select several and delete them together ----------

S.selecting = false;
S.selected = new Set();
S.batchesShown = [];
const deletable = (b) => !isActive(b.status);
function setSelecting(on) {
  S.selecting = on;
  if (!on) S.selected.clear();
  $('select-toggle').setAttribute('aria-pressed', on);
  $('select-toggle').textContent = on ? 'Cancel' : 'Select';
  S.lastBatchesSig = '';
  renderBatches();
}
function renderSelectBar(list) {
  S.batchesShown = list;
  const ids = new Set(S.batches.map((b) => b.id));
  for (const id of [...S.selected]) if (!ids.has(id)) S.selected.delete(id);  // deleted or renamed away
  $('select-bar').hidden = !S.selecting;
  if (!S.selecting) return;
  const picked = S.batches.filter((b) => S.selected.has(b.id));
  const images = picked.reduce((n, b) => n + b.done, 0);
  $('select-count').textContent = picked.length ? `${plural(picked.length, 'batch').replace('batchs', 'batches')} selected · ${plural(images, 'image')}` : 'Select batches to delete';
  $('select-delete').disabled = !picked.length;
  $('select-delete').textContent = picked.length ? `Delete ${picked.length}` : 'Delete selected';
  const shownDeletable = list.filter(deletable);
  $('select-all').disabled = !shownDeletable.length || shownDeletable.every((b) => S.selected.has(b.id));
  $('select-none').disabled = !picked.length;
}
$('select-toggle').addEventListener('click', () => setSelecting(!S.selecting));
$('select-done').addEventListener('click', () => setSelecting(false));
$('select-all').addEventListener('click', () => {  // everything shown (respects the search), except running batches
  S.batchesShown.filter(deletable).forEach((b) => S.selected.add(b.id));
  S.lastBatchesSig = ''; renderBatches();
});
$('select-none').addEventListener('click', () => { S.selected.clear(); S.lastBatchesSig = ''; renderBatches(); });
$('blist').addEventListener('change', (e) => {
  const box = e.target.closest('[data-pick]');
  if (!box) return;
  box.checked ? S.selected.add(box.dataset.pick) : S.selected.delete(box.dataset.pick);
  S.lastBatchesSig = ''; renderBatches();
});
$('blist').addEventListener('click', (e) => {  // in select mode, clicking a card's text area toggles it too
  if (!S.selecting || e.target.closest('button, a, input, label, [data-name]')) return;
  const card = e.target.closest('.bcard-info')?.closest('.bcard');
  const b = card && S.batches.find((x) => x.id === card.dataset.id);
  if (!b || !deletable(b)) return;
  S.selected.has(b.id) ? S.selected.delete(b.id) : S.selected.add(b.id);
  S.lastBatchesSig = ''; renderBatches();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && S.selecting && S.view === 'batches' && !document.querySelector('dialog[open]')) setSelecting(false);
});
$('select-delete').addEventListener('click', async () => {
  const picked = S.batches.filter((b) => S.selected.has(b.id));
  if (!picked.length) return;
  const images = picked.reduce((n, b) => n + b.done, 0);
  const names = picked.slice(0, 5).map((b) => `“${b.name}”`).join(', ') + (picked.length > 5 ? ` and ${picked.length - 5} more` : '');
  if (!await confirmDialog(`Delete ${plural(picked.length, 'batch').replace('batchs', 'batches')}?`,
    `${names} — ${plural(images, 'image')} in all. The folders go to the Recycle Bin, so you can restore them.`, 'Delete')) return;
  $('select-delete').disabled = true;
  try {
    const r = await api('/api/batches-delete', { method: 'POST', json: { ids: picked.map((b) => b.id) } });
    if (picked.some((b) => b.id === S.focusId)) { S.focusId = null; S.pinned = false; }
    toast(r.skipped.length
      ? `Deleted ${r.deleted.length}; ${r.skipped.length} skipped (${r.skipped.map((x) => `${x.name}: ${x.reason}`).join('; ')})`
      : `Deleted ${plural(r.deleted.length, 'batch').replace('batchs', 'batches')} — they're in the Recycle Bin`, !r.skipped.length);
    setSelecting(false);
    await tick(true);
  } catch (err) { toast(err.message); $('select-delete').disabled = false; }
});

function renderBatches() {
  if (S.view !== 'batches' || S.renaming) return;
  const q = $('search').value.trim().toLowerCase();
  let list = S.batches.filter((b) => !q || b.name.toLowerCase().includes(q) || b.first_prompt.toLowerCase().includes(q));
  if (S.sort === 'name') list = [...list].sort((a, b) => a.name.localeCompare(b.name));
  if (S.sort === 'most') list = [...list].sort((a, b) => b.total - a.total);
  // A running batch's elapsed time changes every poll; only redraw for it once a minute.
  const sig = JSON.stringify([q, S.sort, S.selecting, [...S.selected], list.map((b) => ({ ...b, elapsed_seconds: Math.floor((b.elapsed_seconds || 0) / 60) }))]);
  if (sig === S.lastBatchesSig) return;
  S.lastBatchesSig = sig;

  $('batch-total').textContent = `${plural(S.batches.length, 'batch').replace('batchs', 'batches')} on disk`;
  renderSelectBar(list);
  $('image-total').textContent = `${S.batches.reduce((n, b) => n + b.done, 0)} images total`;
  if (!list.length) {
    $('blist').innerHTML = `<div class="card empty">${S.batches.length ? 'No batches match that search.' : 'No batches yet — create one on the Create page.'}</div>`;
    return;
  }
  $('blist').innerHTML = list.map((b) => {
    const s = b.settings;
    const shots = b.thumbs.map((t) => {
      const file = t.split('?')[0];
      return `<figure><a class="frame" data-file="${esc(file)}" href="${fileUrl(b.id, t)}" target="_blank" rel="noopener" style="display:block">
          <img src="${fileUrl(b.id, t, true)}" alt="${esc(file)}" loading="lazy" style="width:100%;height:100%;object-fit:cover"></a>
        <figcaption><span>${esc(file)}</span><span>PNG / ${modelShort(s.model)}</span></figcaption></figure>`;
    });
    while (shots.length < 4) shots.push('<figure><div class="frame"></div><figcaption><span>&nbsp;</span></figcaption></figure>');
    const meta = [plural(b.done, 'image'), b.upscaled ? `${b.upscaled} upscaled` : null, b.has_refs ? 'with reference images' : null, `${s.width} × ${s.height}`, modelShort(s.model),
      b.status === 'done' && b.elapsed_seconds ? `took ${fmtDuration(b.elapsed_seconds)}` : null].filter(Boolean).join('   /   ');
    const elapsedNote = b.status === 'running' ? `Running · ${b.done} of ${b.total}`
      : b.status === 'done' && !b.failed && !b.cancelled ? 'Finished · all prompts processed'
      : [b.failed && `${b.failed} failed`, b.cancelled && `${b.cancelled} cancelled`, b.remaining && `${b.remaining} waiting`].filter(Boolean).join(' · ');
    return `
      <article class="card bcard${S.selecting ? ' picking' : ''}${S.selected.has(b.id) ? ' selected' : ''}" data-id="${b.id}">
        <div class="bcard-info">
          <div class="bcard-head">
            ${S.selecting ? `<label class="bpick" title="${isActive(b.status) ? 'Still generating — can\'t be deleted yet' : 'Select'}">
              <input type="checkbox" data-pick="${b.id}" aria-label="Select ${esc(b.name)}" ${S.selected.has(b.id) ? 'checked' : ''} ${isActive(b.status) ? 'disabled' : ''}></label>` : ''}
            <div class="bcard-id" style="flex:1">
              <div class="row"><span class="micro">${fmtWhen(b.created, ' / ').toUpperCase()}</span>${statusChip(b)}</div>
              <div class="bcard-name" data-name>
                <h2>${esc(b.name)}</h2>
                <button type="button" class="icon-btn" data-act="rename" aria-label="Rename ${esc(b.name)}">${icon('pencil-red', 15)}</button>
              </div>
            </div>
            <div class="bactions">
              <button type="button" class="btn red" data-act="view">${icon('arrow-up-right-light', 18)}View</button>
              <button type="button" class="btn" data-act="open">${icon('folder', 18)}Open folder</button>
              ${b.kind === 'singles' ? '' : `<button type="button" class="btn" data-act="rerun">${icon('rotate-cw', 18)}Re-run</button>`}
              <button type="button" class="btn" data-act="copy">${icon('copy', 18)}Copy prompts</button>
              ${b.done ? '<button type="button" class="btn" data-act="export">Export</button>' : ''}
              ${isActive(b.status) ? '' : '<button type="button" class="btn danger-light" data-act="delete">Delete</button>'}
            </div>
          </div>
          <span class="micro" style="font-weight:400;text-transform:none;white-space:pre-wrap">${esc(meta)}</span>
        </div>
        <div class="sheet">${shots.join('')}</div>
        <div class="receipt">
          <div class="fact"><span class="micro">Output</span><strong>${plural(b.done, 'image')}</strong><span>${b.mixed ? `${b.prompt_count} prompts · per-prompt settings` : `${b.prompt_count} prompts × ${plural(s.per_prompt, 'image')}`}</span></div>
          <span class="vrule"></span>
          <div class="fact"><span class="micro">Elapsed</span><strong>${b.elapsed_seconds != null ? fmtDuration(b.elapsed_seconds) : '—'}</strong><span>${esc(elapsedNote || '—')}</span></div>
          <span class="vrule"></span>
          <div class="fact"><span class="micro">Generation</span><strong>${s.width} × ${s.height}</strong><span>${esc(modelLabel(s.model))}${b.has_refs ? ' · reference images' : ''}</span></div>
          <button type="button" class="view-all" data-act="viewall">${icon('arrow-up-right-lg', 24)}View all ${b.total}</button>
        </div>
        <div class="first-prompt"><span class="micro">First prompt</span><p title="${esc(b.first_prompt)}">${esc(b.first_prompt)}</p></div>
      </article>`;
  }).join('');
}
$('blist').addEventListener('click', (e) => {
  const shot = e.target.closest('a[data-file]');
  if (shot && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    openViewer(shot.closest('[data-id]').dataset.id, { file: shot.dataset.file });
    return;
  }
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const id = btn.closest('[data-id]').dataset.id;
  const b = S.batches.find((x) => x.id === id);
  const act = btn.dataset.act;
  if (act === 'view' || act === 'viewall') {
    S.focusId = id; S.pinned = true; S.galleryAll = act === 'viewall';
    location.hash = '#create'; tick(true);
  } else if (act === 'rename') {
    S.renaming = id;
    startRename(btn.closest('[data-name]'), b.name, (name) => renameBatch(id, name));
  } else batchAction(id, act);
});
$('search').addEventListener('input', renderBatches);
$('sort').addEventListener('click', () => {
  const keys = Object.keys(SORTS);
  S.sort = keys[(keys.indexOf(S.sort) + 1) % keys.length];
  $('sort-label').textContent = SORTS[S.sort];
  renderBatches();
});

// ---------- settings ----------

async function loadSettings() {
  try {
    if (!S.state) S.state = await api('/api/state');
    S.settings = await api('/api/settings');
    const f = $('settings-form');
    $('set-model').innerHTML = (S.state?.models || []).filter((m) => m.installed)
      .map((m) => `<option value="${m.key}">${esc(m.label)}</option>`).join('');
    for (const el of f.elements) {
      if (!el.name || !(el.name in S.settings)) continue;
      if (el.type === 'checkbox') el.checked = Boolean(S.settings[el.name]); else el.value = S.settings[el.name];
    }
    $('set-key').value = S.settings.api_key;
    loadUpdate();
    $('app-version').textContent = `VERSION ${S.settings.version}`;
    $('app-version').href = S.settings.repo_url;
    loadModels();
    renderAgents();
    loadReferences();
    $('key-save').hidden = true;
    renderFooter();
    renderMachine();
  } catch (err) { toast(err.message); }
}
$('settings-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {};
  for (const el of e.target.elements) {
    if (el.name) body[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value.trim();
  }
  try {
    const r = await api('/api/settings', { method: 'PUT', json: body });
    toast(r.restart_needed ? 'Saved. Restart Fatima Image Studio to use the new port.' : 'Settings saved', true);
    S.lastBatchesSig = '';
    await loadSettings(); await tick(true);
  } catch (err) { toast(err.message); }
});
$('key-show').addEventListener('click', () => {
  const k = $('set-key'); const show = k.type === 'password';
  k.type = show ? 'text' : 'password'; k.readOnly = !show;  // editable while shown
  $('key-show').textContent = show ? 'Hide' : 'Show';
});
$('set-key').addEventListener('input', () => { $('key-save').hidden = $('set-key').value.trim() === S.settings?.api_key; });
$('key-save').addEventListener('click', async () => {
  try {
    await api('/api/settings', { method: 'PUT', json: { api_key: $('set-key').value.trim() } });
    toast('API key saved — tools using the old key need the new one', true);
    await loadSettings();
  } catch (err) { toast(err.message); }
});
$('key-copy').addEventListener('click', async () => { await navigator.clipboard.writeText($('set-key').value); toast('API key copied', true); });
$('url-copy').addEventListener('click', async () => { await navigator.clipboard.writeText($('set-api-base').textContent); toast('Base URL copied', true); });

// ---------- settings: folders, agents (MCP), named references ----------

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-browse]');
  if (!btn) return;
  const input = $(btn.dataset.browse);
  btn.disabled = true;
  toast('Choose a folder in the window that opened', true);
  try {
    const r = await api('/api/browse-folder', { method: 'POST', json: { start: input.value } });
    if (r.path) { input.value = r.path; toast('Folder chosen — press Save settings to keep it', true); }
  } catch (err) { toast(err.message); }
  btn.disabled = false;
});

const MCP_AGENTS = {
  claude: { where: 'Run this once in a terminal:', text: (u) => `claude mcp add --transport http fatima-image-studio ${u} --header "Authorization: Bearer {KEY}"` },
  codex: { where: 'Add to ~/.codex/config.toml:', text: (u) => `[mcp_servers.fatima-image-studio]\nurl = "${u}"\nhttp_headers = { "Authorization" = "Bearer {KEY}" }` },
  antigravity: { where: 'Add to ~/.gemini/config/mcp_config.json (or .agents/mcp_config.json in a project):',
                 text: (u) => JSON.stringify({ mcpServers: { 'fatima-image-studio': { serverUrl: u, headers: { Authorization: 'Bearer {KEY}' } } } }, null, 2) },
  hermes: { where: 'Add to ~/.hermes/config.yaml:', text: (u) => `mcp_servers:\n  fatima-image-studio:\n    url: "${u}"\n    headers:\n      Authorization: "Bearer {KEY}"\n    timeout: 1800` },
  stdio: { where: 'For any agent that starts local commands (the usual "mcpServers" JSON):',
           text: () => JSON.stringify({ mcpServers: { 'fatima-image-studio': { command: S.settings?.python_exe || 'python', args: [S.settings?.mcp_script || 'studio_mcp.py'] } } }, null, 2) },
};
let mcpAgent = 'claude';
function renderAgents() {
  if (!S.settings || !S.state) return;
  const url = S.state.api_base.replace(/\/v1$/, '/mcp');
  const a = MCP_AGENTS[mcpAgent];
  $('mcp-where').textContent = a.where;
  $('mcp-snippet').textContent = a.text(url).replaceAll('{KEY}', '••••••••');
  $('mcp-tabs').querySelectorAll('[data-agent]').forEach((b) => b.setAttribute('aria-selected', b.dataset.agent === mcpAgent));
  $('set-agents-nc').checked = Boolean(S.settings.agents_noncommercial);
  setHtml($('agent-dirs'), (S.settings.agent_read_dirs || []).map((d, i) => `<div class="dir-row"><span>${esc(d)}</span>
    <button type="button" class="text-btn red" data-dir="${i}">Remove</button></div>`).join('') || '<p class="help">No extra folders.</p>');
}
$('mcp-tabs').addEventListener('click', (e) => {
  const b = e.target.closest('[data-agent]');
  if (b) { mcpAgent = b.dataset.agent; renderAgents(); }
});
$('mcp-copy').addEventListener('click', async () => {
  const url = S.state.api_base.replace(/\/v1$/, '/mcp');
  await navigator.clipboard.writeText(MCP_AGENTS[mcpAgent].text(url).replaceAll('{KEY}', S.settings.api_key));
  toast('Copied — paste it into the agent\'s settings', true);
});
async function saveAgentSetting(body) {
  try { S.settings = { ...S.settings, ...(await api('/api/settings', { method: 'PUT', json: body })) }; renderAgents(); }
  catch (err) { toast(err.message); }
}
$('agent-dirs').addEventListener('click', (e) => {
  const b = e.target.closest('[data-dir]');
  if (b) saveAgentSetting({ agent_read_dirs: S.settings.agent_read_dirs.filter((_, i) => i !== +b.dataset.dir) });
});
$('agent-dir-add').addEventListener('click', async () => {
  toast('Choose a folder in the window that opened', true);
  try {
    const r = await api('/api/browse-folder', { method: 'POST', json: { start: '' } });
    if (r.path) await saveAgentSetting({ agent_read_dirs: [...(S.settings.agent_read_dirs || []), r.path] });
  } catch (err) { toast(err.message); }
});
$('set-agents-nc').addEventListener('change', (e) => saveAgentSetting({ agents_noncommercial: e.target.checked }));

async function loadReferences() {
  let refs = [];
  try { refs = await api('/api/references'); } catch { return; }
  setHtml($('ref-lib'), refs.map((r) => `<div class="ref-card"><img src="/api/references/${r.name}/image?s=${r.size}" alt="${esc(r.name)}" loading="lazy">
    <div><span>${esc(r.name)}</span><button type="button" class="text-btn red" data-ref-del="${esc(r.name)}">Remove</button></div></div>`).join(''));
}
$('ref-lib').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-ref-del]');
  if (!b || !await confirmDialog(`Remove “${b.dataset.refDel}”?`, 'Agents will no longer be able to use this name. Batches already made keep their copies.', 'Remove')) return;
  try { await api(`/api/references/${b.dataset.refDel}`, { method: 'DELETE' }); await loadReferences(); } catch (err) { toast(err.message); }
});
$('ref-new-btn').addEventListener('click', () => {
  const name = $('ref-new-name').value.trim();
  if (!name) { toast('Type a name first, like egg-character.'); $('ref-new-name').focus(); return; }
  pickFile(async (file) => {
    const form = new FormData();
    form.append('file', file);
    form.append('name', name);
    try { const r = await api('/api/references', { method: 'POST', form }); toast(`Saved reference “${r.name}”`, true); $('ref-new-name').value = ''; await loadReferences(); }
    catch (err) { toast(err.message); }
  });
});

let MODEL_LIST = [], UPSCALER_LIST = [];
async function loadModels() {
  try { [MODEL_LIST, UPSCALER_LIST] = await Promise.all([api('/api/models'), api('/api/upscalers')]); } catch { return; }
  renderModelList();
  renderUpscalerList();
}
function renderUpscalerList() {
  setHtml($('upscaler-list'), UPSCALER_LIST.map((u) => {
    const job = u.job;
    let act;
    if (u.installed) act = `<span class="chip lime">${icon('dot-ink', 5)}Installed</span>
      <button type="button" class="text-btn red" data-up-act="remove" data-key="${u.key}">Remove from disk</button>`;
    else if (job?.status === 'downloading') act = `<div class="dl-track"><div style="width:${job.total ? (job.done / job.total) * 100 : 0}%"></div></div>
      <span class="micro" style="font-weight:400">${gb(job.done)} of ${gb(job.total)}</span>
      <button type="button" class="btn sm" data-up-act="cancel" data-key="${u.key}">Cancel</button>`;
    else act = (job?.status === 'failed' ? `<span class="help warn-text">${esc(job.error)}</span>` : '') +
      `<button type="button" class="btn" data-up-act="download" data-key="${u.key}">${u.partial ? 'Resume download' : 'Download'} · ${gb(u.size - u.partial)}</button>` +
      (u.partial ? `<button type="button" class="text-btn red" data-up-act="discard" data-key="${u.key}">Discard ${gb(u.partial)} downloaded</button>` : '');
    return `<div class="model-row"><div class="model-info"><strong>${esc(u.label)}</strong><p>${esc(u.hint)}</p></div>
      <div class="model-act">${act}</div></div>`;
  }).join(''));
}
$('upscaler-list').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-up-act]');
  if (!btn) return;
  const u = UPSCALER_LIST.find((x) => x.key === btn.dataset.key), act = btn.dataset.upAct;
  if (act === 'remove' && !await confirmDialog(`Remove the ${u.label} upscaler?`, 'You can download it again later.', 'Remove')) return;
  try {
    UPSCALER_LIST = await api(`/api/upscalers/${u.key}/${act}`, { method: 'POST' });
    renderUpscalerList();
    tick(true);
  } catch (err) { toast(err.message); }
});
const gb = (bytes) => bytes < 1e9 ? `${Math.max(1, Math.round(bytes / 1e6))} MB` : `${(bytes / 1e9).toFixed(1)} GB`;
function renderModelList() {
  setHtml($('model-list'), MODEL_LIST.map((m) => {
    const job = m.job, busy = job?.status === 'downloading';
    const isDefault = m.key === S.settings?.default_model;
    let act;
    if (m.installed) {
      act = `<span class="chip lime">${icon('dot-ink', 5)}Installed${isDefault ? ' · default' : ''}</span>` +
        (isDefault ? '' : `<button type="button" class="text-btn red" data-model-act="delete" data-key="${m.key}">Remove from disk</button>`);
    } else if (busy) {
      act = `<div class="dl-track"><div style="width:${job.total ? (job.done / job.total) * 100 : 0}%"></div></div>
        <span class="micro" style="font-weight:400">${gb(job.done)} of ${gb(job.total)}</span>
        <button type="button" class="btn sm" data-model-act="cancel" data-key="${m.key}">Cancel</button>`;
    } else {
      const paused = m.partial > 0;
      act = (job?.status === 'failed' ? `<span class="help warn-text">${esc(job.error)}</span>` : '') +
        `<button type="button" class="btn ${m.noncommercial ? '' : 'red'}" data-model-act="download" data-key="${m.key}">${paused ? 'Resume download' : 'Download'} · ${gb(m.to_download - m.partial)}</button>` +
        (paused ? `<button type="button" class="text-btn red" data-model-act="discard" data-key="${m.key}">Discard ${gb(m.partial)} downloaded</button>` : '');
    }
    return `<div class="model-row">
      <div class="model-info">
        <strong>${esc(m.label)}</strong>
        <p>${esc(m.about)}</p>
        <div class="model-tags">
          <span class="tag ${m.noncommercial ? 'bad' : 'ok'}">${esc(m.license)}</span>
          <span class="tag">${gb(m.size)} on disk</span>
          <span class="tag">${m.steps} steps</span>
          <span class="tag">${m.refs ? 'Reference images ✓' : 'No reference images'}</span>
        </div>
      </div>
      <div class="model-act">${act}</div>
    </div>`;
  }).join(''));
}
$('model-list').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-model-act]');
  if (!btn) return;
  const m = MODEL_LIST.find((x) => x.key === btn.dataset.key);
  try {
    if (btn.dataset.modelAct === 'download') {
      if (m.noncommercial && !await confirmDialog(`${m.label} is non-commercial`,
        `Its licence (${m.license}) doesn't allow using its images in monetized videos, ads or client work. Download it anyway?`, 'Download')) return;
      MODEL_LIST = await api(`/api/models/${m.key}/download`, { method: 'POST' });
    } else if (btn.dataset.modelAct === 'cancel') {
      MODEL_LIST = await api(`/api/models/${m.key}/cancel`, { method: 'POST' });
    } else if (btn.dataset.modelAct === 'discard') {
      if (!await confirmDialog(`Discard the ${m.label} download?`, `The ${gb(m.partial)} downloaded so far is deleted. Downloading it later starts from the beginning.`, 'Discard')) return;
      MODEL_LIST = (await api(`/api/models/${m.key}/partial`, { method: 'DELETE' })).models;
      toast('Partial download discarded', true);
    } else if (btn.dataset.modelAct === 'delete') {
      if (!await confirmDialog(`Remove ${m.label}?`, 'Its files are deleted from disk (files other installed models share are kept). You can download it again later.', 'Remove')) return;
      MODEL_LIST = (await api(`/api/models/${m.key}`, { method: 'DELETE' })).models;
      toast(`${m.label} removed`, true);
    }
    $('model-list')._html = null;
    renderModelList();
    tick(true);
  } catch (err) { toast(err.message); }
});

// ---------- setup: hardware, engine, model, speed test ----------

let SETUP = null, benchRunning = false;
async function loadSetup(refresh = false) {
  try { SETUP = await api('/api/setup' + (refresh ? '?refresh=true' : '')); } catch { return; }
  if (S.view === 'setup') renderSetup();
  renderSize();  // the size warning depends on the GPU and Low-memory mode
}
const FIT = {
  fits: ['ok', 'Runs fully on your GPU'], tight: ['', 'Slower on your GPU'],
  too_big: ['bad', 'Too big for your GPU'], cpu: ['', 'Runs on the CPU'],
};
function dlProgress(job, key, kind) {
  if (job.status === 'installing') return `<div class="dl-track"><div style="width:100%"></div></div><span class="micro">Unpacking…</span>`;
  return `<div class="dl-track"><div style="width:${job.total ? (job.done / job.total) * 100 : 0}%"></div></div>
    <div class="choice-foot"><span class="micro" style="font-weight:400">${gb(job.done)} of ${gb(job.total)}</span>
    <button type="button" class="btn sm" data-setup="${kind}:cancel" data-key="${key}">Cancel</button></div>`;
}
function choiceCard({ label, about, badges, tags = '', active, body }) {
  return `<div class="choice${active ? ' active' : ''}">
    <strong>${esc(label)}</strong>
    <div class="choice-badges">${badges}</div>
    <p>${esc(about)}</p>
    ${tags ? `<div class="model-tags">${tags}</div>` : ''}
    ${body}</div>`;
}
function renderSetup() {
  const s = SETUP, hw = s.hardware, g = s.gpu;
  const others = hw.gpus.filter((x) => x.name !== g?.name);
  setHtml($('setup-hw'), [
    ['Graphics card', g ? esc(g.name) : 'None found', g?.driver ? `Driver ${esc(g.driver)}` : (others.length ? 'Only integrated graphics' : '')],
    ['GPU memory', g ? `${g.vram_gb} GB` : '—', g ? (g.vram_gb >= 12 ? 'Plenty for every model' : g.vram_gb >= 8 ? 'Good for the 4B models' : 'Low-memory mode helps') : ''],
    ['RAM', `${hw.ram_gb} GB`, hw.ram_gb >= 16 ? 'Enough' : '16 GB recommended'],
    ['Processor', esc(hw.cpu), `${hw.cores} threads`],
    ['Free space', s.disk_free == null ? '—' : gb(s.disk_free), 'Where models are stored'],
  ].map(([k, v, sub]) => `<div class="hw-stat"><span class="micro">${k}</span><strong>${v}</strong><span>${sub}</span></div>`).join('') +
    (others.length ? `<p class="help" style="grid-column:1/-1">Also found: ${others.map((x) => esc(x.name) + (x.integrated ? ' (integrated, not used)' : '')).join(', ')}</p>` : ''));
  setHtml($('setup-warns'), s.warnings.map((w) => `<div class="setup-warn ${w.level}">${esc(w.message)}</div>`).join('') ||
    (g ? `<div class="setup-warn ok">Your PC is ready for local image generation.</div>` : ''));

  const step = (id, done, n) => { $(id).className = 'step-num' + (done ? ' done' : ''); $(id).textContent = done ? '✓' : n; };
  step('step-engine', s.steps.engine, 1); step('step-model', s.steps.model, 2); step('step-bench', s.steps.benchmark, 3);

  setHtml($('setup-engines'), s.engines.map((e) => {
    const job = e.job, busy = ['downloading', 'installing'].includes(job?.status);
    let body;
    if (busy) body = dlProgress(job, e.key, 'engine');
    else if (e.active) body = `<div class="choice-foot"><span class="micro">On this PC</span></div>`;
    else if (e.installed) body = `<div class="choice-foot"><span class="micro">On this PC</span><span class="acts">
        <button type="button" class="text-btn red" data-setup="engine:remove" data-key="${e.key}">Remove</button>
        <button type="button" class="btn sm" data-setup="engine:use" data-key="${e.key}">Use this</button></span></div>`;
    else body = (job?.status === 'failed' ? `<span class="help warn-text">${esc(job.error)}</span>` : '') +
      `<div class="choice-foot"><span class="micro">${gb(e.size - e.partial)} download</span><span class="acts">` +
      (e.partial ? `<button type="button" class="text-btn red" data-setup="engine:discard" data-key="${e.key}">Discard</button>` : '') +
      `<button type="button" class="btn sm ${e.recommended ? 'red' : ''}" data-setup="engine:download" data-key="${e.key}">${e.partial ? 'Resume' : 'Download'}</button></span></div>`;
    return choiceCard({ label: e.label, about: e.about, active: e.active, body,
      badges: (e.recommended ? `<span class="chip red">Recommended</span>` : '') + (e.active ? `<span class="chip lime">In use</span>` : '') });
  }).join(''));

  $('setup-model-help').textContent = 'The AI model that draws the images. One-time download; it resumes if interrupted.' +
    (g ? ` Your GPU has ${g.vram_gb} GB.` : '');
  setHtml($('setup-models'), s.models.map((m) => {
    const job = m.job, busy = job?.status === 'downloading';
    const [fitCls, fitText] = FIT[m.fit];
    let body;
    if (busy) body = dlProgress(job, m.key, 'model');
    else if (m.default && m.installed) body = `<div class="choice-foot"><span class="micro">On this PC</span></div>`;
    else if (m.installed) body = `<div class="choice-foot"><span class="micro">On this PC</span>
        <button type="button" class="btn sm" data-setup="model:default" data-key="${m.key}">Make default</button></div>`;
    else body = (job?.status === 'failed' ? `<span class="help warn-text">${esc(job.error)}</span>` : '') +
      `<div class="choice-foot"><span class="micro">${gb(m.to_download - m.partial)} download</span><span class="acts">` +
      (m.partial ? `<button type="button" class="text-btn red" data-setup="model:discard" data-key="${m.key}">Discard</button>` : '') +
      `<button type="button" class="btn sm ${m.recommended ? 'red' : ''}" data-setup="model:download" data-key="${m.key}" ${m.enough_disk ? '' : 'disabled title="Free up disk space first"'}>${m.partial ? 'Resume' : 'Download'}</button></span></div>`;
    return choiceCard({ label: m.label, about: m.about, active: m.default && m.installed, body,
      badges: (m.recommended ? `<span class="chip red">Recommended</span>` : '') +
        (m.default && m.installed ? `<span class="chip lime">Default</span>` : m.installed ? `<span class="chip ghost">Downloaded</span>` : '') +
        (m.enough_disk ? '' : `<span class="chip warn">Not enough disk space</span>`),
      tags: `<span class="tag ${fitCls}">${fitText}</span>` + (m.noncommercial ? `<span class="tag bad">Non-commercial</span>` : '') +
        `<span class="tag">${gb(m.size)}</span>` });
  }).join(''));

  $('setup-mem').querySelectorAll('[data-mem]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.mem === s.low_vram));
  $('setup-mem-help').textContent = 'Keeps model weights in RAM and decodes in tiles. Fits small GPUs, but slower. ' +
    (s.low_vram === 'auto' ? `Auto: ${s.low_vram_active ? 'on' : 'off'} for your GPU.` : `Now ${s.low_vram_active ? 'on' : 'off'}.`);

  const b = s.benchmark;
  const modelLabel = (k) => s.models.find((m) => m.key === k)?.label || k;
  const engineLabel = (k) => s.engines.find((e) => e.key === k)?.label || k;
  const defaultKey = s.models.find((m) => m.default)?.key;
  setHtml($('setup-bench'), benchRunning ? `<div class="setup-warn">Testing… the first run also loads the model.</div>` : b ? `<div class="bench">
      <div class="hw-stat"><span class="micro">First image</span><strong>${b.load_s} s</strong><span>Includes loading the model, once</span></div>
      <div class="hw-stat"><span class="micro">512 × 512</span><strong>${b.s512} s</strong><span>per image</span></div>
      <div class="hw-stat"><span class="micro">1024 × 1024</span><strong>${b.s1024} s</strong><span>per image</span></div>
      <div class="hw-stat"><span class="micro">Per minute</span><strong>≈ ${Math.max(1, Math.floor(60 / b.s1024))} images</strong><span>at 1024 × 1024</span></div>
    </div><p class="help" style="margin-top:10px">Measured with ${esc(modelLabel(b.model))} on ${esc(engineLabel(b.engine))}${b.low_vram ? ', low-memory mode on' : ''}.${
      b.model !== defaultKey ? ' Your default model has changed since; run it again to update.' : ''}</p>` : '');
  $('setup-bench-run').disabled = benchRunning || !s.ready;
  $('setup-bench-run').textContent = benchRunning ? 'Testing…' : b ? 'Run again' : 'Run speed test';

  $('setup-hint').textContent = s.ready ? 'All set. The engine and your default model are ready.'
    : !s.steps.engine ? 'Download an engine to continue.' : 'Download a model to continue.';
  $('setup-go').hidden = !s.ready;
}
$('setup-recheck').addEventListener('click', async () => { $('setup-hw')._html = null; await loadSetup(true); toast('Hardware checked again', true); });
$('setup-mem').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-mem]');
  if (!b) return;
  try { S.settings = { ...S.settings, ...(await api('/api/settings', { method: 'PUT', json: { low_vram: b.dataset.mem } })) }; await loadSetup(); }
  catch (err) { toast(err.message); }
});
$('setup-bench-run').addEventListener('click', async () => {
  benchRunning = true; renderSetup();
  try { SETUP = await api('/api/setup/benchmark', { method: 'POST' }); toast('Speed test done', true); }
  catch (err) { toast(err.message); }
  benchRunning = false; renderSetup(); tick(true);
});
$('view-setup').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-setup]');
  if (!btn) return;
  const [kind, act] = btn.dataset.setup.split(':'), key = btn.dataset.key;
  const item = (kind === 'engine' ? SETUP.engines : SETUP.models).find((x) => x.key === key);
  try {
    if (kind === 'engine') {
      if (act === 'remove' && !await confirmDialog(`Remove the ${item.label} engine?`, 'Its files are deleted. You can download it again later.', 'Remove')) return;
      if (act === 'discard' && !await confirmDialog(`Discard the ${item.label} download?`, `The ${gb(item.partial)} downloaded so far is deleted.`, 'Discard')) return;
      SETUP = await api(`/api/setup/engines/${key}/${act}`, { method: 'POST' });
      if (act === 'use') toast(`Now using ${item.label}`, true);
    } else {
      if (act === 'download') {
        if (item.noncommercial && !await confirmDialog(`${item.label} is non-commercial`,
          `Its licence (${item.license}) doesn't allow using its images in monetized videos, ads or client work. Download it anyway?`, 'Download')) return;
        await api(`/api/models/${key}/download`, { method: 'POST' });
      } else if (act === 'cancel') await api(`/api/models/${key}/cancel`, { method: 'POST' });
      else if (act === 'discard') {
        if (!await confirmDialog(`Discard the ${item.label} download?`, `The ${gb(item.partial)} downloaded so far is deleted. Downloading it later starts from the beginning.`, 'Discard')) return;
        await api(`/api/models/${key}/partial`, { method: 'DELETE' });
      } else if (act === 'default') {
        S.settings = { ...S.settings, ...(await api('/api/settings', { method: 'PUT', json: { default_model: key } })) };
        toast(`${item.label} is now the default`, true);
      }
      await loadSetup();
    }
    renderSetup();
    tick(true);
  } catch (err) { toast(err.message); }
});

// ---------- create: single image ----------

S.cmode = (() => { try { return localStorage.getItem('createMode') || 'single'; } catch { return 'single'; } })();
function setCreateMode(m) {
  S.cmode = m;
  try { localStorage.setItem('createMode', m); } catch { /* private mode */ }
  $('composer').dataset.cmode = m;
  $('create-mode').querySelectorAll('[data-cmode]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.cmode === m));
  $('focus')._html = null;
  updateSummary();
  tick(true);
}
$('create-mode').addEventListener('click', (e) => {
  const b = e.target.closest('[data-cmode]');
  if (b && b.dataset.cmode !== S.cmode) setCreateMode(b.dataset.cmode);
});
$('single-prompt').addEventListener('input', updateSummary);
$('single-prompt').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!$('start').disabled) $('start').click(); }
});

async function startSingle() {
  const text = $('single-prompt').value.trim();
  if (!text) return;
  const seedText = $('seed').value.trim();
  if (seedText && !/^\d{1,10}$/.test(seedText)) { toast('Seed must be a whole number (or leave it empty for random).'); return; }
  const [width, height] = getSize();
  const form = new FormData();
  form.append('spec', JSON.stringify({
    text,
    settings: { model: S.model, width, height, count: S.perPrompt, seed: seedText ? Number(seedText) : null,
                style: { text: $('style').value.trim(), position: S.stylePos }, upscale: upscaleChoice($('upscale').value),
                loras: S.loraPicks },
  }));
  S.pins.forEach((x, k) => form.append(`ref_${k}`, x.file));
  $('start').disabled = true;
  try {
    const r = await api('/api/singles', { method: 'POST', form });
    S.focusId = r.batch.id; S.pinned = true; S.galleryAll = false;
    S.singleLast = r.items;  // the hero shows these until they're done
    await tick(true);
  } catch (err) {
    toast(err.message);
  } finally {
    updateSummary();
  }
}

function renderSingleHero(d) {
  const box = $('single-hero');
  if (S.cmode !== 'single' || !d || d.kind !== 'singles' || !d.prompts.length) { setHtml(box, ''); return; }
  const last = d.prompts[d.prompts.length - 1];
  const items = d.items.filter((it) => it.prompt === last.n && !it.kind);
  const s = { ...d.settings, ...(last.settings || {}) };
  const ratio = `${s.width} / ${s.height}; --ar: ${s.width / s.height}`;
  const tiles = items.map((it) => {
    if (it.status === 'done') {
      return `<a class="hero-img" href="#" data-hero-open="${it.id}" style="aspect-ratio:${ratio}" aria-label="Open in the viewer">
        <img src="${fileUrl(d.id, it.file)}?v=${encodeURIComponent(it.finished || '')}" alt="${esc(last.text)}"></a>`;
    }
    const label = { running: 'Generating…', queued: 'Waiting…', failed: 'Failed', cancelled: 'Cancelled' }[it.status] || it.status;
    return `<div class="hero-img wait ${it.status}" style="aspect-ratio:${ratio}"><span>${label}</span></div>`;
  }).join('');
  const first = items.find((it) => it.status === 'done');
  const failed = items.find((it) => it.status === 'failed');
  setHtml(box, `<div class="hero">
    <div class="hero-grid n${items.length}">${tiles}</div>
    <div class="hero-cap">
      <p>${esc(last.text)}<span class="micro">${s.width} × ${s.height} · ${esc(modelShort(s.model))} · seed ${last.seed}${items.length > 1 ? `–${last.seed + items.length - 1}` : ''}${failed ? ` · ${esc(failed.error || 'failed')}` : ''}</span></p>
      <div class="hero-acts">
        ${first ? `<button type="button" class="btn sm" data-hero="open" data-id="${first.id}">Open</button>` : ''}
        <button type="button" class="btn sm" data-hero="seed" data-seed="${last.seed}">Use this seed</button>
        <button type="button" class="btn sm" data-hero="prompt">Reuse prompt</button>
        ${failed ? `<button type="button" class="btn sm" data-hero="retry" data-id="${failed.id}">Retry</button>` : ''}
      </div>
    </div></div>`);
  box.dataset.batch = d.id;
  box.dataset.prompt = last.text;
}
$('single-hero').addEventListener('click', async (e) => {
  const box = $('single-hero');
  const open = e.target.closest('[data-hero-open], [data-hero="open"]');
  if (open) { e.preventDefault(); openViewer(box.dataset.batch, { id: open.dataset.heroOpen || open.dataset.id }); return; }
  const b = e.target.closest('[data-hero]');
  if (!b) return;
  if (b.dataset.hero === 'seed') { $('seed').value = b.dataset.seed; toast('Seed set — the next image uses it', true); }
  if (b.dataset.hero === 'prompt') { $('single-prompt').value = box.dataset.prompt; $('single-prompt').focus(); updateSummary(); }
  if (b.dataset.hero === 'retry') {
    try { await api(`/api/batches/${box.dataset.batch}/items/${b.dataset.id}/retry`, { method: 'POST' }); tick(true); }
    catch (err) { toast(err.message); }
  }
});

// Fresh install: everything but Setup is locked until an engine and a model are installed.
function setLocked(locked) {
  if (locked === S.locked) return;
  const wasLocked = S.locked;
  S.locked = locked;
  document.body.classList.toggle('locked', locked);
  document.querySelectorAll('.nav a:not([data-view="setup"])').forEach((a) => {
    if (locked) { a.setAttribute('aria-disabled', 'true'); a.setAttribute('tabindex', '-1'); a.title = 'Finish setup first'; }
    else { a.removeAttribute('aria-disabled'); a.removeAttribute('tabindex'); a.title = ''; }
  });
  $('setup-kicker').textContent = locked ? 'FIRST RUN' : 'SETTINGS / SETUP';
  if (locked || S.view === 'setup') showView();
  if (wasLocked && !locked) toast('Setup complete — everything is unlocked. Press Start creating to begin.', true);
}

// ---------- settings: updates ----------

let UPD = null, updPoll = null;
const fmtMB = (b) => `${(b / 1e6).toFixed(b < 1e7 ? 1 : 0)} MB`;
async function loadUpdate() {
  try { UPD = await api('/api/update'); } catch { return; }
  renderUpdate();
}
function renderUpdate() {
  const u = UPD;
  if (!u) return;
  $('upd-version').textContent = `VERSION ${u.current}`;
  const busy = ['downloading', 'applying', 'restarting'].includes(u.status);
  const when = u.checked ? new Date(u.checked * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
  $('upd-status').textContent = {
    idle: 'Not checked yet.',
    checking: 'Checking GitHub for a newer version…',
    up_to_date: `You're up to date — version ${u.current}. Checked ${when}.`,
    available: `Version ${u.latest} is available. You have ${u.current}.`,
    manual: `Version ${u.latest} is available. Download its installer from the release page.`,
    downloading: `Downloading version ${u.latest}…`,
    applying: 'Installing…',
    restarting: 'Installing — the app closes and opens again by itself. This page reloads when it’s back.',
    error: u.error || 'Something went wrong.',
  }[u.status] || u.status;
  $('upd-status').classList.toggle('warn-text', u.status === 'error' || (u.status === 'available' && !!u.error));
  if (u.status === 'available' && u.error) $('upd-status').textContent += ` ${u.error}`;
  const p = u.progress;
  $('upd-progress').hidden = u.status !== 'downloading' || !p;
  if (p) {
    $('upd-progress').querySelector('.dl-track div').style.width = `${p.total ? (p.done / p.total) * 100 : 0}%`;
    $('upd-progress').querySelector('.micro').textContent = `${fmtMB(p.done)} of ${fmtMB(p.total)}`;
  }
  const showNotes = ['available', 'manual'].includes(u.status) && u.notes;
  $('upd-notes').hidden = !showNotes;
  if (showNotes) $('upd-notes').textContent = u.notes;
  const offer = u.status === 'available';
  $('upd-quick').hidden = !offer || !u.app;
  $('upd-full').hidden = !offer || !u.full;
  if (u.app) $('upd-quick').textContent = `Quick update · ${fmtMB(u.app.size)}`;
  if (u.full) $('upd-full').textContent = `Full update · ${fmtMB(u.full.size)}`;
  $('upd-quick').disabled = !u.installed || !u.quick_ok || busy;
  $('upd-full').disabled = !u.installed || busy;
  $('upd-check').disabled = busy || u.status === 'checking';
  $('upd-help').textContent = !u.installed
    ? 'This copy runs from source, so it updates with git pull. Checking still shows what the latest release is.'
    : offer && !u.quick_ok ? 'This version changes the bundled runtime, so it needs the full update (the installer).'
    : offer ? 'Quick update replaces only the app’s own files. Full update runs the installer. Both keep your models, settings and images, and restart the app.'
    : '';
  if (u.status === 'manual' && u.url) $('upd-help').innerHTML = `<a href="${esc(u.url)}" target="_blank" rel="noopener">Open the release page ↗</a>`;
  $('set-check-updates').checked = S.settings?.check_updates !== false;
}
$('upd-check').addEventListener('click', async () => {
  UPD = { ...(UPD || {}), status: 'checking' }; renderUpdate();
  try { UPD = await api('/api/update/check', { method: 'POST' }); } catch (err) { toast(err.message); }
  renderUpdate(); tick(true);
});
for (const [id, kind] of [['upd-quick', 'quick'], ['upd-full', 'full']]) {
  $(id).addEventListener('click', async () => {
    const running = (S.state?.queue || []).length;
    if (!await confirmDialog(`Update to version ${UPD.latest}?`,
      `The app closes, ${kind === 'quick' ? 'replaces its own files' : 'runs the installer'} and opens again by itself — about a minute.` +
      (running ? ' Queued images carry on after the restart.' : '') + ' Your models, settings and images are kept.', 'Update now')) return;
    try { UPD = await api(`/api/update/${kind}`, { method: 'POST' }); renderUpdate(); watchUpdate(); }
    catch (err) { toast(err.message); }
  });
}
function watchUpdate() {  // follow the download, then wait for the restarted app and reload into it
  clearInterval(updPoll);
  const target = UPD.latest;
  updPoll = setInterval(async () => {
    try {
      const u = await api('/api/update');
      if (u.current === target) { clearInterval(updPoll); toast(`Updated to version ${target}`, true); setTimeout(() => location.reload(), 800); return; }
      UPD = u; renderUpdate();
    } catch { UPD = { ...UPD, status: 'restarting' }; renderUpdate(); }  // the app is restarting
  }, 1000);
}
$('set-check-updates').addEventListener('change', async (e) => {
  try { S.settings = { ...S.settings, ...(await api('/api/settings', { method: 'PUT', json: { check_updates: e.target.checked } })) }; }
  catch (err) { toast(err.message); }
});
let updToasted = false;
function renderUpdateBadge() {
  const available = S.state?.update?.status === 'available';
  const link = document.querySelector('.nav a[data-view="settings"]');
  let dot = link.querySelector('.badge');
  if (available && !dot) { dot = document.createElement('span'); dot.className = 'badge'; dot.title = 'Update available'; link.append(dot); }
  if (!available && dot) dot.remove();
  if (available && !updToasted && !S.locked) {
    updToasted = true;
    toast(`Version ${S.state.update.latest} is available — Settings → General → Updates`, true);
  }
}

function renderMachine() {
  const e = S.state?.engine;
  if (!e || !S.settings) return;
  const idle = Number(S.settings.idle_unload_minutes);
  $('m-gpu').textContent = e.gpu;
  $('m-model').textContent = e.label || modelLabel(S.state.default_model);
  $('m-model-state').textContent = { stopped: 'Standby · loads on first image', starting: 'Loading…', ready: 'Loaded · ready',
    busy: 'Loaded · generating', error: 'Engine error' }[e.state];
  $('m-idle').textContent = idle > 0 ? `Unload after ${idle} min idle` : 'Always loaded';
  $('m-idle-help').textContent = idle > 0 ? 'GPU memory is freed between sessions.' : 'The model stays in GPU memory until you unload it.';
  $('m-state').textContent = { stopped: 'Standby', starting: 'Loading', ready: 'Ready', busy: 'Working', error: 'Error' }[e.state];
  $('set-api-base').textContent = S.state.api_base;
}
function renderFooter() {
  if (S.settings) $('foot-dir').textContent = S.settings.batches_dir;
  if (S.state) {
    const steps = Number(S.settings?.steps) || 0;
    $('foot-meta').textContent = `LOCAL ONLY   /   ${S.state.api_base.replace(/^https?:\/\//, '').replace(/\/v1$/, '')}   /   ${steps ? `${steps} STEPS` : 'AUTO STEPS'}`;
  }
}

// ---------- header + polling ----------

let engineBusy = false;
function renderHeader() {
  const e = S.state?.engine;
  if (!e) return;
  const label = e.label || modelLabel(S.state.default_model);
  $('engine-text').textContent = `${{ stopped: 'STANDBY', starting: 'LOADING', ready: 'READY', busy: 'WORKING', error: 'ERROR' }[e.state]} · ${label}`;
  $('engine-sub').textContent = e.state === 'error' ? (e.error || 'Engine error')
    : `${e.gpu} · ${{ stopped: 'loads on first image', starting: 'loading model…', ready: 'model loaded', busy: 'generating' }[e.state]}`;
  $('engine-sub').title = e.error || '';
  $('engine-dot').className = 'state-dot ' + e.state;
  if (!S.state.setup_ready && e.state !== 'busy') {  // fresh install: nothing to load yet
    $('engine-text').textContent = 'SETUP NEEDED';
    $('engine-sub').textContent = 'Download an engine and a model on the Setup page';
    $('engine-btn').hidden = false;
    $('engine-btn').dataset.act = 'setup';
    $('engine-btn-label').textContent = 'Open Setup';
    $('engine-btn').disabled = false;
    return;
  }

  // Load / unload: loads the model picked on the Create form (or swaps to it).
  const btn = $('engine-btn');
  const installed = (k) => S.state.models.some((m) => m.key === k && m.installed);
  const want = installed(S.model) ? S.model : S.state.default_model;
  const working = e.state === 'busy' || e.state === 'starting' || S.state.queue.some((id) => S.batches.find((b) => b.id === id)?.status !== 'paused');
  btn.hidden = false;
  let text;
  if (e.state === 'ready' && e.model !== want && !working) { text = `Switch to ${modelShort(want)}`; btn.dataset.act = 'load'; }
  else if (e.state === 'ready' || e.state === 'busy') { text = 'Unload model'; btn.dataset.act = 'unload'; }
  else { text = e.state === 'starting' ? 'Loading…' : 'Load model'; btn.dataset.act = 'load'; }
  $('engine-btn-label').textContent = text;
  btn.disabled = engineBusy || e.state === 'starting' || (btn.dataset.act === 'unload' && working);
  btn.title = btn.disabled && btn.dataset.act === 'unload' ? 'Pause or finish running batches to unload' : '';
}
$('engine-btn').addEventListener('click', async (ev) => {
  const act = ev.currentTarget.dataset.act;
  if (act === 'setup') { location.hash = '#setup'; return; }
  engineBusy = true; renderHeader();
  if (act === 'load') setTimeout(tick, 300);  // show "Loading…" while the request runs
  try {
    if (act === 'load') await api('/api/engine/load', { method: 'POST', json: { model: S.model || S.state.default_model } });
    else await api('/api/engine/unload', { method: 'POST' });
    toast(act === 'load' ? 'Model loaded' : 'Model unloaded — GPU memory freed', true);
  } catch (err) { toast(err.message); }
  engineBusy = false;
  await tick(true);
});

let pollTimer, ticking = false, tickAgain = false, forceAgain = false;
async function tick(force = false) {
  if (ticking) { tickAgain = true; forceAgain ||= force; return; }  // one refresh at a time
  ticking = true;
  clearTimeout(pollTimer);
  let busy = false;
  try {
    [S.state, S.batches] = await Promise.all([api('/api/state'), api('/api/batches')]);
    busy = S.state.queue.length > 0 || S.state.api_busy || S.state.engine.state === 'starting';
    setLocked(!S.state.setup_ready);
    renderUpdateBadge();
    renderHeader();
    const installedSig = [...S.state.models, ...S.state.upscalers].filter((m) => m.installed).map((m) => m.key).join();
    if (installedSig !== S.modelsSig) { renderModels(); renderUpscalers(); renderLoraPicker(); }  // also after a download finishes
    await refreshDetail(force);
    renderFocus();
    if ($('viewer').open && S.detail?.id === V.batchId) { V.detail = S.detail; renderViewer(); }
    renderBatches();
    renderMachine();
    renderFooter();
    updateSummary();
    if (S.view === 'models') { await loadModels(); await loadLoras(); }
    if (Object.values(S.loraJobs).some((j) => j.status === 'downloading' || j.status === 'converting')) { busy = true; if (S.view !== 'settings') await loadLoras(); }
    if ([...MODEL_LIST, ...UPSCALER_LIST].some((m) => m.job?.status === 'downloading')) busy = true;
    if (S.view === 'setup') await loadSetup();
    if (SETUP && [...SETUP.engines, ...SETUP.models].some((x) => ['downloading', 'installing'].includes(x.job?.status))) busy = true;
  } catch (err) {
    $('engine-text').textContent = 'Not reachable';
    $('engine-sub').textContent = 'Is Fatima Image Studio still running?';
    $('engine-dot').className = 'state-dot error';
  }
  ticking = false;
  if (tickAgain) {
    const f = forceAgain;
    tickAgain = forceAgain = false;
    return tick(f);
  }
  pollTimer = setTimeout(tick, busy ? 700 : 2500);
}

(async function init() {
  $('composer').dataset.cmode = S.cmode;
  $('create-mode').querySelectorAll('[data-cmode]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.cmode === S.cmode));
  renderSize();
  renderRows();
  showView();
  try { S.settings = await api('/api/settings'); } catch { /* shown by tick */ }
  loadPresets();
  loadLoras();
  await tick(true);
  await loadSetup();
})();
