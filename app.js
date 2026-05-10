/* ── SPACE-FINDX · UI CONTROLLER ──────────────────────────────────────────── */
'use strict';

// ── STATE ─────────────────────────────────────────────────────────────────────
const state = {
  running: false,
  scienceLoaded: false,
  refLoaded: false,
  progress: 0,
  startTime: null,
  tracklets: [],
  currentFilter: 'all',
};

// ── DOM REFS ──────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const terminalBody = $('terminal-body');
const progressFill = $('progress-fill');
const progressLabel = $('progress-label');
const statusBadge = $('status-badge');
const statusText = $('status-text');
const bottombarStatus = $('bottombar-status');
const bottombarInfo = $('bottombar-info');

// ── LOGGING ───────────────────────────────────────────────────────────────────
const LEVELS = {
  OK:   { cls: 'log-prefix--ok',    msgCls: 'log-msg--ok',    pre: '[OK]   ' },
  WARN: { cls: '',                   msgCls: 'log-msg--warn',  pre: '[WARN] ' },
  ERR:  { cls: '',                   msgCls: 'log-msg--error', pre: '[ERR]  ' },
  SYS:  { cls: '',                   msgCls: 'log-msg--system',pre: '[SYS]  ' },
  INFO: { cls: '',                   msgCls: '',               pre: '[INFO] ' },
};

function log(level, msg, accent = null) {
  const cfg = LEVELS[level] || LEVELS.INFO;
  const line = document.createElement('div');
  line.className = 'log-line';
  const pre = document.createElement('span');
  pre.className = `log-prefix ${cfg.cls}`;
  pre.textContent = cfg.pre;
  const text = document.createElement('span');
  text.className = `log-msg ${cfg.msgCls}`;
  if (accent) {
    text.innerHTML = msg.replace(accent, `<span class="log-accent">${accent}</span>`);
  } else {
    text.textContent = msg;
  }
  line.appendChild(pre);
  line.appendChild(text);
  terminalBody.appendChild(line);
  terminalBody.scrollTop = terminalBody.scrollHeight;
}

function logSep() {
  const line = document.createElement('div');
  line.className = 'log-line log-line--separator';
  line.innerHTML = '<span>─────────────────────────────────────────────────────────────────</span>';
  terminalBody.appendChild(line);
}

// ── PROGRESS ──────────────────────────────────────────────────────────────────
function setProgress(pct) {
  state.progress = pct;
  progressFill.style.width = pct + '%';
  progressLabel.textContent = pct + '%';
  const bar = document.querySelector('.progress-bar');
  bar.setAttribute('aria-valuenow', pct);
}

// ── STATUS BADGE ──────────────────────────────────────────────────────────────
function setStatus(mode, text) {
  statusBadge.className = 'status-badge ' + (mode || '');
  statusText.textContent = text;
}

// ── PIPELINE STEPS ────────────────────────────────────────────────────────────
function setStep(n, state_) {
  const el = $('step-' + n);
  const st = $('step-' + n + '-status');
  if (!el) return;
  el.className = 'step ' + state_;
  const labels = { active: 'RUNNING', done: 'COMPLETED', error: 'ERROR', '': 'IDLE' };
  st.textContent = labels[state_] || 'IDLE';
}

// ── STATS BAR ─────────────────────────────────────────────────────────────────
function setStat(id, val) { const el = $(id); if (el) el.textContent = val; }

// ── TABS ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => {
      t.classList.remove('tab--active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.panel').forEach(p => p.classList.add('panel--hidden'));
    tab.classList.add('tab--active');
    tab.setAttribute('aria-selected', 'true');
    const panelId = tab.getAttribute('aria-controls');
    const panel = $(panelId);
    if (panel) panel.classList.remove('panel--hidden');
  });
});

// ── SLIDERS ───────────────────────────────────────────────────────────────────
$('slider-sigma').addEventListener('input', e => {
  $('sigma-value').textContent = parseFloat(e.target.value).toFixed(1) + 'σ';
});
$('slider-elong').addEventListener('input', e => {
  $('elong-value').textContent = parseFloat(e.target.value).toFixed(1) + '×';
});
$('slider-chi2').addEventListener('input', e => {
  $('chi2-value').textContent = parseFloat(e.target.value).toFixed(1);
});

// ── CHECKBOXES → badges ───────────────────────────────────────────────────────
document.querySelectorAll('.param-checkbox').forEach(chk => {
  chk.addEventListener('change', () => {
    const badge = chk.closest('.param-item').querySelector('.param-badge');
    if (chk.checked) { badge.textContent = 'ON'; badge.classList.add('active'); }
    else { badge.textContent = 'OFF'; badge.classList.remove('active'); }
  });
});

