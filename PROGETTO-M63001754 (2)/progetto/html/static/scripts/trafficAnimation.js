// trafficAnimation.js
// Orange balls moving along SVG link segments for the duration of a traffic simulation.
// Exposes: window.TrafficAnim.start(simId, linkIdsArray, srcHostId, dstHostId) and window.TrafficAnim.stop(simId)

(function(){
  const NS = "http://www.w3.org/2000/svg";
  const ACTIVE = new Map(); // simId -> {circles, raf}

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------

  function dist(a, b){
    return Math.hypot((a?.x || 0) - (b?.x || 0), (a?.y || 0) - (b?.y || 0));
  }

  function getSvg(){
    return document.getElementById("topoSvg");
  }

  function toSvgPoint(svg, el, x, y){

    if(!svg || !el) return null;
    const pt = svg.createSVGPoint();
    pt.x = x; pt.y = y;
    const m = el.getCTM();
    if(!m) return {x, y};
    const p = pt.matrixTransform(m);
    return {x: p.x, y: p.y};
  }

  function hostCenter(hostId){
    if(!hostId) return null;
    const svg = getSvg();
    const el = document.getElementById(hostId);
    if(!svg || !el) return null;


    let bb;
    try{ bb = el.getBBox(); }catch(e){ return null; }
    const cx = bb.x + bb.width/2;
    const cy = bb.y + bb.height/2;
    return toSvgPoint(svg, el, cx, cy);
  }

  function getLine(id){
    return document.getElementById(id);
  }

  function buildPolylinePoints(linkIds){

    const points = [];
    let last = null;
    for(const lid of linkIds){
      const ln = getLine(lid);
      if(!ln) continue;
      const x1 = parseFloat(ln.getAttribute("x1"));
      const y1 = parseFloat(ln.getAttribute("y1"));
      const x2 = parseFloat(ln.getAttribute("x2"));
      const y2 = parseFloat(ln.getAttribute("y2"));
      const a = {x:x1, y:y1}, b={x:x2, y:y2};

      if(!last){
        points.push(a, b);
        last = b;
        continue;
      }

      const d1 = Math.hypot(a.x-last.x, a.y-last.y);
      const d2 = Math.hypot(b.x-last.x, b.y-last.y);
      if(d1 <= d2){
        points.push(a, b);
        last = b;
      } else {
        points.push(b, a);
        last = a;
      }
    }

    const out=[];
    for(const p of points){
      const q=out[out.length-1];
      if(!q || q.x!==p.x || q.y!==p.y) out.push(p);
    }
    return out;
  }

  function polyLength(pts){
    let L=0;
    for(let i=1;i<pts.length;i++){
      L += Math.hypot(pts[i].x-pts[i-1].x, pts[i].y-pts[i-1].y);
    }
    return L;
  }

  function pointAt(pts, dist){
    // dist in [0, total]
    for(let i=1;i<pts.length;i++){
      const a=pts[i-1], b=pts[i];
      const seg = Math.hypot(b.x-a.x, b.y-a.y);
      if(seg <= 0.0001) continue;
      if(dist <= seg){
        const t = dist/seg;
        return {x: a.x + (b.x-a.x)*t, y: a.y + (b.y-a.y)*t};
      }
      dist -= seg;
    }
    return pts[pts.length-1] || {x:0,y:0};
  }

  function ensureLayer(){
    const svg = document.getElementById("topoSvg");
    if(!svg) return null;
    let g = document.getElementById("trafficAnimLayer");
    if(!g){
      g = document.createElementNS(NS, "g");
      g.setAttribute("id", "trafficAnimLayer");
      g.setAttribute("pointer-events", "none");
      svg.appendChild(g);
    }
    return g;
  }

  function start(simId, linkIds, srcHostId, dstHostId){
    stop(simId);
    const layer = ensureLayer();
    if(!layer) return;

    const pts = buildPolylinePoints(linkIds);
    if(pts.length < 2) return;

    const srcC = hostCenter(srcHostId);
    const dstC = hostCenter(dstHostId);

    // Enforce direction: src -> dst when both endpoints are known.
    if(srcC && dstC && pts.length >= 2){
      const a0 = pts[0], a1 = pts[pts.length-1];
      const dF = dist(a0, srcC) + dist(a1, dstC);
      const dR = dist(a1, srcC) + dist(a0, dstC);
      if(dR < dF){ pts.reverse(); }
    } else if(srcC && pts.length >= 2){
      // Best-effort: make the path start near src.
      const dFirst = dist(pts[0], srcC);
      const dLast  = dist(pts[pts.length-1], srcC);
      if(dLast < dFirst){ pts.reverse(); }
    }

    const total = polyLength(pts);
    if(total < 5) return;

    // Orange balls (adaptive count for nicer visuals)
    const ballCount = Math.min(18, Math.max(10, Math.round(total / 110)));
    const balls = [];
    for(let i=0;i<ballCount;i++){
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("r", "4");
      c.setAttribute("class", "traffic-ball");
      layer.appendChild(c);
      balls.push(c);
    }

    const speed = 180; // px/s (slightly faster)
    const spacing = total / balls.length;

    let t0 = performance.now();
    function frame(now){
      const dt = (now - t0)/1000;
      const base = (dt * speed) % total;
      for(let i=0;i<balls.length;i++){
        const d = (base + i*spacing) % total;
        const p = pointAt(pts, d);
        balls[i].setAttribute("cx", String(p.x));
        balls[i].setAttribute("cy", String(p.y));
      }
      const h = requestAnimationFrame(frame);
      ACTIVE.get(simId).raf = h;
    }

    ACTIVE.set(simId, {circles: balls, raf: null});
    const h = requestAnimationFrame(frame);
    ACTIVE.get(simId).raf = h;
  }

  function stop(simId){
    const obj = ACTIVE.get(simId);
    if(!obj) return;
    if(obj.raf) cancelAnimationFrame(obj.raf);
    for(const c of obj.circles){
      try{ c.remove(); }catch(e){}
    }
    ACTIVE.delete(simId);
  }

  window.TrafficAnim = { start, stop };
})();