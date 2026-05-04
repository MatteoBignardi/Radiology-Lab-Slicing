// topology.js - UI a segmenti (no immagini overlay), pronta per futuro "send packet"
const API = (window.location.port === "8080") ? "" : "http://localhost:8080";

// In earlier iterations we swapped SVG <image> icons between day/night themes.
// The current UI uses theme-aware CSS only, so icons don't need any runtime work.
// We keep this as a no-op to avoid breaking older event handlers.
function applyNodeIcons(){ /* intentionally empty */ }

const els = {
  modeLabel: document.getElementById("modeLabel"),
  slicingLabel: document.getElementById("slicingLabel"),

  btnDay: document.getElementById("btnDay"),
  btnNight: document.getElementById("btnNight"),
  btnTopo: document.getElementById("btnTopo"),
  btnService: document.getElementById("btnService"),

  cardTopology: document.getElementById("cardTopology"),
  cardService: document.getElementById("cardService"),

  slice1: document.getElementById("slice1"),
  slice2: document.getElementById("slice2"),
  slice3: document.getElementById("slice3"),

  videoToggle: document.getElementById("videoToggle"),

  openSendPacket: document.getElementById("openSendPacket"),
  modal: document.getElementById("modal"),
  closeModal: document.getElementById("closeModal"),
  spCancel: document.getElementById("spCancel"),
};

const linkIds = {
  // Top path
  s1s2: "l_s1_s2",
  s2s3: "l_s2_s3",
  s3s4: "l_s3_s4",
  s4s5: "l_s4_s5",
  s5s8: "l_s5_s8",
  // Bottom path
  s1s6: "l_s1_s6",
  s6s3: "l_s6_s3",
  s3s7: "l_s3_s7",
  s7s8: "l_s7_s8",
  s5brokerBase: "l_s5_broker_base",
  s5brokerOv: "l_s5_broker_ov",
  s6secsrvOv: "l_s6_secsrv_ov",
  s6cam1Ov: "l_s6_cam1_ov",
  s6cam2Ov: "l_s6_cam2_ov",
  s8adminpcOv: "l_s8_adminpc_ov",
  s1ws1: "l_s1_ws1",
  s1ws2: "l_s1_ws2",
  s1prnD: "l_s1_prnD",
  s2acq1: "l_s2_acq1",
  s2prnI: "l_s2_prnI",
  s2acq2: "l_s2_acq2",
  s5pacs: "l_s5_pacs",
  s5ris: "l_s5_ris",
  s6secsrv: "l_s6_secsrv",
  s6cam1: "l_s6_cam1",
  s6cam2: "l_s6_cam2",
  s8adminpc: "l_s8_adminpc",
  s8risws1: "l_s8_risws1",
  s8risws2: "l_s8_risws2",
};

// Slice->segment mapping (coerente con le tue figure)

/* ===================== EXTRA ANNOTATIONS (ports + host details) ===================== */
/**
 * Port numbers are taken from first_topology/topology.py (LINKS + HOST_LINKS).
 * The SVG lines are named like l_s1_s2, l_s1_ws1, ... so we can derive where to put labels.
 */
