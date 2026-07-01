#!/usr/bin/env python3
"""
Generate a self-contained interactive HTML visualiser for conv2d golden vectors.

Usage:
    python scripts/visualize.py [--out tb/visualize.html] [--vec-dir tb/vectors]

Produces a single HTML file with all vector data embedded as JSON.
Open in any browser — no server required.

Modes
-----
Explore   : click any pixel in the frame to inspect its tap window.
Simulate  : step/play through every clock cycle in sequence, showing the
            input pixel being accepted and the 3-stage pipeline draining
            through to the output tap window.
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


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>conv2d Visualiser</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;display:flex;height:100vh;overflow:hidden;
     background:#12121f;color:#e0e0e0;font-size:13px}

/* ── Sidebar ─────────────────────────────────────────────────────────── */
#sidebar{width:224px;min-width:224px;background:#1a1a2e;display:flex;
  flex-direction:column;padding:10px 8px;gap:7px;overflow-y:auto;
  border-right:1px solid #252540}
#sidebar h2{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;
  color:#4a5a6a;padding-bottom:4px;border-bottom:1px solid #252540}
.fg{display:flex;flex-direction:column;gap:2px}
.fg>span{font-size:10px;color:#6a7a8a;text-transform:uppercase;letter-spacing:.5px}
.fg select{padding:3px 6px;border-radius:4px;border:1px solid #252540;
  background:#0e1428;color:#c0d0e0;font-size:12px;cursor:pointer}
.fg select:focus{outline:none;border-color:#3a6fdc}
.rg{display:flex;flex-direction:column;gap:1px}
.rg label{display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;
  padding:2px 4px;border-radius:3px}
.rg label:hover{background:#1e2840}
.rg input{accent-color:#3a6fdc}
#cfg-count{font-size:10px;color:#4a5a6a;text-align:right}
#config-list{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:1px}
.ci{padding:4px 7px;border-radius:4px;cursor:pointer;font-size:11px;
  line-height:1.5;border-left:3px solid transparent}
.ci:hover{background:#1e2840}
.ci.active{background:#1e3a7a;border-left-color:#3a6fdc}
.ci.hidden{display:none}
.cn{font-weight:700;color:#5a9fff;font-size:10px}
.ci.active .cn{color:#90c0ff}
hr.sp{border:none;border-top:1px solid #252540;margin:2px 0}

/* ── Main ────────────────────────────────────────────────────────────── */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden;
  padding:8px;gap:6px;min-width:0}

/* Mode bar */
#modebar{display:flex;align-items:center;gap:8px;flex-shrink:0}
#modebar h1{font-size:13px;font-weight:700;color:#5a9fff;margin-right:4px}
.mbtn{padding:4px 14px;border-radius:20px;border:1px solid #2a3a5a;
  background:transparent;color:#8090a0;cursor:pointer;font-size:12px}
.mbtn.act{background:#1e3a7a;color:#90c0ff;border-color:#3a6fdc}
.mbtn:hover:not(.act){background:#1e2840;color:#c0d0e0}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700}
.bz{background:#1e2a1e;color:#7abf7a}
.br{background:#1a2a40;color:#7ab0df}
.bt{background:#2a1a40;color:#b07adf}
.bf{background:#3a2a00;color:#dfb050}
#toplbl{font-size:11px;color:#6a7a8a;margin-left:2px}
#pxinfo{margin-left:auto;font-size:11px;color:#c0a050;white-space:nowrap}

/* ── Content ─────────────────────────────────────────────────────────── */
#content{flex:1;display:flex;gap:8px;overflow:hidden;min-height:0}
.panel{background:#1a1a2e;border-radius:8px;padding:8px;
  display:flex;flex-direction:column;gap:6px;overflow:hidden}
#frame-panel{flex:1;min-width:0}
#right-panel{width:340px;min-width:280px;display:flex;flex-direction:column;gap:8px}

/* ── Frame panel controls ─────────────────────────────────────────────── */
#explore-ctrl,#sim-ctrl{display:flex;flex-direction:column;gap:4px}
#explore-ctrl.hidden,#sim-ctrl.hidden{display:none}

/* Explore: frame nav + flush player */
#fnav{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
#fnav button,#fctrl button{background:#1e2840;border:1px solid #2a3a5a;
  color:#c0d0e0;border-radius:4px;padding:3px 9px;cursor:pointer}
#fnav button:hover,#fctrl button:hover{background:#3a6fdc;border-color:#3a6fdc}
#flbl{font-size:12px;min-width:64px;text-align:center;color:#9ab0c0}
#hint{font-size:10px;color:#3a4a5a}

#fplay{display:none;flex-direction:column;gap:4px;padding-top:4px;
  border-top:1px solid #252540}
#fplay.vis{display:flex}
#fplay-title{font-size:11px;font-weight:700;color:#dfb050}
#ftl{display:flex;gap:3px;flex-wrap:wrap;align-items:center}
.tr{width:24px;height:24px;border-radius:4px;cursor:pointer;display:flex;
  align-items:center;justify-content:center;font-size:10px;font-weight:700;
  border:2px solid transparent;user-select:none}
.tr.real{background:#1a3a2a;color:#7adf9a}
.tr.real:hover{background:#1a5a3a}
.tr.flush-tl{background:#1e1e2e;color:#5a6a7a;border-style:dashed;border-color:#3a3a5a}
.tr.flush-tl:hover{background:#252535}
.tr.act{border-color:#dfb050!important;box-shadow:0 0 0 1px #dfb050}
.tlsep{color:#3a4a5a;font-size:16px;margin:0 2px;line-height:24px}
#fctrl{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
#fplbl{font-size:11px;color:#9090a0}
#fspeed select{padding:2px 4px;background:#0e1428;border:1px solid #2a3a5a;
  color:#c0d0e0;border-radius:3px;font-size:11px}

/* Sim controls */
#sim-header{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
#sim-step-lbl{font-size:12px;color:#9ab0c0;min-width:110px}
#sim-pos-lbl{font-size:11px;color:#7a8a9a}
.sbtn{background:#1e2840;border:1px solid #2a3a5a;color:#c0d0e0;
  border-radius:4px;padding:3px 9px;cursor:pointer;font-size:14px;line-height:1}
.sbtn:hover{background:#3a6fdc;border-color:#3a6fdc}
#scrubber{flex:1;min-width:80px;accent-color:#3a6fdc;cursor:pointer;height:4px}
#sim-speed select{padding:2px 4px;background:#0e1428;border:1px solid #2a3a5a;
  color:#c0d0e0;border-radius:3px;font-size:11px}

/* Frame canvas wrap */
.cwrap{overflow:auto;flex:1;min-height:0}
canvas{display:block;image-rendering:pixelated;cursor:crosshair}

/* ── Right panel ──────────────────────────────────────────────────────── */
/* Pipeline (sim only) */
#pipeline-panel{display:none;flex-direction:column;gap:4px;
  background:#141428;border-radius:6px;padding:8px;flex-shrink:0}
#pipeline-panel.vis{display:flex}
#pipe-title{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#4a5a6a}
.pstage{display:flex;align-items:center;gap:6px;padding:5px 8px;
  border-radius:5px;border:1px solid #252540;background:#1a1a2e}
.pstage.s-out  {border-color:#3a6fdc}
.pstage.s-dly  {border-color:#8a6a10}
.pstage.s-in   {border-color:#1a6a30}
.pstage.s-next {border-color:#303050;opacity:.6}
.pstage-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot-out {background:#3a8fff}
.dot-dly {background:#dfb050}
.dot-in  {background:#3abf7a}
.dot-next{background:#505070}
.pstage-info{flex:1;min-width:0}
.pstage-label{font-size:10px;font-weight:700;color:#7a8a9a}
.pstage-pos{font-size:11px;color:#c0d0e0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pstage-val{font-size:10px;color:#7a8a9a;font-family:monospace}
.pstage-swatch{width:28px;height:28px;border-radius:3px;flex-shrink:0;border:1px solid #252540}
.parrow{text-align:center;font-size:16px;color:#3a4a5a;line-height:1.2}

/* Tap panel */
#tap-panel{flex:1;display:flex;flex-direction:column;gap:5px;
  background:#141428;border-radius:6px;padding:8px;min-height:0;overflow:hidden}
#tp-title{font-size:12px;font-weight:700;color:#c0d0e0}
#tp-coords{font-size:10px;color:#6a7a8a;min-height:14px}
.tapwrap{overflow:auto;display:flex;flex-direction:column;flex:1;min-height:0}
#tcol-labels{display:flex;margin-left:24px}
#trow-wrap{display:flex;flex:1;min-height:0}
#trow-labels{display:flex;flex-direction:column;width:24px}
.tlbl{display:flex;align-items:center;justify-content:center;
  font-size:9px;overflow:hidden;font-family:monospace}
#legend{margin-top:4px;display:flex;flex-direction:column;gap:3px;
  border-top:1px solid #252540;padding-top:5px;flex-shrink:0}
#legend-title{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#4a5a6a}
.lrow{display:flex;align-items:center;gap:5px;font-size:10px;color:#8090a0}
.lsw{width:12px;height:12px;border-radius:2px;border:2px solid transparent;flex-shrink:0}
</style>
</head>
<body>

<!-- Sidebar -->
<div id="sidebar">
  <h2>conv2d Visualiser</h2>
  <div class="fg"><span>Data width</span>
    <select id="f-dw" onchange="applyFilters()">
      <option value="all">All widths</option>
      <option value="8">8-bit</option>
      <option value="16">16-bit</option>
      <option value="24">24-bit</option>
    </select>
  </div>
  <div class="fg"><span>Kernel</span>
    <select id="f-kern" onchange="applyFilters()">
      <option value="all">All sizes</option>
      <option value="3x3">3&times;3</option>
      <option value="5x5">5&times;5</option>
      <option value="3x5">3&times;5</option>
      <option value="7x7">7&times;7</option>
    </select>
  </div>
  <div class="fg"><span>Edge mode</span>
    <div class="rg">
      <label><input type="radio" name="fmode" value="all" checked onchange="applyFilters()"> All</label>
      <label><input type="radio" name="fmode" value="ZERO"      onchange="applyFilters()"> ZERO</label>
      <label><input type="radio" name="fmode" value="REPLICATE" onchange="applyFilters()"> REPLICATE</label>
      <label><input type="radio" name="fmode" value="TOROIDAL"  onchange="applyFilters()"> TOROIDAL</label>
    </div>
  </div>
  <div class="fg"><span>FLUSH</span>
    <select id="f-flush" onchange="applyFilters()">
      <option value="all">All</option>
      <option value="on">On</option>
      <option value="off">Off</option>
    </select>
  </div>
  <div class="fg"><span>Frame size</span>
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
  <hr class="sp">
  <div id="cfg-count"></div>
  <div id="config-list"></div>
</div>

<!-- Main -->
<div id="main">
  <!-- Mode bar -->
  <div id="modebar">
    <h1>conv2d</h1>
    <button class="mbtn act" id="btn-explore" onclick="setMode('explore')">&#128269; Explore</button>
    <button class="mbtn"     id="btn-simulate" onclick="setMode('simulate')">&#9654; Simulate</button>
    <span id="b-mode" class="badge"></span>
    <span id="b-flush" class="badge bf" style="display:none">FLUSH</span>
    <span id="toplbl"></span>
    <span id="pxinfo"></span>
  </div>

  <div id="content">
    <!-- Frame panel -->
    <div id="frame-panel" class="panel">

      <!-- Explore controls -->
      <div id="explore-ctrl">
        <div id="fnav">
          <button onclick="prevFrame()">&#9664;</button>
          <span id="flbl">Frame 0/2</span>
          <button onclick="nextFrame()">&#9654;</button>
          <span id="hint" style="font-size:10px;color:#3a4a5a">
            Click cell &middot; &uarr;&darr;&larr;&rarr; move &middot; , / . config &middot; Alt+&#8592;&#8594; frame
          </span>
        </div>
        <div id="fplay">
          <div id="fplay-title">&#9881; Flush player</div>
          <div id="ftl"></div>
          <div id="fctrl">
            <button onclick="fPrev()">&#9664;</button>
            <button id="btn-fplay" onclick="toggleFlushPlay()">&#9654; Play</button>
            <button onclick="fNext()">&#9654;</button>
            <button onclick="exitFlush()">&times; Real</button>
            <span id="fplbl"></span>
            <span style="margin-left:auto;font-size:11px;color:#6a7a8a">
              Speed: <select id="fspd">
                <option value="800">0.5&times;</option>
                <option value="400" selected>1&times;</option>
                <option value="200">2&times;</option>
                <option value="100">4&times;</option>
              </select>
            </span>
          </div>
        </div>
      </div>

      <!-- Simulate controls -->
      <div id="sim-ctrl" class="hidden">
        <div id="sim-header">
          <button class="sbtn" onclick="simGoFirst()" title="First">&#9198;</button>
          <button class="sbtn" onclick="simStepBck()" title="Step back">&#9664;</button>
          <button class="sbtn" id="btn-simplay" onclick="toggleSimPlay()">&#9654;</button>
          <button class="sbtn" onclick="simStepFwd()" title="Step forward">&#9654;</button>
          <button class="sbtn" onclick="simGoLast()" title="Last">&#9197;</button>
          <input type="range" id="scrubber" min="0" value="0" oninput="scrubTo(+this.value)">
          <span id="sim-step-lbl">Step 0 / 0</span>
          <span style="font-size:11px;color:#6a7a8a">
            Speed: <select id="spd">
              <option value="1000">&#189;&times;</option>
              <option value="500" selected>1&times;</option>
              <option value="250">2&times;</option>
              <option value="100">4&times;</option>
              <option value="40">10&times;</option>
              <option value="16">25&times;</option>
            </select>
          </span>
        </div>
        <div id="sim-pos-lbl"></div>
      </div>

      <!-- Shared frame canvas -->
      <div class="cwrap" id="frame-wrap">
        <canvas id="fc"></canvas>
      </div>
    </div>

    <!-- Right panel (pipeline + tap) -->
    <div id="right-panel">
      <!-- Pipeline (simulate mode only) -->
      <div id="pipeline-panel">
        <div id="pipe-title">Pipeline stages</div>
        <div class="pstage s-in"   id="ps-in">
          <div class="pstage-dot dot-in"></div>
          <div class="pstage-info">
            <div class="pstage-label">&#9654; ACCEPT (stage 1)</div>
            <div class="pstage-pos"  id="ps-in-pos">—</div>
            <div class="pstage-val"  id="ps-in-val"></div>
          </div>
          <div class="pstage-swatch" id="ps-in-sw"></div>
        </div>
        <div class="parrow">&#8595;</div>
        <div class="pstage s-dly"  id="ps-dly">
          <div class="pstage-dot dot-dly"></div>
          <div class="pstage-info">
            <div class="pstage-label">&#9670; DELAY (stage 2)</div>
            <div class="pstage-pos"  id="ps-dly-pos">—</div>
            <div class="pstage-val"  id="ps-dly-val"></div>
          </div>
          <div class="pstage-swatch" id="ps-dly-sw"></div>
        </div>
        <div class="parrow">&#8595;</div>
        <div class="pstage s-out"  id="ps-out">
          <div class="pstage-dot dot-out"></div>
          <div class="pstage-info">
            <div class="pstage-label">&#9646; OUTPUT (stage 3) &larr; current</div>
            <div class="pstage-pos"  id="ps-out-pos">—</div>
            <div class="pstage-val"  id="ps-out-val"></div>
          </div>
          <div class="pstage-swatch" id="ps-out-sw"></div>
        </div>
      </div>

      <!-- Tap window -->
      <div id="tap-panel">
        <div id="tp-title">Tap window</div>
        <div id="tp-coords"></div>
        <div class="tapwrap">
          <div id="tcol-labels"></div>
          <div id="trow-wrap">
            <div id="trow-labels"></div>
            <canvas id="tc"></canvas>
          </div>
        </div>
        <div id="legend"><div id="legend-title">Legend</div></div>
      </div>
    </div>
  </div>
</div>

<script>
'use strict';
const DATA = __DATA_JSON__;
const CFG  = DATA.configs;
const NTOT = CFG.length;

// Pipeline depth (accept → delay → output_reg = 3 clock cycles, but
// for display we show 3 stages: what's at the output NOW, what's in
// stage-2, and what's being accepted at the input).
const PIPE = 3;

// ── Color utilities ───────────────────────────────────────────────────────
function ylOrRd(t) {
  t = Math.max(0,Math.min(1,t));
  const s=[[255,255,178],[253,141,60],[189,0,38]];
  const i=t<.5?0:1, u=t<.5?t*2:(t-.5)*2;
  return s[i].map((v,k)=>Math.round(v+u*(s[i+1][k]-v)));
}
function rgb(c){return`rgb(${c[0]},${c[1]},${c[2]})`}
function lum(c){return(0.299*c[0]+0.587*c[1]+0.114*c[2])/255}
function txtClr(c){return lum(c)>.5?'#111':'#eee'}

// ── Explore state ─────────────────────────────────────────────────────────
const ex = {ci:0, fn:0, row:3, col:3, isFlush:false, fr:0,
             fplaying:false, ftimer:null};

// ── Sim state ─────────────────────────────────────────────────────────────
const sm = {step:0, seq:[], playing:false, timer:null};

let mode = 'explore'; // 'explore' | 'simulate'

// ── Build simulation sequence ─────────────────────────────────────────────
// Each element: {fn, isFlush, row, col, fr}
// Output event k = element k (matches golden vector order exactly).
// The pixel being ACCEPTED at the same physical clock is element k+2
// (pipeline depth=3: accepted at T, output at T+2 after two register stages).
function buildSeq(c) {
  const seq = [];
  for (let fn=0; fn<c.num_frames; fn++) {
    for (let r=0; r<c.frame_height; r++)
      for (let col=0; col<c.line_width; col++)
        seq.push({fn, isFlush:false, row:r, col, fr:-1});
    if (c.flush && c.kern_rows>1)
      for (let fr=0; fr<c.kern_rows-1; fr++)
        for (let col=0; col<c.line_width; col++)
          seq.push({fn, isFlush:true, row:-1, col, fr});
  }
  return seq;
}

function getTaps(c, evt) {
  if (!evt) return null;
  if (evt.isFlush) {
    const fi = evt.fr*c.line_width + evt.col;
    return c.flush_taps[evt.fn] && c.flush_taps[evt.fn][fi];
  }
  const pi = evt.row*c.line_width + evt.col;
  return c.taps[evt.fn] && c.taps[evt.fn][pi];
}

function getVal(c, evt) {
  if (!evt || evt.isFlush) return 0;
  return c.frames[evt.fn][evt.row*c.line_width + evt.col];
}

function evtLabel(c, evt, slot) {
  if (!evt) return slot < 0 ? '— (output not yet valid)' : '— (stream ended)';
  if (evt.isFlush) return `F${evt.fn} · flush row ${evt.fr} · col ${evt.col} · val=0`;
  return `F${evt.fn} · row ${evt.row} · col ${evt.col} · val=${getVal(c,evt)}`;
}

// ── Filters ───────────────────────────────────────────────────────────────
function applyFilters() {
  const dw   = document.getElementById('f-dw').value;
  const kern = document.getElementById('f-kern').value;
  const mode_ = document.querySelector('input[name="fmode"]:checked').value;
  const fl   = document.getElementById('f-flush').value;
  const fr   = document.getElementById('f-frame').value;
  let vis=0, firstVis=-1;
  document.querySelectorAll('.ci').forEach((el,i)=>{
    const c=CFG[i];
    const ok=(dw==='all'||String(c.data_width)===dw) &&
             (kern==='all'||`${c.kern_rows}x${c.kern_cols}`===kern) &&
             (mode_==='all'||c.edge_mode===mode_) &&
             (fl==='all'||(fl==='on')===c.flush) &&
             (fr==='all'||`${c.line_width}x${c.frame_height}`===fr);
    el.classList.toggle('hidden',!ok);
    if(ok){vis++;if(firstVis<0)firstVis=i;}
  });
  document.getElementById('cfg-count').textContent=`${vis} / ${NTOT} configs`;
  if(document.querySelector('.ci.active.hidden')&&firstVis>=0) selectCfg(firstVis);
}

function buildList() {
  const list=document.getElementById('config-list');
  CFG.forEach((c,i)=>{
    const el=document.createElement('div');
    el.className='ci'; el.dataset.i=i;
    el.innerHTML=`<span class="cn">CFG${String(i+1).padStart(2,'0')}</span> ${c.label}`;
    el.onclick=()=>selectCfg(i);
    list.appendChild(el);
  });
}

function selectCfg(i) {
  ex.ci=i; ex.fn=0; ex.isFlush=false; ex.fr=0;
  ex.fplaying=false; clearInterval(ex.ftimer);
  const c=CFG[i];
  ex.row=Math.min(ex.row,c.frame_height-1);
  ex.col=Math.min(ex.col,c.line_width-1);
  sm.step=0;
  sm.seq=buildSeq(c);
  sm.playing=false; clearInterval(sm.timer);

  const sc=document.getElementById('scrubber');
  sc.max=Math.max(0,sm.seq.length-1); sc.value=0;

  document.querySelectorAll('.ci').forEach(el=>el.classList.remove('active'));
  const el=document.querySelector(`.ci[data-i="${i}"]`);
  if(el){el.classList.add('active');el.scrollIntoView({block:'nearest'});}
  render();
}

// ── Mode switch ───────────────────────────────────────────────────────────
function setMode(m) {
  mode=m;
  document.getElementById('btn-explore') .classList.toggle('act',m==='explore');
  document.getElementById('btn-simulate').classList.toggle('act',m==='simulate');
  document.getElementById('explore-ctrl').classList.toggle('hidden',m!=='explore');
  document.getElementById('sim-ctrl')    .classList.toggle('hidden',m!=='simulate');
  document.getElementById('pipeline-panel').classList.toggle('vis', m==='simulate');
  if(m==='simulate'&&sm.seq.length===0) sm.seq=buildSeq(CFG[ex.ci]);
  render();
}

// ── Explore: frame nav ────────────────────────────────────────────────────
function prevFrame(){const nf=CFG[ex.ci].num_frames;ex.fn=(ex.fn-1+nf)%nf;ex.isFlush=false;render();}
function nextFrame(){const nf=CFG[ex.ci].num_frames;ex.fn=(ex.fn+1)%nf;ex.isFlush=false;render();}

// ── Explore: flush player ─────────────────────────────────────────────────
function buildFlushTL() {
  const c=CFG[ex.ci], tl=document.getElementById('ftl');
  tl.innerHTML='';
  if(!c.flush||c.kern_rows<=1) return;
  for(let r=0;r<c.frame_height;r++){
    const el=document.createElement('div');
    el.className='tr real'; el.textContent=r;
    el.style.fontSize=c.frame_height>9?'9px':'10px';
    el.title=`Real row ${r}`;
    const _r=r; el.onclick=()=>{ex.isFlush=false;ex.row=_r;render();};
    tl.appendChild(el);
  }
  const sep=document.createElement('span');
  sep.className='tlsep'; sep.textContent='│'; tl.appendChild(sep);
  for(let f=0;f<c.kern_rows-1;f++){
    const el=document.createElement('div');
    el.className='tr flush-tl'; el.textContent=`F${f}`;
    el.title=`Flush row ${f} (virtual row ${c.frame_height+f})`;
    const _f=f; el.onclick=()=>{ex.isFlush=true;ex.fr=_f;render();};
    tl.appendChild(el);
  }
}
function updateFlushTL(){
  document.querySelectorAll('.tr.real'   ).forEach((el,i)=>el.classList.toggle('act',!ex.isFlush&&i===ex.row));
  document.querySelectorAll('.tr.flush-tl').forEach((el,i)=>el.classList.toggle('act', ex.isFlush&&i===ex.fr));
  const c=CFG[ex.ci];
  document.getElementById('fplbl').textContent=ex.isFlush
    ?`Flush row ${ex.fr} · virtual row ${c.frame_height+ex.fr}`:`Real row ${ex.row}`;
}
function updateFlushPlayer(){
  const c=CFG[ex.ci], fp=document.getElementById('fplay');
  if(c.flush&&c.kern_rows>1){fp.classList.add('vis');buildFlushTL();updateFlushTL();}
  else{fp.classList.remove('vis');ex.isFlush=false;ex.fplaying=false;clearInterval(ex.ftimer);}
}
function totalExRows(){const c=CFG[ex.ci];return c.frame_height+(c.flush&&c.kern_rows>1?c.kern_rows-1:0);}
function exCurPos(){const c=CFG[ex.ci];return ex.isFlush?c.frame_height+ex.fr:ex.row;}
function exSetPos(pos){const c=CFG[ex.ci];if(pos<c.frame_height){ex.isFlush=false;ex.row=pos;}else{ex.isFlush=true;ex.fr=pos-c.frame_height;}}
function fNext(){exSetPos((exCurPos()+1)%totalExRows());render();}
function fPrev(){exSetPos((exCurPos()-1+totalExRows())%totalExRows());render();}
function exitFlush(){ex.isFlush=false;render();}
function toggleFlushPlay(){
  ex.fplaying=!ex.fplaying;
  document.getElementById('btn-fplay').textContent=ex.fplaying?'⏸ Pause':'▶ Play';
  clearInterval(ex.ftimer);
  if(ex.fplaying) ex.ftimer=setInterval(fNext,+document.getElementById('fspd').value);
}
document.getElementById('fspd').onchange=()=>{
  if(ex.fplaying){clearInterval(ex.ftimer);ex.ftimer=setInterval(fNext,+document.getElementById('fspd').value);}
};

// ── Sim controls ──────────────────────────────────────────────────────────
function simStepFwd(){if(sm.step<sm.seq.length-1){sm.step++;render();}}
function simStepBck(){if(sm.step>0){sm.step--;render();}}
function simGoFirst(){sm.step=0;render();}
function simGoLast(){sm.step=Math.max(0,sm.seq.length-1);render();}
function scrubTo(v){sm.step=v;render();}
function toggleSimPlay(){
  sm.playing=!sm.playing;
  document.getElementById('btn-simplay').textContent=sm.playing?'⏸':'▶';
  clearInterval(sm.timer);
  if(sm.playing){
    sm.timer=setInterval(()=>{
      if(sm.step>=sm.seq.length-1){sm.playing=false;document.getElementById('btn-simplay').textContent='▶';clearInterval(sm.timer);}
      else{sm.step++;render();}
    },+document.getElementById('spd').value);
  }
}
document.getElementById('spd').onchange=()=>{
  if(sm.playing){clearInterval(sm.timer);sm.timer=setInterval(()=>{
    if(sm.step>=sm.seq.length-1){sm.playing=false;clearInterval(sm.timer);}
    else{sm.step++;render();}
  },+document.getElementById('spd').value);}
};

// ── Cell sizing ───────────────────────────────────────────────────────────
function csz(maxDim,avail){return Math.max(14,Math.min(64,Math.floor(avail/maxDim)));}

// ── Hatch fill ────────────────────────────────────────────────────────────
function hatch(ctx,x,y,w,h){
  ctx.save();ctx.beginPath();ctx.rect(x,y,w,h);ctx.clip();
  ctx.strokeStyle='rgba(255,255,255,0.06)';ctx.lineWidth=1;
  for(let d=-h;d<w+h;d+=7){ctx.beginPath();ctx.moveTo(x+d,y);ctx.lineTo(x+d+h,y+h);ctx.stroke();}
  ctx.restore();
}

// ── Frame canvas ──────────────────────────────────────────────────────────
// highlights: array of {row, col, isFlush, fr, style:'out'|'dly'|'in'|'ex'}
// kernelAnchor: {row,col,isFlush,fr} or null
function renderFrameCanvas(c, frameIdx, flushR, highlights, kernelAnchor) {
  const lw=c.line_width, fh=c.frame_height, kr=c.kern_rows, kc=c.kern_cols, dw=c.data_width;
  const vmax=(1<<Math.min(dw,30))-1;
  const totR=fh+flushR;

  const wrap=document.getElementById('frame-wrap');
  const avW=wrap.clientWidth||400;
  const cell=csz(Math.max(lw,totR,kr+1),Math.min(avW,600));

  const can=document.getElementById('fc');
  can.width=lw*cell; can.height=totR*cell;
  const ctx=can.getContext('2d');

  const frame=c.frames[frameIdx]||[];

  // Real cells
  for(let r=0;r<fh;r++) for(let col=0;col<lw;col++){
    const val=frame[r*lw+col]||0;
    const clr=ylOrRd(val/vmax);
    ctx.fillStyle=rgb(clr); ctx.fillRect(col*cell,r*cell,cell,cell);
    ctx.strokeStyle='rgba(0,0,0,0.18)';ctx.lineWidth=.5;
    ctx.strokeRect(col*cell+.5,r*cell+.5,cell-1,cell-1);
    if(cell>=18){
      ctx.fillStyle=txtClr(clr);
      ctx.font=`${Math.max(8,cell/4.5)|0}px monospace`;
      ctx.textAlign='center';ctx.textBaseline='middle';
      const hex=dw>8?`0x${val.toString(16).toUpperCase().padStart(Math.ceil(dw/4),'0')}`:`${val}`;
      ctx.fillText(hex,col*cell+cell/2,r*cell+cell/2);
    }
  }

  // Flush virtual rows
  for(let f=0;f<flushR;f++){
    const r=fh+f;
    for(let col=0;col<lw;col++){
      ctx.fillStyle='#1a1a2e'; ctx.fillRect(col*cell,r*cell,cell,cell);
      hatch(ctx,col*cell,r*cell,cell,cell);
      ctx.strokeStyle='rgba(100,100,150,0.3)';ctx.lineWidth=.5;
      ctx.strokeRect(col*cell+.5,r*cell+.5,cell-1,cell-1);
      if(cell>=16){
        ctx.fillStyle='#4a4a6a';
        ctx.font=`${Math.max(7,cell/5)|0}px monospace`;
        ctx.textAlign='center';ctx.textBaseline='middle';
        ctx.fillText(`F${f}`,col*cell+cell/2,r*cell+cell/2);
      }
    }
  }
  if(flushR>0){
    ctx.setLineDash([5,3]);ctx.strokeStyle='#dfb050';ctx.lineWidth=1.5;
    ctx.strokeRect(.5,.5,lw*cell-1,fh*cell-1);ctx.setLineDash([]);
  }

  // Kernel footprint
  if(kernelAnchor){
    const ka=kernelAnchor;
    const curR=ka.isFlush?fh+ka.fr:ka.row;
    const topR=curR-(kr-1), leftC=ka.col-(kc-1);
    ctx.fillStyle='rgba(58,111,220,0.18)';
    for(let dr=0;dr<kr;dr++) for(let dc=0;dc<kc;dc++){
      const pr=topR+dr, pc=leftC+dc;
      if(pr>=0&&pr<totR&&pc>=0&&pc<lw) ctx.fillRect(pc*cell+1,pr*cell+1,cell-2,cell-2);
    }
    ctx.strokeStyle='rgba(58,111,220,0.4)';ctx.lineWidth=.8;
    for(let dr=0;dr<kr;dr++) for(let dc=0;dc<kc;dc++){
      const pr=topR+dr, pc=leftC+dc;
      if(pr>=0&&pr<totR&&pc>=0&&pc<lw) ctx.strokeRect(pc*cell+1,pr*cell+1,cell-2,cell-2);
    }
  }

  // Per-highlight markers
  const styles={
    ex: {stroke:'#3a8fff',fill:'rgba(58,143,255,0)',circle:true},
    out:{stroke:'#3a8fff',fill:'rgba(58,143,255,0.15)',circle:false},
    dly:{stroke:'#dfb050',fill:'rgba(223,176,80,0.15)',circle:false},
    in: {stroke:'#3abf7a',fill:'rgba(58,191,122,0.2)',circle:false},
  };
  for(const h of highlights){
    const canRow=h.isFlush?fh+h.fr:h.row;
    if(canRow<0||canRow>=totR||h.col<0||h.col>=lw) continue;
    const st=styles[h.style]||styles.ex;
    const x=h.col*cell, y=canRow*cell;
    ctx.fillStyle=st.fill; ctx.fillRect(x+2,y+2,cell-4,cell-4);
    ctx.strokeStyle=st.stroke; ctx.lineWidth=2.5;
    if(st.circle){
      ctx.beginPath();ctx.arc(x+cell/2,y+cell/2,cell*.32,0,2*Math.PI);ctx.stroke();
    } else {
      ctx.strokeRect(x+2,y+2,cell-4,cell-4);
    }
  }
}

// ── Tap canvas ────────────────────────────────────────────────────────────
function renderTapCanvas(c, taps, curRow, curCol) {
  const kr=c.kern_rows, kc=c.kern_cols, dw=c.data_width, mode=c.edge_mode;
  const fh=c.frame_height, lw=c.line_width;
  const vmax=(1<<Math.min(dw,30))-1;
  if(!taps) return;

  const tpW=document.getElementById('tap-panel').clientWidth-24-28;
  const cell=csz(Math.max(kr,kc),Math.min(tpW,300));

  // Column labels
  const colDiv=document.getElementById('tcol-labels');
  colDiv.innerHTML='';
  for(let dc=0;dc<kc;dc++){
    const srcCol=curCol-(kc-1-dc);
    const oob=srcCol<0||srcCol>=lw;
    const el=document.createElement('div');
    el.className='tlbl'; el.style.width=cell+'px'; el.style.height='14px';
    el.style.color=oob?'#df5050':'#5a7a8a'; el.textContent=srcCol;
    colDiv.appendChild(el);
  }
  // Row labels
  const rowDiv=document.getElementById('trow-labels');
  rowDiv.innerHTML='';
  for(let r=0;r<kr;r++){
    const srcRow=curRow-(kr-1-r);
    const oob=srcRow<0||srcRow>=fh;
    const el=document.createElement('div');
    el.className='tlbl'; el.style.height=cell+'px'; el.style.width='24px';
    el.style.color=oob?'#df5050':'#5a7a8a';
    el.textContent=srcRow<0?srcRow:srcRow>=fh?`F${srcRow-fh}`:srcRow;
    rowDiv.appendChild(el);
  }

  const can=document.getElementById('tc');
  can.width=kc*cell; can.height=kr*cell;
  const ctx=can.getContext('2d');

  for(let r=0;r<kr;r++) for(let dc=0;dc<kc;dc++){
    const col=kc-1-dc;
    const val=taps[r*kc+col];
    const norm=val/vmax;
    const srcRow=curRow-(kr-1-r), srcCol=curCol-col;
    const isOob=srcRow<0||srcRow>=fh||srcCol<0||srcCol>=lw;
    const isFlushedSrc=srcRow>=fh;

    let face,edge;
    if(isOob&&mode==='ZERO')     {face=[180,180,190];edge=null;}
    else if(isOob&&mode==='REPLICATE'){face=ylOrRd(norm);edge='#4682b4';}
    else if(isOob&&mode==='TOROIDAL') {face=ylOrRd(norm);edge='#9370db';}
    else                          {face=ylOrRd(norm);edge=null;}

    ctx.fillStyle=rgb(face); ctx.fillRect(dc*cell,r*cell,cell,cell);
    if(isFlushedSrc) hatch(ctx,dc*cell,r*cell,cell,cell);

    if(edge){ctx.strokeStyle=edge;ctx.lineWidth=2.5;ctx.strokeRect(dc*cell+1.5,r*cell+1.5,cell-3,cell-3);}
    else{ctx.strokeStyle='rgba(0,0,0,0.2)';ctx.lineWidth=.5;ctx.strokeRect(dc*cell+.5,r*cell+.5,cell-1,cell-1);}

    if(cell>=16){
      ctx.fillStyle=txtClr(face);
      ctx.font=`${Math.max(8,cell/4.5)|0}px monospace`;
      ctx.textAlign='center';ctx.textBaseline='middle';
      const hex=dw>8?`0x${val.toString(16).toUpperCase().padStart(Math.ceil(dw/4),'0')}`:`${val}`;
      ctx.fillText(hex+(isOob?'*':''),dc*cell+cell/2,r*cell+cell/2);
    }
  }
  // Current pixel (bottom-right in display)
  ctx.strokeStyle='#3a8fff';ctx.lineWidth=3;
  ctx.strokeRect((kc-1)*cell+2,(kr-1)*cell+2,cell-4,cell-4);
}

// ── Legend ────────────────────────────────────────────────────────────────
function renderLegend(c, simMode) {
  const entries=[];
  if(simMode){
    entries.push({sw:'background:none;border:2px solid #3abf7a',txt:'&#9654; ACCEPT: pixel currently entering pipeline (green)'});
    entries.push({sw:'background:none;border:2px solid #dfb050',txt:'&#9670; DELAY: pixel in delay register (amber)'});
    entries.push({sw:'background:rgba(58,143,255,.15);border:2px solid #3a8fff',txt:'&#9646; OUTPUT: pixel producing this tap window (blue)'});
  }
  entries.push({sw:'background:#b4b4c2;border:none',txt:'OOB tap &rarr; 0 (zero-extend)'});
  if(c.edge_mode==='REPLICATE')
    entries.push({sw:'background:#fd8d3c;border:2px solid #4682b4',txt:'* OOB &rarr; clamped edge pixel (REPLICATE)'});
  if(c.edge_mode==='TOROIDAL')
    entries.push({sw:'background:#fd8d3c;border:2px solid #9370db',txt:'* OOB &rarr; causal wrap (TOROIDAL)'});
  if(c.flush&&c.kern_rows>1)
    entries.push({sw:'background:#1a1a2e;border:2px dashed #dfb050',txt:'Hatched = injected zero row (FLUSH)'});
  entries.push({sw:'background:none;border:2.5px solid #3a8fff',txt:'Blue square = current pixel (bottom-right tap)'});

  document.getElementById('legend').innerHTML='<div id="legend-title">Legend</div>'+
    entries.map(e=>`<div class="lrow"><div class="lsw" style="${e.sw}"></div><span>${e.txt}</span></div>`).join('');
}

// ── Pipeline panel ────────────────────────────────────────────────────────
function renderPipeline(c, seq, step) {
  const vmax=(1<<Math.min(c.data_width,30))-1;
  // stage indices relative to current output step:
  //   output  = step   (what's valid at stage-3 output reg)
  //   delay   = step+1 (what was accepted 1 cycle after the output pixel — now in delay reg)
  //   accept  = step+2 (what was accepted 2 cycles after the output pixel — now at input stage)
  const outEvt  = seq[step];
  const dlyEvt  = seq[step+1];
  const inEvt   = seq[step+2];

  function fill(id_pos, id_val, id_sw, evt, isSim) {
    document.getElementById(id_pos).textContent = evtLabel(c, evt, isSim?1:-1);
    const val = getVal(c, evt);
    document.getElementById(id_val).textContent = evt
      ? (evt.isFlush?`val = 0 (flush zero)`:`val = ${val}  (${Math.round(val/vmax*100)}%)`)
      : '';
    const sw = document.getElementById(id_sw);
    if(evt && !evt.isFlush) {
      const clr = ylOrRd(val/vmax);
      sw.style.background = rgb(clr);
    } else {
      sw.style.background = evt ? '#252535' : '#1a1a2e';
    }
    sw.style.borderColor = evt ? '#3a3a5a' : 'transparent';
  }

  fill('ps-out-pos','ps-out-val','ps-out-sw', outEvt, false);
  fill('ps-dly-pos','ps-dly-val','ps-dly-sw', dlyEvt, true);
  fill('ps-in-pos', 'ps-in-val', 'ps-in-sw',  inEvt,  true);
}

// ── Top bar ───────────────────────────────────────────────────────────────
function renderTopBar(c, ci) {
  const bm=document.getElementById('b-mode');
  bm.textContent=c.edge_mode;
  bm.className='badge '+{ZERO:'bz',REPLICATE:'br',TOROIDAL:'bt'}[c.edge_mode];
  document.getElementById('b-flush').style.display=c.flush?'':'none';
  document.getElementById('toplbl').textContent=
    `CFG${String(ci+1).padStart(2,'0')} · ${c.data_width}b · ${c.kern_rows}×${c.kern_cols} · ${c.line_width}×${c.frame_height}px`;
}

// ── Main render ───────────────────────────────────────────────────────────
function render() {
  const c = CFG[ex.ci];
  const ci = ex.ci;
  renderTopBar(c, ci);

  if (mode === 'explore') {
    // Frame nav label
    document.getElementById('flbl').textContent=`Frame ${ex.fn} / ${c.num_frames-1}`;
    updateFlushPlayer();

    // Frame canvas
    const flushR = c.flush&&c.kern_rows>1 ? c.kern_rows-1 : 0;
    const highlights = [{
      row:ex.isFlush?-1:ex.row, col:ex.col,
      isFlush:ex.isFlush, fr:ex.fr, style:'ex'
    }];
    renderFrameCanvas(c, ex.fn, flushR, highlights,
      {row:ex.row, col:ex.col, isFlush:ex.isFlush, fr:ex.fr});

    // Tap
    const curRow = ex.isFlush ? c.frame_height+ex.fr : ex.row;
    let taps;
    if(ex.isFlush){taps=c.flush_taps[ex.fn]&&c.flush_taps[ex.fn][ex.fr*c.line_width+ex.col];}
    else{taps=c.taps[ex.fn]&&c.taps[ex.fn][ex.row*c.line_width+ex.col];}
    renderTapCanvas(c, taps, curRow, ex.col);

    document.getElementById('tp-title').textContent=
      ex.isFlush?`Kernel @ flush row ${ex.fr} (virtual ${c.frame_height+ex.fr})`
                :`Kernel @ (row ${ex.row}, col ${ex.col})`;
    document.getElementById('tp-coords').textContent=
      `Frame ${ex.fn} · ${c.edge_mode}`;
    document.getElementById('pxinfo').textContent=
      ex.isFlush?`▶ flush F${ex.fr}, col ${ex.col}`:`px (${ex.row},${ex.col})`;
    renderLegend(c, false);

  } else {
    // ── Simulate mode ──
    const seq = sm.seq;
    if(!seq.length){document.getElementById('sim-pos-lbl').textContent='No data';return;}
    const step = sm.step;
    const outEvt = seq[step];
    const dlyEvt = seq[step+1];
    const inEvt  = seq[step+2];

    // Scrubber
    document.getElementById('scrubber').value=step;
    document.getElementById('sim-step-lbl').textContent=`Step ${step+1} / ${seq.length}`;

    // Position label
    function posStr(evt) {
      if(!evt) return '—';
      if(evt.isFlush) return `F${evt.fn} flush-row ${evt.fr} col ${evt.col}`;
      return `F${evt.fn} row ${evt.row} col ${evt.col}`;
    }
    document.getElementById('sim-pos-lbl').textContent=
      `OUT: ${posStr(outEvt)}   DLY: ${posStr(dlyEvt)}   IN: ${posStr(inEvt)}`;
    document.getElementById('pxinfo').textContent=`step ${step+1}/${seq.length}`;

    // Frame: show the frame for the OUTPUT event (what's currently at output)
    const showFn = outEvt ? outEvt.fn : 0;
    const flushR = c.flush&&c.kern_rows>1 ? c.kern_rows-1 : 0;

    // Build highlights for all 3 pipeline stages (only those in same frame)
    const highlights = [];
    // output stage (blue filled square)
    if(outEvt) highlights.push({row:outEvt.isFlush?-1:outEvt.row, col:outEvt.col,
      isFlush:outEvt.isFlush, fr:outEvt.fr, style:'out'});
    // delay stage (amber square) — only if same frame
    if(dlyEvt&&dlyEvt.fn===showFn) highlights.push({row:dlyEvt.isFlush?-1:dlyEvt.row, col:dlyEvt.col,
      isFlush:dlyEvt.isFlush, fr:dlyEvt.fr, style:'dly'});
    // accept stage (green square) — only if same frame
    if(inEvt&&inEvt.fn===showFn) highlights.push({row:inEvt.isFlush?-1:inEvt.row, col:inEvt.col,
      isFlush:inEvt.isFlush, fr:inEvt.fr, style:'in'});

    renderFrameCanvas(c, showFn, flushR, highlights,
      outEvt ? {row:outEvt.row, col:outEvt.col, isFlush:outEvt.isFlush, fr:outEvt.fr} : null);

    // Pipeline diagram
    renderPipeline(c, seq, step);

    // Tap for output event
    const curRow = outEvt ? (outEvt.isFlush?c.frame_height+outEvt.fr:outEvt.row) : 0;
    const curCol = outEvt ? outEvt.col : 0;
    const taps   = outEvt ? getTaps(c, outEvt) : null;
    renderTapCanvas(c, taps, curRow, curCol);

    document.getElementById('tp-title').textContent=
      outEvt&&outEvt.isFlush?`Output @ flush-row ${outEvt.fr} (virtual ${c.frame_height+outEvt.fr})`
                             :`Output @ (row ${curRow}, col ${curCol})`;
    document.getElementById('tp-coords').textContent=
      `Frame ${showFn} · ${c.edge_mode}`;
    renderLegend(c, true);
  }
}

// ── Frame canvas click ────────────────────────────────────────────────────
document.getElementById('fc').addEventListener('click', function(e) {
  if(mode!=='explore') return;
  const c=CFG[ex.ci], lw=c.line_width, fh=c.frame_height;
  const flushR=c.flush&&c.kern_rows>1?c.kern_rows-1:0;
  const rect=this.getBoundingClientRect();
  const cell=this.width/lw;
  const col=Math.floor((e.clientX-rect.left)/cell);
  const row=Math.floor((e.clientY-rect.top)/cell);
  if(col<0||col>=lw||row<0||row>=fh+flushR) return;
  ex.col=col;
  if(row<fh){ex.isFlush=false;ex.row=row;}else{ex.isFlush=true;ex.fr=row-fh;}
  render();
});

// ── Keyboard ──────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if(e.target.tagName==='SELECT'||e.target.tagName==='INPUT') return;
  const c=CFG[ex.ci]; let handled=true;

  if(mode==='explore'){
    const fh=c.frame_height, lw=c.line_width;
    if(e.altKey&&e.key==='ArrowLeft')  prevFrame();
    else if(e.altKey&&e.key==='ArrowRight') nextFrame();
    else if(e.key==='ArrowLeft')  {ex.col=Math.max(0,ex.col-1);render();}
    else if(e.key==='ArrowRight') {ex.col=Math.min(lw-1,ex.col+1);render();}
    else if(e.key==='ArrowUp')    {if(!ex.isFlush)ex.row=Math.max(0,ex.row-1);render();}
    else if(e.key==='ArrowDown')  {if(!ex.isFlush)ex.row=Math.min(fh-1,ex.row+1);render();}
    else if(e.key===',')          selectCfg(Math.max(0,ex.ci-1));
    else if(e.key==='.')          selectCfg(Math.min(NTOT-1,ex.ci+1));
    else if((e.key==='f'||e.key==='F')&&c.flush&&c.kern_rows>1) fNext();
    else if(e.key==='r'||e.key==='R') exitFlush();
    else if(e.key===' '&&c.flush&&c.kern_rows>1) toggleFlushPlay();
    else handled=false;
  } else {
    if(e.key==='ArrowRight'||e.key==='ArrowDown') simStepFwd();
    else if(e.key==='ArrowLeft'||e.key==='ArrowUp') simStepBck();
    else if(e.key==='Home') simGoFirst();
    else if(e.key==='End')  simGoLast();
    else if(e.key===' ')   toggleSimPlay();
    else if(e.key===',')   selectCfg(Math.max(0,ex.ci-1));
    else if(e.key==='.')   selectCfg(Math.min(NTOT-1,ex.ci+1));
    else handled=false;
  }
  if(handled) e.preventDefault();
});

window.addEventListener('resize', render);

// ── Init ──────────────────────────────────────────────────────────────────
buildList();
applyFilters();
selectCfg(3); // CFG04 = 8b 3×3 REPLICATE FLUSH=on
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
