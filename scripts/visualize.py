#!/usr/bin/env python3
"""
Generate a self-contained interactive HTML visualiser for conv2d golden vectors.

Usage:
    python scripts/visualize.py [--out tb/visualize.html] [--vec-dir tb/vectors]

Modes
-----
Explore  : click any pixel in the frame to inspect its centred tap window.
Simulate : per-clock-cycle walkthrough of the RTL pipeline — every reset, stall,
           fill, and output cycle is shown with input pixel and tap-grid output
           simultaneously visible.
"""

import json
import os
import sys
import argparse

_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
from gen_tb import CONFIGS, N

# Back-pressure scenarios (0-indexed config indices)
BP_INFO = {
    0:  {'type': 'post_reset', 'stall_cycles': 10,
         'note': 'Back-pressure: m_tready(0)=0 for 10 cycles post-reset'},
    39: {'type': 'mid_sim',   'start_cycle': 210, 'stall_cycles': 15,
         'note': 'Back-pressure: m_tready(0)=0 for 15 cycles mid-flush (cycles 210-224 post-reset)'},
    54: {'type': 'post_reset', 'stall_cycles': 10,
         'note': 'Back-pressure: m_tready(NUM_TAPS-1)=0 for 10 cycles post-reset (last tap port)'},
}


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
        mode   = cfg.get('edge_mode', 'ZERO')
        half_r = (kr - 1) // 2
        # Streaming: FLUSH=false or TOROIDAL uses cross-frame output triggering.
        streaming = (not fl) or (mode == 'TOROIDAL')
        flush_out_rows = []
        if not streaming and fl and half_r > 0:
            for flush_row in range(half_r):
                out_r = fh + flush_row - half_r
                if out_r >= 0:
                    flush_out_rows.append(out_r)
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
        if streaming:
            # Streaming layout in expected.txt:
            # fn=0..nf-2: fh*lw outputs each (in-frame + tail from fn+1's pixels)
            # fn=nf-1:    (fh-half_r)*lw outputs (no tail — no next frame)
            full_sz = fh * lw
            last_sz = max(0, fh - half_r) * lw
            offset = 0
            for n in range(nf):
                sz = last_sz if n == nf - 1 else full_sz
                taps.append(trows[offset:offset + sz])
                offset += sz
            flsh = [[] for _ in range(nf)]
        else:
            real_out_per_frame  = max(0, fh - half_r) * lw
            flush_out_per_frame = len(flush_out_rows) * lw
            tppf = real_out_per_frame + flush_out_per_frame
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
        e['streaming'] = (not cfg['flush']) or (cfg.get('edge_mode', 'ZERO') == 'TOROIDAL')
        d = vd[idx]
        e['frames']         = d['frames']         if d else []
        e['taps']           = d['taps']           if d else []
        e['flush_taps']     = d['flush_taps']     if d else []
        e['flush_out_rows'] = d['flush_out_rows'] if d else []
        bp = BP_INFO.get(idx)
        e['bp'] = bp if bp else None
        cs.append(e)
    return json.dumps({'configs': cs}, separators=(',', ':'))


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>conv2d Visualiser</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#12121f;--bg2:#1a1a2e;--bg3:#141428;
  --bdr:#252540;--text:#e0e0e0;--dim:#6a7a8a;
  --blue:#3a8fff;--green:#44bb66;--amber:#dfb050;--cyan:#33bbbb;--red:#e05050;
}
body{font-family:system-ui,sans-serif;display:flex;height:100vh;overflow:hidden;
     background:var(--bg);color:var(--text);font-size:13px}
/* ─── Sidebar ─────────────────────────────────────────────── */
#sidebar{width:190px;min-width:140px;background:var(--bg2);display:flex;flex-direction:column;
  padding:8px 6px;gap:5px;overflow-y:auto;border-right:1px solid var(--bdr);flex-shrink:0}
#sidebar h2{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);
  padding-bottom:4px;border-bottom:1px solid var(--bdr)}