const DEFAULT_PORT_DEFS = [
  // switch-switch (top path)
  { lineId: "l_s1_s2", a: "s1", portA: 1, b: "s2", portB: 1 },
  { lineId: "l_s2_s3", a: "s2", portA: 2, b: "s3", portB: 1 },
  { lineId: "l_s3_s4", a: "s3", portA: 2, b: "s4", portB: 1 },
  { lineId: "l_s4_s5", a: "s4", portA: 2, b: "s5", portB: 1 },
  { lineId: "l_s5_s8", a: "s5", portA: 2, b: "s8", portB: 1 },

  // switch-switch (bottom path)
  { lineId: "l_s1_s6", a: "s1", portA: 2, b: "s6", portB: 1 },
  { lineId: "l_s6_s3", a: "s6", portA: 2, b: "s3", portB: 3 },
  { lineId: "l_s3_s7", a: "s3", portA: 4, b: "s7", portB: 1 },
  { lineId: "l_s7_s8", a: "s7", portA: 2, b: "s8", portB: 2 },

  // host-switch (switch ports only)
  { lineId: "l_s1_ws1", switch: "s1", port: 3, at: "x1y1" },
  { lineId: "l_s1_ws2", switch: "s1", port: 4, at: "x1y1" },
  { lineId: "l_s1_prnD", switch: "s1", port: 5, at: "x1y1" },

  { lineId: "l_s2_acq1", switch: "s2", port: 3, at: "x1y1" },
  { lineId: "l_s2_acq2", switch: "s2", port: 4, at: "x1y1" },
  { lineId: "l_s2_prnI", switch: "s2", port: 5, at: "x1y1" },

  { lineId: "l_s6_secsrv", switch: "s6", port: 3, at: "x1y1" },
  { lineId: "l_s6_cam1", switch: "s6", port: 4, at: "x1y1" },
  { lineId: "l_s6_cam2", switch: "s6", port: 5, at: "x1y1" },

  { lineId: "l_s8_adminpc", switch: "s8", port: 3, at: "x1y1" },
  { lineId: "l_s8_risws1", switch: "s8", port: 4, at: "x1y1" },
  { lineId: "l_s8_risws2", switch: "s8", port: 5, at: "x1y1" },

  { lineId: "l_s5_pacs", switch: "s5", port: 3, at: "x1y1" },
  { lineId: "l_s5_ris", switch: "s5", port: 5, at: "x1y1" },

  // broker has two lines (base + overlay). We label using the base one.
  { lineId: "l_s5_broker_base", switch: "s5", port: 6, at: "x1y1" },
];

// PORT_DEFS effettivi: proviamo a caricarli dal controller (/ui/port-defs).
// Se il controller non espone l'endpoint, usiamo i DEFAULT_PORT_DEFS.
let PORT_DEFS = DEFAULT_PORT_DEFS;


// Host defs (label/mac/ip) vengono dal controller (/ui/host-defs).
let HOST_DEFS = {};


function createSvgText(svgNS, parent, x, y, txt, cls, anchor="middle"){
  const t = document.createElementNS(svgNS, "text");
  t.setAttribute("x", x);
  t.setAttribute("y", y);
  t.setAttribute("text-anchor", anchor);
  t.setAttribute("class", cls);
  t.textContent = txt;
  parent.appendChild(t);
  return t;
}

function createPortBadge(svgNS, parent, cx, cy, label){
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", "port-badge");

  const c = document.createElementNS(svgNS, "circle");
  c.setAttribute("cx", cx);
  c.setAttribute("cy", cy);
  c.setAttribute("r", "7");
  g.appendChild(c);

  const t = document.createElementNS(svgNS, "text");
  t.setAttribute("x", cx);
  t.setAttribute("y", cy+0.5); // tiny optical correction
  t.textContent = label;
  g.appendChild(t);

  parent.appendChild(g);
  return g;
}

function placeAlongLineNearEnd(x1, y1, x2, y2, whichEnd /* "start"|"end" */, along=14, perp=8){
  // returns point slightly away from the chosen end to avoid overlap with the node icon
  let ex, ey, ox, oy;
  if(whichEnd === "start"){ ex = x1; ey = y1; ox = x2; oy = y2; }
  else { ex = x2; ey = y2; ox = x1; oy = y1; }

  let vx = ox - ex, vy = oy - ey;
  const len = Math.hypot(vx, vy) || 1;
  vx /= len; vy /= len;

  // perpendicular (rotate 90°). Pick a stable side based on direction to reduce overlaps.
  let px = -vy, py = vx;
  const side = (vx + vy) >= 0 ? 1 : -1;

  return {
    x: ex + vx * along + px * perp * side,
    y: ey + vy * along + py * perp * side
  };
}

