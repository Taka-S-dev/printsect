// ---- graph view --------------------------------------------------------------
// Only what is on screen is put in the page, so a large build stays quick; the layout
// is worked out for the nodes kept by the filters, in steps when it is a long one.
const NODE_W = 210, NODE_H = 30, GAP_X = 70, GAP_Y = 14;
const FORCE_MAX = 400;     // above this, a force layout would not settle in reasonable time
const WHOLE_MAX = 1500;    // above this, the whole graph starts without objects and headers
const DRAW_MAX = 1800;     // nodes in the page at once
const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  return e;
};
const TAGS = { out: "OUT", archive: "LIB", object: "OBJ", source: "SRC", header: "H", made: "GEN", input: "IN",
  deploy: "DEP", module: "MOD" };
const EDGE_TEXT = { includes: "is included by", compiles_to: "is compiled into",
  archives: "is archived into", links: "is linked into", generates: "generates",
  invokes: "runs to generate", copies_to: "is copied to", uses: "is used by" };

let depsEdgesMemo = null;
function depsEdges() {
  if (depsEdgesMemo) return depsEdgesMemo;
  const E = [];
  for (const [o, info] of Object.entries(objects)) {
    for (const h of info.headers) E.push({ a: h, b: o, type: "includes" });
    if (info.source && !madeOf.has(info.source)) E.push({ a: info.source, b: o, type: "compiles_to" });
  }
  for (const [a, info] of Object.entries(archives)) {
    for (const o of info.objects) E.push({ a: o, b: a, type: "archives" });
  }
  for (const [o, info] of Object.entries(outs)) {
    for (const x of [...info.objects, ...info.archives]) E.push({ a: x, b: o, type: "links" });
  }
  for (const [g, info] of madeOf) {
    for (const x of info.from) E.push({ a: x, b: g, type: "generates" });
    if (info.by) E.push({ a: info.by, b: g, type: "invokes" });
    for (const o of users.get(g) || []) {
      if (objects[o].source === g) E.push({ a: g, b: o, type: "compiles_to" });
    }
  }
  for (const [target, source] of copiesOf) E.push({ a: source, b: target, type: "copies_to" });
  depsEdgesMemo = E;
  return E;
}

// The node a picked file stands for: a source is drawn as the object made from it.
function nodeFor(path) {
  if (!path) return null;
  const kind = kindAny(path);
  if (kind && (kind !== "source" || state.gkinds.has("source"))) return path;
  const own = users.get(path);
  return own && own.length ? own[0] : null;
}

function moduleData() {
  const groups = new Map(), owner = D.map[state.ggroup];
  for (const [file, module] of Object.entries(owner)) push(groups, module, file);
  const between = new Map();
  for (const [user, definer, symbols] of D.map.edges) {
    const a = owner[user], b = owner[definer];
    if (!a || !b || a === b) continue;
    const key = a + "\u0000" + b;
    between.set(key, (between.get(key) || 0) + symbols.length);
  }
  return {
    groups,
    edges: [...between].map(([key, count]) => {
      const [a, b] = key.split("\u0000");
      return { a: b, b: a, type: "uses", count };
    }),
  };
}

// The parts that go into `focus` or that it goes into, as far as the depth asks.
function reachOf(edges, focus) {
  const reach = new Set([focus]);
  const up = new Map(), down = new Map();
  for (const e of edges) { push(up, e.b, e.a); push(down, e.a, e.b); }
  const walk = (table) => {
    let level = [focus], depth = 0;
    while (level.length && (!state.gdepth || depth < state.gdepth)) {
      const next = [];
      for (const n of level) for (const m of table.get(n) || []) {
        if (!reach.has(m)) { reach.add(m); next.push(m); }
      }
      level = next; depth++;
    }
  };
  if (state.gdir !== "down") walk(up);
  if (state.gdir !== "up") walk(down);
  return reach;
}