.fg{display:flex;flex-direction:column;gap:2px}
.fg>span{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.fg select{padding:3px 6px;border-radius:4px;border:1px solid var(--bdr);
  background:#0e1428;color:#c0d0e0;font-size:12px;cursor:pointer}
.rg{display:flex;flex-direction:column;gap:1px}
.rg label{display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;
  padding:2px 4px;border-radius:3px}
.rg label:hover{background:#1e2840}
.rg input{accent-color:var(--blue)}
#cfg-count{font-size:10px;color:var(--dim);text-align:right}
#config-list{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:1px}
.ci{padding:4px 7px;border-radius:4px;cursor:pointer;font-size:11px;line-height:1.5;border-left:3px solid transparent}
.ci:hover{background:#1e2840}.ci.active{background:#1e3a7a;border-left-color:var(--blue)}.ci.hidden{display:none}
.cn{font-weight:700;color:#5a9fff;font-size:10px}.ci.active .cn{color:#90c0ff}
hr.sp{border:none;border-top:1px solid var(--bdr);margin:2px 0}
/* ─── Main layout ─────────────────────────────────────────── */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
#topbar{height:46px;display:flex;align-items:center;gap:6px;padding:0 8px;
  border-bottom:1px solid var(--bdr);flex-shrink:0;overflow:hidden}
#topbar h1{font-size:14px;font-weight:700;color:#5a9fff;margin-right:2px;flex-shrink:0}
.mbtn{padding:4px 12px;border-radius:20px;border:1px solid #2a3a5a;background:transparent;
  color:#8090a0;cursor:pointer;font-size:12px;flex-shrink:0}
.mbtn.act{background:#1e3a7a;color:#90c0ff;border-color:var(--blue)}
.mbtn:hover:not(.act){background:#1e2840;color:#c0d0e0}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;flex-shrink:0}
.bz{background:#1e2a1e;color:#7abf7a}.br{background:#1a2a40;color:#7ab0df}
.bt{background:#2a1a40;color:#b07adf}.bf{background:#3a2a00;color:var(--amber)}
#cfg-toplbl{font-size:11px;color:var(--dim);flex-shrink:0}
#sim-ctrl{display:none;align-items:center;gap:4px;margin-left:auto}
#sim-ctrl.vis{display:flex}
.sbtn{background:#1e2840;border:1px solid #2a3a5a;color:#c0d0e0;border-radius:4px;
  padding:3px 8px;cursor:pointer;font-size:14px;line-height:1}
.sbtn:hover{background:var(--blue);border-color:var(--blue)}
#scrubber{width:100px;accent-color:var(--blue);cursor:pointer}
#cyc-lbl{font-size:11px;color:#9ab0c0;white-space:nowrap}
#spd-sel{background:#1e2840;border:1px solid var(--bdr);color:#c0d0e0;
  border-radius:4px;padding:2px 4px;font-size:11px;cursor:pointer}
/* ─── Content ─────────────────────────────────────────────── */
#content{flex:1;display:flex;overflow:hidden;min-height:0}
/* Timeline */
#tl-panel{width:205px;min-width:160px;flex-shrink:0;background:var(--bg3);
  border-right:1px solid var(--bdr);display:none;flex-direction:column;overflow:hidden}
#tl-panel.vis{display:flex}
#tl-head{padding:5px 8px;font-size:10px;text-transform:uppercase;letter-spacing:1px;
  color:var(--dim);border-bottom:1px solid var(--bdr);flex-shrink:0;display:flex;align-items:center;justify-content:space-between}
#tl-list{flex:1;overflow-y:auto}
.tc{display:flex;align-items:center;gap:4px;padding:2px 5px;cursor:pointer;
  border-left:3px solid transparent;min-height:22px}
.tc:hover{background:#1e2840}.tc.cur{background:#1e3060;border-left-color:var(--blue)}
.tc-num{font-size:10px;color:var(--dim);font-family:monospace;width:30px;text-align:right;flex-shrink:0}
.tc-ph{font-size:9px;font-weight:700;padding:1px 4px;border-radius:3px;
  text-transform:uppercase;letter-spacing:.3px;flex-shrink:0;min-width:58px;text-align:center}
.tc-info{font-size:10px;color:#6a8090;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
/* Phase colours */
.ph-RESET{background:#1e1e35;color:#8888cc}
.ph-STALL{background:#2a1800;color:#cc8800}
.ph-FILL{background:#0a2030;color:#4499cc}
.ph-OUTPUT{background:#0a2a10;color:#44bb66}
.ph-FLUSH_OUT{background:#002a2a;color:#33bbbb}
.ph-TAIL_OUT{background:#1a2a00;color:#88bb44}
.ph-DUMMY{background:#181828;color:#556688}
.ph-FLUSH{background:#002020;color:#338888}
/* Center panel */
#center{flex:1;display:flex;flex-direction:column;overflow:hidden;padding:6px;gap:5px;min-width:0}
/* State bar */
#state-bar{background:var(--bg3);border-radius:6px;padding:5px 10px;
  display:flex;align-items:center;gap:8px;flex-shrink:0;min-height:32px}
.ph-big{font-size:13px;font-weight:700;padding:2px 10px;border-radius:5px;
  text-transform:uppercase;letter-spacing:.5px;flex-shrink:0}
#state-desc{font-size:12px;color:#b0bec8;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* Pipeline cards */
#pipe-row{display:flex;align-items:stretch;gap:4px;flex-shrink:0}
.pcard{flex:1;background:var(--bg3);border-radius:6px;border:1px solid var(--bdr);
  padding:7px 8px;display:flex;flex-direction:column;gap:3px;min-width:0;overflow:hidden}
.pcard-hdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--dim)}
.pcard.p-in {border-color:#2a5a8a}.pcard.p-out{border-color:#1a6a30}.pcard.p-null{opacity:.5}
.pcard-pos{font-size:12px;color:#c0d0e0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pcard-val{font-size:11px;color:#9ab0c0;font-family:monospace}
.pcard-sw{width:100%;height:20px;border-radius:3px;border:1px solid #252540;margin-top:2px;flex-shrink:0}
.parr{text-align:center;font-size:20px;color:#3a5a7a;flex-shrink:0;align-self:center;padding:0 1px}
/* Output section */
#out-section{flex:1;display:flex;flex-direction:column;gap:4px;overflow:hidden;min-height:0}
#out-header{background:var(--bg3);border-radius:6px;padding:6px 10px;
  display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}
#out-status{font-size:13px;font-weight:700}
#out-pos{font-size:11px;color:#9ab0c0}
#tap-card{flex:1;background:var(--bg3);border-radius:6px;padding:6px;
  display:flex;flex-direction:column;overflow:hidden;min-height:0}
#tp-title{font-size:11px;font-weight:700;color:#c0d0e0;flex-shrink:0}
#tp-coords{font-size:10px;color:var(--dim);flex-shrink:0;min-height:14px}
.tgwrap{overflow:auto;display:flex;flex-direction:column;flex:1;min-height:0}
#tcol-labels{display:flex;margin-left:24px}
#trow-outer{display:flex;flex:1;min-height:0}
#trow-labels{display:flex;flex-direction:column;width:24px;flex-shrink:0}
.tlbl{display:flex;align-items:center;justify-content:center;font-size:9px;overflow:hidden;font-family:monospace}
/* Explore center (tap window) */
#explore-center{display:none;flex:1;flex-direction:column;padding:6px;gap:5px;overflow:hidden}
#explore-center.vis{display:flex}
#exp-tap-card{flex:1;background:var(--bg3);border-radius:6px;padding:6px;
  display:flex;flex-direction:column;overflow:hidden;min-height:0}
#exp-tp-title{font-size:11px;font-weight:700;color:#c0d0e0;flex-shrink:0}
#exp-tp-coords{font-size:10px;color:var(--dim);flex-shrink:0;min-height:14px}
.tgwrap2{overflow:auto;display:flex;flex-direction:column;flex:1;min-height:0}
#exp-tcol-labels{display:flex;margin-left:24px}
#exp-trow-outer{display:flex;flex:1;min-height:0}
#exp-trow-labels{display:flex;flex-direction:column;width:24px;flex-shrink:0}
/* Legend card */
#legend-card{background:var(--bg3);border-radius:6px;padding:6px;flex-shrink:0}
#legend-title{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-bottom:3px}
#legend-rows{display:flex;flex-direction:column;gap:2px}
.lrow{display:flex;align-items:center;gap:5px;font-size:10px;color:#8090a0}
.lsw{width:12px;height:12px;border-radius:2px;border:2px solid transparent;flex-shrink:0}
/* Right panel: frame */
#right-panel{width:clamp(260px,26vw,360px);flex-shrink:0;display:flex;flex-direction:column;
  padding:6px;gap:4px;overflow:hidden;border-left:1px solid var(--bdr)}
#frame-nav{display:flex;align-items:center;gap:5px;flex-shrink:0;flex-wrap:wrap}
#frame-nav button{background:#1e2840;border:1px solid var(--bdr);color:#c0d0e0;
  border-radius:4px;padding:2px 8px;cursor:pointer;font-size:12px}
#frame-nav button:hover{background:var(--blue);border-color:var(--blue)}
#flbl{font-size:12px;min-width:60px;text-align:center;color:#9ab0c0}
#frame-hint{font-size:10px;color:#3a4a5a}
#sim-frame-info{display:none;font-size:11px;color:#9ab0c0;flex-shrink:0}
#sim-frame-info.vis{display:block}
.cwrap{overflow:auto;flex:1;min-height:0}
canvas{display:block;image-rendering:pixelated;cursor:crosshair}
/* Flush player */
#fplay-wrap{display:none;flex-direction:column;gap:3px;padding-top:4px;
  border-top:1px solid var(--bdr);flex-shrink:0}
#fplay-wrap.vis{display:flex}
#ftl{display:flex;gap:3px;flex-wrap:wrap;align-items:center}
.tr{width:24px;height:24px;border-radius:4px;cursor:pointer;display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:700;border:2px solid transparent;user-select:none}
.tr.real{background:#1a3a2a;color:#7adf9a}.tr.real:hover{background:#1a5a3a}
.tr.flush-tl{background:#1e1e2e;color:var(--amber);border-style:dashed;border-color:#5a4a20}
.tr.flush-tl:hover{background:#252520}.tr.act{border-color:var(--amber)!important;box-shadow:0 0 0 1px var(--amber)}
.tlsep{color:#3a4a5a;font-size:16px;margin:0 2px;line-height:24px}
#fctrl{display:flex;align-items:center;gap:5px;flex-wrap:wrap}
#fplbl{font-size:11px;color:#9090a0}
</style>
</head>
<body>
<!-- Sidebar: config list -->
<div id="sidebar">
  <h2>conv2d Visualiser</h2>
  <div class="fg"><span>Data width</span>
    <select id="f-dw" onchange="applyFilters()">
      <option value="all">All widths</option>
      <option value="8">8-bit</option><option value="16">16-bit</option><option value="24">24-bit</option>
    </select>
  </div>
  <div class="fg"><span>Kernel</span>
    <select id="f-kern" onchange="applyFilters()">
      <option value="all">All sizes</option>
      <option value="3x3">3&#215;3</option><option value="5x5">5&#215;5</option>
      <option value="3x5">3&#215;5</option><option value="7x7">7&#215;7</option>
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
      <option value="all">All</option><option value="on">On</option><option value="off">Off</option>
    </select>
  </div>
  <div class="fg"><span>Frame size</span>
    <select id="f-frame" onchange="applyFilters()">
      <option value="all">All frames</option>
      <option value="8x8">8&#215;8</option><option value="16x4">16&#215;4</option>
      <option value="4x16">4&#215;16</option><option value="3x3">3&#215;3</option>
      <option value="5x5">5&#215;5</option><option value="8x1">8&#215;1</option>
    </select>
  </div>
  <hr class="sp">
  <div id="cfg-count"></div>
  <div id="config-list"></div>
</div>

<!-- Main content -->
<div id="main">
  <!-- Top bar -->
  <div id="topbar">
    <h1>conv2d</h1>
    <button class="mbtn act" id="btn-explore"  onclick="setMode('explore')">&#128269; Explore</button>
    <button class="mbtn"     id="btn-simulate" onclick="setMode('simulate')">&#9654; Simulate</button>
    <span id="b-mode" class="badge"></span>
    <span id="b-flush" class="badge bf" style="display:none">FLUSH</span>
    <span id="cfg-toplbl"></span>
    <!-- Simulate controls (hidden in explore) -->
    <div id="sim-ctrl">
      <button class="sbtn" onclick="simGoFirst()" title="First">&#9198;</button>
      <button class="sbtn" onclick="simStepBck()" title="Back">&#9664;</button>
      <button class="sbtn" id="btn-play" onclick="togglePlay()">&#9654;</button>
      <button class="sbtn" onclick="simStepFwd()" title="Forward">&#9654;</button>
      <button class="sbtn" onclick="simGoLast()"  title="Last">&#9197;</button>
      <input type="range" id="scrubber" min="0" value="0" oninput="scrubTo(+this.value)">
      <span id="cyc-lbl">Cycle 0/0</span>
      <select id="spd-sel">
        <option value="800">0.5&#215;</option><option value="400" selected>1&#215;</option>
        <option value="200">2&#215;</option><option value="80">5&#215;</option>
        <option value="30">15&#215;</option>
      </select>
    </div>
  </div>

  <!-- Content area -->
  <div id="content">
    <!-- Timeline (simulate mode only) -->
    <div id="tl-panel">
      <div id="tl-head">
        <span>Clock Cycles</span>
        <span id="tl-count" style="font-size:10px;color:var(--dim)"></span>
      </div>
      <div id="tl-list"></div>
    </div>

    <!-- Center: pipeline view (simulate) OR tap grid (explore) -->
    <div id="center">
      <!-- Pipeline view shown in simulate mode -->
      <div id="state-bar">
        <span id="ph-big" class="ph-big ph-RESET">RESET</span>
        <span id="state-desc">Select simulate mode to step through every clock cycle.</span>
      </div>
      <div id="pipe-row">
        <div class="pcard" id="pc-t2">
          <div class="pcard-hdr">T&#8722;2 &#8594; OUTPUT TRIGGER</div>
          <div class="pcard-pos" id="pt2-pos">&#8212;</div>
          <div class="pcard-val" id="pt2-val"></div>
          <div class="pcard-sw"  id="pt2-sw" ></div>
        </div>
        <div class="parr">&#8594;</div>
        <div class="pcard" id="pc-t1">
          <div class="pcard-hdr">T&#8722;1 &#8594; DELAY REGISTER</div>
          <div class="pcard-pos" id="pt1-pos">&#8212;</div>
          <div class="pcard-val" id="pt1-val"></div>
          <div class="pcard-sw"  id="pt1-sw" ></div>
        </div>
        <div class="parr">&#8594;</div>
        <div class="pcard" id="pc-t0">
          <div class="pcard-hdr">T &#8594; INPUT ACCEPTED</div>
          <div class="pcard-pos" id="pt0-pos">&#8212;</div>
          <div class="pcard-val" id="pt0-val"></div>
          <div class="pcard-sw"  id="pt0-sw" ></div>
        </div>
      </div>
      <div id="out-section">
        <div id="out-header">
          <span id="out-status" style="color:var(--dim);font-size:13px;font-weight:700">&#8212; NO OUTPUT THIS CYCLE</span>
          <span id="out-pos"></span>
        </div>
        <div id="tap-card">
          <div id="tp-title">Output tap grid</div>
          <div id="tp-coords"></div>
          <div class="tgwrap">
            <div id="tcol-labels"></div>
            <div id="trow-outer">
              <div id="trow-labels"></div>
              <canvas id="tc"></canvas>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Explore tap grid (shown in explore mode, hidden in simulate) -->
    <div id="explore-center">
      <div id="exp-tap-card">
        <div id="exp-tp-title">Tap window</div>
        <div id="exp-tp-coords"></div>
        <div class="tgwrap2">
          <div id="exp-tcol-labels"></div>
          <div id="exp-trow-outer">
            <div id="exp-trow-labels"></div>
            <canvas id="exp-tc"></canvas>
          </div>
        </div>
      </div>
      <div id="legend-card">
        <div id="legend-title">Legend</div>
        <div id="legend-rows"></div>
      </div>
    </div>

    <!-- Right: frame canvas (always visible) -->
    <div id="right-panel">
      <!-- Explore controls -->
      <div id="explore-ctrl" style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
        <div id="frame-nav">
          <button onclick="prevFrame()">&#9664;</button>
          <span id="flbl">Frame 0/0</span>
          <button onclick="nextFrame()">&#9654;</button>
          <span id="frame-hint">Click pixel &middot; &#8593;&#8595;&#8592;&#8594; move &middot; ,/. config &middot; Alt+&#8592;&#8594; frame</span>
        </div>
        <div id="fplay-wrap">
          <div style="font-size:11px;font-weight:700;color:var(--amber)">&#9881; Flush outputs</div>
          <div id="ftl"></div>
          <div id="fctrl">
            <button onclick="fPrev()">&#9664;</button>
            <button onclick="fNext()">&#9654;</button>
            <button onclick="exitFlush()">&times; Real</button>
            <span id="fplbl"></span>
          </div>
        </div>
      </div>
      <!-- Simulate: frame info line -->
      <div id="sim-frame-info"></div>
      <!-- Frame canvas -->
      <div class="cwrap" id="frame-wrap"><canvas id="fc"></canvas></div>
      <!-- Simulate legend -->
      <div id="sim-legend-card" style="display:none;background:var(--bg3);border-radius:6px;padding:6px;flex-shrink:0">
        <div id="sl-title" style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-bottom:3px">Legend</div>
        <div id="sl-rows" style="display:flex;flex-direction:column;gap:2px"></div>
      </div>
    </div>
  </div>
</div>

<script>
'use strict';
const DATA=__DATA_JSON__;
const CFG=DATA.configs;
const NTOT=CFG.length;

// ── Colour utilities ──────────────────────────────────────────────────────
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
const sm={cyc:0,tl:[],playing:false,timer:null,built_for:-1};
let mode='explore';

// ── Timeline builder ─────────────────────────────────────────────────────
// Each entry: {cycle,phase,t0,t1,t2,output,stall_note}
// t0/t1/t2: null | {fn,row,col_eff,value,is_dummy,is_flush_row,is_sof}
// output: null | {out_r,out_c,fn,taps,is_flush}
function getOutput(c,p2){
  if(!p2)return null;
  const hr=c.half_r,hc=c.half_c,lw=c.line_width,fh=c.frame_height,nf=c.num_frames;
  if(c.streaming){
    // Cross-frame output: global_row drives output coordinates.
    const global_row=p2.fn*fh+p2.row;
    if(global_row<hr||p2.col_eff<hc)return null;
    const o_r_global=global_row-hr;
    const out_fn=Math.floor(o_r_global/fh);
    const o_r=o_r_global%fh;
    const o_c=p2.col_eff-hc;
    if(o_c>=lw||out_fn>=nf)return null;
    const taps=(c.taps[out_fn]||[])[o_r*lw+o_c];
    return taps?{out_r:o_r,out_c:o_c,fn:out_fn,taps,is_flush:false}:null;
  }
  // FLUSH=true non-TOROIDAL: per-frame row check.
  if(p2.row<hr||p2.col_eff<hc)return null;
  const o_r=p2.row-hr,o_c=p2.col_eff-hc;
  if(o_c>=lw)return null;
  if(p2.row>=fh){
    const foi=c.flush_out_rows.indexOf(o_r);
    if(foi<0)return null;
    const taps=(c.flush_taps[p2.fn]||[])[foi*lw+o_c];
    return taps?{out_r:o_r,out_c:o_c,fn:p2.fn,taps,is_flush:true}:null;
  }
  const taps=(c.taps[p2.fn]||[])[o_r*lw+o_c];
  return taps?{out_r:o_r,out_c:o_c,fn:p2.fn,taps,is_flush:false}:null;
}

function buildTimeline(c){
  const lw=c.line_width,fh=c.frame_height,nf=c.num_frames;
  const hr=c.half_r,hc=c.half_c,eff=lw+hc;
  const cyc=[],bp=c.bp||null;
  let p1=null,p2=null,acc=0;

  const addReset=()=>cyc.push({cycle:cyc.length,phase:'RESET',t0:null,t1:null,t2:null,output:null,stall_note:null});
  const addStall=(note)=>cyc.push({cycle:cyc.length,phase:'STALL',t0:null,t1:p1,t2:p2,output:null,stall_note:note});
  const addAccept=(pix)=>{
    const out=getOutput(c,p2);
    let phase;
    if(out)                                        phase='OUTPUT';
    else if(pix.is_flush_row&&!pix.is_dummy)        phase='FLUSH';
    else if(pix.is_dummy&&!out)                     phase='DUMMY';
    else                                            phase='FILL';
    // Refine: flush row or streaming-tail producing an output
    if(out&&p2&&p2.row>=fh)                         phase='FLUSH_OUT';
    if(out&&c.streaming&&p2&&out.fn<p2.fn)          phase='TAIL_OUT';
    cyc.push({cycle:cyc.length,phase,t0:pix,t1:p1,t2:p2,output:out,stall_note:null});
    p2=p1; p1=pix; acc++;
  };

  // 5 reset cycles
  for(let i=0;i<5;i++) addReset();

  // Post-reset back-pressure stall
  if(bp&&bp.type==='post_reset')
    for(let i=0;i<bp.stall_cycles;i++) addStall(bp.note);

  let mid_done=false;

  for(let fn=0;fn<nf;fn++){
    const frame=c.frames[fn]||[];
    for(let row=0;row<fh;row++){
      for(let ce=0;ce<eff;ce++){
        // Mid-sim back-pressure check
        if(bp&&bp.type==='mid_sim'&&!mid_done&&acc>=bp.start_cycle){
          for(let i=0;i<bp.stall_cycles;i++) addStall(bp.note);
          mid_done=true;
        }
        const val=ce<lw?((frame[row*lw+ce])||0):0;
        addAccept({fn,row,col_eff:ce,col:ce<lw?ce:null,value:val,
                   is_dummy:ce>=lw,is_flush_row:false,
                   is_sof:row===0&&ce===0});
      }
    }
    if(c.flush&&hr>0){
      for(let fr=0;fr<hr;fr++){
        const vrow=fh+fr;
        for(let ce=0;ce<eff;ce++){
          const val=0;
          addAccept({fn,row:vrow,col_eff:ce,col:ce<lw?ce:null,value:val,
                     is_dummy:ce>=lw,is_flush_row:true,
                     is_sof:false});
        }
      }
    }
  }
  // Two drain cycles so last two pixels get their output shown
  for(let i=0;i<2;i++){
    const out=getOutput(c,p2);
    const phase=out?(p2&&p2.row>=fh?'FLUSH_OUT':(c.streaming&&out&&p2&&out.fn<p2.fn?'TAIL_OUT':'OUTPUT')):'FILL';
    cyc.push({cycle:cyc.length,phase,t0:null,t1:p1,t2:p2,output:out,stall_note:null});
    p2=p1; p1=null;
  }
  return cyc;
}

function ensureTimeline(c){
  if(sm.built_for===c.idx&&sm.tl.length)return;
  sm.tl=buildTimeline(c);
  sm.built_for=c.idx;
  sm.cyc=0;
}

// ── Padding pixel (for explore + frame canvas) ────────────────────────────
function getPaddingPixel(c,frame,pr,pc){
  const fh=c.frame_height,lw=c.line_width;
  if(pr>=0&&pr<fh&&pc>=0&&pc<lw)return frame[pr*lw+pc];
  if(c.flush&&c.half_r>0&&pr>=fh&&pr<fh+c.half_r)return 0;
  if(c.edge_mode==='ZERO')return 0;
  if(c.edge_mode==='REPLICATE'){
    return frame[Math.max(0,Math.min(pr,fh-1))*lw+Math.max(0,Math.min(pc,lw-1))];
  }
  if(c.edge_mode==='TOROIDAL'){
    return frame[((pr%fh)+fh)%fh*lw+((pc%lw)+lw)%lw];
  }
  return 0;
}

function computeExploreTaps(c,fn,out_r,out_c){
  const frame=c.frames[fn]||[];
  const hr=c.half_r,hc=c.half_c,kr=c.kern_rows,kc=c.kern_cols;
  const taps=[];
  for(let tr=0;tr<kr;tr++)
    for(let tc=0;tc<kc;tc++)
      taps.push(getPaddingPixel(c,frame,out_r+tr-hr,out_c+tc-hc));
  return taps;
}

// ── Hatch ─────────────────────────────────────────────────────────────────
function hatch(ctx,x,y,w,h){
  ctx.save();ctx.beginPath();ctx.rect(x,y,w,h);ctx.clip();
  ctx.strokeStyle='rgba(255,255,255,0.06)';ctx.lineWidth=1;
  for(let d=-h;d<w+h;d+=7){ctx.beginPath();ctx.moveTo(x+d,y);ctx.lineTo(x+d+h,y+h);ctx.stroke();}
  ctx.restore();
}

// ── Frame canvas ──────────────────────────────────────────────────────────
function renderFrameCanvas(c,frameIdx,highlights,kernelAnchor){
  const lw=c.line_width,fh=c.frame_height,hr=c.half_r,hc=c.half_c;
  const kr=c.kern_rows,kc=c.kern_cols,dw=c.data_width;
  const vmax=(1<<Math.min(dw,30))-1;
  const flushHr=c.flush&&hr>0?hr:0;
  const totR=fh+2*hr,totC=lw+2*hc;
  const wrap=document.getElementById('frame-wrap');
  const avW=wrap.clientWidth||300,avH=wrap.clientHeight||300;
  const cell=Math.max(8,Math.min(52,Math.floor(Math.min(avW-4,520)/Math.max(totC,1)),
                                    Math.floor(Math.min(avH-4,520)/Math.max(totR,1))));
  const can=document.getElementById('fc');
  can.width=totC*cell;can.height=totR*cell;
  const ctx=can.getContext('2d');
  ctx.clearRect(0,0,can.width,can.height);
  const frame=c.frames[frameIdx]||[];
  for(let pr=-hr;pr<fh+hr;pr++){
    for(let pc=-hc;pc<lw+hc;pc++){
      const canR=pr+hr,canC=pc+hc;
      const inReal=pr>=0&&pr<fh&&pc>=0&&pc<lw;
      const inFlush=!inReal&&pr>=fh&&pr<fh+hr&&flushHr>0&&pc>=0&&pc<lw;
      const val=getPaddingPixel(c,frame,pr,pc);
      const norm=val/vmax;
      if(inReal)               ctx.fillStyle=rgb(ylOrRd(norm));
      else if(inFlush)         ctx.fillStyle='#141428';
      else if(c.edge_mode==='ZERO') ctx.fillStyle='rgba(60,60,80,0.7)';
      else{const cl=ylOrRd(norm);ctx.fillStyle=`rgba(${cl[0]},${cl[1]},${cl[2]},0.55)`;}
      ctx.fillRect(canC*cell,canR*cell,cell,cell);
      if(inFlush)hatch(ctx,canC*cell,canR*cell,cell,cell);
      ctx.strokeStyle=inReal?'rgba(0,0,0,0.18)':'rgba(100,100,150,0.15)';
      ctx.lineWidth=.5;ctx.strokeRect(canC*cell+.5,canR*cell+.5,cell-1,cell-1);
      if(cell>=16&&inReal){
        const cl=ylOrRd(norm);ctx.fillStyle=txtClr(cl);
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
  ctx.setLineDash([4,3]);ctx.strokeStyle='rgba(180,180,220,0.35)';ctx.lineWidth=1;
  ctx.strokeRect(hc*cell+.5,hr*cell+.5,lw*cell-1,fh*cell-1);ctx.setLineDash([]);
  if(flushHr>0){
    ctx.setLineDash([5,3]);ctx.strokeStyle='#dfb050';ctx.lineWidth=1.5;
    ctx.strokeRect(hc*cell+.5,hr*cell+.5,lw*cell-1,fh*cell-1);ctx.setLineDash([]);
  }
  if(kernelAnchor){
    const ka=kernelAnchor;
    ctx.fillStyle='rgba(58,111,220,0.18)';
    for(let dr=0;dr<kr;dr++)for(let dc=0;dc<kc;dc++){
      const cr=ka.out_r+dr,cc=ka.out_c+dc;
      if(cr>=0&&cr<totR&&cc>=0&&cc<totC)ctx.fillRect(cc*cell+1,cr*cell+1,cell-2,cell-2);
    }
    ctx.strokeStyle='rgba(58,143,255,0.5)';ctx.lineWidth=1;
    for(let dr=0;dr<kr;dr++)for(let dc=0;dc<kc;dc++){
      const cr=ka.out_r+dr,cc=ka.out_c+dc;
      if(cr>=0&&cr<totR&&cc>=0&&cc<totC)ctx.strokeRect(cc*cell+1,cr*cell+1,cell-2,cell-2);
    }
  }
  const ST={
    ex: {stroke:'#3a8fff',fill:'rgba(58,143,255,0)',  circle:true},
    out:{stroke:'#3a8fff',fill:'rgba(58,143,255,0.18)',circle:false},
    trg:{stroke:'#3abf7a',fill:'rgba(58,191,122,0.22)',circle:false},
    inp:{stroke:'#dfb050',fill:'rgba(220,180,80,0.18)',circle:false},
  };
  for(const h of highlights){
    const cr=h.out_r+hr,cc=h.out_c+hc;
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
function renderTapCanvas(c,taps,out_r,out_c,canvasId,colLabId,rowLabId,rowOuterId){
  const kr=c.kern_rows,kc=c.kern_cols,dw=c.data_width;
  const fh=c.frame_height,lw=c.line_width,hr=c.half_r,hc=c.half_c,em=c.edge_mode;
  const vmax=(1<<Math.min(dw,30))-1;
  if(!taps)return;
  const panel=document.getElementById(rowOuterId).parentElement.parentElement;
  const pW=panel.clientWidth-28-12,pH=panel.clientHeight-60;
  const cell=Math.max(12,Math.min(48,
    Math.floor(Math.min(pW,280)/Math.max(kc,1)),
    Math.floor(Math.min(pH,280)/Math.max(kr,1))));
  // Column labels
  const colDiv=document.getElementById(colLabId);colDiv.innerHTML='';
  for(let tc=0;tc<kc;tc++){
    const sc=out_c+tc-hc,oob=sc<0||sc>=lw;
    const el=document.createElement('div');
    el.className='tlbl';el.style.width=cell+'px';el.style.height='14px';
    el.style.color=oob?'#df5050':'#5a7a8a';el.textContent=sc;
    colDiv.appendChild(el);
  }
  // Row labels
  const rowDiv=document.getElementById(rowLabId);rowDiv.innerHTML='';
  for(let tr=0;tr<kr;tr++){
    const sr=out_r+tr-hr,oob=sr<0||sr>=fh;
    const el=document.createElement('div');
    el.className='tlbl';el.style.height=cell+'px';el.style.width='24px';
    el.style.color=oob?'#df5050':'#5a7a8a';el.textContent=sr;
    rowDiv.appendChild(el);
  }
  const can=document.getElementById(canvasId);
  can.width=kc*cell;can.height=kr*cell;
  const ctx=can.getContext('2d');
  for(let tr=0;tr<kr;tr++)for(let tc=0;tc<kc;tc++){
    const val=taps[tr*kc+tc];
    const norm=val/vmax;
    const sr=out_r+tr-hr,sc=out_c+tc-hc;
    const isOob=sr<0||sr>=fh||sc<0||sc>=lw;
    let face,edge;
    if(isOob&&em==='ZERO')          {face=[90,90,110];edge=null;}
    else if(isOob&&em==='REPLICATE'){face=ylOrRd(norm);edge='#4682b4';}
    else if(isOob&&em==='TOROIDAL') {face=ylOrRd(norm);edge='#9370db';}
    else                            {face=ylOrRd(norm);edge=null;}
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
  ctx.strokeStyle='#3a8fff';ctx.lineWidth=3;
  ctx.strokeRect(hc*cell+2,hr*cell+2,cell-4,cell-4);
}

// ── Timeline list rendering ───────────────────────────────────────────────
const PHASE_DESC={
  RESET:     'Synchronous reset active — all registers zeroed',
  STALL:     'Back-pressure: downstream not ready — pipeline frozen',
  FILL:      'Pixel accepted — pipeline filling, no output yet',
  OUTPUT:    'Pixel accepted — real frame output at port this cycle',
  FLUSH_OUT: 'Flush zero accepted — flush output at port this cycle',
  TAIL_OUT:  'Next-frame pixel accepted — streaming tail output for previous frame',
  DUMMY:     'Dummy column zero accepted — right-edge padding',
  FLUSH:     'Flush zero row accepted — pipeline draining',
};
const PHASE_INFOSTR={
  RESET:    n=>'rst=1',
  STALL:    n=>'bp',
  FILL:     n=>n.t0?pixStr(n.t0):'drain',
  OUTPUT:   n=>n.t0?pixStr(n.t0):'drain',
  FLUSH_OUT:n=>n.t0?pixStr(n.t0):'drain',
  TAIL_OUT: n=>n.t0?pixStr(n.t0):'drain',
  DUMMY:    n=>n.t0?`ce=${n.t0.col_eff}`:'',
  FLUSH:    n=>n.t0?`r=${n.t0.row},ce=${n.t0.col_eff}`:'',
};
function pixStr(p){
  if(!p)return'—';
  if(p.is_dummy)return`dummy ce=${p.col_eff}`;
  if(p.is_flush_row)return`flush r=${p.row}`;
  return`r=${p.row},c=${p.col_eff}`;
}

function buildTimelineDOM(c){
  const list=document.getElementById('tl-list');
  list.innerHTML='';
  const tl=sm.tl;
  document.getElementById('tl-count').textContent=`${tl.length} cycles`;
  const frag=document.createDocumentFragment();
  for(let i=0;i<tl.length;i++){
    const e=tl[i];
    const el=document.createElement('div');
    el.className='tc';el.dataset.i=i;
    const info=(PHASE_INFOSTR[e.phase]||(_=>e.phase))(e);
    el.innerHTML=
      `<span class="tc-num">${i}</span>`+
      `<span class="tc-ph ph-${e.phase}">${e.phase.replace('_OUT','&#8599;')}</span>`+
      `<span class="tc-info">${e.stall_note?'&#9888; '+e.stall_note.slice(0,20):info}</span>`;
    el.onclick=()=>{sm.cyc=i;renderSim();};
    frag.appendChild(el);
  }
  list.appendChild(frag);
}

function updateTimelineCursor(){
  document.querySelectorAll('.tc').forEach(el=>{
    el.classList.toggle('cur',+el.dataset.i===sm.cyc);
  });
  const cur=document.querySelector('.tc.cur');
  if(cur)cur.scrollIntoView({block:'nearest'});
}

// ── Pipeline card update ──────────────────────────────────────────────────
function setPcard(id,swId,posId,valId,p,c,highlight){
  const vmax=(1<<Math.min(c.data_width,30))-1;
  const card=document.getElementById(id);
  card.className='pcard'+(highlight?' '+highlight:'')+(p?'':' p-null');
  const sw=document.getElementById(swId);
  if(!p){
    document.getElementById(posId).textContent='— (empty)';
    document.getElementById(valId).textContent='';
    sw.style.background='#1e1e35';sw.style.borderColor='#252540';return;
  }
  let pos,val_s,swc;
  if(p.is_flush_row&&!p.is_dummy){
    pos=`Frame ${p.fn} · flush row ${p.row}` + (p.col!==null?` · col ${p.col}`:'');
    val_s='value = 0  (flush-injected zero)';
    swc='#1e2a3a';
  } else if(p.is_dummy){
    pos=`Frame ${p.fn} · row ${p.row} · col_eff ${p.col_eff} (dummy)`;
    val_s='value = 0  (right-edge zero padding)';
    swc='#1e1e35';
  } else {
    pos=`Frame ${p.fn} · row ${p.row} · col ${p.col_eff}`;
    val_s=`value = ${p.value}  (${Math.round(p.value/vmax*100)}%)`;
    swc=rgb(ylOrRd(p.value/vmax));
  }
  document.getElementById(posId).textContent=pos;
  document.getElementById(valId).textContent=val_s;
  sw.style.background=swc;sw.style.borderColor='#3a3a5a';
}

// ── State bar ─────────────────────────────────────────────────────────────
function setStateBar(phase,entry,c){
  const badge=document.getElementById('ph-big');
  badge.className=`ph-big ph-${phase}`;
  badge.textContent=phase.replace('_OUT','→OUT');
  let desc=PHASE_DESC[phase]||phase;
  if(entry.stall_note)desc=entry.stall_note;
  else if(entry.output){
    const o=entry.output;
    desc+=`  →  Output @ (row ${o.out_r}, col ${o.out_c}) Frame ${o.fn}` +(o.is_flush?' [flush]':'');
  }
  document.getElementById('state-desc').textContent=desc;
}

// ── Sim render ────────────────────────────────────────────────────────────
function renderSim(){
  const c=CFG[ex.ci];
  ensureTimeline(c);
  const tl=sm.tl;
  if(!tl.length)return;
  const e=tl[sm.cyc];
  const sc=document.getElementById('scrubber');
  sc.max=tl.length-1;sc.value=sm.cyc;
  document.getElementById('cyc-lbl').textContent=`Cycle ${sm.cyc}/${tl.length-1}`;
  // State bar
  setStateBar(e.phase,e,c);
  // Pipeline cards
  setPcard('pc-t2','pt2-sw','pt2-pos','pt2-val',e.t2,c,'p-out');
  setPcard('pc-t1','pt1-sw','pt1-pos','pt1-val',e.t1,c,'');
  setPcard('pc-t0','pt0-sw','pt0-pos','pt0-val',e.t0,c,'p-in');
  // Output section
  if(e.output){
    const o=e.output;
    const vmax=(1<<Math.min(c.data_width,30))-1;
    document.getElementById('out-status').textContent=
      o.is_flush?'&#10003; FLUSH OUTPUT':'&#10003; OUTPUT VALID';
    document.getElementById('out-status').style.color=o.is_flush?'var(--cyan)':'var(--green)';
    document.getElementById('out-pos').textContent=
      `Frame ${o.fn} · row ${o.out_r} · col ${o.out_c}`;
    document.getElementById('tp-title').textContent=
      `Tap grid @ (row ${o.out_r}, col ${o.out_c})`;
    document.getElementById('tp-coords').textContent=
      `centre=(${o.out_r},${o.out_c}) · ${c.edge_mode}${o.is_flush?' · flush output':''}`;
    renderTapCanvas(c,o.taps,o.out_r,o.out_c,'tc','tcol-labels','trow-labels','trow-outer');
  } else {
    document.getElementById('out-status').innerHTML='&#8212; NO OUTPUT THIS CYCLE';
    document.getElementById('out-status').style.color='var(--dim)';
    document.getElementById('out-pos').textContent='';
    document.getElementById('tp-title').textContent='Output tap grid';
    document.getElementById('tp-coords').textContent='(no output this cycle)';
    // Clear tap canvas
    const can=document.getElementById('tc');
    const ctx=can.getContext('2d');
    ctx.clearRect(0,0,can.width,can.height);
    document.getElementById('tcol-labels').innerHTML='';
    document.getElementById('trow-labels').innerHTML='';
  }
  // Frame canvas highlights
  const hl=[];
  // Blue = output pixel
  if(e.output) hl.push({out_r:e.output.out_r,out_c:e.output.out_c,style:'out'});
  // Green = trigger pixel (t2, if in real frame)
  if(e.t2&&!e.t2.is_flush_row&&!e.t2.is_dummy&&e.t2.row<c.frame_height)
    hl.push({out_r:e.t2.row,out_c:e.t2.col_eff,style:'trg'});
  // Amber = input pixel (t0, if real)
  if(e.t0&&!e.t0.is_flush_row&&!e.t0.is_dummy&&e.t0.row<c.frame_height)
    hl.push({out_r:e.t0.row,out_c:e.t0.col_eff,style:'inp'});
  // Show the frame the OUTPUT belongs to so the highlighted output pixel is visible.
  // Fall back to trigger → delay → input → 0.
  const fn=e.output?e.output.fn:e.t2?e.t2.fn:e.t1?e.t1.fn:e.t0?e.t0.fn:0;
  const ka=e.output?{out_r:e.output.out_r,out_c:e.output.out_c}:null;
  renderFrameCanvas(c,fn,hl,ka);
  document.getElementById('sim-frame-info').textContent=
    `Frame ${fn} of ${c.num_frames-1}  |  Cycle ${sm.cyc}/${tl.length-1}`;
  updateTimelineCursor();
  // Sim legend
  renderSimLegend(c);
}

function renderSimLegend(c){
  const card=document.getElementById('sim-legend-card');
  card.style.display='block';
  const rows=[
    {sw:'background:rgba(220,180,80,.18);border:2px solid #dfb050',txt:'Amber = pixel accepted this cycle (T)'},
    {sw:'background:rgba(58,191,122,.22);border:2px solid #3abf7a',txt:'Green = trigger pixel 2 cycles ago (T&#8722;2)'},
    {sw:'background:rgba(58,143,255,.18);border:2px solid #3a8fff',txt:'Blue = centre of output this cycle'},
    {sw:'background:rgba(58,111,220,.18);border:1px solid rgba(58,143,255,.5)',txt:'Tint = kernel footprint'},
    {sw:'background:rgba(60,60,80,.7)',txt:'Dark = OOB zero padding (ZERO mode)'},
  ];
  if(c.edge_mode==='REPLICATE')
    rows.push({sw:'background:#fd8d3c;opacity:.55',txt:'Amber dim = replicated edge (REPLICATE)'});
  if(c.edge_mode==='TOROIDAL')
    rows.push({sw:'background:#b07adf;opacity:.55',txt:'Purple dim = toroidal wrap (TOROIDAL)'});
  if(c.streaming&&c.half_r>0)
    rows.push({sw:'background:#1a2a00;border:2px solid #88bb44',txt:'Green-dim = streaming tail output (previous frame)'});
  if(c.flush&&c.half_r>0)
    rows.push({sw:'background:#141428;border:2px dashed #dfb050',txt:'Hatched = flush-injected zeros'});
  document.getElementById('sl-rows').innerHTML=
    rows.map(r=>`<div class="lrow"><div class="lsw" style="${r.sw}"></div><span>${r.txt}</span></div>`).join('');
}

// ── Explore render ────────────────────────────────────────────────────────
function renderExplore(){
  const c=CFG[ex.ci];
  document.getElementById('flbl').textContent=`Frame ${ex.fn}/${c.num_frames-1}`;
  updateFlushPlayer();
  let taps;
  if(ex.isFlush)taps=c.flush_taps[ex.fn]&&c.flush_taps[ex.fn][ex.fr*c.line_width+ex.col];
  else          taps=c.taps[ex.fn]&&c.taps[ex.fn][ex.row*c.line_width+ex.col];
  if(!taps)taps=computeExploreTaps(c,ex.fn,ex.row,ex.col);
  renderFrameCanvas(c,ex.fn,
    [{out_r:ex.row,out_c:ex.col,style:'ex'}],
    {out_r:ex.row,out_c:ex.col});
  renderTapCanvas(c,taps,ex.row,ex.col,'exp-tc','exp-tcol-labels','exp-trow-labels','exp-trow-outer');
  const rc=Math.max(0,c.frame_height-c.half_r);
  const noOut=!ex.isFlush&&ex.row>=rc;
  document.getElementById('exp-tp-title').textContent=
    ex.isFlush?`Kernel @ flush output row ${ex.row}`
    :noOut?`Kernel @ (${ex.row},${ex.col}) [no RTL output — computed]`
    :`Kernel @ (${ex.row}, ${ex.col})`;
  document.getElementById('exp-tp-coords').textContent=
    `Frame ${ex.fn} · centre=(${ex.row},${ex.col}) · ${c.edge_mode}`+(noOut?' · FLUSH=off: no output here':'');
  renderExploreLegend(c);
}

function renderExploreLegend(c){
  const rows=[
    {sw:'background:none;border:2px solid #3a8fff;border-radius:50%',txt:'Blue circle = selected pixel'},
    {sw:'background:rgba(58,111,220,.18);border:1px solid rgba(58,143,255,.5)',txt:'Tint = kernel footprint'},
    {sw:'background:rgba(60,60,80,.7)',txt:'Dark = OOB zero (ZERO mode)'},
  ];
  if(c.edge_mode==='REPLICATE')
    rows.push({sw:'background:#fd8d3c;opacity:.55',txt:'Amber (dim) = replicated edge'});
  if(c.edge_mode==='TOROIDAL')
    rows.push({sw:'background:#b07adf;opacity:.55',txt:'Purple (dim) = toroidal wrap'});
  if(c.flush&&c.half_r>0)
    rows.push({sw:'background:#141428;border:2px dashed #dfb050',txt:'Hatched = flush zeros'});
  rows.push({sw:'background:none;border:2.5px solid #3a8fff',txt:'Blue square = centre tap'});
  rows.push({sw:'background:none;border:2px solid #4682b4',txt:'* = OOB clamped (REPLICATE)'});
  rows.push({sw:'background:none;border:2px solid #9370db',txt:'* = OOB wrapped (TOROIDAL)'});
  document.getElementById('legend-rows').innerHTML=
    rows.map(r=>`<div class="lrow"><div class="lsw" style="${r.sw}"></div><span>${r.txt}</span></div>`).join('');
}

// ── Top bar ───────────────────────────────────────────────────────────────
function renderTopBar(c,ci){
  const bm=document.getElementById('b-mode');
  bm.textContent=c.edge_mode;
  bm.className='badge '+{ZERO:'bz',REPLICATE:'br',TOROIDAL:'bt'}[c.edge_mode];
  document.getElementById('b-flush').style.display=c.flush?'':'none';
  document.getElementById('cfg-toplbl').textContent=
    `CFG${String(ci+1).padStart(2,'0')} · ${c.data_width}b · ${c.kern_rows}&#215;${c.kern_cols} · ${c.line_width}&#215;${c.frame_height}`;
}

// ── Main render entry ─────────────────────────────────────────────────────
function render(){
  const c=CFG[ex.ci];
  renderTopBar(c,ex.ci);
  if(mode==='simulate') renderSim();
  else renderExplore();
}

// ── Mode switch ───────────────────────────────────────────────────────────
function setMode(m){
  mode=m;
  const isSim=m==='simulate';
  document.getElementById('btn-explore') .classList.toggle('act',!isSim);
  document.getElementById('btn-simulate').classList.toggle('act', isSim);
  document.getElementById('tl-panel')       .classList.toggle('vis', isSim);
  document.getElementById('sim-ctrl')       .classList.toggle('vis', isSim);
  document.getElementById('center')         .style.display=isSim?'flex':'none';
  document.getElementById('explore-center') .classList.toggle('vis',!isSim);
  document.getElementById('explore-ctrl')   .style.display=isSim?'none':'flex';
  document.getElementById('sim-frame-info') .classList.toggle('vis', isSim);
  document.getElementById('sim-legend-card').style.display=isSim?'':'none';
  if(isSim){
    ensureTimeline(CFG[ex.ci]);
    buildTimelineDOM(CFG[ex.ci]);
  }
  render();
}

// ── Config selection ──────────────────────────────────────────────────────
function selectCfg(i){
  ex.ci=i;ex.fn=0;ex.isFlush=false;ex.fr=0;
  ex.fplaying=false;clearInterval(ex.ftimer);
  const c=CFG[i];
  ex.row=Math.min(ex.row,c.frame_height-1);
  ex.col=Math.min(ex.col,c.line_width-1);
  sm.built_for=-1;sm.cyc=0;sm.playing=false;clearInterval(sm.timer);
  document.querySelectorAll('.ci').forEach(el=>el.classList.remove('active'));
  const el=document.querySelector(`.ci[data-i="${i}"]`);
  if(el){el.classList.add('active');el.scrollIntoView({block:'nearest'});}
  if(mode==='simulate'){
    ensureTimeline(c);
    buildTimelineDOM(c);
  }
  render();
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
  document.getElementById('cfg-count').textContent=`${vis}/${NTOT} configs`;
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

// ── Sim controls ──────────────────────────────────────────────────────────
function simStepFwd(){if(sm.cyc<sm.tl.length-1){sm.cyc++;renderSim();updateTimelineCursor();}}
function simStepBck(){if(sm.cyc>0){sm.cyc--;renderSim();updateTimelineCursor();}}
function simGoFirst(){sm.cyc=0;renderSim();updateTimelineCursor();}
function simGoLast() {sm.cyc=Math.max(0,sm.tl.length-1);renderSim();updateTimelineCursor();}
function scrubTo(v) {sm.cyc=v;renderSim();updateTimelineCursor();}
function togglePlay(){
  sm.playing=!sm.playing;
  document.getElementById('btn-play').textContent=sm.playing?'⏸':'▶';
  clearInterval(sm.timer);
  if(sm.playing){
    sm.timer=setInterval(()=>{
      if(sm.cyc>=sm.tl.length-1){sm.playing=false;document.getElementById('btn-play').textContent='▶';clearInterval(sm.timer);}
      else{sm.cyc++;renderSim();updateTimelineCursor();}
    },+document.getElementById('spd-sel').value);
  }
}
document.getElementById('spd-sel').onchange=()=>{
  if(sm.playing){clearInterval(sm.timer);sm.timer=setInterval(()=>{
    if(sm.cyc>=sm.tl.length-1){sm.playing=false;clearInterval(sm.timer);}
    else{sm.cyc++;renderSim();updateTimelineCursor();}
  },+document.getElementById('spd-sel').value);}
};

// ── Explore: frame nav ────────────────────────────────────────────────────
function prevFrame(){const nf=CFG[ex.ci].num_frames;ex.fn=(ex.fn-1+nf)%nf;ex.isFlush=false;render();}
function nextFrame(){const nf=CFG[ex.ci].num_frames;ex.fn=(ex.fn+1)%nf;ex.isFlush=false;render();}

// ── Explore: flush player ─────────────────────────────────────────────────
function buildFlushTL(){
  const c=CFG[ex.ci],tl=document.getElementById('ftl');tl.innerHTML='';
  const rc=Math.max(0,c.frame_height-c.half_r);
  if(!c.flush||!c.flush_out_rows.length)return;
  for(let r=0;r<rc;r++){
    const el=document.createElement('div');el.className='tr real';el.textContent=r;
    const _r=r;el.onclick=()=>{ex.isFlush=false;ex.row=_r;render();};tl.appendChild(el);
  }
  const sep=document.createElement('span');sep.className='tlsep';sep.textContent='│';tl.appendChild(sep);
  c.flush_out_rows.forEach((out_r,fi)=>{
    const el=document.createElement('div');el.className='tr flush-tl';el.textContent=out_r;
    const _fi=fi,_r=out_r;el.onclick=()=>{ex.isFlush=true;ex.fr=_fi;ex.row=_r;render();};tl.appendChild(el);
  });
}
function updateFlushTL(){
  document.querySelectorAll('.tr.real'    ).forEach((el,i)=>el.classList.toggle('act',!ex.isFlush&&i===ex.row));
  document.querySelectorAll('.tr.flush-tl').forEach((el,i)=>el.classList.toggle('act', ex.isFlush&&i===ex.fr));
  document.getElementById('fplbl').textContent=
    ex.isFlush?`Flush · row ${ex.row}`:`Real · row ${ex.row}`;
}
function updateFlushPlayer(){
  const c=CFG[ex.ci],fp=document.getElementById('fplay-wrap');
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
  clearInterval(ex.ftimer);
  if(ex.fplaying)ex.ftimer=setInterval(fNext,400);
}

// ── Frame canvas click (explore mode) ────────────────────────────────────
document.getElementById('fc').addEventListener('click',function(e){
  if(mode!=='explore')return;
  const c=CFG[ex.ci],lw=c.line_width,fh=c.frame_height,hr=c.half_r,hc=c.half_c;
  const totC=lw+2*hc,cell=this.width/totC;
  const canR=Math.floor((e.clientY-this.getBoundingClientRect().top)/cell);
  const canC=Math.floor((e.clientX-this.getBoundingClientRect().left)/cell);
  const pr=canR-hr,pc=canC-hc;
  if(pr<0||pr>=fh||pc<0||pc>=lw)return;
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
    const lw=c.line_width;
    if(e.altKey&&e.key==='ArrowLeft')  prevFrame();
    else if(e.altKey&&e.key==='ArrowRight') nextFrame();
    else if(e.key==='ArrowLeft') {ex.col=Math.max(0,ex.col-1);render();}
    else if(e.key==='ArrowRight'){ex.col=Math.min(lw-1,ex.col+1);render();}
    else if(e.key==='ArrowUp')   {if(!ex.isFlush&&ex.row>0){ex.row--;render();}}
    else if(e.key==='ArrowDown') {if(!ex.isFlush&&ex.row<c.frame_height-1){ex.row++;render();}}
    else if(e.key===',')selectCfg(Math.max(0,ex.ci-1));
    else if(e.key==='.')selectCfg(Math.min(NTOT-1,ex.ci+1));
    else if((e.key==='f'||e.key==='F')&&c.flush&&c.flush_out_rows.length)fNext();
    else if(e.key==='r'||e.key==='R')exitFlush();
    else handled=false;
  } else {
    if(e.key==='ArrowRight'||e.key==='ArrowDown')simStepFwd();
    else if(e.key==='ArrowLeft'||e.key==='ArrowUp')simStepBck();
    else if(e.key==='Home')simGoFirst();
    else if(e.key==='End') simGoLast();
    else if(e.key===' ')   togglePlay();
    else if(e.key===',')   selectCfg(Math.max(0,ex.ci-1));
    else if(e.key==='.')   selectCfg(Math.min(NTOT-1,ex.ci+1));
    else handled=false;
  }
  if(handled)e.preventDefault();
});

window.addEventListener('resize',render);

// ── Init ──────────────────────────────────────────────────────────────────
buildList();
applyFilters();
setMode('simulate');
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