function renderPortLabels(){
  const svg = document.getElementById("topoSvg");
  const g = document.getElementById("portLabels");
  if(!svg || !g) return;

  // clear old labels
  while(g.firstChild) g.removeChild(g.firstChild);

  const svgNS = "http://www.w3.org/2000/svg";

  PORT_DEFS.forEach(def=>{
    const line = document.getElementById(def.lineId);
    if(!line) return;

    const x1 = parseFloat(line.getAttribute("x1"));
    const y1 = parseFloat(line.getAttribute("y1"));
    const x2 = parseFloat(line.getAttribute("x2"));
    const y2 = parseFloat(line.getAttribute("y2"));

    if(def.a && def.b){
      // switch-switch: label both ends
      const pA = placeAlongLineNearEnd(x1, y1, x2, y2, "start", 14, 12);
      const pB = placeAlongLineNearEnd(x1, y1, x2, y2, "end", 14, 12);
      createPortBadge(svgNS, g, pA.x, pA.y, String(def.portA));
      createPortBadge(svgNS, g, pB.x, pB.y, String(def.portB));
      return;
    }

    // host-switch (label switch-side only)
    const whichEnd = "start";
    const p = placeAlongLineNearEnd(x1, y1, x2, y2, whichEnd, 14, 12);
    createPortBadge(svgNS, g, p.x, p.y, String(def.port));
  });

  // keep labels above nodes
  svg.appendChild(g);
}


function renderHostDetails(){
  const tooltip = document.getElementById("hostTooltip");
  // UI/UX: hover shows quick info; click opens a stable panel; click outside closes.
  let panelOpen = false;
  let panelHost = null;
  let hideTimer = null;

  const closePanel = ()=>{
    panelOpen = false;
    panelHost = null;
    if(hideTimer){ clearTimeout(hideTimer); hideTimer = null; }
    if(tooltip){ tooltip.style.display = "none"; tooltip.dataset.mode = ""; }
  };

  // Close on outside click / ESC

  document.addEventListener("keydown", (e)=>{
    if(e.key === "Escape" && panelOpen){
      closePanel();
    }
  });

  const svg = document.getElementById("topoSvg");
  if(!svg) return;

  // Remove any previously injected inline texts (from older builds)
  Object.keys(HOST_DEFS || {}).forEach(hid=>{
    const g = document.getElementById(hid);
    if(!g) return;
    g.querySelectorAll(".host-info").forEach(n=>n.remove());
  });

  if(!tooltip) return;

  const show = (evt, hid)=>{
    if(panelOpen && panelHost && hid !== panelHost) return;
    const g = document.getElementById(hid);
    if(!g) return;

    // Prefer the visible short label already present in the SVG (e.g., "WS", "PACS", ...)
    let shortName = "";
    const t = g.querySelector("text");
    if(t && t.textContent) shortName = t.textContent.trim();

    const mac = (HOST_DEFS[hid] && HOST_DEFS[hid].mac) ? HOST_DEFS[hid].mac : "";
    const ip  = (HOST_DEFS[hid] && HOST_DEFS[hid].ip) ? HOST_DEFS[hid].ip : "";
    tooltip.innerHTML = `
      <div class="tt-title">${shortName || hid}</div>
      <div class="tt-row"><span class="tt-k">Host</span><span class="tt-v">${hid}</span></div>
      <div class="tt-row"><span class="tt-k">IP</span><span class="tt-v">${ip}</span></div>
      <div class="tt-row"><span class="tt-k">MAC</span><span class="tt-v">${mac}</span></div>
    `;
    // ADD: traffic simulation controls (if provided by extra script)
    try{ if(window.trafficSimRenderControls){ window.trafficSimRenderControls(hid, tooltip); } }catch(e){}

    tooltip.style.display = "block";
    position(evt);
  };

  const hide = (evt)=>{
    // Keep tooltip visible if a panel is open
    if(panelOpen) return;
    try{
      if(evt && evt.relatedTarget && tooltip.contains(evt.relatedTarget)) return;
    }catch(e){}
    if(hideTimer){ clearTimeout(hideTimer); }
    hideTimer = setTimeout(()=>{ tooltip.style.display = "none"; }, 180);
  };

  const position = (evt)=>{
    // Position near cursor, clamped inside viewport
    const pad = 12;
    const w = tooltip.offsetWidth;
    const h = tooltip.offsetHeight;
    let x = evt.clientX + pad;
    let y = evt.clientY + pad;

    const maxX = window.innerWidth - w - 8;
    const maxY = window.innerHeight - h - 8;
    if(x > maxX) x = evt.clientX - w - pad;
    if(y > maxY) y = evt.clientY - h - pad;
    if(x < 8) x = 8;
    if(y < 8) y = 8;

    tooltip.style.left = x + "px";
    tooltip.style.top  = y + "px";
  };

  // Bind events once
  const hostIds = Object.keys(HOST_DEFS || {});
  const ids = hostIds.length ? hostIds : Array.from(document.querySelectorAll('g.host')).map(g=>g.id).filter(Boolean);

  ids.forEach(hid=>{
    const g = document.getElementById(hid);
    if(!g) return;

    if(g.dataset.tooltipBound === "1") return;
    g.dataset.tooltipBound = "1";

    // Option A UX:
    // - Hover ONLY on label text shows quick info (no buttons)
    // - Click on the device opens a stable panel (buttons live there)
    const labelEl = g.querySelector("text");
    if(labelEl){
      labelEl.addEventListener("mouseenter", (e)=>{ if(!panelOpen) { tooltip.dataset.mode = "hover"; show(e, hid); } });
      labelEl.addEventListener("mousemove", (e)=>{ if(!panelOpen) position(e); });
      labelEl.addEventListener("mouseleave", (e)=>hide(e));
    } else {
      // fallback: hover on whole icon if label is missing
      g.addEventListener("mouseenter", (e)=>{ if(!panelOpen) { tooltip.dataset.mode = "hover"; show(e, hid); } });
      g.addEventListener("mousemove", (e)=>{ if(!panelOpen) position(e); });
      g.addEventListener("mouseleave", (e)=>hide(e));
    }

g.addEventListener("click", (e)=>{
      e.preventDefault();
      e.stopPropagation();
      if(panelOpen && panelHost === hid){
        closePanel();
        return;
      }
      panelOpen = true;
      panelHost = hid;
      tooltip.dataset.mode = "panel";
      show(e, hid);
    });

    const ip  = (HOST_DEFS[hid] && HOST_DEFS[hid].ip) ? HOST_DEFS[hid].ip : "";
    const mac = (HOST_DEFS[hid] && HOST_DEFS[hid].mac) ? HOST_DEFS[hid].mac : "";
    g.setAttribute("title", `${hid}
${ip}
${mac}`);
  });

  // Also hide tooltip if you scroll or click elsewhere
  window.addEventListener("scroll", ()=>{ if(panelOpen){ closePanel(); } else { hide(); } }, {passive:true});
  document.addEventListener("click", (e)=>{
    // keep it if clicking on a host, otherwise hide
    const target = e.target;
    if(!(target && target.closest && target.closest("g.host"))) hide();
  });
}