// ── DROP ZONES ────────────────────────────────────────────────────────────────
function setupDropZone(zoneId, labelId, stateKey, hint) {
  const zone = $(zoneId);
  const label = $(labelId);
  zone.addEventListener('click', () => simulateFileLoad(zone, label, stateKey, hint));
  zone.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') simulateFileLoad(zone, label, stateKey, hint);
  });
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.style.borderColor = 'var(--accent)'; });
  zone.addEventListener('dragleave', () => { zone.style.borderColor = ''; });
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.style.borderColor = '';
    const file = e.dataTransfer.files[0];
    if (file) loadFile(zone, label, stateKey, file.name);
  });
}

function simulateFileLoad(zone, label, stateKey, hint) {
  const names = {
    science: '/obs/2024-12-01/science/',
    ref: 'reference_stacked.fits',
    bias: 'master_bias.fits',
    dark: 'master_dark_300s.fits',
    flat: 'master_flat_V.fits',
  };
  loadFile(zone, label, stateKey, names[stateKey] || 'data.fits');
}

function loadFile(zone, label, stateKey, name) {
  zone.classList.add('loaded');
  label.textContent = name;
  state[stateKey + 'Loaded'] = true;
  log('OK', `Data ingested: ${name}`);
  bottombarInfo.textContent = name;
  updateRunButton();
}

setupDropZone('drop-zone-science', 'science-label', 'science', 'Science FITS directory');
setupDropZone('drop-zone-ref', 'ref-label', 'ref', 'Reference Frame .fits');

['btn-bias','btn-dark','btn-flat'].forEach(id => {
  const key = id.replace('btn-','');
  $(`${id}`) && $(`${id}`).addEventListener('click', () => {
    const names = { bias: 'master_bias.fits', dark: 'master_dark_300s.fits', flat: 'master_flat_V.fits' };
    log('OK', `Master ${key.toUpperCase()} ingested: ${names[key]}`);
    $(`${id}`).style.borderColor = 'var(--ok)';
    $(`${id}`).style.color = 'var(--ok)';
  });
});

$('btn-output-dir').addEventListener('click', () => {
  $('output-path-text').textContent = '~/space-findx/output/' + new Date().toISOString().slice(0,10);
  log('INFO', 'Output directory mapped.');
});

function updateRunButton() {
  $('btn-run').disabled = !(state.scienceLoaded && state.refLoaded) && false;
}

// ── CLEAR LOG ─────────────────────────────────────────────────────────────────
$('btn-clear-log').addEventListener('click', () => {
  terminalBody.innerHTML = '';
  log('SYS', 'Terminal output cleared by user override.');
});

// ── PIPELINE SIMULATION ───────────────────────────────────────────────────────
const PIPELINE_STEPS = [
  {
    n: 1, name: 'CALIBRATION', pct: 16,
    logs: [
      ['INFO', 'Ingesting chronological FITS series via DATE-OBS metadata...'],
      ['INFO', 'CCDData struct initialized — unit=adu, dtype=float64'],
      ['INFO', 'Master bias subtraction: σ_bias = 3.2 ADU'],
      ['INFO', 'Dark current scaling applied: t_exp=300s → t_dark=300s (scale=1.0)'],
      ['INFO', 'Flat field normalization: median=32741.4 ADU'],
      ['WARN', 'Hot pixels identified via σ-clip: 847 (0.0051% of matrix) — masked'],
      ['OK',   'Calibration sequence complete: 12 frames structured in float64'],
    ]
  },
  {
    n: 2, name: 'ASTROMETRY', pct: 32,
    logs: [
      ['INFO', 'Querying Gaia EDR3 catalog — target vector: RA=210.4°, Dec=+41.2°, r=0.5°'],
      ['INFO', 'Gaia EDR3 retrieval: 1247 reference stars (G < 20, noise < 1.0)'],
      ['INFO', 'Coordinate matching: 89/94 sources within 2.0" radius'],
      ['INFO', 'WCS matrix refinement — computing 3rd order SIP polynomial via SVD'],
      ['OK',   'Astrometric residual RMS = 0.031" — below validation threshold of 0.5"'],
      ['INFO', 'Executing bicubic reprojection: aligning temporal frames to reference grid'],
    ]
  },
  {
    n: 3, name: 'SUBTRACTION', pct: 50,
    logs: [
      ['INFO', 'Executing 2D FFT (numpy.fft) — matrix: (4096, 4096) float64'],
      ['INFO', 'PSF modeling: Science FWHM=3.2 px | Ref FWHM=2.9 px'],
      ['INFO', 'Background variance extracted: σ_n²=14.3 ADU² | σ_r²=12.1 ADU²'],
      ['INFO', 'Applying ZOGY proper image subtraction with regularization ε=1e-10'],
      ['INFO', 'Inverse 2D FFT mapping → calculating difference image D(x,y)'],
      ['OK',   'Scorr significance image: RMS=1.003 (nominal ≈ 1.0 under H₀) — ZOGY valid'],
    ]
  },
  {
    n: 4, name: 'DETECTION', pct: 68,
    logs: [
      ['INFO', 'DAOStarFinder routine active: detection limit=5.0σ, FWHM=3.2 px'],
      ['INFO', '312 transient sources detected above 5σ background limit'],
      ['WARN', '241 rejections: sharpness constraint > 0.9 → COSMIC_RAY_ANOMALY'],
      ['WARN', '18 rejections: elongation > 2.0 → SATELLITE_STREAK (Starlink signature)'],
      ['WARN', '7 rejections: |roundness| parameters outside [-1.0, 1.0]'],
      ['OK',   '46 viable candidates successfully passed morphological vetting'],
    ]
  },
  {
    n: 5, name: 'TRAJECTORY', pct: 84,
    logs: [
      ['INFO', 'Initiating kinematic linkage across 12 temporal frames (Δt ≈ 15 min)'],
      ['INFO', 'Search vector radius: max_speed=7200"/hr × Δt=0.25hr = 1800"'],
      ['INFO', 'Applying linear least-squares fit: α(t)=α₀+μ_α·t, δ(t)=δ₀+μ_δ·t'],
      ['OK',   'TRK_0001: μ_α=+42.3"/hr, μ_δ=-18.7"/hr, χ²_red=0.84 ✓'],
      ['OK',   'TRK_0002: μ_α=+7.1"/hr, μ_δ=+3.2"/hr, χ²_red=1.12 ✓'],
      ['OK',   'TRK_0003: μ_α=+124.8"/hr, μ_δ=-67.3"/hr, χ²_red=2.31 ✓ [FAST NEO DETECTION]'],
      ['WARN', '4 tracklets dropped: χ²_red > 3.0 (non-linear trajectory anomaly)'],
    ]
  },
  {
    n: 6, name: 'ADES EXPORT', pct: 100,
    logs: [
      ['INFO', 'Constructing ADES XML tree (IAU 2017 standard)'],
      ['INFO', 'Validating payload fields: obsTime · ra · dec · astCat · rmsRA · rmsDec · rmsCorr'],
      ['INFO', 'Enforcing PII Privacy Policy: verifying omission of personal <comment> data ✓'],
      ['INFO', 'Astrometric Base: GaiaEDR3 | Active Observatory: W86'],
      ['OK',   'ADES XML compiled: ades_submission_2024-12-01_W86.xml'],
      ['OK',   'Telemetry output: 3 tracklets · 36 individual astrometric observations'],
    ]
  },
];

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

