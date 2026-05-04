// trafficSim.js


(function(){
  const API = (window.location.port === "8080") ? "" : "http://localhost:8080";

  const SIM_BY_HOST = {
    "hImgDev1": "imgacq1_pacs",
    "hRadWS1":  "radws1_pacs",
    "hCam1":    "cam1_nvr",
    "hCam2":    "cam2_lcs"
  };


  const ENDPOINTS = {
    "imgacq1_pacs": {src:"hImgDev1", dst:"hPACS"},
    "radws1_pacs":  {src:"hRadWS1", dst:"hPACS"},
    "cam1_nvr":     {src:"hCam1", dst:"nvr"},
    "cam2_lcs":     {src:"hCam2", dst:"lCS"}
  };
  const PATHS = {
    "imgacq1_pacs": ["l_s2_acq1","l_s2_s3","l_s3_s4","l_s4_s5","l_s5_pacs"],
    "radws1_pacs":  ["l_s1_ws1","l_s1_s2","l_s2_s3","l_s3_s4","l_s4_s5","l_s5_pacs"],
    "cam1_nvr":     ["l_s6_cam1","l_s6_secsrv"],
    "cam2_lcs":     ["l_s6_cam2","l_s6_s3","l_s3_s7","l_s7_s8","l_s8_adminpc"]
  };


  let lastStatus = null;
  let pollTimer = null;

  function apiGet(path){
    return fetch(API + path, {method:"GET"}).then(r=>r.json());
  }
  function apiPost(path, body){
    return fetch(API + path, {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(body||{})
    }).then(r=>r.json().then(j=>({ok:r.ok, status:r.status, json:j})));
  }

  function getAllowed(simId){
    const st = window.__STATE || {};
    if(st.slicing_mode !== "topology") return false;
    const enabled = new Set((st.enabled_topology || []).map(Number));
    // slice mapping: radiology=1, security=2 (as per controller constants)
    const sliceId = (simId === "cam1_nvr" || simId === "cam2_lcs") ? 2 : 1;
    return enabled.has(sliceId);
  }

  function simStatus(simId){
    if(!lastStatus || !lastStatus.sims) return {status:"unknown"};
    return lastStatus.sims[simId] || {status:"unknown"};
  }

  function statusLabel(st){
    const s = (st.status || "unknown").toLowerCase();
    if(s === "running") return "IN CORSO";
    if(s === "terminated") return "TERMINATA";
    if(s === "interrupted") return "INTERROTTA";
    if(s === "error") return "ERRORE";
    if(s === "idle") return "PRONTA";
    return s.toUpperCase();
  }

  function updateAnimations(){
    if(!window.TrafficAnim) return;
    if(!lastStatus || !lastStatus.sims) return;

    for(const [simId, st] of Object.entries(lastStatus.sims)){
      const status = (st.status || "unknown").toLowerCase();
      const ep = ENDPOINTS[simId] || {};
      if(status === "running"){
        // Directional flow: src -> dst
        window.TrafficAnim.start(simId, PATHS[simId] || [], ep.src || null, ep.dst || null);
      } else {
        // Hard stop: no animation after completion/interruption.
        window.TrafficAnim.stop(simId);
      }
    }
  }



  function renderControls(hid, tooltipEl){
    const mode = (tooltipEl && tooltipEl.dataset && tooltipEl.dataset.mode) ? tooltipEl.dataset.mode : "";
    const simId = SIM_BY_HOST[hid];
    if(!simId) return;

    const st = simStatus(simId);
    const allowed = getAllowed(simId);

    const statusTxt = statusLabel(st);
    const disabledBecauseDone = (st.status === "terminated");
    const disabledBecauseRunning = (st.status === "running");
    const disabledBecauseNotAllowed = !allowed;

    const canStart = !(disabledBecauseDone || disabledBecauseRunning || disabledBecauseNotAllowed);

    const btnLabel = (st.status === "running") ? "In corso…" :
                     (st.status === "terminated") ? "Terminata" :
                     "Avvia simulazione";

    // insert/replace block
    let block = tooltipEl.querySelector(".tt-sim");
    if(!block){
      block = document.createElement("div");
      block.className = "tt-sim";
      tooltipEl.appendChild(block);
    }

    block.innerHTML = `
      <div class="tt-divider"></div>
      <div class="tt-row"><span class="tt-k">Simulazione</span><span class="tt-v tt-status">${statusTxt}</span></div>
      ${mode === "panel" ? `<button class="tt-btn" ${canStart ? "" : "disabled"} data-simid="${simId}">${btnLabel}</button>` : ``}
      ${mode === "panel" && st.error ? `<div class="tt-err">${String(st.error)}</div>` : ``}
    `;

    const btn = block.querySelector("button.tt-btn");
    if(btn){
      btn.onclick = async (e)=>{
        e.preventDefault();
        e.stopPropagation();
        const sid = btn.getAttribute("data-simid");
        if(!sid) return;
        // optimistic UI: disable immediately
        btn.disabled = true;
        const resp = await apiPost("/sim/traffic/start", {sim_id: sid});
        // refresh right away
        await pollOnce();
        // if still startable (interrupted), render will re-enable
        renderControls(hid, tooltipEl);
      };
    }
  }

  async function pollOnce(){
    try{
      const j = await apiGet("/sim/traffic/status");
      lastStatus = j;
      updateAnimations();
      // refresh open tooltip if visible
      const tt = document.getElementById("hostTooltip");
      if(tt && tt.style.display === "block"){
        // tooltip has a hidden attribute? we re-render based on last host id stored on dataset
        const hid = tt.dataset.hid;
        if(hid) renderControls(hid, tt);
      }
    }catch(e){
      // ignore
    }
  }

  function startPolling(){
    if(pollTimer) return;
    pollOnce();
    pollTimer = setInterval(pollOnce, 900);
    startUiWatcher();
  }



  let lastUiFingerprint = null;

  function uiFingerprint(){
    const s = window.__STATE || {};

    const mode = s.active_slicing_mode ?? s.active_mode ?? "";
    const dayNight = (document.body && document.body.dataset) ? (document.body.dataset.theme || "") : "";
    const enabledTopo = Array.isArray(s.enabled_topology) ? s.enabled_topology.join(",") : "";
    const enabledSec = Array.isArray(s.enabled_security) ? s.enabled_security.join(",") : "";
    return [mode, dayNight, enabledTopo, enabledSec].join("|");
  }

  async function stopAll(reason){

    try{ await apiPost("/sim/traffic/stop_all", {reason}); }catch(e){}
    if(window.TrafficAnim){
      if(lastStatus && lastStatus.sims){
        for(const simId of Object.keys(lastStatus.sims)){
          window.TrafficAnim.stop(simId);
        }
      }
    }

  }

  function startUiWatcher(){
    if(lastUiFingerprint !== null) return;
    lastUiFingerprint = uiFingerprint();
    setInterval(()=>{
      const fp = uiFingerprint();
      if(fp !== lastUiFingerprint){
        lastUiFingerprint = fp;
        stopAll("ui_context_change");
      }
    }, 500);
  }


  window.trafficSimRenderControls = function(hid, tooltipEl){
    try{
      if(tooltipEl) tooltipEl.dataset.hid = hid;
      renderControls(hid, tooltipEl);
      startPolling();
    }catch(e){}
  };

})();