function setWrappedSvgText(textEl, label, maxChars = 16, maxLines = 2){
  // remove old tspans/text
  while(textEl.firstChild) textEl.removeChild(textEl.firstChild);

  const words = String(label || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";

  for(const w of words){
    const candidate = line ? (line + " " + w) : w;
    if(candidate.length <= maxChars){
      line = candidate;
    }else{
      if(line) lines.push(line);
      line = w;
    }
    if(lines.length >= maxLines) break;
  }
  if(lines.length < maxLines && line) lines.push(line);

  // ellipsis if truncated
  if(words.length && lines.length === maxLines){
    const used = lines.join(" ").split(/\s+/).length;
    if(used < words.length){
      lines[maxLines-1] = lines[maxLines-1].replace(/\s*$/, "") + "…";
    }
  }

  const x = textEl.getAttribute("x") || "0";
  const y = parseFloat(textEl.getAttribute("y") || "0");
  const lineHeight = 12;

  lines.forEach((ln, i)=>{
    const tspan = document.createElementNS("http://www.w3.org/2000/svg", "tspan");
    tspan.setAttribute("x", x);
    tspan.setAttribute("y", String(y + i * lineHeight));
    tspan.textContent = ln;
    textEl.appendChild(tspan);
  });

  // native tooltip with full name
  if(textEl.parentNode) textEl.parentNode.setAttribute("title", label);
}

async function loadHostDefs(){
  // Host labels + tooltip info: prendiamo tutto dal controller (derivato da topology.py)
  try{
    const res = await fetch(API + "/ui/host-defs");
    if(!res.ok) throw new Error("http " + res.status);
    const data = await res.json();
    HOST_DEFS = (data && data.hosts) ? data.hosts : {};
  }catch(e){
    console.warn("Cannot load /ui/host-defs, fallback to empty HOST_DEFS:", e);
    HOST_DEFS = {};
  }

  // Aggiorna le etichette sotto le icone host, se presenti nel SVG
  Object.keys(HOST_DEFS).forEach(hid=>{
    const g = document.getElementById(hid);
    if(!g) return;
    const t = g.querySelector("text");
    if(!t) return;
    const lbl = HOST_DEFS[hid]?.label || HOST_DEFS[hid]?.name || hid;
    setWrappedSvgText(t, lbl, 16, 2);
});
}

async function loadPortDefs(){
  try{
    const data = await apiGet("/ui/port-defs");
    if(data && Array.isArray(data.port_defs) && data.port_defs.length){
      PORT_DEFS = data.port_defs;
    }
  } catch(e){
    // fallback ai default
    console.warn("PORT_DEFS: uso DEFAULT (endpoint /ui/port-defs non disponibile)", e);
  }
}

function initExtraAnnotations(){
  renderPortLabels();
  renderHostDetails();

  // re-render port labels if the SVG scale changes
  window.addEventListener("resize", ()=>{
    // coordinates are viewBox-based; re-rendering keeps labels crisp after zoom/resize
    renderPortLabels();
  }, {passive:true});
}
/* =================== END EXTRA ANNOTATIONS =================== */


const ACCESS_LINKS = new Set([
  linkIds.s1ws1,
  linkIds.s1ws2,
  linkIds.s1prnD,
  linkIds.s2acq1,
  linkIds.s2prnI,
  linkIds.s2acq2,
  linkIds.s5pacs,
  linkIds.s5ris,
  linkIds.s6secsrv,
  linkIds.s6cam1,
  linkIds.s6cam2,
  linkIds.s8adminpc,
  linkIds.s8risws1,
  linkIds.s8risws2
]);

const segments = {
  topology: {
    day: {
      radiology: [
        linkIds.s1s2, linkIds.s2s3, linkIds.s3s4, linkIds.s4s5, linkIds.s5brokerBase,
        linkIds.s1ws1, linkIds.s1ws2, linkIds.s1prnD,
        linkIds.s2acq1, linkIds.s2prnI, linkIds.s2acq2,
        // radiology reaches PACS DB in day
        linkIds.s5pacs,
        // RIS is part of Administrative slice, not Radiology.
      ],
      security: [
        linkIds.s6s3, linkIds.s3s7, linkIds.s7s8,
        linkIds.s6secsrv, linkIds.s6cam1, linkIds.s6cam2,
        // Admin PC is red in both day/night
        linkIds.s8adminpc
      ],
      admin: [
        linkIds.s5s8, linkIds.s5brokerOv,
        linkIds.s8risws1, linkIds.s8risws2
      ],
      // in day: Admin Core includes RIS (+ broker overlay)
      admin_server_green: [linkIds.s5ris, linkIds.s5brokerOv]
    },
    night: {
      radiology: [],
      // IMPORTANT: at night the link s1-s6 is NOT part of Security slice (must stay gray)
      security: [
        linkIds.s6s3, linkIds.s3s7, linkIds.s7s8,
        linkIds.s6secsrv, linkIds.s6cam1, linkIds.s6cam2,
        linkIds.s8adminpc
      ],
      admin: [
        linkIds.s5s8,
        linkIds.s8risws1, linkIds.s8risws2
      ],
      // at night: green on RIS (NOT broker, NOT pacs)
      admin_server_green: [linkIds.s5ris]
    }
  },
  service: {
    nonvideo: [
      linkIds.s1s6,

      linkIds.s1s2, linkIds.s2s3, linkIds.s3s4, linkIds.s4s5, linkIds.s5s8, linkIds.s5brokerBase,
      linkIds.s1ws1, linkIds.s1ws2, linkIds.s1prnD,
      linkIds.s2acq1, linkIds.s2prnI, linkIds.s2acq2,
      linkIds.s5pacs, linkIds.s5ris,
      linkIds.s8risws1, linkIds.s8risws2,

      // Access links with classification (base = Non-video)
      linkIds.s6secsrv, linkIds.s6cam1, linkIds.s6cam2,
      linkIds.s8adminpc
    ],
    video: [
      linkIds.s6s3, linkIds.s3s7, linkIds.s7s8,

      // Access links with classification (overlay = Video UDP/9999)
      linkIds.s6secsrvOv, linkIds.s6cam1Ov, linkIds.s6cam2Ov,
      linkIds.s8adminpcOv
    ]
  }
};

let last = null;
let busy = false;

function $(id){ return document.getElementById(id); }
function setBtnActive(btn, active, colorClass=null){
  btn.classList.toggle("active", !!active);
  if(colorClass){
    btn.classList.toggle(colorClass, !!active);
  }
}

function clearLinkColors(){
  Object.values(linkIds).forEach(id=>{
    const el = $(id);
    if(!el) return;
    // reset all color classes
    el.classList.remove("blue","red","green");
    // base links go back to base, overlay links get hidden.
    // Our SVG ids use suffix "_ov" (lowercase), not "Ov".
    const isOverlay = id.endsWith("_ov") || id.endsWith("Ov");
    if(isOverlay){
      el.classList.add("overlay","hidden");
      el.classList.remove("base");
    } else {
      el.classList.remove("hidden");
      el.classList.add("base");
    }
  });
}

function colorLinks(ids, cls){
  ids.forEach(id=>{
    const el = $(id);
    if(!el) return;
    // make the latest slice color win (avoid overlapping classes like blue+green)
    el.classList.remove("blue","red","green");
    // overlay links: show them (remove hidden) and ensure overlay class
    const isOverlay = id.endsWith("_ov") || id.endsWith("Ov");
    if(isOverlay){
      el.classList.add("overlay");
      el.classList.remove("hidden");
      el.classList.remove("base");
      el.classList.add(cls);
      return;
    }
    // normal links
    el.classList.remove("base");
    el.classList.add(cls);
  });
}

async function apiGet(path){
  const res = await fetch(API + path, {method:"GET"});
  return await res.json();
}
async function apiPost(path, body){
  const res = await fetch(API + path, {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body || {})
  });
  return await res.json();
}

