#!/usr/bin/env python3
"""
Generate a self-contained interactive HTML visualiser for conv2d golden vectors.

Usage:
    python scripts/visualize.py [--out tb/visualize.html] [--vec-dir tb/vectors]

Produces a single HTML file with all vector data embedded as JSON.
Open in any browser — no server required.
"""

import json
import os
import sys
import argparse

_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
from gen_tb import CONFIGS, N


def cfg_label(c):
    lw, fh = c['line_width'], c['frame_height']
    fs = f" {lw}×{fh}" if (lw, fh) != (8, 8) else ""
    fl = "FLUSH=on" if c['flush'] else "FLUSH=off"
    return f"{c['data_width']}b {c['kern_rows']}×{c['kern_cols']} {c['edge_mode']} {fl}{fs}"


def load_vectors(vec_dir):
    out = []
    for idx, cfg in enumerate(CONFIGS):
        pre = f"c{idx+1:02d}_"
        lw, fh, nf = cfg['line_width'], cfg['frame_height'], cfg['num_frames']
        kr, kc, fl = cfg['kern_rows'], cfg['kern_cols'], cfg['flush']
        ip = os.path.join(vec_dir, pre + "input.txt")
        ep = os.path.join(vec_dir, pre + "expected.txt")
        if not os.path.exists(ip) or not os.path.exists(ep):
            out.append(None)
            continue
        with open(ip) as f:
            pxs = [int(x) for x in f if x.strip()]
        frames = [pxs[n * fh * lw:(n + 1) * fh * lw] for n in range(nf)]
        with open(ep) as f:
            trows = [list(map(int, x.split())) for x in f if x.strip()]
        rppf = fh * lw
        fppf = (kr - 1) * lw if (fl and kr > 1) else 0
        tppf = rppf + fppf
        taps, flsh = [], []
        for n in range(nf):
            b = n * tppf
            taps.append(trows[b:b + rppf])
            flsh.append(trows[b + rppf:b + tppf] if fppf else [])
        out.append({'frames': frames, 'taps': taps, 'flush_taps': flsh})
    return out


def build_json(vd):
    cs = []
    for idx, cfg in enumerate(CONFIGS):
        e = {k: bool(v) if k == 'flush' else v for k, v in cfg.items()}
        e['idx'] = idx
        e['label'] = cfg_label(cfg)
        d = vd[idx]
        e['frames']     = d['frames']     if d else []
        e['taps']       = d['taps']       if d else []
        e['flush_taps'] = d['flush_taps'] if d else []
        cs.append(e)
    return json.dumps({'configs': cs}, separators=(',', ':'))


# ---------------------------------------------------------------------------
# HTML template — all JS/CSS inline for a self-contained file
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>conv2d Visualiser</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;display:flex;height:100vh;overflow:hidden;background:#12121f;color:#e0e0e0}

/* ---- Sidebar ----------------------------------------------------------- */
#sidebar{width:230px;min-width:230px;background:#1a1a2e;display:flex;flex-direction:column;
  padding:12px 10px;gap:8px;overflow-y:auto;border-right:1px solid #2a2a4a}
#sidebar h2{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#5a6a7a;
  padding-bottom:4px;border-bottom:1px solid #2a2a4a}