async function runPipeline() {
  if (state.running) return;
  state.running = true;
  state.startTime = Date.now();
  state.tracklets = [];

  $('btn-run').disabled = true;
  setStatus('running', '● COMPUTING');
  bottombarStatus.textContent = 'Processing telemetry sequence...';
  logSep();
  log('SYS', '⌖ SPACE-FINDX Telemetry Active — UTC: ' + new Date().toISOString().slice(0, 19).replace('T', ' '));
  logSep();

  for (const step of PIPELINE_STEPS) {
    setStep(step.n, 'active');
    bottombarStatus.textContent = `Phase ${step.n}/6: ${step.name}`;

    for (const [lvl, msg] of step.logs) {
      await delay(220 + Math.random() * 180);
      log(lvl, msg);
    }

    await delay(300);
    setStep(step.n, 'done');
    setProgress(step.pct);

    const elapsed = ((Date.now() - state.startTime) / 1000).toFixed(1);
    setStat('stat-time', elapsed + 's');

    if (step.n === 1) { setStat('stat-frames', '12'); }
    if (step.n === 4) { setStat('stat-detections', '312'); setStat('stat-candidates', '46'); }
    if (step.n === 5) { setStat('stat-neos', '3'); setStat('stat-rms', '0.031"'); }
  }

  // Build demo tracklets
  state.tracklets = [
    { id: 'TRK_0001', ra: '14 01 23.412', dec: '+41 12 08.34', mu_ra: 42.3, mu_dec: -18.7, chi2: 0.84, rmsRA: 0.031, rmsDec: 0.028, frames: 12, confirmed: true },
    { id: 'TRK_0002', ra: '14 01 55.871', dec: '+41 08 42.11', mu_ra: 7.1, mu_dec: 3.2, chi2: 1.12, rmsRA: 0.029, rmsDec: 0.033, frames: 12, confirmed: true },
    { id: 'TRK_0003', ra: '14 02 11.043', dec: '+41 19 55.72', mu_ra: 124.8, mu_dec: -67.3, chi2: 2.31, rmsRA: 0.041, rmsDec: 0.038, frames: 9, confirmed: true },
  ];

  logSep();
  log('SYS', `Telemetry analysis complete in ${((Date.now() - state.startTime)/1000).toFixed(1)}s — 3 valid NEO signatures confirmed.`);
  logSep();

  setStatus('ok', '● READY');
  bottombarStatus.textContent = 'Sequence Completed';
  bottombarInfo.textContent = '3 validated tracklets — ADES XML available';
  state.running = false;
  $('btn-run').disabled = false;

  renderResultsTable();
  renderADES();

  $('btn-validate-ades').disabled = false;
  $('btn-download-ades').disabled = false;
  $('btn-submit-mpc').disabled = false;
}

$('btn-run').addEventListener('click', runPipeline);