function showModal(show){
  // Send Packet UI removed; retained for backward compatibility.
  if(!els.modal) return;
  if(show) els.modal.classList.remove("hidden");
  else els.modal.classList.add("hidden");
}


function applyState(s){
  // ADD: expose last state for traffic simulation UI
  window.__STATE = s;
  // labels
  els.modeLabel.textContent = s.active_mode || "—";
  // theme: day = chiaro, night = scuro
  document.body.dataset.theme = (s.active_mode === "night") ? "night" : "day";
  els.slicingLabel.textContent = s.slicing_mode || "—";

  const isDay = (s.active_mode === "day");
  const isTopo = (s.slicing_mode === "topology");
  const enabled = new Set((s.enabled_topology || []).map(Number));
  const videoEnabled = !!s.video_enabled;

  // buttons
  setBtnActive(els.btnDay, isDay);
  setBtnActive(els.btnNight, !isDay);

  // In SERVICE mode: DAY/NIGHT disabilitati (come richiesto)
  const dayNightAllowed = isTopo;
  els.btnDay.disabled = !dayNightAllowed;
  els.btnNight.disabled = !dayNightAllowed;
  setBtnActive(els.btnTopo, isTopo);
  setBtnActive(els.btnService, !isTopo);

  // show correct cards
  els.cardTopology.style.display = isTopo ? "block" : "none";
  els.cardService.style.display = isTopo ? "none" : "block";

  // slice buttons (topology)
  const radiologyAllowed = isDay; // Radiology disponibile solo di giorno
  els.slice1.disabled = !radiologyAllowed;
  setBtnActive(els.slice1, enabled.has(1) && radiologyAllowed, "blue");
  els.slice2.disabled = false;
  setBtnActive(els.slice2, enabled.has(2), "red");
  els.slice3.disabled = false;
  setBtnActive(els.slice3, enabled.has(3), "green");

  // service button (only video)
  setBtnActive(els.videoToggle, videoEnabled, "red");

  // link coloring
  clearLinkColors();

  if(isTopo){
    const topoSeg = isDay ? segments.topology.day : segments.topology.night;
    if(enabled.has(1) && isDay) colorLinks(topoSeg.radiology, "blue");
    if(enabled.has(2)) colorLinks(topoSeg.security, "red");
    if(enabled.has(3)) {
      colorLinks(topoSeg.admin, "green");
      colorLinks(topoSeg.admin_server_green, "green");
    }
  } else {
    // service:
    // - Video OFF = OPEN => tutto grigio (base)
    // - Video ON  = nonvideo blu + video rosso
    if(videoEnabled){
      // ADD: in SERVICE+VIDEO, access links (host↔switch) are shared by every slice -> keep them uncolored
      colorLinks(segments.service.nonvideo.filter(id=>!ACCESS_LINKS.has(id)), "blue");
      colorLinks(segments.service.video.filter(id=>!ACCESS_LINKS.has(id)), "red");
      // ensure access links are not colored
      ACCESS_LINKS.forEach(id=>{ const el=$(id); if(el){ el.classList.remove('blue','red','green'); } });
    }
  }
}

