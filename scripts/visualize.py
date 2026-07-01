#!/usr/bin/env python3
"""
Generate a self-contained interactive HTML visualiser for conv2d golden vectors.

Usage:
    python scripts/visualize.py [--out tb/visualize.html] [--vec-dir tb/vectors]

Produces a single HTML file with all vector data embedded as JSON.
Open in any browser — no server required.

Modes
-----
Explore   : click any pixel in the frame to inspect its centred tap window.
Simulate  : step through every output event. Shows which pixel was accepted
            1 clock ago to trigger that output (the TRIGGER pixel).
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
        half_r = (kr - 1) // 2
        half_c = (kc - 1) // 2
        flush_out_rows = []
        if fl and half_r > 0:
            for flush_row in range(half_r):
                out_r = fh + flush_row - half_r
                if out_r >= 0:
                    flush_out_rows.append(out_r)
        real_out_per_frame  = max(0, fh - half_r) * lw
        flush_out_per_frame = len(flush_out_rows) * lw
        tppf = real_out_per_frame + flush_out_per_frame
        ip = os.path.join(vec_dir, pre + "input.txt")
        ep = os.path.join(vec_dir, pre + "expected.txt")
        if not os.path.exists(ip) or not os.path.exists(ep):
            out.append(None); continue
        with open(ip) as f:
            pxs = [int(x) for x in f if x.strip()]
        frames = [pxs[n * fh * lw:(n + 1) * fh * lw] for n in range(nf)]
        with open(ep) as f:
            trows = [list(map(int, x.split())) for x in f if x.strip()]
        taps, flsh = [], []
        for n in range(nf):
            b = n * tppf
            taps.append(trows[b:b + real_out_per_frame])
            flsh.append(trows[b + real_out_per_frame:b + tppf] if flush_out_per_frame else [])
        out.append({'frames': frames, 'taps': taps, 'flush_taps': flsh,
                    'flush_out_rows': flush_out_rows})
    return out


def build_json(vd):
    cs = []
    for idx, cfg in enumerate(CONFIGS):
        e = {k: bool(v) if k == 'flush' else v for k, v in cfg.items()}
        e['idx']   = idx
        e['label'] = cfg_label(cfg)
        kr, kc     = cfg['kern_rows'], cfg['kern_cols']
        e['half_r'] = (kr - 1) // 2
        e['half_c'] = (kc - 1) // 2
        d = vd[idx]
        e['frames']         = d['frames']         if d else []
        e['taps']           = d['taps']           if d else []
        e['flush_taps']     = d['flush_taps']     if d else []
        e['flush_out_rows'] = d['flush_out_rows'] if d else []
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
#sidebar{width:200px;min-width:160px;background:#1a1a2e;display:flex;
  flex-direction:column;padding:8px 6px;gap:5px;overflow-y:auto;
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
#main{flex:1;display:flex;flex-direction:column;overflow:hidden;padding:6px;gap:4px;min-width:0}
#modebar{display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap}
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
#content{flex:1;display:flex;gap:6px;overflow:hidden;min-height:0}
.panel{background:#1a1a2e;border-radius:8px;padding:6px;
  display:flex;flex-direction:column;gap:4px;overflow:hidden}
#frame-panel{flex:1;min-width:0}
#right-panel{width:300px;min-width:220px;display:flex;flex-direction:column;gap:6px}
#explore-ctrl,#sim-ctrl{display:flex;flex-direction:column;gap:4px}
#explore-ctrl.hidden,#sim-ctrl.hidden{display:none}
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
.tr.flush-tl{background:#1e1e2e;color:#dfb050;border-style:dashed;border-color:#5a4a20}
.tr.flush-tl:hover{background:#252520}
.tr.act{border-color:#dfb050!important;box-shadow:0 0 0 1px #dfb050}
.tlsep{color:#3a4a5a;font-size:16px;margin:0 2px;line-height:24px}
#fctrl{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
#fplbl{font-size:11px;color:#9090a0}
#sim-header{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
#sim-step-lbl{font-size:12px;color:#9ab0c0;min-width:110px}
#sim-pos-lbl{font-size:11px;color:#7a8a9a}
.sbtn{background:#1e2840;border:1px solid #2a3a5a;color:#c0d0e0;
  border-radius:4px;padding:3px 9px;cursor:pointer;font-size:14px;line-height:1}
.sbtn:hover{background:#3a6fdc;border-color:#3a6fdc}
#scrubber{flex:1;min-width:80px;accent-color:#3a6fdc;cursor:pointer;height:4px}
.cwrap{overflow:auto;flex:1;min-height:0}
canvas{display:block;image-rendering:pixelated;cursor:crosshair}
/* Pipeline panel */
#pipeline-panel{display:none;flex-direction:column;gap:3px;
  background:#141428;border-radius:6px;padding:6px;flex-shrink:0}
#pipeline-panel.vis{display:flex}
#pipe-title{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#4a5a6a}
.pstage{display:flex;align-items:center;gap:6px;padding:5px 8px;
  border-radius:5px;border:1px solid #252540;background:#1a1a2e}
.pstage.s-out{border-color:#3a6fdc}
.pstage.s-trg{border-color:#1a6a30}
.pstage-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot-out{background:#3a8fff}
.dot-trg{background:#3abf7a}
.pstage-info{flex:1;min-width:0}
.pstage-label{font-size:10px;font-weight:700;color:#7a8a9a}
.pstage-pos{font-size:11px;color:#c0d0e0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pstage-val{font-size:10px;color:#7a8a9a;font-family:monospace}
.pstage-swatch{width:28px;height:28px;border-radius:3px;flex-shrink:0;border:1px solid #252540}
.parrow{text-align:center;font-size:11px;color:#3a8a5a;line-height:1.6;
  font-style:italic;padding:1px 0}
/* Tap panel */
#tap-panel{flex:1;display:flex;flex-direction:column;gap:3px;
  background:#141428;border-radius:6px;padding:6px;min-height:0;overflow:hidden}
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

<div id="main">
  <div id="modebar">
    <h1>conv2d</h1>
    <button class="mbtn act" id="btn-explore"  onclick="setMode('explore')">&#128269; Explore</button>
    <button class="mbtn"     id="btn-simulate" onclick="setMode('simulate')">&#9654; Simulate</button>
    <span id="b-mode" class="badge"></span>
    <span id="b-flush" class="badge bf" style="display:none">FLUSH</span>
    <span id="toplbl"></span>
    <span id="pxinfo"></span>
  </div>

  <div id="content">
    <div id="frame-panel" class="panel">
      <!-- Explore controls -->
      <div id="explore-ctrl">
        <div id="fnav">
          <button onclick="prevFrame()">&#9664;</button>
          <span id="flbl">Frame 0/2</span>
          <button onclick="nextFrame()">&#9654;</button>
          <span id="hint">Click pixel &middot; &uarr;&darr;&larr;&rarr; move &middot; , / . config &middot; Alt+&#8592;&#8594; frame</span>
        </div>
        <div id="fplay">
          <div id="fplay-title">&#9881; Flush outputs (bottom-edge pixels)</div>
          <div id="ftl"></div>
          <div id="fctrl">
            <button onclick="fPrev()">&#9664;</button>
            <button id="btn-fplay" onclick="toggleFlushPlay()">&#9654; Play</button>
            <button onclick="fNext()">&#9654;</button>
            <button onclick="exitFlush()">&times; Real</button>
            <span id="fplbl"></span>
            <span style="margin-left:auto;font-size:11px;color:#6a7a8a">Speed:
              <select id="fspd">
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
          <button class="sbtn" onclick="simGoFirst()">&#9198;</button>
          <button class="sbtn" onclick="simStepBck()">&#9664;</button>
          <button class="sbtn" id="btn-simplay" onclick="toggleSimPlay()">&#9654;</button>
          <button class="sbtn" onclick="simStepFwd()">&#9654;</button>
          <button class="sbtn" onclick="simGoLast()">&#9197;</button>
          <input type="range" id="scrubber" min="0" value="0" oninput="scrubTo(+this.value)">
          <span id="sim-step-lbl">Step 0 / 0</span>
          <span style="font-size:11px;color:#6a7a8a">Speed:
            <select id="spd">
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

      <div class="cwrap" id="frame-wrap"><canvas id="fc"></canvas></div>
    </div>

    <div id="right-panel">
      <!-- Pipeline (simulate only) -->
      <div id="pipeline-panel">
        <div id="pipe-title">Pipeline — each step = 1 output clock</div>
        <div class="pstage s-trg" id="ps-trg">
          <div class="pstage-dot dot-trg"></div>
          <div class="pstage-info">
            <div class="pstage-label">&#9654; ACCEPT — trigger pixel accepted 2 clocks ago (green)</div>
            <div class="pstage-pos" id="ps-trg-pos">—</div>
            <div class="pstage-val" id="ps-trg-val"></div>
          </div>
          <div class="pstage-swatch" id="ps-trg-sw"></div>
        </div>
        <div class="parrow">&#8595; 2 register stages later &rarr; output valid</div>
        <div class="pstage s-out" id="ps-out">
          <div class="pstage-dot dot-out"></div>
          <div class="pstage-info">
            <div class="pstage-label">&#9646; OUTPUT — window now at output port (blue)</div>
            <div class="pstage-pos" id="ps-out-pos">—</div>
            <div class="pstage-val" id="ps-out-val"></div>
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

// ── Color utilities ───────────────────────────────────────────────────────
function ylOrRd(t){
  t=Math.max(0,Math.min(1,t));
  const s=[[255,255,178],[253,141,60],[189,0,38]];
  const i=t<.5?0:1,u=t<.5?t*2:(t-.5)*2;
  return s[i].map((v,k)=>Math.round(v+u*(s[i+1][k]-v)));
}
function rgb(c){return`rgb(${c[0]},${c[1]},${c[2]})`}
function lum(c){return(0.299*c[0]+0.587*c[1]+0.114*c[2])/255}
function txtClr(c){return lum(c)>.5?'#111':'#eee'}

// ── State ─────────────────────────────────────────────────────────────────
const ex={ci:0,fn:0,row:3,col:3,isFlush:false,fr:0,fplaying:false,ftimer:null};
const sm={step:0,seq:[],playing:false,timer:null};
let mode='explore';

// ── Sequence builder ──────────────────────────────────────────────────────
// Each element: {fn, out_r, out_c, is_flush, flush_idx}
function buildSeq(c){
  const seq=[];
  const lw=c.line_width,fh=c.frame_height,hr=c.half_r;
  for(let fn=0;fn<c.num_frames;fn++){
    for(let r=0;r<Math.max(0,fh-hr);r++)
      for(let col=0;col<lw;col++)
        seq.push({fn,out_r:r,out_c:col,is_flush:false,flush_idx:-1});
    c.flush_out_rows.forEach((out_r,fi)=>{
      for(let col=0;col<lw;col++)
        seq.push({fn,out_r,out_c:col,is_flush:true,flush_idx:fi});
    });
  }
  return seq;
}

function getTaps(c,evt){
  if(!evt)return null;
  if(evt.is_flush){
    const fi=evt.flush_idx*c.line_width+evt.out_c;
    return c.flush_taps[evt.fn]&&c.flush_taps[evt.fn][fi];
  }
  const pi=evt.out_r*c.line_width+evt.out_c;
  return c.taps[evt.fn]&&c.taps[evt.fn][pi];
}

// TRIGGER pixel: the pixel accepted 2 clocks BEFORE this output fires.
// Pipeline: T=accept → T+1=p_delay (valid_out_d1) → T+2=p_out_reg (m_tvalid).
// When output (out_r, out_c) is valid at T+2, the RTL accepted
// the pixel at effective stream position (row = out_r+hr, col_eff = out_c+hc) at T.
// col_eff >= lw means it is a dummy-column zero, not a real pixel.
// For flush outputs, row >= fh so it is a flush virtual zero.
function getTriggerPixel(c,evt){
  if(!evt)return null;
  const lw=c.line_width,fh=c.frame_height,hr=c.half_r,hc=c.half_c;
  const tRow=evt.out_r+hr;
  const tCol=evt.out_c+hc;          // effective column (may be >= lw)
  const isVirtual=tRow>=fh;          // flush virtual row
  const isDummy=tCol>=lw;            // dummy column padding
  const isReal=!isVirtual&&!isDummy;
  const val=isReal?(c.frames[evt.fn]||[])[tRow*lw+tCol]||0:0;
  return{row:tRow,col:tCol,val,isReal,isDummy,isVirtual};
}

// ── On-the-fly tap computation (for explore mode / non-output rows) ──────────
// Used when a clicked pixel has no stored golden vector (e.g. bottom HALF_R rows
// in FLUSH=off mode).  Uses getPaddingPixel so edge mode is respected.
function computeExploreTaps(c,fn,out_r,out_c){
  const frame=c.frames[fn]||[];
  const hr=c.half_r,hc=c.half_c,kr=c.kern_rows,kc=c.kern_cols;
  const taps=[];
  for(let tr=0;tr<kr;tr++)
    for(let tc=0;tc<kc;tc++)
      taps.push(getPaddingPixel(c,frame,out_r+tr-hr,out_c+tc-hc));
  return taps;
}

// ── Padding pixel value (for border display) ──────────────────────────────
// pr,pc are real-frame coordinates (may be negative or >= fh/lw)
function getPaddingPixel(c,frame,pr,pc){
  const fh=c.frame_height,lw=c.line_width;
  if(pr>=0&&pr<fh&&pc>=0&&pc<lw)return frame[pr*lw+pc];
  // Flush bottom rows inject zeros
  if(c.flush&&c.half_r>0&&pr>=fh&&pr<fh+c.half_r)return 0;
  if(c.edge_mode==='ZERO')return 0;
  if(c.edge_mode==='REPLICATE'){
    const rr=Math.max(0,Math.min(pr,fh-1));
    const rc=Math.max(0,Math.min(pc,lw-1));
    return frame[rr*lw+rc];
  }
  if(c.edge_mode==='TOROIDAL'){
    const rr=((pr%fh)+fh)%fh;
    const rc=((pc%lw)+lw)%lw;
    return frame[rr*lw+rc];
  }
  return 0;
}

// ── Hatch ─────────────────────────────────────────────────────────────────
function hatch(ctx,x,y,w,h){
  ctx.save();ctx.beginPath();ctx.rect(x,y,w,h);ctx.clip();
  ctx.strokeStyle='rgba(255,255,255,0.06)';ctx.lineWidth=1;
  for(let d=-h;d<w+h;d+=7){ctx.beginPath();ctx.moveTo(x+d,y);ctx.lineTo(x+d+h,y+h);ctx.stroke();}
  ctx.restore();
}

// ── Frame canvas ──────────────────────────────────────────────────────────
// Canvas shows a border of half_r rows / half_c cols around the real frame,
// filled with the edge-mode padding values so the user can see exactly what
// each OOB tap reads.
//
// Coordinate mapping:
//   canvas row = real_frame_row + half_r
//   canvas col = real_frame_col + half_c
// So the kernel footprint for output (out_r, out_c) spans canvas rows
//   out_r .. out_r+kr-1  and  canvas cols  out_c .. out_c+kc-1
// (the half_r/half_c offsets cancel exactly — very clean).
//
// highlights: [{out_r, out_c, style:'out'|'trg'|'ex'}]
// kernelAnchor: {out_r, out_c} or null
function renderFrameCanvas(c,frameIdx,highlights,kernelAnchor){
  const lw=c.line_width,fh=c.frame_height;
  const hr=c.half_r,hc=c.half_c;
  const kr=c.kern_rows,kc=c.kern_cols,dw=c.data_width;
  const vmax=(1<<Math.min(dw,30))-1;
  const flushHr=c.flush&&hr>0?hr:0;
  // canvas grid: rows -hr..fh+hr-1 in real coords = 0..fh+2hr-1 in canvas
  const totR=fh+2*hr, totC=lw+2*hc;

  const wrap=document.getElementById('frame-wrap');
  const avW=wrap.clientWidth||400;
  const avH=wrap.clientHeight||300;
  const cellW=Math.floor(Math.min(avW-4,560)/Math.max(totC,1));
  const cellH=Math.floor(Math.min(avH-4,560)/Math.max(totR,1));
  const cell=Math.max(8,Math.min(52,cellW,cellH));

  const can=document.getElementById('fc');
  can.width=totC*cell; can.height=totR*cell;
  const ctx=can.getContext('2d');
  ctx.clearRect(0,0,can.width,can.height);

  const frame=c.frames[frameIdx]||[];

  // Draw every cell (real frame + padding border)
  for(let pr=-hr;pr<fh+hr;pr++){
    for(let pc=-hc;pc<lw+hc;pc++){
      const canR=pr+hr, canC=pc+hc;
      const inReal=pr>=0&&pr<fh&&pc>=0&&pc<lw;
      const inFlush=!inReal&&pr>=fh&&pr<fh+hr&&flushHr>0&&pc>=0&&pc<lw;
      const val=getPaddingPixel(c,frame,pr,pc);
      const norm=val/vmax;

      if(inReal){
        ctx.fillStyle=rgb(ylOrRd(norm));
      } else if(inFlush){
        ctx.fillStyle='#141428';
      } else if(c.edge_mode==='ZERO'){
        // OOB ZERO: dark grey
        ctx.fillStyle='rgba(60,60,80,0.7)';
      } else {
        // OOB REPLICATE/TOROIDAL: show value at reduced opacity
        const clr=ylOrRd(norm);
        ctx.fillStyle=`rgba(${clr[0]},${clr[1]},${clr[2]},0.55)`;
      }
      ctx.fillRect(canC*cell,canR*cell,cell,cell);
      if(inFlush)hatch(ctx,canC*cell,canR*cell,cell,cell);

      // Cell border
      ctx.strokeStyle=inReal?'rgba(0,0,0,0.18)':'rgba(100,100,150,0.15)';
      ctx.lineWidth=.5;
      ctx.strokeRect(canC*cell+.5,canR*cell+.5,cell-1,cell-1);

      // Value text
      if(cell>=16&&inReal){
        const clr=ylOrRd(norm);
        ctx.fillStyle=txtClr(clr);
        ctx.font=`${Math.max(8,cell/4.5)|0}px monospace`;
        ctx.textAlign='center';ctx.textBaseline='middle';
        const s=dw>8?`0x${val.toString(16).toUpperCase().padStart(Math.ceil(dw/4),'0')}`:`${val}`;
        ctx.fillText(s,canC*cell+cell/2,canR*cell+cell/2);
      } else if(cell>=16&&!inReal&&!inFlush&&c.edge_mode!=='ZERO'&&val>0){
        ctx.fillStyle='rgba(220,220,220,0.6)';
        ctx.font=`${Math.max(7,cell/5.5)|0}px monospace`;
        ctx.textAlign='center';ctx.textBaseline='middle';
        ctx.fillText(`${val}`,canC*cell+cell/2,canR*cell+cell/2);
      }
    }
  }

  // Real-frame border (thin white dashed)
  ctx.setLineDash([4,3]);ctx.strokeStyle='rgba(180,180,220,0.35)';ctx.lineWidth=1;
  ctx.strokeRect(hc*cell+.5,hr*cell+.5,lw*cell-1,fh*cell-1);
  ctx.setLineDash([]);

  // FLUSH border (amber dashed) around real frame
  if(flushHr>0){
    ctx.setLineDash([5,3]);ctx.strokeStyle='#dfb050';ctx.lineWidth=1.5;
    ctx.strokeRect(hc*cell+.5,hr*cell+.5,lw*cell-1,fh*cell-1);
    ctx.setLineDash([]);
  }

  // Kernel footprint — canvas coords = real coords (half offsets cancel)
  if(kernelAnchor){
    const ka=kernelAnchor;
    ctx.fillStyle='rgba(58,111,220,0.18)';
    for(let dr=0;dr<kr;dr++) for(let dc=0;dc<kc;dc++){
      const cr=ka.out_r+dr,cc=ka.out_c+dc;
      if(cr>=0&&cr<totR&&cc>=0&&cc<totC)ctx.fillRect(cc*cell+1,cr*cell+1,cell-2,cell-2);
    }
    ctx.strokeStyle='rgba(58,143,255,0.5)';ctx.lineWidth=1;
    for(let dr=0;dr<kr;dr++) for(let dc=0;dc<kc;dc++){
      const cr=ka.out_r+dr,cc=ka.out_c+dc;
      if(cr>=0&&cr<totR&&cc>=0&&cc<totC)ctx.strokeRect(cc*cell+1,cr*cell+1,cell-2,cell-2);
    }
  }

  // Highlights — canvas coords = real coords + (hr, hc)
  const ST={
    ex: {stroke:'#3a8fff',fill:'rgba(58,143,255,0)',   circle:true},
    out:{stroke:'#3a8fff',fill:'rgba(58,143,255,0.18)',circle:false},
    trg:{stroke:'#3abf7a',fill:'rgba(58,191,122,0.22)',circle:false},
  };
  for(const h of highlights){
    const cr=h.out_r+hr, cc=h.out_c+hc;
    if(cr<0||cr>=totR||cc<0||cc>=totC)continue;
    const st=ST[h.style]||ST.ex;
    const x=cc*cell,y=cr*cell;
    ctx.fillStyle=st.fill;ctx.fillRect(x+2,y+2,cell-4,cell-4);
    ctx.strokeStyle=st.stroke;ctx.lineWidth=2.5;
    if(st.circle){ctx.beginPath();ctx.arc(x+cell/2,y+cell/2,cell*.32,0,2*Math.PI);ctx.stroke();}
    else ctx.strokeRect(x+2,y+2,cell-4,cell-4);
  }
}

// ── Tap canvas ────────────────────────────────────────────────────────────
// taps[tr*kc + tc] — no reversal; tc=0 is leftmost column
function renderTapCanvas(c,taps,out_r,out_c){
  const kr=c.kern_rows,kc=c.kern_cols,dw=c.data_width;
  const fh=c.frame_height,lw=c.line_width;
  const hr=c.half_r,hc=c.half_c,em=c.edge_mode;
  const vmax=(1<<Math.min(dw,30))-1;
  if(!taps)return;
  const tp=document.getElementById('tap-panel');
  const tpW=tp.clientWidth-24-28;
  const tpH=tp.clientHeight-60;
  const cellW2=Math.floor(Math.min(tpW,260)/Math.max(kc,1));
  const cellH2=Math.floor(Math.min(tpH,260)/Math.max(kr,1));
  const cell=Math.max(12,Math.min(48,cellW2,cellH2));

  const colDiv=document.getElementById('tcol-labels');colDiv.innerHTML='';
  for(let tc=0;tc<kc;tc++){
    const sc=out_c+tc-hc,oob=sc<0||sc>=lw;
    const el=document.createElement('div');
    el.className='tlbl';el.style.width=cell+'px';el.style.height='14px';
    el.style.color=oob?'#df5050':'#5a7a8a';el.textContent=sc;
    colDiv.appendChild(el);
  }
  const rowDiv=document.getElementById('trow-labels');rowDiv.innerHTML='';
  for(let tr=0;tr<kr;tr++){
    const sr=out_r+tr-hr,oob=sr<0||sr>=fh;
    const el=document.createElement('div');
    el.className='tlbl';el.style.height=cell+'px';el.style.width='24px';
    el.style.color=oob?'#df5050':'#5a7a8a';el.textContent=sr;
    rowDiv.appendChild(el);
  }

  const can=document.getElementById('tc');
  can.width=kc*cell;can.height=kr*cell;
  const ctx=can.getContext('2d');
  for(let tr=0;tr<kr;tr++) for(let tc=0;tc<kc;tc++){
    const val=taps[tr*kc+tc];
    const norm=val/vmax;
    const sr=out_r+tr-hr,sc=out_c+tc-hc;
    const isOob=sr<0||sr>=fh||sc<0||sc>=lw;
    let face,edge;
    if(isOob&&em==='ZERO')        {face=[90,90,110];edge=null;}
    else if(isOob&&em==='REPLICATE'){face=ylOrRd(norm);edge='#4682b4';}
    else if(isOob&&em==='TOROIDAL') {face=ylOrRd(norm);edge='#9370db';}
    else                           {face=ylOrRd(norm);edge=null;}
    ctx.fillStyle=rgb(face);ctx.fillRect(tc*cell,tr*cell,cell,cell);
    if(edge){ctx.strokeStyle=edge;ctx.lineWidth=2.5;ctx.strokeRect(tc*cell+1.5,tr*cell+1.5,cell-3,cell-3);}
    else{ctx.strokeStyle='rgba(0,0,0,0.2)';ctx.lineWidth=.5;ctx.strokeRect(tc*cell+.5,tr*cell+.5,cell-1,cell-1);}
    if(cell>=14){
      ctx.fillStyle=txtClr(face);
      ctx.font=`${Math.max(8,cell/4.5)|0}px monospace`;
      ctx.textAlign='center';ctx.textBaseline='middle';
      const s=dw>8?`0x${val.toString(16).toUpperCase().padStart(Math.ceil(dw/4),'0')}`:`${val}`;
      ctx.fillText(s+(isOob?'*':''),tc*cell+cell/2,tr*cell+cell/2);
    }
  }
  // Centre tap highlight
  ctx.strokeStyle='#3a8fff';ctx.lineWidth=3;
  ctx.strokeRect(hc*cell+2,hr*cell+2,cell-4,cell-4);
}

// ── Pipeline panel ────────────────────────────────────────────────────────
function renderPipeline(c,outEvt,trgPixel){
  const vmax=(1<<Math.min(c.data_width,30))-1;
  // TRIGGER row
  if(trgPixel){
    let pos,val_str;
    if(trgPixel.isVirtual){
      pos=`row ${trgPixel.row} (flush zero row, outside frame)`;
      val_str='val = 0  (flush-injected zero)';
    } else if(trgPixel.isDummy){
      pos=`row ${trgPixel.row} · col_eff ${trgPixel.col}  (dummy column zero)`;
      val_str='val = 0  (right-edge dummy column)';
    } else {
      pos=`Frame ${outEvt.fn} · row ${trgPixel.row} · col ${trgPixel.col}`;
      val_str=`val = ${trgPixel.val}  (${Math.round(trgPixel.val/vmax*100)}%)`;
    }
    document.getElementById('ps-trg-pos').textContent=pos;
    document.getElementById('ps-trg-val').textContent=val_str;
    const sw=document.getElementById('ps-trg-sw');
    sw.style.background=trgPixel.isReal?rgb(ylOrRd(trgPixel.val/vmax)):'#252535';
    sw.style.borderColor='#3a3a5a';
  } else {
    document.getElementById('ps-trg-pos').textContent='—';
    document.getElementById('ps-trg-val').textContent='';
    document.getElementById('ps-trg-sw').style.background='#1a1a2e';
    document.getElementById('ps-trg-sw').style.borderColor='transparent';
  }
  // OUTPUT row
  if(outEvt){
    const centreVal=outEvt.is_flush?0:(c.frames[outEvt.fn]||[])[outEvt.out_r*c.line_width+outEvt.out_c]||0;
    const pos=outEvt.is_flush
      ?`Frame ${outEvt.fn} · flush output · centre row ${outEvt.out_r} · col ${outEvt.out_c}`
      :`Frame ${outEvt.fn} · row ${outEvt.out_r} · col ${outEvt.out_c}`;
    document.getElementById('ps-out-pos').textContent=pos;
    document.getElementById('ps-out-val').textContent=outEvt.is_flush
      ?'centre val = 0 (flush row)'
      :`centre val = ${centreVal}  (${Math.round(centreVal/vmax*100)}%)`;
    const sw=document.getElementById('ps-out-sw');
    sw.style.background=outEvt.is_flush?'#252535':rgb(ylOrRd(centreVal/vmax));
    sw.style.borderColor='#3a3a5a';
  } else {
    document.getElementById('ps-out-pos').textContent='—';
    document.getElementById('ps-out-val').textContent='';
    document.getElementById('ps-out-sw').style.background='#1a1a2e';
    document.getElementById('ps-out-sw').style.borderColor='transparent';
  }
}

// ── Legend ────────────────────────────────────────────────────────────────
function renderLegend(c,simMode){
  const e=[];
  if(simMode){
    e.push({sw:'background:none;border:2px solid #3abf7a',
            txt:'&#9654; Green = trigger pixel accepted 2 clocks ago'});
    e.push({sw:'background:rgba(58,143,255,.18);border:2px solid #3a8fff',
            txt:'&#9646; Blue = centre pixel whose window is now output'});
  } else {
    e.push({sw:'background:none;border:2px solid #3a8fff;border-radius:50%',
            txt:'Blue circle = selected centre pixel'});
  }
  e.push({sw:'background:rgba(58,111,220,0.18);border:1px solid rgba(58,143,255,0.5)',
          txt:'Blue tint = kernel footprint'});
  e.push({sw:'background:rgba(60,60,80,0.7);border:none',
          txt:'Dark grey padding = OOB zero (ZERO mode)'});
  if(c.edge_mode==='REPLICATE'||c.edge_mode==='all')
    e.push({sw:'background:#fd8d3c;opacity:.55;border:none',
            txt:'Amber (dim) padding = replicated edge value (REPLICATE)'});
  if(c.edge_mode==='TOROIDAL'||c.edge_mode==='all')
    e.push({sw:'background:#b07adf;opacity:.55;border:none',
            txt:'Purple (dim) padding = toroidal wrap value (TOROIDAL)'});
  if(c.flush&&c.half_r>0)
    e.push({sw:'background:#141428;border:2px dashed #dfb050',
            txt:'Hatched = flush-injected zero rows'});
  e.push({sw:'background:none;border:2.5px solid #3a8fff',
          txt:'Blue square in tap grid = centre tap (current pixel)'});
  e.push({sw:'background:none;border:2px solid #4682b4',
          txt:'* in tap grid = OOB &rarr; clamped (REPLICATE)'});
  e.push({sw:'background:none;border:2px solid #9370db',
          txt:'* in tap grid = OOB &rarr; wrapped (TOROIDAL)'});
  document.getElementById('legend').innerHTML='<div id="legend-title">Legend</div>'+
    e.map(x=>`<div class="lrow"><div class="lsw" style="${x.sw}"></div><span>${x.txt}</span></div>`).join('');
}

// ── Top bar ───────────────────────────────────────────────────────────────
function renderTopBar(c,ci){
  const bm=document.getElementById('b-mode');
  bm.textContent=c.edge_mode;
  bm.className='badge '+{ZERO:'bz',REPLICATE:'br',TOROIDAL:'bt'}[c.edge_mode];
  document.getElementById('b-flush').style.display=c.flush?'':'none';
  document.getElementById('toplbl').textContent=
    `CFG${String(ci+1).padStart(2,'0')} · ${c.data_width}b · ${c.kern_rows}×${c.kern_cols} · ${c.line_width}×${c.frame_height}px`;
}

// ── Filters ───────────────────────────────────────────────────────────────
function applyFilters(){
  const dw=document.getElementById('f-dw').value;
  const kern=document.getElementById('f-kern').value;
  const m=document.querySelector('input[name="fmode"]:checked').value;
  const fl=document.getElementById('f-flush').value;
  const fr=document.getElementById('f-frame').value;
  let vis=0,first=-1;
  document.querySelectorAll('.ci').forEach((el,i)=>{
    const c=CFG[i];
    const ok=(dw==='all'||String(c.data_width)===dw)&&
             (kern==='all'||`${c.kern_rows}x${c.kern_cols}`===kern)&&
             (m==='all'||c.edge_mode===m)&&
             (fl==='all'||(fl==='on')===c.flush)&&
             (fr==='all'||`${c.line_width}x${c.frame_height}`===fr);
    el.classList.toggle('hidden',!ok);
    if(ok){vis++;if(first<0)first=i;}
  });
  document.getElementById('cfg-count').textContent=`${vis} / ${NTOT} configs`;
  if(document.querySelector('.ci.active.hidden')&&first>=0)selectCfg(first);
}

function buildList(){
  const list=document.getElementById('config-list');
  CFG.forEach((c,i)=>{
    const el=document.createElement('div');
    el.className='ci';el.dataset.i=i;
    el.innerHTML=`<span class="cn">CFG${String(i+1).padStart(2,'0')}</span> ${c.label}`;
    el.onclick=()=>selectCfg(i);
    list.appendChild(el);
  });
}

function selectCfg(i){
  ex.ci=i;ex.fn=0;ex.isFlush=false;ex.fr=0;
  ex.fplaying=false;clearInterval(ex.ftimer);
  const c=CFG[i];
  ex.row=Math.min(ex.row,c.frame_height-1);
  ex.col=Math.min(ex.col,c.line_width-1);
  sm.step=0;sm.seq=buildSeq(c);sm.playing=false;clearInterval(sm.timer);
  const sc=document.getElementById('scrubber');
  sc.max=Math.max(0,sm.seq.length-1);sc.value=0;
  document.querySelectorAll('.ci').forEach(el=>el.classList.remove('active'));
  const el=document.querySelector(`.ci[data-i="${i}"]`);
  if(el){el.classList.add('active');el.scrollIntoView({block:'nearest'});}
  render();
}

// ── Mode switch ───────────────────────────────────────────────────────────
function setMode(m){
  mode=m;
  document.getElementById('btn-explore') .classList.toggle('act',m==='explore');
  document.getElementById('btn-simulate').classList.toggle('act',m==='simulate');
  document.getElementById('explore-ctrl').classList.toggle('hidden',m!=='explore');
  document.getElementById('sim-ctrl')    .classList.toggle('hidden',m!=='simulate');
  document.getElementById('pipeline-panel').classList.toggle('vis',m==='simulate');
  if(m==='simulate'&&sm.seq.length===0)sm.seq=buildSeq(CFG[ex.ci]);
  render();
}

// ── Frame nav ─────────────────────────────────────────────────────────────
function prevFrame(){const nf=CFG[ex.ci].num_frames;ex.fn=(ex.fn-1+nf)%nf;ex.isFlush=false;render();}
function nextFrame(){const nf=CFG[ex.ci].num_frames;ex.fn=(ex.fn+1)%nf;ex.isFlush=false;render();}

// ── Flush player ──────────────────────────────────────────────────────────
function buildFlushTL(){
  const c=CFG[ex.ci],tl=document.getElementById('ftl');
  tl.innerHTML='';
  const rc=Math.max(0,c.frame_height-c.half_r);
  if(!c.flush||!c.flush_out_rows.length)return;
  for(let r=0;r<rc;r++){
    const el=document.createElement('div');
    el.className='tr real';el.textContent=r;
    el.title=`Real output: centre row ${r}`;
    const _r=r;el.onclick=()=>{ex.isFlush=false;ex.row=_r;render();};
    tl.appendChild(el);
  }
  const sep=document.createElement('span');sep.className='tlsep';sep.textContent='│';tl.appendChild(sep);
  c.flush_out_rows.forEach((out_r,fi)=>{
    const el=document.createElement('div');
    el.className='tr flush-tl';el.textContent=out_r;
    el.title=`Flush output: centre row ${out_r} (bottom-edge, needs flush zeros)`;
    const _fi=fi,_r=out_r;el.onclick=()=>{ex.isFlush=true;ex.fr=_fi;ex.row=_r;render();};
    tl.appendChild(el);
  });
}
function updateFlushTL(){
  document.querySelectorAll('.tr.real'    ).forEach((el,i)=>el.classList.toggle('act',!ex.isFlush&&i===ex.row));
  document.querySelectorAll('.tr.flush-tl').forEach((el,i)=>el.classList.toggle('act', ex.isFlush&&i===ex.fr));
  const c=CFG[ex.ci];
  document.getElementById('fplbl').textContent=ex.isFlush
    ?`Flush output · centre row ${ex.row}`:`Real output · row ${ex.row}`;
}
function updateFlushPlayer(){
  const c=CFG[ex.ci],fp=document.getElementById('fplay');
  if(c.flush&&c.flush_out_rows.length){fp.classList.add('vis');buildFlushTL();updateFlushTL();}
  else{fp.classList.remove('vis');ex.isFlush=false;ex.fplaying=false;clearInterval(ex.ftimer);}
}
function totalExRows(){const c=CFG[ex.ci];return Math.max(0,c.frame_height-c.half_r)+(c.flush?c.flush_out_rows.length:0);}
function exCurPos(){const c=CFG[ex.ci];return ex.isFlush?Math.max(0,c.frame_height-c.half_r)+ex.fr:ex.row;}
function exSetPos(pos){
  const c=CFG[ex.ci],rc=Math.max(0,c.frame_height-c.half_r);
  if(pos<rc){ex.isFlush=false;ex.row=pos;}
  else{ex.isFlush=true;ex.fr=pos-rc;ex.row=c.flush_out_rows[ex.fr];}
}
function fNext(){exSetPos((exCurPos()+1)%totalExRows());render();}
function fPrev(){exSetPos((exCurPos()-1+totalExRows())%totalExRows());render();}
function exitFlush(){const c=CFG[ex.ci];ex.isFlush=false;ex.row=Math.min(ex.row,Math.max(0,c.frame_height-c.half_r-1));render();}
function toggleFlushPlay(){
  ex.fplaying=!ex.fplaying;
  document.getElementById('btn-fplay').textContent=ex.fplaying?'⏸ Pause':'▶ Play';
  clearInterval(ex.ftimer);
  if(ex.fplaying)ex.ftimer=setInterval(fNext,+document.getElementById('fspd').value);
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

// ── Main render ───────────────────────────────────────────────────────────
function render(){
  const c=CFG[ex.ci],ci=ex.ci;
  renderTopBar(c,ci);

  if(mode==='explore'){
    document.getElementById('flbl').textContent=`Frame ${ex.fn} / ${c.num_frames-1}`;
    updateFlushPlayer();

    let taps;
    if(ex.isFlush){taps=c.flush_taps[ex.fn]&&c.flush_taps[ex.fn][ex.fr*c.line_width+ex.col];}
    else{taps=c.taps[ex.fn]&&c.taps[ex.fn][ex.row*c.line_width+ex.col];}
    // Fall back to on-the-fly computation when this row has no stored output
    // (e.g. bottom HALF_R rows in FLUSH=off mode).
    if(!taps)taps=computeExploreTaps(c,ex.fn,ex.row,ex.col);

    renderFrameCanvas(c,ex.fn,
      [{out_r:ex.row,out_c:ex.col,style:'ex'}],
      {out_r:ex.row,out_c:ex.col});
    renderTapCanvas(c,taps,ex.row,ex.col);

    const rc2=Math.max(0,c.frame_height-c.half_r);
    const noOutput=!ex.isFlush&&ex.row>=rc2;
    document.getElementById('tp-title').textContent=
      ex.isFlush?`Kernel @ flush output row ${ex.row}`
      :noOutput?`Kernel @ (row ${ex.row}, col ${ex.col})  [no RTL output — computed]`
      :`Kernel @ (row ${ex.row}, col ${ex.col})`;
    document.getElementById('tp-coords').textContent=
      `Frame ${ex.fn} · centre=(${ex.row},${ex.col}) · ${c.edge_mode}`+(noOutput?' · FLUSH=off: no output here':'');
    document.getElementById('pxinfo').textContent=
      ex.isFlush?`flush r=${ex.row},c=${ex.col}`:`px(${ex.row},${ex.col})`;
    renderLegend(c,false);

  } else {
    const seq=sm.seq;
    if(!seq.length){document.getElementById('sim-pos-lbl').textContent='No data';return;}
    const step=sm.step;
    const outEvt=seq[step];
    const trgPixel=outEvt?getTriggerPixel(c,outEvt):null;

    document.getElementById('scrubber').value=step;
    document.getElementById('sim-step-lbl').textContent=`Step ${step+1} / ${seq.length}`;

    // Position label — explains the timing clearly
    const outStr=outEvt
      ?(outEvt.is_flush?`F${outEvt.fn} flush r=${outEvt.out_r} c=${outEvt.out_c}`
                       :`F${outEvt.fn} r=${outEvt.out_r} c=${outEvt.out_c}`):'—';
    const trgStr=trgPixel
      ?(trgPixel.isReal?`(${trgPixel.row},${trgPixel.col}) val=${trgPixel.val}`
       :trgPixel.isDummy?`col_eff ${trgPixel.col} dummy=0`:'flush zero'):'—';
    document.getElementById('sim-pos-lbl').textContent=
      `OUTPUT: ${outStr}   |   ACCEPTED 2 CLOCKS AGO: ${trgStr}`;
    document.getElementById('pxinfo').textContent=`step ${step+1}/${seq.length}`;

    // Highlights: blue=output centre, green=trigger (if real pixel in frame)
    const hl=[{out_r:outEvt.out_r,out_c:outEvt.out_c,style:'out'}];
    if(trgPixel&&trgPixel.isReal)hl.push({out_r:trgPixel.row,out_c:trgPixel.col,style:'trg'});

    renderFrameCanvas(c,outEvt.fn,hl,{out_r:outEvt.out_r,out_c:outEvt.out_c});
    renderPipeline(c,outEvt,trgPixel);

    const taps=getTaps(c,outEvt);
    renderTapCanvas(c,taps,outEvt.out_r,outEvt.out_c);

    document.getElementById('tp-title').textContent=
      outEvt.is_flush?`Output @ flush row ${outEvt.out_r} col ${outEvt.out_c}`
                     :`Output @ (row ${outEvt.out_r}, col ${outEvt.out_c})`;
    document.getElementById('tp-coords').textContent=
      `Frame ${outEvt.fn} · centre=(${outEvt.out_r},${outEvt.out_c}) · ${c.edge_mode}`;
    renderLegend(c,true);
  }
}

// ── Frame canvas click ────────────────────────────────────────────────────
// Canvas coords: canR = real_row + half_r, canC = real_col + half_c
document.getElementById('fc').addEventListener('click',function(e){
  if(mode!=='explore')return;
  const c=CFG[ex.ci],lw=c.line_width,fh=c.frame_height,hr=c.half_r,hc=c.half_c;
  const totC=lw+2*hc;
  const cell=this.width/totC;
  const canR=Math.floor((e.clientY-this.getBoundingClientRect().top)/cell);
  const canC=Math.floor((e.clientX-this.getBoundingClientRect().left)/cell);
  const pr=canR-hr,pc=canC-hc;  // real frame coords
  if(pr<0||pr>=fh||pc<0||pc>=lw)return;  // clicked in padding — ignore
  ex.col=pc;
  const rc=Math.max(0,fh-hr);
  if(pr<rc){ex.isFlush=false;ex.row=pr;}
  else{
    const fi=pr-rc;
    if(fi<c.flush_out_rows.length){ex.isFlush=true;ex.fr=fi;ex.row=c.flush_out_rows[fi];}
    else{ex.isFlush=false;ex.row=pr;}
  }
  render();
});

// ── Keyboard ──────────────────────────────────────────────────────────────
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='SELECT'||e.target.tagName==='INPUT')return;
  const c=CFG[ex.ci];let handled=true;
  if(mode==='explore'){
    const lw=c.line_width,rc=Math.max(0,c.frame_height-c.half_r);
    if(e.altKey&&e.key==='ArrowLeft')  prevFrame();
    else if(e.altKey&&e.key==='ArrowRight') nextFrame();
    else if(e.key==='ArrowLeft') {ex.col=Math.max(0,ex.col-1);render();}
    else if(e.key==='ArrowRight'){ex.col=Math.min(lw-1,ex.col+1);render();}
    else if(e.key==='ArrowUp')  {if(!ex.isFlush&&ex.row>0){ex.row--;render();}}
    else if(e.key==='ArrowDown'){if(!ex.isFlush&&ex.row<c.frame_height-1){ex.row++;render();}}
    else if(e.key===',')selectCfg(Math.max(0,ex.ci-1));
    else if(e.key==='.')selectCfg(Math.min(NTOT-1,ex.ci+1));
    else if((e.key==='f'||e.key==='F')&&c.flush&&c.flush_out_rows.length)fNext();
    else if(e.key==='r'||e.key==='R')exitFlush();
    else if(e.key===' '&&c.flush&&c.flush_out_rows.length)toggleFlushPlay();
    else handled=false;
  } else {
    if(e.key==='ArrowRight'||e.key==='ArrowDown')simStepFwd();
    else if(e.key==='ArrowLeft'||e.key==='ArrowUp')simStepBck();
    else if(e.key==='Home')simGoFirst();
    else if(e.key==='End') simGoLast();
    else if(e.key===' ')  toggleSimPlay();
    else if(e.key===',')  selectCfg(Math.max(0,ex.ci-1));
    else if(e.key==='.')  selectCfg(Math.min(NTOT-1,ex.ci+1));
    else handled=false;
  }
  if(handled)e.preventDefault();
});

window.addEventListener('resize',render);

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
    ap.add_argument('--out', default=os.path.join('tb', 'visualize.html'))
    ap.add_argument('--vec-dir', default=os.path.join('tb', 'vectors'))
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