// ── RESULTS TABLE ─────────────────────────────────────────────────────────────
function renderResultsTable(filter = 'all') {
  const tbody = $('results-tbody');
  tbody.innerHTML = '';
  const list = filter === 'confirmed' ? state.tracklets.filter(t => t.confirmed)
             : filter === 'rejected'  ? state.tracklets.filter(t => !t.confirmed)
             : state.tracklets;

  if (!list.length) {
    tbody.innerHTML = `<tr class="results-table__empty"><td colspan="10"><span class="empty-icon">⌖</span><span>No data matches criteria</span></td></tr>`;
    return;
  }

  list.forEach(t => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong style="color:var(--accent)">${t.id}</strong></td>
      <td>${t.ra}</td>
      <td>${t.dec}</td>
      <td>${t.mu_ra > 0 ? '+' : ''}${t.mu_ra.toFixed(1)}</td>
      <td>${t.mu_dec > 0 ? '+' : ''}${t.mu_dec.toFixed(1)}</td>
      <td style="color:${t.chi2 < 2 ? 'var(--ok)' : 'var(--warn)'}">${t.chi2.toFixed(2)}</td>
      <td>${t.rmsRA.toFixed(3)}</td>
      <td>${t.rmsDec.toFixed(3)}</td>
      <td>${t.frames}</td>
      <td><span class="${t.confirmed ? 'badge-confirmed' : 'badge-rejected'}">${t.confirmed ? '● VERIFIED' : '○ DISCARDED'}</span></td>
    `;
    tr.addEventListener('click', () => showCandidateOverlay(t));
    tr.addEventListener('dblclick', () => showCandidateInViewer(t));
    tr.title = 'Click: details · Double-click: FITS Viewer';
    tbody.appendChild(tr);
  });
}

['filter-all','filter-confirmed','filter-rejected'].forEach(id => {
  $(id) && $(id).addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('filter-btn--active'));
    $(id).classList.add('filter-btn--active');
    const f = id.replace('filter-','');
    renderResultsTable(f);
  });
});

// ── CANDIDATE OVERLAY ─────────────────────────────────────────────────────────
function showCandidateOverlay(t) {
  $('overlay-title').textContent = `CANDIDATE METRICS: ${t.id}`;
  $('overlay-content').innerHTML = `
    <div class="overlay-row"><span class="overlay-key">Tracklet ID</span><span class="overlay-val overlay-val--accent">${t.id}</span></div>
    <div class="overlay-row"><span class="overlay-key">Right Ascension (α)</span><span class="overlay-val">${t.ra}</span></div>
    <div class="overlay-row"><span class="overlay-key">Declination (δ)</span><span class="overlay-val">${t.dec}</span></div>
    <div class="overlay-row"><span class="overlay-key">μ_α (arcsec/hr)</span><span class="overlay-val">${t.mu_ra > 0 ? '+' : ''}${t.mu_ra.toFixed(2)}"</span></div>
    <div class="overlay-row"><span class="overlay-key">μ_δ (arcsec/hr)</span><span class="overlay-val">${t.mu_dec > 0 ? '+' : ''}${t.mu_dec.toFixed(2)}"</span></div>
    <div class="overlay-row"><span class="overlay-key">Linearity (χ²_red)</span><span class="overlay-val" style="color:${t.chi2<2?'var(--ok)':'var(--warn)'}">${t.chi2.toFixed(3)}</span></div>
    <div class="overlay-row"><span class="overlay-key">Positional σ_RA</span><span class="overlay-val">${t.rmsRA.toFixed(4)}"</span></div>
    <div class="overlay-row"><span class="overlay-key">Positional σ_Dec</span><span class="overlay-val">${t.rmsDec.toFixed(4)}"</span></div>
    <div class="overlay-row"><span class="overlay-key">Detections (Frames)</span><span class="overlay-val">${t.frames}</span></div>
    <div class="overlay-row"><span class="overlay-key">Astrometric Ref</span><span class="overlay-val overlay-val--accent">Gaia EDR3</span></div>
    <div class="overlay-row"><span class="overlay-key">Clearance Status</span><span class="overlay-val overlay-val--ok">● VERIFIED</span></div>
  `;
  $('candidate-overlay').hidden = false;
}

$('overlay-close').addEventListener('click', () => { $('candidate-overlay').hidden = true; });
$('candidate-overlay').addEventListener('click', e => {
  if (e.target === $('candidate-overlay')) $('candidate-overlay').hidden = true;
});

// ── ADES XML PREVIEW ──────────────────────────────────────────────────────────
function renderADES() {
  const obsCode = $('input-obs-code').value || 'W86';
  $('ades-obs-badge').textContent = obsCode;
  const now = new Date().toISOString().replace('.','.').slice(0, -4) + 'Z';

  const obs = state.tracklets.map((t, i) => `
  <optical>
    <permID></permID>
    <provID>${t.id}</provID>
    <obsTime>${now}</obsTime>
    <ra>${Math.random() * 10 + 210}.${Math.floor(Math.random()*999999999)}</ra>
    <dec>+${Math.random() * 5 + 41}.${Math.floor(Math.random()*999999999)}</dec>
    <astCat>GaiaEDR3</astCat>
    <rmsRA>${t.rmsRA.toFixed(4)}</rmsRA>
    <rmsDec>${t.rmsDec.toFixed(4)}</rmsDec>
    <rmsCorr>0.000</rmsCorr>
    <stn>${obsCode}</stn>
    <mode>CCD</mode>
    <tech>N</tech>
  </optical>`).join('\n');

  $('ades-code').textContent =
`<?xml version="1.0" encoding="UTF-8"?>
<ades version="2017">
  <!-- Generated by space-findx v1.0 | UTC: ${now} | Observatory: ${obsCode} -->
  <submitter><subCode>${obsCode}</subCode></submitter>
  <observatory>
    <mpcCode>${obsCode}</mpcCode>
    <name>Observatory ${obsCode}</name>
  </observatory>
  <telescope>
    <aperture>0.50</aperture>
    <design>0.5m f/8 Ritchey-Chretien</design>
  </telescope>
  <software>
    <astrometry>space-findx v1.0</astrometry>
  </software>
  <obsBlock>${obs}
  </obsBlock>
</ades>`;
}

$('btn-validate-ades').addEventListener('click', () => {
  log('OK', 'ADES XML schema verified against submit.xsd (MPC/IAU 2017) — 0 anomalies');
});

$('btn-download-ades').addEventListener('click', () => {
  const xml = $('ades-code').textContent;
  const blob = new Blob([xml], { type: 'application/xml' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `ades_submission_${new Date().toISOString().slice(0,10)}_W86.xml`;
  a.click();
  log('OK', 'ADES XML payload committed to local storage.');
});

$('btn-export-csv').addEventListener('click', () => {
  if (!state.tracklets.length) return;
  const header = 'tracklet_id,ra,dec,mu_ra,mu_dec,chi2_red,rms_ra,rms_dec,frames,confirmed\n';
  const rows = state.tracklets.map(t =>
    `${t.id},"${t.ra}","${t.dec}",${t.mu_ra},${t.mu_dec},${t.chi2},${t.rmsRA},${t.rmsDec},${t.frames},${t.confirmed}`
  ).join('\n');
  const blob = new Blob([header + rows], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'space_findx_candidates.csv';
  a.click();
  log('OK', 'Candidate metrics exported to CSV.');
});

// ── OBS CODE SYNC ─────────────────────────────────────────────────────────────
$('input-obs-code').addEventListener('input', e => {
  $('ades-obs-badge').textContent = e.target.value || 'W86';
});

// ══════════════════════════════════════════════════════════════════════════════
// FITS VIEWER MODULE
// Renderiza imagem astronômica simulada com normalização de contraste,
// colormaps, bounding boxes e interação zoom/pan.
// ══════════════════════════════════════════════════════════════════════════════

const fitsViewer = {
  canvas: null,
  ctx: null,
  overlay: null,
  data: null,       // Float64Array com pixels simulados
  width: 0,
  height: 0,
  vmin: 0,
  vmax: 1,
  zoom: 1,
  panX: 0,
  panY: 0,
  dragging: false,
  lastMouse: { x: 0, y: 0 },
  candidates: [],   // Posições de candidatos para marcação

  init() {
    this.canvas = $('fits-canvas');
    this.ctx = this.canvas.getContext('2d');
    this.overlay = $('fits-overlay');
    this.viewport = $('fits-viewport');

    // Zoom com scroll
    this.viewport.addEventListener('wheel', (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 0.87;
      const rect = this.viewport.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      // Zoom centrado no cursor
      this.panX = mx - (mx - this.panX) * factor;
      this.panY = my - (my - this.panY) * factor;
      this.zoom *= factor;
      this.zoom = Math.max(0.1, Math.min(50, this.zoom));
      this.render();
    });

    // Pan com drag
    this.viewport.addEventListener('mousedown', (e) => {
      this.dragging = true;
      this.lastMouse = { x: e.clientX, y: e.clientY };
      this.viewport.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', (e) => {
      if (!this.dragging) return;
      this.panX += e.clientX - this.lastMouse.x;
      this.panY += e.clientY - this.lastMouse.y;
      this.lastMouse = { x: e.clientX, y: e.clientY };
      this.render();
    });
    window.addEventListener('mouseup', () => {
      this.dragging = false;
      if (this.viewport) this.viewport.style.cursor = 'crosshair';
    });

    // Cursor position tracking
    this.viewport.addEventListener('mousemove', (e) => {
      if (this.dragging || !this.data) return;
      const rect = this.viewport.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const px = Math.floor((mx - this.panX) / this.zoom);
      const py = Math.floor((my - this.panY) / this.zoom);
      if (px >= 0 && px < this.width && py >= 0 && py < this.height) {
        const idx = (this.height - 1 - py) * this.width + px;  // origin=lower
        const val = this.data[idx];
        $('fits-px').textContent = px;
        $('fits-py').textContent = py;
        $('fits-val').textContent = val.toFixed(1);
        $('fits-cursor-pos').textContent = `(${px}, ${py})`;
        // Simulated WCS → RA/Dec
        const ra = 210.4 + (px - this.width/2) * 0.000277;
        const dec = 41.2 + (py - this.height/2) * 0.000277;
        $('fits-ra').textContent = this.degToHMS(ra);
        $('fits-dec').textContent = this.degToDMS(dec);
      }
    });

    // Contrast/cmap changes
    $('fits-contrast').addEventListener('change', () => this.applyContrast());
    $('fits-cmap').addEventListener('change', () => this.render());
    $('btn-fits-reset').addEventListener('click', () => this.resetView());
    $('btn-fits-fullview').addEventListener('click', () => this.fitToView());
  },

  /**
   * Gera imagem FITS simulada com ruído Gaussiano de fundo,
   * estrelas sintéticas (perfil Gaussiano 2D) e candidatos NEO.
   */
  generateSimulatedField(w = 512, h = 512) {
    this.width = w;
    this.height = h;
    this.data = new Float64Array(w * h);

    // Fundo: ruído Gaussiano (μ=1000, σ=30 ADU)
    const mu = 1000, sigma = 30;
    for (let i = 0; i < w * h; i++) {
      this.data[i] = mu + sigma * this.gaussRandom();
    }

    // Estrelas de campo (perfil Gaussiano 2D, brilhos variados)
    const stars = [];
    for (let s = 0; s < 80; s++) {
      const sx = Math.random() * w;
      const sy = Math.random() * h;
      const flux = 500 + Math.random() * 15000;
      const fwhm = 2.5 + Math.random() * 2.0;
      const sig = fwhm / 2.355;
      stars.push({ x: sx, y: sy, flux, sig });
    }

    // Candidatos NEO (posições específicas, mais tênues)
    const detections = [
      { x: w * 0.35, y: h * 0.42, flux: 800, fwhm: 3.2, id: 'TRK_0001' },
      { x: w * 0.62, y: h * 0.28, flux: 400, fwhm: 3.0, id: 'TRK_0002' },
      { x: w * 0.78, y: h * 0.71, flux: 1200, fwhm: 3.5, id: 'TRK_0003' },
    ];
    detections.forEach(d => {
      stars.push({ x: d.x, y: d.y, flux: d.flux, sig: d.fwhm / 2.355 });
    });
    this.candidates = detections;

    // Renderiza estrelas no array
    for (const star of stars) {
      const r = Math.ceil(star.sig * 4);
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          const px = Math.round(star.x + dx);
          const py = Math.round(star.y + dy);
          if (px < 0 || px >= w || py < 0 || py >= h) continue;
          const g = star.flux * Math.exp(-(dx*dx + dy*dy) / (2 * star.sig * star.sig));
          this.data[(h - 1 - py) * w + px] += g;  // origin=lower
        }
      }
    }

    // Adiciona 2 raios cósmicos (hot pixels pontuais)
    for (let c = 0; c < 3; c++) {
      const ci = Math.floor(Math.random() * w * h);
      this.data[ci] += 40000 + Math.random() * 20000;
    }

    $('fits-placeholder').hidden = true;
    $('fits-filename').textContent = 'science_frame_001.fits (simulated)';
    $('fits-dims').textContent = `${w} × ${h} px`;

    this.applyContrast();
    this.fitToView();
  },

  /** Box-Muller para ruído Gaussiano */
  gaussRandom() {
    let u, v, s;
    do { u = Math.random() * 2 - 1; v = Math.random() * 2 - 1; s = u*u + v*v; } while (s >= 1 || s === 0);
    return u * Math.sqrt(-2 * Math.log(s) / s);
  },

  /**
   * Calcula vmin/vmax usando ZScale ou sigma-clip.
   *
   * ZScale: amostra pixels, ordena, ajusta reta ao histograma
   * cumulativo e calcula intersecções. Rejeita blooming e
   * raios cósmicos naturalmente.
   *
   * Sigma: vmin = μ - 2σ, vmax = μ + 5σ (assimetria intencional
   * porque fontes astronômicas estão ACIMA do fundo).
   */
  applyContrast() {
    if (!this.data) return;
    const method = $('fits-contrast').value;
    const n = this.data.length;

    if (method === 'zscale') {
      // Amostrar ~1000 pixels, ordenar, ajustar reta
      const sampleSize = Math.min(1000, n);
      const sample = [];
      for (let i = 0; i < sampleSize; i++) {
        sample.push(this.data[Math.floor(Math.random() * n)]);
      }
      sample.sort((a, b) => a - b);
      // Remove 5% extremos
      const lo = Math.floor(sampleSize * 0.05);
      const hi = Math.floor(sampleSize * 0.95);
      const trimmed = sample.slice(lo, hi);
      // Ajuste linear simples (iterativo) → vmin/vmax
      const tLen = trimmed.length;
      const x = Array.from({ length: tLen }, (_, i) => i);
      const sumX = x.reduce((a, b) => a + b, 0);
      const sumY = trimmed.reduce((a, b) => a + b, 0);
      const sumXY = x.reduce((a, i) => a + i * trimmed[i], 0);
      const sumXX = x.reduce((a, i) => a + i * i, 0);
      const slope = (tLen * sumXY - sumX * sumY) / (tLen * sumXX - sumX * sumX);
      const intercept = (sumY - slope * sumX) / tLen;
      const contrast = 0.25;
      this.vmin = intercept + slope * lo * contrast;
      this.vmax = intercept + slope * hi * contrast;
      if (this.vmin >= this.vmax) {
        this.vmin = trimmed[0];
        this.vmax = trimmed[tLen - 1];
      }
    } else if (method === 'sigma') {
      // σ-clip: calcula μ e σ iterativamente
      let vals = Array.from(this.data);
      for (let iter = 0; iter < 3; iter++) {
        const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
        const std = Math.sqrt(vals.reduce((a, v) => a + (v - mean) ** 2, 0) / vals.length);
        vals = vals.filter(v => Math.abs(v - mean) < 3 * std);
      }
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      const std = Math.sqrt(vals.reduce((a, v) => a + (v - mean) ** 2, 0) / vals.length);
      this.vmin = mean - 2 * std;
      this.vmax = mean + 5 * std;
    } else {
      // Linear: min/max bruto
      this.vmin = Infinity; this.vmax = -Infinity;
      for (let i = 0; i < n; i++) {
        if (this.data[i] < this.vmin) this.vmin = this.data[i];
        if (this.data[i] > this.vmax) this.vmax = this.data[i];
      }
    }

    $('fits-vmin').textContent = this.vmin.toFixed(0);
    $('fits-vmax').textContent = this.vmax.toFixed(0);
    this.render();
  },

  /** Colormaps astronômicos */
  applyColormap(t) {
    const cmap = $('fits-cmap').value;
    t = Math.max(0, Math.min(1, t));
    if (cmap === 'gray') return [t * 255, t * 255, t * 255];
    if (cmap === 'inverted') return [(1-t)*255, (1-t)*255, (1-t)*255];
    if (cmap === 'heat') {
      const r = Math.min(1, t * 3) * 255;
      const g = Math.min(1, Math.max(0, t * 3 - 1)) * 255;
      const b = Math.min(1, Math.max(0, t * 3 - 2)) * 255;
      return [r, g, b];
    }
    if (cmap === 'viridis') {
      // Viridis approximation
      const r = (0.267 + t * (0.003 + t * (1.096 - t * 0.366))) * 255;
      const g = (0.004 + t * (1.513 - t * 0.578)) * 255;
      const b = (0.329 + t * (1.442 + t * (-3.544 + t * 2.100))) * 255;
      return [Math.max(0, Math.min(255, r)), Math.max(0, Math.min(255, g)), Math.max(0, Math.min(255, b))];
    }
    return [t * 255, t * 255, t * 255];
  },

  /** Renderiza array no canvas com transformação de contraste e colormap */
  render() {
    if (!this.data || !this.canvas) return;
    const w = this.width, h = this.height;

    this.canvas.width = w;
    this.canvas.height = h;
    this.canvas.style.width = (w * this.zoom) + 'px';
    this.canvas.style.height = (h * this.zoom) + 'px';
    this.canvas.style.left = this.panX + 'px';
    this.canvas.style.top = this.panY + 'px';

    const imgData = this.ctx.createImageData(w, h);
    const range = this.vmax - this.vmin;
    const inv = range > 0 ? 1 / range : 1;

    for (let i = 0; i < w * h; i++) {
      const t = (this.data[i] - this.vmin) * inv;
      const [r, g, b] = this.applyColormap(t);
      imgData.data[i * 4] = r;
      imgData.data[i * 4 + 1] = g;
      imgData.data[i * 4 + 2] = b;
      imgData.data[i * 4 + 3] = 255;
    }

    this.ctx.putImageData(imgData, 0, 0);
    this.renderOverlay();
  },

  /** Renderiza bounding boxes SVG dos candidatos */
  renderOverlay() {
    if (!this.overlay) return;
    this.overlay.innerHTML = '';

    for (const cand of this.candidates) {
      const fwhm = cand.fwhm || 3.0;
      const boxSize = fwhm * 4;
      const half = boxSize / 2;

      // Coordenadas em pixels da imagem → viewport (com zoom/pan)
      let x0 = Math.max(0, cand.x - half);
      let y0_img = Math.max(0, cand.y - half);
      let x1 = Math.min(this.width, cand.x + half);
      let y1_img = Math.min(this.height, cand.y + half);

      // Converter para canvas (origin=lower → invertido)
      const cy_canvas = this.height - 1 - cand.y;
      const y0_canvas = this.height - 1 - y1_img;
      const y1_canvas = this.height - 1 - y0_img;

      // Viewport coords
      const vx = x0 * this.zoom + this.panX;
      const vy = y0_canvas * this.zoom + this.panY;
      const vw = (x1 - x0) * this.zoom;
      const vh = (y1_canvas - y0_canvas) * this.zoom;

      const isActive = cand._active;
      const cls = isActive ? 'fits-bbox fits-bbox--active' : 'fits-bbox';

      // Rectangle
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', vx);
      rect.setAttribute('y', vy);
      rect.setAttribute('width', vw);
      rect.setAttribute('height', vh);
      rect.setAttribute('class', cls);
      this.overlay.appendChild(rect);

      // Crosshair
      const cx_v = cand.x * this.zoom + this.panX;
      const cy_v = cy_canvas * this.zoom + this.panY;
      const crSize = 8;
      const line1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line1.setAttribute('x1', cx_v - crSize); line1.setAttribute('y1', cy_v);
      line1.setAttribute('x2', cx_v + crSize); line1.setAttribute('y2', cy_v);
      line1.setAttribute('class', 'fits-crosshair');
      const line2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line2.setAttribute('x1', cx_v); line2.setAttribute('y1', cy_v - crSize);
      line2.setAttribute('x2', cx_v); line2.setAttribute('y2', cy_v + crSize);
      line2.setAttribute('class', 'fits-crosshair');
      this.overlay.appendChild(line1);
      this.overlay.appendChild(line2);

      // Label
      if (cand.id) {
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', vx + 3);
        text.setAttribute('y', vy - 4);
        text.setAttribute('class', 'fits-label');
        text.textContent = cand.id;
        this.overlay.appendChild(text);
      }
    }
  },

  /** Destaca um candidato específico */
  highlightCandidate(candidateId) {
    this.candidates.forEach(c => c._active = (c.id === candidateId));

    const cand = this.candidates.find(c => c.id === candidateId);
    if (cand) {
      // Zoom no candidato
      const rect = this.viewport.getBoundingClientRect();
      this.zoom = 3;
      const cy_canvas = this.height - 1 - cand.y;
      this.panX = rect.width / 2 - cand.x * this.zoom;
      this.panY = rect.height / 2 - cy_canvas * this.zoom;
    }
    this.render();
  },

  resetView() {
    this.zoom = 1; this.panX = 0; this.panY = 0;
    this.candidates.forEach(c => c._active = false);
    this.render();
  },

  fitToView() {
    if (!this.viewport || !this.data) return;
    const rect = this.viewport.getBoundingClientRect();
    const scaleX = rect.width / this.width;
    const scaleY = rect.height / this.height;
    this.zoom = Math.min(scaleX, scaleY) * 0.95;
    this.panX = (rect.width - this.width * this.zoom) / 2;
    this.panY = (rect.height - this.height * this.zoom) / 2;
    this.render();
  },

  degToHMS(deg) {
    const h = deg / 15;
    const hh = Math.floor(h);
    const mm = Math.floor((h - hh) * 60);
    const ss = ((h - hh) * 60 - mm) * 60;
    return `${String(hh).padStart(2,'0')}h ${String(mm).padStart(2,'0')}m ${ss.toFixed(2)}s`;
  },

  degToDMS(deg) {
    const sign = deg >= 0 ? '+' : '-';
    const abs = Math.abs(deg);
    const dd = Math.floor(abs);
    const mm = Math.floor((abs - dd) * 60);
    const ss = ((abs - dd) * 60 - mm) * 60;
    return `${sign}${String(dd).padStart(2,'0')}° ${String(mm).padStart(2,'0')}' ${ss.toFixed(1)}"`;
  },
};