.fg{display:flex;flex-direction:column;gap:3px}
.fg label{font-size:10px;color:#7a8a9a;text-transform:uppercase;letter-spacing:.5px}
.fg select{padding:4px 6px;border-radius:5px;border:1px solid #2a2a4a;
  background:#0e1428;color:#c0d0e0;font-size:12px;cursor:pointer}
.fg select:focus{outline:none;border-color:#3a6fdc}
.rg{display:flex;flex-direction:column;gap:2px}
.rg label{display:flex;align-items:center;gap:7px;font-size:12px;cursor:pointer;
  padding:2px 4px;border-radius:4px}
.rg label:hover{background:#1e2840}
.rg input[type=radio]{accent-color:#3a6fdc;cursor:pointer}
#cfg-count{font-size:10px;color:#5a6a7a;text-align:right}
#config-list{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:1px}
.ci{padding:5px 8px;border-radius:5px;cursor:pointer;font-size:11px;line-height:1.5;
  border-left:3px solid transparent;transition:background .1s}
.ci:hover{background:#1e2840}
.ci.active{background:#1e3a7a;border-left-color:#3a6fdc}
.ci.hidden{display:none}
.cn{font-weight:700;color:#5a9fff;font-size:10px}
.ci.active .cn{color:#90c0ff}
.sep{border:none;border-top:1px solid #2a2a4a;margin:2px 0}

/* ---- Main -------------------------------------------------------------- */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden;padding:10px;gap:8px;min-width:0}
#topbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
#topbar h1{font-size:13px;font-weight:700;color:#5a9fff;white-space:nowrap}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;white-space:nowrap}
.bz{background:#1e2a1e;color:#7abf7a}
.br{background:#1a2a40;color:#7ab0df}
.bt{background:#2a1a40;color:#b07adf}
.bf{background:#3a2a00;color:#dfb050}
#lbl{font-size:11px;color:#8090a0}
#pxinfo{margin-left:auto;font-size:11px;color:#c0a050;white-space:nowrap}

/* ---- Content ----------------------------------------------------------- */
#content{flex:1;display:flex;gap:10px;overflow:hidden;min-height:0}

/* Frame panel */
#fp{display:flex;flex-direction:column;gap:6px;background:#1a1a2e;border-radius:8px;
  padding:10px;min-width:0;flex:1;overflow:hidden}
#fnav{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
#fnav button{background:#1e2840;border:1px solid #2a3a5a;color:#c0d0e0;border-radius:5px;
  padding:3px 10px;cursor:pointer;font-size:13px}
#fnav button:hover{background:#3a6fdc;border-color:#3a6fdc}
#flbl{font-size:12px;min-width:70px;text-align:center;color:#a0b0c0}
#hint{font-size:10px;color:#4a5a6a;margin-left:4px}
.cwrap{overflow:auto;flex:1;min-height:0}
canvas{display:block;image-rendering:pixelated;cursor:crosshair}

/* Flush player */
#fplay{display:none;flex-direction:column;gap:5px;padding-top:4px;
  border-top:1px solid #2a2a4a;margin-top:2px}
#fplay.vis{display:flex}
#fplay-title{font-size:11px;font-weight:700;color:#dfb050}
#ftl{display:flex;gap:3px;flex-wrap:wrap;align-items:center}
.tr{width:26px;height:26px;border-radius:4px;cursor:pointer;display:flex;align-items:center;
  justify-content:center;font-size:10px;font-weight:700;border:2px solid transparent;
  transition:border-color .1s,background .1s;user-select:none}
.tr.real{background:#1a3a2a;color:#7adf9a}
.tr.real:hover{background:#1a5a3a}
.tr.flush{background:#1e1e2e;color:#5a6a7a;border-style:dashed;border-color:#3a3a5a}
.tr.flush:hover{background:#252535}
.tr.act{border-color:#dfb050 !important;box-shadow:0 0 0 1px #dfb050}
.tlsep{color:#3a4a5a;font-size:18px;margin:0 2px;line-height:26px}
#fctrl{display:flex;align-items:center;gap:6px}
#fctrl button{background:#1e2840;border:1px solid #2a3a5a;color:#c0d0e0;
  border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px}
#fctrl button:hover{background:#3a6fdc;border-color:#3a6fdc}
#fplbl{font-size:11px;color:#a0a0b0}
#fspeed{font-size:11px;color:#7a8a9a;display:flex;align-items:center;gap:4px;margin-left:auto}
#fspeed select{padding:2px 4px;background:#0e1428;border:1px solid #2a3a5a;
  color:#c0d0e0;border-radius:4px;font-size:11px}

/* Tap panel */
#tp{width:350px;min-width:280px;display:flex;flex-direction:column;gap:6px;
  background:#1a1a2e;border-radius:8px;padding:10px;overflow:hidden}
#tp-title{font-size:13px;font-weight:700;color:#c0d0e0}
#tp-coords{font-size:11px;color:#7a8a9a;min-height:16px}
.tapwrap{overflow:auto;display:flex;flex-direction:column}
#tcol-labels{display:flex;margin-left:24px}
.tlbl{display:flex;align-items:center;justify-content:center;font-size:9px;overflow:hidden}
#trow-wrap{display:flex}
#trow-labels{display:flex;flex-direction:column;width:24px}
#legend{margin-top:6px;display:flex;flex-direction:column;gap:4px;
  border-top:1px solid #2a2a4a;padding-top:6px}
#legend-title{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#5a6a7a}
.lrow{display:flex;align-items:center;gap:6px;font-size:11px;color:#9090a0}
.lsw{width:14px;height:14px;border-radius:3px;border:2px solid transparent;flex-shrink:0}
</style>
</head>
<body>

<div id="sidebar">
  <h2>conv2d Visualiser</h2>

  <div class="fg">
    <label>Data width</label>
    <select id="f-dw" onchange="applyFilters()">
      <option value="all">All widths</option>
      <option value="8">8-bit</option>
      <option value="16">16-bit</option>
      <option value="24">24-bit</option>
    </select>
  </div>

  <div class="fg">
    <label>Kernel size</label>
    <select id="f-kern" onchange="applyFilters()">
      <option value="all">All sizes</option>
      <option value="3x3">3&times;3</option>
      <option value="5x5">5&times;5</option>
      <option value="3x5">3&times;5</option>
      <option value="7x7">7&times;7</option>
    </select>
  </div>

  <div class="fg">
    <label>Edge mode</label>
    <div class="rg">
      <label><input type="radio" name="fmode" value="all" checked onchange="applyFilters()"> All</label>
      <label><input type="radio" name="fmode" value="ZERO"      onchange="applyFilters()"> ZERO</label>
      <label><input type="radio" name="fmode" value="REPLICATE" onchange="applyFilters()"> REPLICATE</label>
      <label><input type="radio" name="fmode" value="TOROIDAL"  onchange="applyFilters()"> TOROIDAL</label>
    </div>
  </div>

  <div class="fg">
    <label>FLUSH</label>
    <select id="f-flush" onchange="applyFilters()">
      <option value="all">All</option>
      <option value="on">On</option>
      <option value="off">Off</option>
    </select>
  </div>

  <div class="fg">
    <label>Frame size</label>
    <select id="f-frame" onchange="applyFilters()">
      <option value="all">All frames</option>
      <option value="8x8">8&times;8</option>
      <option value="16x4">16&times;4</option>
      <option value="4x16">4&times;16</option>
      <option value="3x3">3&times;3</option>
      <option value="5x5">5&times;5</option>
      <option value="8x1">8&times;1</option>
    </select>
  </div>

  <hr class="sep">
  <div id="cfg-count"></div>
  <div id="config-list"></div>
</div>

<div id="main">
  <div id="topbar">
    <h1>conv2d</h1>
    <span id="b-mode" class="badge"></span>
    <span id="b-flush" class="badge bf" style="display:none">FLUSH</span>
    <span id="lbl"></span>
    <span id="pxinfo"></span>
  </div>

  <div id="content">
    <!-- Frame panel -->
    <div id="fp">
      <div id="fnav">
        <button onclick="prevFrame()">&#9664;</button>
        <span id="flbl">Frame 0/2</span>
        <button onclick="nextFrame()">&#9654;</button>
        <span id="hint">Click cell &middot; &uarr;&darr;&larr;&rarr; move &middot; , / . config &middot; Alt+&larr;&rarr; frame</span>
      </div>
      <div class="cwrap" id="frame-wrap">
        <canvas id="fc"></canvas>
      </div>
      <div id="fplay">
        <div id="fplay-title">&#9881; Flush player</div>
        <div id="ftl"></div>
        <div id="fctrl">
          <button onclick="fPrev()">&#9664;</button>
          <button id="btn-play" onclick="togglePlay()">&#9654; Play</button>
          <button onclick="fNext()">&#9654;</button>
          <button onclick="exitFlush()" title="Return to real frame view">&times; Real</button>
          <span id="fplbl"></span>
          <div id="fspeed">
            Speed: <select id="spd">
              <option value="800">0.5&times;</option>
              <option value="400" selected>1&times;</option>
              <option value="200">2&times;</option>
              <option value="100">4&times;</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- Tap panel -->
    <div id="tp">
      <div id="tp-title">Kernel window</div>
      <div id="tp-coords"></div>
      <div class="tapwrap">
        <div id="tcol-labels"></div>
        <div id="trow-wrap">
          <div id="trow-labels"></div>
          <canvas id="tc"></canvas>
        </div>
      </div>
      <div id="legend">
        <div id="legend-title">Legend</div>
      </div>
    </div>
  </div>
</div>

<script>
'use strict';
const DATA = __DATA_JSON__;
const CFG = DATA.configs;
const N = CFG.length;

// ---- State ---------------------------------------------------------------
const s = {
  ci: 0,       // config index
  fn: 0,       // frame number
  row: 3, col: 3,
  isFlush: false,
  fr: 0,       // flush row index
  playing: false,
  timer: null,
};

// ---- Color helpers -------------------------------------------------------
function ylOrRd(t) {
  t = Math.max(0, Math.min(1, t));
  // stops: pale-yellow → orange → dark-red
  const s0 = [[255,255,178],[253,141,60],[189,0,38]];
  const i = t < 0.5 ? 0 : 1, u = t < 0.5 ? t*2 : (t-0.5)*2;
  return s0[i].map((v, k) => Math.round(v + u*(s0[i+1][k]-v)));
}
function rgb(c) { return `rgb(${c[0]},${c[1]},${c[2]})`; }
function lum(c) { return (0.299*c[0] + 0.587*c[1] + 0.114*c[2])/255; }
function txtClr(c) { return lum(c) > 0.5 ? '#111' : '#eee'; }
function grey(v) { return [v,v,v]; }

// ---- Filters -------------------------------------------------------------
function applyFilters() {
  const dw   = document.getElementById('f-dw').value;
  const kern = document.getElementById('f-kern').value;
  const mode = document.querySelector('input[name="fmode"]:checked').value;
  const fl   = document.getElementById('f-flush').value;
  const fr   = document.getElementById('f-frame').value;
  let vis = 0;
  let firstVis = -1;
  document.querySelectorAll('.ci').forEach((el, i) => {
    const c = CFG[i];
    const ok =
      (dw   === 'all' || String(c.data_width) === dw) &&
      (kern === 'all' || `${c.kern_rows}x${c.kern_cols}` === kern) &&
      (mode === 'all' || c.edge_mode === mode) &&
      (fl   === 'all' || (fl==='on') === c.flush) &&
      (fr   === 'all' || `${c.line_width}x${c.frame_height}` === fr);
    el.classList.toggle('hidden', !ok);
    if (ok) { vis++; if (firstVis < 0) firstVis = i; }
  });
  document.getElementById('cfg-count').textContent = `${vis} / ${N} configs`;
  // If active config hidden, jump to first visible
  if (document.querySelector('.ci.active.hidden') && firstVis >= 0)
    selectCfg(firstVis);
}

// ---- Config list ---------------------------------------------------------
function buildList() {
  const list = document.getElementById('config-list');
  CFG.forEach((c, i) => {
    const el = document.createElement('div');
    el.className = 'ci';
    el.dataset.i = i;
    el.innerHTML = `<span class="cn">CFG${String(i+1).padStart(2,'0')}</span> ${c.label}`;
    el.onclick = () => selectCfg(i);
    list.appendChild(el);
  });
}

function selectCfg(i) {
  s.ci = i; s.fn = 0; s.isFlush = false; s.fr = 0;
  s.playing = false; clearInterval(s.timer);
  const c = CFG[i];
  s.row = Math.min(s.row, c.frame_height-1);
  s.col = Math.min(s.col, c.line_width-1);
  document.querySelectorAll('.ci').forEach(el => el.classList.remove('active'));
  const el = document.querySelector(`.ci[data-i="${i}"]`);
  if (el) { el.classList.add('active'); el.scrollIntoView({block:'nearest'}); }
  render();
}

// ---- Frame nav -----------------------------------------------------------
function prevFrame() { const nf=CFG[s.ci].num_frames; s.fn=(s.fn-1+nf)%nf; s.isFlush=false; render(); }
function nextFrame() { const nf=CFG[s.ci].num_frames; s.fn=(s.fn+1)%nf; s.isFlush=false; render(); }

// ---- Flush player --------------------------------------------------------
function buildTimeline() {
  const c = CFG[s.ci];
  const tl = document.getElementById('ftl');
  tl.innerHTML = '';
  if (!c.flush || c.kern_rows <= 1) return;

  for (let r = 0; r < c.frame_height; r++) {
    const el = document.createElement('div');
    el.className = 'tr real';
    el.title = `Real row ${r} — click to jump`;
    el.textContent = r;
    el.style.fontSize = c.frame_height > 9 ? '9px' : '10px';
    const _r = r;
    el.onclick = () => { s.isFlush = false; s.row = _r; render(); };
    tl.appendChild(el);
  }
  const sep = document.createElement('span');
  sep.className = 'tlsep'; sep.textContent = '│';
  tl.appendChild(sep);

  for (let f = 0; f < c.kern_rows-1; f++) {
    const el = document.createElement('div');
    el.className = 'tr flush';
    el.title = `Flush row ${f} — virtual row ${c.frame_height+f}`;
    el.textContent = `F${f}`;
    const _f = f;
    el.onclick = () => { s.isFlush = true; s.fr = _f; render(); };
    tl.appendChild(el);
  }
}

function updateTL() {
  document.querySelectorAll('.tr.real' ).forEach((el,i) => el.classList.toggle('act', !s.isFlush && i===s.row));
  document.querySelectorAll('.tr.flush').forEach((el,i) => el.classList.toggle('act',  s.isFlush && i===s.fr));
  const c = CFG[s.ci];
  const fh = c.frame_height;
  document.getElementById('fplbl').textContent = s.isFlush
    ? `Flush row ${s.fr} · virtual row ${fh+s.fr}`
    : `Real row ${s.row}`;
  document.getElementById('btn-play').textContent = s.playing ? '⏸ Pause' : '▶ Play';
}

function totalRows() { const c=CFG[s.ci]; return c.frame_height + (c.flush&&c.kern_rows>1?c.kern_rows-1:0); }

function rowFromPos(pos) {
  const c = CFG[s.ci];
  if (pos < c.frame_height) { s.isFlush = false; s.row = pos; }
  else { s.isFlush = true; s.fr = pos - c.frame_height; }
}

function curPos() { const c=CFG[s.ci]; return s.isFlush ? c.frame_height+s.fr : s.row; }

function fNext() { rowFromPos((curPos()+1) % totalRows()); render(); }
function fPrev() { rowFromPos((curPos()-1+totalRows()) % totalRows()); render(); }
function exitFlush() { s.isFlush = false; render(); }
function togglePlay() {
  s.playing = !s.playing;
  clearInterval(s.timer);
  if (s.playing) {
    const go = () => { fNext(); };
    s.timer = setInterval(go, +document.getElementById('spd').value);
  }
  updateTL();
}
document.getElementById('spd').onchange = () => {
  if (s.playing) { clearInterval(s.timer); s.timer = setInterval(fNext, +document.getElementById('spd').value); }
};

// ---- Cell sizing ---------------------------------------------------------
function cs(maxDim, avail) { return Math.max(14, Math.min(64, Math.floor(avail/maxDim))); }

// ---- Hatch fill helper ---------------------------------------------------
function hatch(ctx, x, y, w, h) {
  ctx.save();
  ctx.beginPath(); ctx.rect(x,y,w,h); ctx.clip();
  ctx.strokeStyle='rgba(255,255,255,0.06)'; ctx.lineWidth=1;
  for (let d=-h; d<w+h; d+=7) {
    ctx.beginPath(); ctx.moveTo(x+d,y); ctx.lineTo(x+d+h,y+h); ctx.stroke();
  }
  ctx.restore();
}

// ---- Render frame canvas -------------------------------------------------
function renderFrame() {
  const c = CFG[s.ci];
  if (!c.frames.length) return;
  const lw=c.line_width, fh=c.frame_height, kr=c.kern_rows, kc=c.kern_cols, dw=c.data_width;
  const vmax = (1<<Math.min(dw,30))-1;
  const flushR = c.flush && kr>1 ? kr-1 : 0;
  const totR = fh + flushR;

  const wrap = document.getElementById('frame-wrap');
  const avW = wrap.clientWidth || 400;
  const avH = wrap.clientHeight || 300;
  const cell = cs(Math.max(lw, totR), Math.min(avW, Math.max(avH*lw/totR, avW)));

  const can = document.getElementById('fc');
  can.width = lw*cell; can.height = totR*cell;
  const ctx = can.getContext('2d');

  const frame = c.frames[s.fn];

  // Real cells
  for (let r=0; r<fh; r++) {
    for (let col=0; col<lw; col++) {
      const val = frame[r*lw+col];
      const clr = ylOrRd(val/vmax);
      ctx.fillStyle = rgb(clr);
      ctx.fillRect(col*cell, r*cell, cell, cell);
      ctx.strokeStyle='rgba(0,0,0,0.18)'; ctx.lineWidth=.5;
      ctx.strokeRect(col*cell+.5, r*cell+.5, cell-1, cell-1);
      if (cell >= 18) {
        ctx.fillStyle = txtClr(clr);
        ctx.font = `${Math.max(8,cell/4.5)|0}px monospace`;
        ctx.textAlign='center'; ctx.textBaseline='middle';
        const hex = dw>8 ? `0x${val.toString(16).toUpperCase().padStart(Math.ceil(dw/4),'0')}` : `${val}`;
        ctx.fillText(hex, col*cell+cell/2, r*cell+cell/2);
      }
    }
  }

  // Flush virtual rows (hatched)
  for (let f=0; f<flushR; f++) {
    const r = fh+f;
    for (let col=0; col<lw; col++) {
      ctx.fillStyle='#1a1a2e'; ctx.fillRect(col*cell,r*cell,cell,cell);
      hatch(ctx, col*cell, r*cell, cell, cell);
      ctx.strokeStyle='rgba(100,100,150,0.3)'; ctx.lineWidth=.5;
      ctx.strokeRect(col*cell+.5,r*cell+.5,cell-1,cell-1);
      if (cell>=16) {
        ctx.fillStyle='#4a4a6a'; ctx.font=`${Math.max(7,cell/5)|0}px monospace`;
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(`F${f}`, col*cell+cell/2, r*cell+cell/2);
      }
    }
  }

  // Separator line between real and flush rows
  if (flushR > 0) {
    ctx.setLineDash([5,3]); ctx.strokeStyle='#dfb050'; ctx.lineWidth=1.5;
    ctx.strokeRect(.5,.5,lw*cell-1,fh*cell-1);
    ctx.setLineDash([]);
  }

  // Kernel footprint
  const curRow = s.isFlush ? fh+s.fr : s.row;
  const topRow = curRow - (kr-1);
  const leftCol = s.col - (kc-1);
  ctx.fillStyle='rgba(58,111,220,0.22)';
  for (let dr=0; dr<kr; dr++) for (let dc=0; dc<kc; dc++) {
    const pr=topRow+dr, pc=leftCol+dc;
    if (pr>=0&&pr<totR&&pc>=0&&pc<lw) ctx.fillRect(pc*cell+1,pr*cell+1,cell-2,cell-2);
  }
  // Kernel border outline
  ctx.strokeStyle='rgba(58,111,220,0.5)'; ctx.lineWidth=1;
  ctx.setLineDash([]);
  for (let dr=0; dr<kr; dr++) for (let dc=0; dc<kc; dc++) {
    const pr=topRow+dr, pc=leftCol+dc;
    if (pr>=0&&pr<totR&&pc>=0&&pc<lw) { ctx.strokeRect(pc*cell+1,pr*cell+1,cell-2,cell-2); }
  }

  // Current pixel indicator
  if (!s.isFlush) {
    ctx.strokeStyle='#3a8fff'; ctx.lineWidth=2.5;
    ctx.beginPath();
    ctx.arc(s.col*cell+cell/2, s.row*cell+cell/2, cell*.32, 0, 2*Math.PI);
    ctx.stroke();
  } else {
    // Highlight the flush cell being viewed
    ctx.strokeStyle='#dfb050'; ctx.lineWidth=2.5;
    ctx.strokeRect(s.col*cell+2,(fh+s.fr)*cell+2,cell-4,cell-4);
    ctx.fillStyle='rgba(223,176,80,0.2)';
    ctx.fillRect(s.col*cell+2,(fh+s.fr)*cell+2,cell-4,cell-4);
  }

  // Row index labels on the right edge (outside canvas — drawn as y-axis in tap panel)
}

// ---- Render tap canvas ---------------------------------------------------
function renderTap() {
  const c = CFG[s.ci];
  if (!c.frames.length) return;
  const kr=c.kern_rows, kc=c.kern_cols, dw=c.data_width, mode=c.edge_mode;
  const fh=c.frame_height, lw=c.line_width;
  const vmax = (1<<Math.min(dw,30))-1;
  const curRow = s.isFlush ? fh+s.fr : s.row;

  // Tap data
  let taps;
  if (s.isFlush) {
    const fi = s.fr*lw+s.col;
    taps = c.flush_taps[s.fn] && c.flush_taps[s.fn][fi];
  } else {
    const pi = s.row*lw+s.col;
    taps = c.taps[s.fn] && c.taps[s.fn][pi];
  }
  if (!taps) return;

  const tpW = document.getElementById('tp').clientWidth - 24 - 28;
  const cell = cs(Math.max(kr,kc), Math.min(tpW, 300));

  // Column image-coord labels
  const colDiv = document.getElementById('tcol-labels');
  colDiv.innerHTML='';
  for (let dc=0; dc<kc; dc++) {
    const srcCol = s.col - (kc-1-dc);
    const oob = srcCol<0||srcCol>=lw;
    const el=document.createElement('div'); el.className='tlbl';
    el.style.width=cell+'px'; el.style.height='16px';
    el.style.color = oob ? '#df5050' : '#5a7a8a';
    el.textContent = srcCol;
    colDiv.appendChild(el);
  }

  // Row image-coord labels
  const rowDiv = document.getElementById('trow-labels');
  rowDiv.innerHTML='';
  for (let r=0; r<kr; r++) {
    const srcRow = curRow - (kr-1-r);
    const oob = srcRow<0||srcRow>=fh;
    const el=document.createElement('div'); el.className='tlbl';
    el.style.height=cell+'px'; el.style.width='24px';
    el.style.color = oob ? '#df5050' : '#5a7a8a';
    // Label flush virtual rows differently
    const ltext = srcRow < 0 ? srcRow
                : srcRow >= fh ? `F${srcRow-fh}`
                : srcRow;
    el.textContent = ltext;
    rowDiv.appendChild(el);
  }

  const can = document.getElementById('tc');
  can.width=kc*cell; can.height=kr*cell;
  const ctx = can.getContext('2d');

  for (let r=0; r<kr; r++) {
    for (let dc=0; dc<kc; dc++) {
      const col = kc-1-dc;           // display left=oldest, right=newest
      const val = taps[r*kc+col];
      const norm = val/vmax;
      const srcRow = curRow-(kr-1-r);
      const srcCol = s.col-col;
      const isOob = srcRow<0||srcRow>=fh||srcCol<0||srcCol>=lw;
      const isFlushedSrc = srcRow>=fh;  // source is a flush dummy zero row

      let face, edge;
      if (isOob && mode==='ZERO')      { face=grey(180); edge=null; }
      else if (isOob && mode==='REPLICATE') { face=ylOrRd(norm); edge='#4682b4'; }
      else if (isOob && mode==='TOROIDAL')  { face=ylOrRd(norm); edge='#9370db'; }
      else                              { face=ylOrRd(norm); edge=null; }

      ctx.fillStyle=rgb(face); ctx.fillRect(dc*cell,r*cell,cell,cell);

      if (isFlushedSrc) hatch(ctx, dc*cell, r*cell, cell, cell);

      if (edge) {
        ctx.strokeStyle=edge; ctx.lineWidth=2.5;
        ctx.strokeRect(dc*cell+1.5,r*cell+1.5,cell-3,cell-3);
      } else {
        ctx.strokeStyle='rgba(0,0,0,0.2)'; ctx.lineWidth=.5;
        ctx.strokeRect(dc*cell+.5,r*cell+.5,cell-1,cell-1);
      }

      if (cell>=16) {
        ctx.fillStyle=txtClr(face);
        ctx.font=`${Math.max(8,cell/4.5)|0}px monospace`;
        ctx.textAlign='center'; ctx.textBaseline='middle';
        const hex = dw>8 ? `0x${val.toString(16).toUpperCase().padStart(Math.ceil(dw/4),'0')}` : `${val}`;
        ctx.fillText(hex+(isOob?'*':''), dc*cell+cell/2, r*cell+cell/2);
      }
    }
  }

  // Current-pixel indicator: bottom-right in display
  ctx.strokeStyle='#3a8fff'; ctx.lineWidth=3;
  ctx.strokeRect((kc-1)*cell+2,(kr-1)*cell+2,cell-4,cell-4);

  // Update info text
  document.getElementById('tp-title').textContent = s.isFlush
    ? `Kernel @ flush row ${s.fr} (virtual row ${fh+s.fr})`
    : `Kernel @ (row ${s.row}, col ${s.col})`;
  document.getElementById('tp-coords').textContent = s.isFlush
    ? `Frame ${s.fn} · col ${s.col} · edge mode: ${mode}`
    : `Frame ${s.fn} · image coords (${s.row}, ${s.col}) · edge mode: ${mode}`;
}

// ---- Legend --------------------------------------------------------------
function renderLegend() {
  const c = CFG[s.ci];
  const entries = [];
  entries.push({sw:'background:#b4b4c2;border:none', txt:'OOB tap → 0 (zero-extend)'});
  if (c.edge_mode==='REPLICATE')
    entries.push({sw:'background:#fd8d3c;border:2px solid #4682b4', txt:'* OOB → clamped edge pixel (REPLICATE)'});
  if (c.edge_mode==='TOROIDAL')
    entries.push({sw:'background:#fd8d3c;border:2px solid #9370db', txt:'* OOB → causal wrap (TOROIDAL)'});
  if (c.flush && c.kern_rows>1)
    entries.push({sw:'background:#1a1a2e;border:2px dashed #dfb050', txt:'Hatched = injected zero row (FLUSH)'});
  entries.push({sw:'background:none;border:2.5px solid #3a8fff', txt:'Blue outline = current pixel (bottom-right tap)'});

  const leg = document.getElementById('legend');
  leg.innerHTML = '<div id="legend-title">Legend</div>' +
    entries.map(e=>`<div class="lrow"><div class="lsw" style="${e.sw}"></div><span>${e.txt}</span></div>`).join('');
}

// ---- Top bar -------------------------------------------------------------
function renderTopBar() {
  const c = CFG[s.ci];
  const bm = document.getElementById('b-mode');
  bm.textContent = c.edge_mode;
  bm.className = 'badge ' + {ZERO:'bz',REPLICATE:'br',TOROIDAL:'bt'}[c.edge_mode];
  document.getElementById('b-flush').style.display = c.flush ? '' : 'none';
  document.getElementById('lbl').textContent =
    `CFG${String(s.ci+1).padStart(2,'0')} · ${c.data_width}b · ${c.kern_rows}×${c.kern_cols} · ${c.line_width}×${c.frame_height} px`;
  document.getElementById('pxinfo').textContent = s.isFlush
    ? `▶ flush F${s.fr} (vrow ${c.frame_height+s.fr}), col ${s.col}`
    : `px (${s.row}, ${s.col})`;
  document.getElementById('flbl').textContent = `Frame ${s.fn} / ${c.num_frames-1}`;
}

// ---- Flush player visibility ---------------------------------------------
function updateFlushPlayer() {
  const c = CFG[s.ci];
  const fp = document.getElementById('fplay');
  if (c.flush && c.kern_rows > 1) {
    fp.classList.add('vis');
    buildTimeline();
    updateTL();
  } else {
    fp.classList.remove('vis');
    s.isFlush = false;
    s.playing = false;
    clearInterval(s.timer);
  }
}

// ---- Main render ---------------------------------------------------------
function render() {
  renderTopBar();
  updateFlushPlayer();
  renderFrame();
  renderTap();
  renderLegend();
}

// ---- Frame canvas click --------------------------------------------------
document.getElementById('fc').addEventListener('click', function(e) {
  const c = CFG[s.ci];
  const lw=c.line_width, fh=c.frame_height, flushR=c.flush&&c.kern_rows>1?c.kern_rows-1:0;
  const rect = this.getBoundingClientRect();
  const cell = this.width/lw;
  const col = Math.floor((e.clientX-rect.left)/cell);
  const row = Math.floor((e.clientY-rect.top)/cell);
  if (col<0||col>=lw||row<0||row>=fh+flushR) return;
  s.col = col;
  if (row < fh) { s.isFlush=false; s.row=row; }
  else { s.isFlush=true; s.fr=row-fh; }
  render();
});

// ---- Keyboard ------------------------------------------------------------
document.addEventListener('keydown', e => {
  if (e.target.tagName==='SELECT') return;
  const c = CFG[s.ci];
  const fh=c.frame_height, lw=c.line_width;
  let handled=true;
  if (e.altKey && e.key==='ArrowLeft')  prevFrame();
  else if (e.altKey && e.key==='ArrowRight') nextFrame();
  else if (e.key==='ArrowLeft')  { s.col=Math.max(0,s.col-1); render(); }
  else if (e.key==='ArrowRight') { s.col=Math.min(lw-1,s.col+1); render(); }
  else if (e.key==='ArrowUp')    { if(!s.isFlush) s.row=Math.max(0,s.row-1); render(); }
  else if (e.key==='ArrowDown')  { if(!s.isFlush) s.row=Math.min(fh-1,s.row+1); render(); }
  else if (e.key===',')          selectCfg(Math.max(0,s.ci-1));
  else if (e.key==='.')          selectCfg(Math.min(N-1,s.ci+1));
  else if (e.key==='f'||e.key==='F') { if(c.flush&&c.kern_rows>1) fNext(); }
  else if (e.key==='r'||e.key==='R') exitFlush();
  else if (e.key===' ')          { if(c.flush&&c.kern_rows>1) togglePlay(); }
  else handled=false;
  if (handled) e.preventDefault();
});

// ---- Resize --------------------------------------------------------------
window.addEventListener('resize', render);

// ---- Init ----------------------------------------------------------------
buildList();
applyFilters();
selectCfg(3);  // default: CFG04 = 8b 3x3 REPLICATE FLUSH=on
</script>
</body>
</html>"""


def generate_html(data_json):
    return HTML.replace('__DATA_JSON__', data_json)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join('tb', 'visualize.html'),
                    help='Output file (default: tb/visualize.html)')
    ap.add_argument('--vec-dir', default=os.path.join('tb', 'vectors'),
                    help='Vector directory (default: tb/vectors)')
    args = ap.parse_args()

    print(f"Loading {N} configs from {args.vec_dir} ...")
    vd = load_vectors(args.vec_dir)
    loaded = sum(1 for x in vd if x is not None)
    print(f"Loaded {loaded}/{N} configs")

    data_json = build_json(vd)
    html = generate_html(data_json)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w') as f:
        f.write(html)
    size_kb = os.path.getsize(args.out) / 1024
    print(f"Generated {args.out} ({size_kb:.0f} KB)")
    print(f"Open in browser: file://{os.path.abspath(args.out)}")


if __name__ == '__main__':
    main()