// What the filters keep: { nodes: [{id, kind, status}], edges, note }.
function viewData() {
  let edges, kindOfNode, statusOfNode, focus = null, note = "";
  if (state.view === "modules") {
    const m = moduleData();
    focus = state.selected && m.groups.has(state.selected) ? state.selected : null;
    const near = focus && state.gdir !== "all" ? reachOf(m.edges, focus) : null;
    const nodes = [...m.groups.keys()].filter((id) => !near || near.has(id))
      .map((id) => ({ id, kind: "module", status: "" }));
    return { nodes, edges: m.edges.filter((e) => !near || (near.has(e.a) && near.has(e.b))), note: "", focus };
  }
  edges = depsEdges();
  kindOfNode = kindAny;
  statusOfNode = statusOf;
  focus = nodeFor(state.selected);
  const keep = new Set();
  const stageKinds = state.gkinds;
  const all = new Set();
  for (const e of edges) { all.add(e.a); all.add(e.b); }
  for (const r of roots) all.add(r);
  const reach = focus && state.gdir !== "all" ? reachOf(edges, focus) : null;
  for (const id of all) {
    if (reach && !reach.has(id)) continue;
    if (id !== focus && !stageKinds.has(kindOfNode(id) || "input")) continue;
    if (state.gunres && id !== focus && !["unresolved", "excluded"].includes(statusOfNode(id))) continue;
    keep.add(id);
  }
  if (!reach && keep.size > WHOLE_MAX) {
    let dropped = 0;
    for (const id of [...keep]) {
      if (["source", "object", "header"].includes(kindOfNode(id)) && id !== focus) { keep.delete(id); dropped++; }
    }
    note = dropped + " objects and headers are left out of this large view: pick a part and "
      + "choose Upstream or Downstream to see them around it.";
  }
  const nodes = [...keep].map((id) => ({ id, kind: kindOfNode(id) || "input", status: statusOfNode(id) }));
  return { nodes, edges: edges.filter((e) => keep.has(e.a) && keep.has(e.b)), note, focus };
}

// Ranks by longest way from the inputs, then order inside a rank by where neighbours are.
function layered(view) {
  const byId = new Map(view.nodes.map((n) => [n.id, n]));
  const inward = new Map(), outward = new Map();
  for (const e of view.edges) { push(inward, e.b, e.a); push(outward, e.a, e.b); }
  const waiting = new Map(view.nodes.map((n) => [n.id, (inward.get(n.id) || []).length]));
  const rank = new Map();
  const ready = view.nodes.filter((n) => !waiting.get(n.id)).map((n) => n.id);
  ready.forEach((id) => rank.set(id, 0));
  for (let i = 0; i < ready.length; i++) {
    const id = ready[i];
    for (const m of outward.get(id) || []) {
      rank.set(m, Math.max(rank.get(m) || 0, rank.get(id) + 1));
      waiting.set(m, waiting.get(m) - 1);
      if (!waiting.get(m)) ready.push(m);
    }
  }
  for (const n of view.nodes) if (!rank.has(n.id)) rank.set(n.id, 0);  // only in a loop
  const rows = [];
  for (const [id, r] of rank) (rows[r] = rows[r] || []).push(id);
  const row = (r) => rows[r] || [];
  const place = new Map();
  const index = () => rows.forEach((items) => items && items.forEach((id, i) => place.set(id, i)));
  for (const items of rows) if (items) items.sort();
  index();
  const sweep = (r, table) => {
    const mean = (id) => {
      const near = (table.get(id) || []).filter((m) => byId.has(m));
      return near.length ? near.reduce((s, m) => s + place.get(m), 0) / near.length : place.get(id);
    };
    const weights = new Map(row(r).map((id) => [id, mean(id)]));
    row(r).sort((p, q) => weights.get(p) - weights.get(q) || (p < q ? -1 : 1));
  };
  for (let pass = 0; pass < 2; pass++) {
    for (let r = 1; r < rows.length; r++) sweep(r, inward);
    index();
    for (let r = rows.length - 2; r >= 0; r--) sweep(r, outward);
    index();
  }
  const pos = new Map();
  const widest = Math.max(1, ...rows.map((items) => (items || []).length));
  for (let r = 0; r < rows.length; r++) {
    const items = row(r);
    const skip = (widest - items.length) / 2;
    items.forEach((id, i) => {
      pos.set(id, state.glayout === "tb"
        ? { x: (skip + i) * (NODE_W + GAP_Y), y: r * (NODE_H + GAP_X) }
        : { x: r * (NODE_W + GAP_X), y: (skip + i) * (NODE_H + GAP_Y) });
    });
  }
  return pos;
}