async function refresh(){
  if(busy) return;
  try{
    const s = await apiGet("/status");
    last = s;
    applyState(s);
  } catch(e){
    // ignore
  }
}

// --- handlers
els.btnDay.addEventListener("click", async ()=>{
  if(busy) return; busy=true;
  try{ await apiPost("/mode/set", {mode:"day"}); } finally { busy=false; applyNodeIcons();
refresh(); }
});
els.btnNight.addEventListener("click", async ()=>{
  if(busy) return; busy=true;
  try{ await apiPost("/mode/set", {mode:"night"}); } finally { busy=false; refresh(); }
});
els.btnTopo.addEventListener("click", async ()=>{
  if(busy) return; busy=true;
  try{ await apiPost("/slicing/set", {mode:"topology"}); } finally { busy=false; refresh(); }
});
els.btnService.addEventListener("click", async ()=>{
  if(busy) return; busy=true;
  try{
    // SERVICE slicing is allowed only in DAY.
    // Fix: force DAY before switching to SERVICE, otherwise in SERVICE the Day/Night buttons are disabled
    // and you get stuck in Night + Service.
    if(last && last.active_mode !== "day"){
      await apiPost("/mode/set", {mode:"day"});
    }
    await apiPost("/slicing/set", {mode:"service"});
  } finally { busy=false; refresh(); }
});