// Inicializa FITS viewer
fitsViewer.init();

// Modifica o clique nos candidatos da tabela para navegar ao FITS Viewer
function showCandidateInViewer(tracklet) {
  // Gera campo simulado se não existir
  if (!fitsViewer.data) {
    fitsViewer.generateSimulatedField(512, 512);
  }
  // Ativa aba FITS
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.remove('tab--active');
    t.setAttribute('aria-selected', 'false');
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.add('panel--hidden'));
  $('tab-fits').classList.add('tab--active');
  $('tab-fits').setAttribute('aria-selected', 'true');
  $('panel-fits').classList.remove('panel--hidden');

  // Destaca candidato
  fitsViewer.highlightCandidate(tracklet.id);
  log('INFO', `FITS Viewer: candidato ${tracklet.id} destacado — bounding box ativa`);
}

// ── KEYBOARD NAV ──────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') $('candidate-overlay').hidden = true;
  if (e.key === 'F5' && !state.running) runPipeline();
});

// Gera FITS simulado após pipeline
const _origRunPipeline = runPipeline;
async function runPipelineWithFITS() {
  await _origRunPipeline.call(this);
  // Gera campo FITS simulado ao finalizar pipeline
  if (state.tracklets.length > 0) {
    fitsViewer.generateSimulatedField(512, 512);
    log('OK', 'FITS field generated: 512×512 px simulated CCD with ZScale normalization');
  }
}
// Rebind
$('btn-run').removeEventListener('click', _origRunPipeline);
$('btn-run').addEventListener('click', runPipelineWithFITS);