function indented(view) {
  const inward = new Map(), hasOut = new Set();
  for (const e of view.edges) { push(inward, e.b, e.a); hasOut.add(e.a); }
  const pos = new Map();
  let line = 0;
  const seen = new Set();
  const walk = (id, depth) => {
    if (seen.has(id)) return;
    seen.add(id);
    pos.set(id, { x: depth * (NODE_W / 2 + 20), y: line++ * (NODE_H + GAP_Y) });
    for (const m of (inward.get(id) || []).slice().sort()) walk(m, depth + 1);
  };
  for (const n of view.nodes.filter((x) => !hasOut.has(x.id)).sort((p, q) => (p.id < q.id ? -1 : 1))) walk(n.id, 0);
  for (const n of view.nodes) walk(n.id, 0);
  return pos;
}

const G = { view: null, pos: new Map(), tx: 20, ty: 20, k: 1, focus: null, hits: [], hitAt: -1,
  up: new Set(), down: new Set(), timer: 0, frame: 0, drawn: "" };

function emphasis() {
  G.up = new Set(); G.down = new Set();
  if (!G.focus) return;
  const up = new Map(), down = new Map();
  for (const e of G.view.edges) { push(up, e.b, e.a); push(down, e.a, e.b); }
  const walk = (table, into) => {
    const stack = [G.focus];
    while (stack.length) for (const m of table.get(stack.pop()) || []) {
      if (!into.has(m)) { into.add(m); stack.push(m); }
    }
  };
  walk(up, G.up); walk(down, G.down);
}

function renderGraph(M) {
  const left = $("left");
  left.replaceChildren();
  left.classList.add("graph");
  const view = viewData();
  G.view = view; G.focus = view.focus;
  const stage = el("div", "stage");
  stage.id = "stage";
  stage.tabIndex = 0;
  stage.setAttribute("role", "application");
  stage.setAttribute("aria-label", "Graph. Arrow keys move between parts, plus and minus zoom, 0 fits.");
  const svg = svgEl("svg", { id: "gsvg", width: "100%", height: "100%" });
  svg.append(svgEl("g", { id: "gview" }));
  const mini = el("canvas");
  mini.id = "mini"; mini.width = 180; mini.height = 120;
  mini.setAttribute("aria-hidden", "true");
  stage.append(svg, mini);
  if (view.note) { const n = el("div", "warn gnote", view.note); stage.append(n); }
  left.append(stage);
  if (!view.nodes.length) {
    stage.append(el("div", "empty gnote", state.gunres ? "Nothing unresolved here." : "Nothing to draw with these filters."));
    return;
  }
  const hits = M ? view.nodes.filter((n) => M.test(n.id) || M.test(objects[n.id] && objects[n.id].source)) : [];
  G.hits = hits.map((n) => n.id);
  G.hitAt = -1;
  showCount();
  const layout = state.glayout;
  G.pos = layout === "tree" ? indented(view) : layered(view);
  emphasis();
  fit();
  wireStage(stage, svg, mini);
  clearTimeout(G.timer);
  if (layout === "force") {
    if (view.nodes.length > FORCE_MAX) {
      stage.append(el("div", "warn gnote", "A force layout is for " + FORCE_MAX + " parts or fewer: this is the layered one."));
    } else settle(0);
  }
}