async function toggleSlice(n){
  if(!last) return;
  if(busy) return; busy=true;
  try{
    const enabled = new Set((last.enabled_topology || []).map(Number));
    if(enabled.has(n)) await apiPost("/slice/remove", {slice:n});
    else await apiPost("/slice/add", {slice:n});
  } finally {
    busy=false; refresh();
  }
}
els.slice1.addEventListener("click", ()=>toggleSlice(1));
els.slice2.addEventListener("click", ()=>toggleSlice(2));
els.slice3.addEventListener("click", ()=>toggleSlice(3));

els.videoToggle.addEventListener("click", async ()=>{
  if(!last) return;
  if(busy) return; busy=true;
  try{
    if(last.video_enabled) await apiPost("/service/video/off", {});
    else await apiPost("/service/video/on", {});
  } finally {
    busy=false; refresh();
  }
});

// modal (Send Packet removed)
async function boot(){
  // Carica definizioni porte dal controller (coerenti con topology.py / ProblemConstants.py)
  await loadPortDefs();

  // Carica nomi/mac/ip host dal controller (coerenti con topology.py)
  await loadHostDefs();

  // Ensure tooltip div and SVG exist before binding events / drawing overlays.
  initExtraAnnotations();
  refresh();
}

if(document.readyState === "loading"){
  document.addEventListener("DOMContentLoaded", boot, {once:true});
} else {
  boot();
}



// ---- polling (ridotto): evita richieste ogni secondo ----
let refreshTimer = null;

function startPolling(){
  stopPolling();
  refreshTimer = setInterval(()=>{ refresh(); }, 5000);
}
function stopPolling(){
  if(refreshTimer){ clearInterval(refreshTimer); refreshTimer = null; }
}
document.addEventListener("visibilitychange", ()=>{
  if(document.hidden) stopPolling();
  else startPolling();
});
startPolling();