// In steps, so the page stays usable while it settles.
function settle(step) {
  const nodes = G.view.nodes, P2 = G.pos;
  for (let round = 0; round < 4; round++) {
    const push2 = new Map(nodes.map((n) => [n.id, { x: 0, y: 0 }]));
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const p = P2.get(nodes[i].id), q = P2.get(nodes[j].id);
        const dx = (p.x - q.x) || 0.5, dy = (p.y - q.y) || 0.5;
        const d2 = Math.max(dx * dx + dy * dy, 25);
        const f = 90000 / d2;
        const d = Math.sqrt(d2);
        push2.get(nodes[i].id).x += f * dx / d; push2.get(nodes[i].id).y += f * dy / d;
        push2.get(nodes[j].id).x -= f * dx / d; push2.get(nodes[j].id).y -= f * dy / d;
      }
    }
    for (const e of G.view.edges) {
      const p = P2.get(e.a), q = P2.get(e.b);
      const dx = q.x - p.x, dy = q.y - p.y;
      push2.get(e.a).x += dx * 0.05; push2.get(e.a).y += dy * 0.05;
      push2.get(e.b).x -= dx * 0.05; push2.get(e.b).y -= dy * 0.05;
    }
    const cool = Math.max(0.15, 1 - step / 60);
    for (const n of nodes) {
      const p = P2.get(n.id), f = push2.get(n.id);
      p.x += Math.max(-30, Math.min(30, f.x)) * cool;
      p.y += Math.max(-30, Math.min(30, f.y)) * cool;
    }
  }
  fit();
  if (step < 60) G.timer = setTimeout(() => settle(step + 1), 16);
}

function bounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of G.pos.values()) {
    x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y);
    x1 = Math.max(x1, p.x + NODE_W); y1 = Math.max(y1, p.y + NODE_H);
  }
  return { x0, y0, x1, y1 };
}
function fit() {
  const stage = $("stage");
  if (!stage || !G.pos.size) return;
  const b = bounds(), w = stage.clientWidth || 800, h = stage.clientHeight || 600;
  G.k = Math.min(1, (w - 40) / (b.x1 - b.x0), (h - 40) / (b.y1 - b.y0));
  G.tx = 20 - b.x0 * G.k + Math.max(0, (w - 40 - (b.x1 - b.x0) * G.k) / 2);
  G.ty = 20 - b.y0 * G.k;
  G.drawn = "";
  schedule();
}
function zoom(by, cx, cy) {
  const stage = $("stage");
  if (!stage) return;
  const k = Math.max(0.02, Math.min(4, G.k * by));
  const x = cx === undefined ? stage.clientWidth / 2 : cx, y = cy === undefined ? stage.clientHeight / 2 : cy;
  G.tx = x - (x - G.tx) * k / G.k;
  G.ty = y - (y - G.ty) * k / G.k;
  G.k = k;
  schedule();
}
function centreOn(id) {
  const stage = $("stage"), p = G.pos.get(id);
  if (!stage || !p) return;
  G.tx = stage.clientWidth / 2 - (p.x + NODE_W / 2) * G.k;
  G.ty = stage.clientHeight / 2 - (p.y + NODE_H / 2) * G.k;
  schedule();
}
function schedule() {
  if (!G.frame) G.frame = requestAnimationFrame(() => { G.frame = 0; draw(); });
}
function shortLabel(n) {
  const info = objects[n.id];
  const text = info && info.source && !state.gkinds.has("source") ? info.source : n.id;
  return text.length > 30 ? "…" + text.slice(-29) : text;
}
function edgePath(a, b) {
  if (state.glayout === "tb") {
    const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y, m = (y1 + y2) / 2;
    return `M${x1} ${y1}C${x1} ${m} ${x2} ${m} ${x2} ${y2}`;
  }
  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2, m = (x1 + x2) / 2;
  return `M${x1} ${y1}C${m} ${y1} ${m} ${y2} ${x2} ${y2}`;
}
function sideOf(id) {
  return id === G.focus ? "focus" : G.up.has(id) ? "in" : G.down.has(id) ? "go" : G.focus ? "dim" : "";
}
function edgeSide(e) {
  if (!G.focus) return "";
  if ((G.up.has(e.a) || e.a === G.focus) && (G.up.has(e.b) || e.b === G.focus)) return "in";
  if ((G.down.has(e.a) || e.a === G.focus) && (G.down.has(e.b) || e.b === G.focus)) return "go";
  return "dim";
}
function draw() {
  const stage = $("stage"), view = $("gview");
  if (!stage || !view) return;
  view.setAttribute("transform", `translate(${G.tx} ${G.ty}) scale(${G.k})`);
  const w = stage.clientWidth, h = stage.clientHeight;
  const x0 = -G.tx / G.k - NODE_W, y0 = -G.ty / G.k - NODE_H;
  const x1 = (w - G.tx) / G.k, y1 = (h - G.ty) / G.k;
  const shown = [];
  for (const n of G.view.nodes) {
    const p = G.pos.get(n.id);
    if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1) {
      shown.push(n);
      if (shown.length >= DRAW_MAX) break;
    }
  }
  const showing = new Set(shown.map((n) => n.id));
  view.replaceChildren();
  for (const e of G.view.edges) {
    if (!showing.has(e.a) && !showing.has(e.b)) continue;
    const d = edgePath(G.pos.get(e.a), G.pos.get(e.b));
    const hit = svgEl("path", { d, class: "ehit" });
    hit.addEventListener("click", (ev) => { ev.stopPropagation(); showEdge(e); });
    view.append(svgEl("path", { d, class: "edge " + edgeSide(e) }), hit);
  }
  for (const n of shown) view.append(nodeEl(n));
  miniMap();
}
function nodeEl(n) {
  const p = G.pos.get(n.id);
  const hit = G.hits.includes(n.id);
  const g = svgEl("g", { class: "node " + n.kind + " " + sideOf(n.id) + (hit ? " hit" : "")
    + (["unresolved", "inferred", "excluded"].includes(n.status) ? " unsure" : ""),
    transform: `translate(${p.x} ${p.y})` });
  g.dataset.id = n.id;
  g.append(svgEl("rect", { width: NODE_W, height: NODE_H, rx: n.kind === "out" ? 14 : 4 }));
  const tag = svgEl("text", { x: 8, y: 19, class: "tag" });
  tag.textContent = TAGS[n.kind] + (n.status === "unresolved" ? "?" : "");
  const label = svgEl("text", { x: 42, y: 19 });
  label.textContent = shortLabel(n);
  const title = svgEl("title");
  title.textContent = n.id + (n.status ? " (" + n.status + ")" : "");
  g.append(tag, label, title);
  return g;
}
function miniMap() {
  const c = $("mini"), stage = $("stage");
  if (!c || !stage) return;
  const b = bounds();
  const s = Math.min(c.width / (b.x1 - b.x0 + 1), c.height / (b.y1 - b.y0 + 1));
  const x = c.getContext("2d");
  x.clearRect(0, 0, c.width, c.height);
  x.fillStyle = getComputedStyle(c).color;
  for (const n of G.view.nodes) {
    const p = G.pos.get(n.id);
    x.fillRect((p.x - b.x0) * s, (p.y - b.y0) * s, Math.max(1, NODE_W * s), Math.max(1, NODE_H * s));
  }
  x.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
  x.strokeRect((-G.tx / G.k - b.x0) * s, (-G.ty / G.k - b.y0) * s,
    stage.clientWidth / G.k * s, stage.clientHeight / G.k * s);
  G.miniScale = s; G.miniB = b;
}
function wireStage(stage, svg, mini) {
  let press = null;
  svg.addEventListener("pointerdown", (ev) => {
    const g = ev.target.closest ? ev.target.closest(".node") : null;
    press = { x: ev.clientX, y: ev.clientY, id: g ? g.dataset.id : null, moved: false, tx: G.tx, ty: G.ty };
    if (press.id) { const p = G.pos.get(press.id); press.px = p.x; press.py = p.y; }
  });
  svg.addEventListener("pointermove", (ev) => {
    if (!press) return;
    const dx = ev.clientX - press.x, dy = ev.clientY - press.y;
    if (!press.moved && Math.abs(dx) + Math.abs(dy) > 3) {
      press.moved = true;  // captured only for a drag, so a plain click reaches an edge
      svg.setPointerCapture(ev.pointerId);
    }
    if (press.id) {
      const p = G.pos.get(press.id);
      p.x = press.px + dx / G.k; p.y = press.py + dy / G.k;
    } else { G.tx = press.tx + dx; G.ty = press.ty + dy; }
    schedule();
  });
  svg.addEventListener("pointerup", (ev) => {
    const done = press;
    press = null;
    if (done && !done.moved && done.id) pick(done.id);
  });
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const box = svg.getBoundingClientRect();
    zoom(ev.deltaY < 0 ? 1.15 : 1 / 1.15, ev.clientX - box.left, ev.clientY - box.top);
  }, { passive: false });
  mini.addEventListener("click", (ev) => {
    const box = mini.getBoundingClientRect();
    const x = (ev.clientX - box.left) / G.miniScale + G.miniB.x0;
    const y = (ev.clientY - box.top) / G.miniScale + G.miniB.y0;
    G.tx = stage.clientWidth / 2 - x * G.k;
    G.ty = stage.clientHeight / 2 - y * G.k;
    schedule();
  });
  stage.addEventListener("keydown", graphKey);
}
function pick(id) {
  const info = objects[id];
  state.selected = state.view !== "modules" && info && info.source ? info.source : id;
  G.focus = id;
  emphasis();
  G.drawn = "";
  schedule();
  renderDetails();
  writeHash();
}
function graphKey(ev) {
  const key = ev.key;
  if (key === "+" || key === "=") zoom(1.2);
  else if (key === "-") zoom(1 / 1.2);
  else if (key === "0") fit();
  else if (key.startsWith("Arrow")) {
    const nodes = G.view.nodes;
    const from = G.pos.get(G.focus || (nodes[0] && nodes[0].id));
    if (!from) return;
    const sign = key === "ArrowLeft" || key === "ArrowUp" ? -1 : 1;
    const flat = key === "ArrowLeft" || key === "ArrowRight";
    let best = null, score = Infinity;
    for (const n of nodes) {
      if (n.id === G.focus) continue;
      const p = G.pos.get(n.id);
      const along = (flat ? p.x - from.x : p.y - from.y) * sign;
      const across = Math.abs(flat ? p.y - from.y : p.x - from.x);
      if (along <= 0) continue;
      const s = along + across * 3;
      if (s < score) { score = s; best = n.id; }
    }
    if (best) { pick(best); centreOn(best); }
  } else return;
  ev.preventDefault();
}
let listAt = -1;
const drawn = () => ["graph", "modules"].includes(state.view);
const listHits = () => [...document.querySelectorAll("#left [data-path]")].filter((r) => r.querySelector("mark"));
function showCount() {
  const c = $("count");
  const n = drawn() ? G.hits.length : listHits().length;
  c.hidden = !state.q || state.view === "status";
  c.textContent = (n ? ((drawn() ? G.hitAt : listAt) + 1) : 0) + " / " + n;
  $("prev").hidden = $("next").hidden = c.hidden;
}
function stepHit(by) {
  if (!drawn()) {
    const rows = listHits();
    if (!rows.length) return;
    listAt = (listAt + by + rows.length) % rows.length;
    rows[listAt].scrollIntoView({ block: "center" });
    select(rows[listAt].dataset.src || rows[listAt].dataset.path);
    showCount();
    return;
  }
  if (!G.hits.length) return;
  G.hitAt = (G.hitAt + by + G.hits.length) % G.hits.length;
  showCount();
  pick(G.hits[G.hitAt]);
  centreOn(G.hits[G.hitAt]);
}
function showEdge(e) {
  const right = $("right");
  right.replaceChildren(el("h2", "mono", e.a));
  right.append(el("p", "", EDGE_TEXT[e.type] + (e.count ? " (" + e.count + " uses)" : "")));
  right.firstChild.replaceChildren(go(e.a));
  const to = el("h2", "mono");
  to.append(go(e.b));
  right.append(to);
}

// ---- saving what is on screen --------------------------------------------------
function conditions() {
  return { view: state.view, search: state.q, picked: state.selected, kinds: [...state.gkinds],
    direction: state.gdir, depth: state.gdepth, layout: state.glayout, unresolved: state.gunres };
}
function download(name, blob) {
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function exportJson() {
  const data = { conditions: conditions(),
    nodes: G.view.nodes.map((n) => ({ path: n.id, kind: n.kind, status: n.status })),
    edges: G.view.edges.map((e) => ({ from: e.a, to: e.b, type: e.type })) };
  download("deps-view.json", new Blob([JSON.stringify(data, null, 1)], { type: "application/json" }));
}
function svgText() {
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const b = bounds();
  const w = Math.ceil(b.x1 - b.x0 + 40), h = Math.ceil(b.y1 - b.y0 + 40);
  const svg = svgEl("svg", { xmlns: NS, width: w, height: h, viewBox: `${b.x0 - 20} ${b.y0 - 20} ${w} ${h}`,
    "font-family": "monospace", "font-size": 12 });
  svg.append(svgEl("rect", { x: b.x0 - 20, y: b.y0 - 20, width: w, height: h, fill: color("--bg") }));
  for (const e of G.view.edges) {
    svg.append(svgEl("path", { d: edgePath(G.pos.get(e.a), G.pos.get(e.b)), fill: "none", stroke: color("--muted") }));
  }
  for (const n of G.view.nodes) {
    const p = G.pos.get(n.id);
    const g = svgEl("g", { transform: `translate(${p.x} ${p.y})` });
    g.append(svgEl("rect", { width: NODE_W, height: NODE_H, rx: 4, fill: color("--panel"), stroke: color("--line") }));
    const t = svgEl("text", { x: 8, y: 19, fill: color("--text") });
    t.textContent = TAGS[n.kind] + " " + shortLabel(n);
    g.append(t);
    svg.append(g);
  }
  return { text: new XMLSerializer().serializeToString(svg), w, h };
}
function exportSvg() {
  download("deps-view.svg", new Blob([svgText().text], { type: "image/svg+xml" }));
}
function exportPng() {
  const { text, w, h } = svgText();
  const scale = Math.min(2, 16000 / Math.max(w, h));
  const img = new Image();
  img.onload = () => {
    const c = el("canvas");
    c.width = w * scale; c.height = h * scale;
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    c.toBlob((blob) => download("deps-view.png", blob));
  };
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(text);
}

// ---- parts that are not certain --------------------------------------------------
function statusRows() {
  const rows = [];
  for (const [path, why] of excludedWhy) rows.push({ path, status: "excluded", why });
  for (const [path, s] of statusMap) {
    if (s === "excluded") continue;
    const why = archives[path] ? "prebuilt: not made by this build"
      : objects[path] && !objects[path].source ? "no source known" : "said by the build's own file";
    rows.push({ path, status: s, why });
  }
  for (const [path, info] of Object.entries(outs)) {
    for (const lib of info.libs) rows.push({ path, status: "unresolved", why: "linked -l" + lib + ", not made by this build" });
  }
  return rows;
}
function renderStatus(M) {
  const left = $("left");
  left.replaceChildren();
  const inferred = [...headerUsers.keys()].filter((h) => statusOf(h) === "inferred").length;
  left.append(el("p", "hint", "Everything not read for certain, and why. "
    + (inferred ? inferred + " headers are inferred by following #include; #if is not evaluated. " : "")
    + "Every other part: " + D.default_status + "."));
  const rows = statusRows().filter((r) => !M || M.test(r.path));
  if (!rows.length) { left.append(el("div", "empty", "Nothing else to report.")); return; }
  const t = el("table", "h");
  const head = el("tr");
  ["Part", "Status", "Why"].forEach((x) => head.append(el("th", "", x)));
  t.append(head);
  for (const r of rows.slice(0, ROW_BUDGET)) {
    const tr = el("tr");
    tr.dataset.path = r.path;
    tr.append(el("td", "", r.path), el("td", "", r.status), el("td", "", r.why));
    tr.addEventListener("click", () => select(r.path));
    t.append(tr);
  }
  left.append(t);
}
