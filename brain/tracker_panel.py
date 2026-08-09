# =============================================================================
# Mobile tracker dashboard — brain/tracker_panel.py
# =============================================================================
# WHAT: A read-only, mobile-first view of the tracker Hub (project boards,
#       directive statuses, live agent activity), served by Brain at
#       GET /admin/tracker. It is deliberately a SEPARATE page from
#       brain/panel.py's desktop admin console, not another card inside it —
#       it's meant to be opened on a phone (bookmarked/added to homescreen)
#       and has a different density (bottom-tab nav, stacked cards, no
#       tables) than the desktop console's table-heavy layout.
#
# WHY read-only: v1 scope. Answering questions, cancelling or reprioritising
#       directives stays in Telegram via Кая — this is glance-and-close
#       visibility, not another place to take tracker actions from.
#
# WHY it doesn't call the tracker Hub directly: the phone should hold exactly
#       ONE credential (Brain's admin token). Tracker's own admin token stays
#       server-side in Brain's config; this page calls Brain's
#       /admin/tracker/* proxy routes, which is the only thing that knows
#       tracker's token (see brain/tracker_client.py, brain/server.py).
#
# WHY 15-20s polling paused on backgrounding, not tracker's own 7s: tracker's
#       desktop panel is a console left open on a monitor — battery isn't a
#       concern there. A phone tab in a pocket is a different animal, hence
#       the Page Visibility API pause/resume below.
#
# HOW: BrainServer serves TRACKER_PANEL_HTML at /admin/tracker. Login reuses
#       the exact localStorage-token idiom from brain/panel.py:160-165 (this
#       repo has no bundler, so panels are self-contained HTML by
#       convention — the idiom is copied, not imported).
# =============================================================================

TRACKER_PANEL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tracker</title>
<style>
  :root {
    --bg:#0e1014; --card:#171a21; --card2:#1d212c; --line:#262b36; --fg:#e6e9ef;
    --mut:#8b93a7; --accent:#5b9dff;
    --queued:#6b7280; --dispatched:#5b9dff; --running:#e0a94a;
    --blocked:#f0883e; --review:#8957e5;
    --done:#3fb950; --failed:#f85149; --cancelled:#a371f7;
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif;
         padding-bottom:64px; }
  header { padding:14px 16px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:12px; position:sticky; top:0; background:var(--bg); z-index:5; }
  header h1 { font-size:16px; margin:0; font-weight:600; flex:1; }
  header .stale { font-size:12px; color:var(--mut); }
  main { max-width:640px; margin:0 auto; padding:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:14px; margin-bottom:12px; }
  .card h2 { margin:0 0 10px; font-size:13px; color:var(--mut); text-transform:uppercase;
             letter-spacing:.04em; }
  .mut { color:var(--mut); }
  .row { display:flex; gap:8px; align-items:center; }
  .badge { display:inline-block; padding:2px 9px; border-radius:20px; font-size:12px;
           color:#0e1014; font-weight:600; }
  input,button { font:inherit; color:var(--fg); background:var(--bg);
    border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  button { cursor:pointer; }
  #login { max-width:420px; margin:60px auto; }
  #login button { width:100%; background:var(--accent); border-color:var(--accent); color:#fff; }
  .hide { display:none; }
  .proj-row { padding:10px 0; border-top:1px solid var(--line); cursor:pointer; }
  .proj-row:first-child { border-top:none; }
  .proj-name { font-weight:600; }
  .directive-row { padding:8px 0 8px 12px; border-left:2px solid var(--line); margin-top:6px; }
  .directive-title { font-size:14px; }
  .agent-card { padding:10px 0; border-top:1px solid var(--line); }
  .agent-card:first-child { border-top:none; }
  .donut-row { display:flex; flex-wrap:wrap; gap:6px 14px; margin-top:6px; }
  nav.tabs { position:fixed; bottom:0; left:0; right:0; display:flex; background:var(--card2);
             border-top:1px solid var(--line); z-index:5; }
  nav.tabs button { flex:1; background:none; border:none; border-radius:0; color:var(--mut);
                     padding:12px 0; font-size:13px; }
  nav.tabs button.active { color:var(--fg); border-top:2px solid var(--accent); }
  .empty { color:var(--mut); padding:8px 0; }
</style>
</head>
<body>
<header>
  <h1>📋 Tracker</h1>
  <span id="stale" class="stale"></span>
  <button id="logout" class="hide" style="background:transparent;border:none;color:var(--mut)">exit</button>
</header>

<main>
  <div id="login" class="card">
    <h2>Admin token</h2>
    <div class="row" style="flex-direction:column;align-items:stretch">
      <input id="token" type="password" placeholder="BRAIN_ADMIN_TOKEN">
      <button onclick="login()">Enter</button>
    </div>
    <p id="loginErr" class="mut"></p>
  </div>

  <div id="app" class="hide">
    <div id="view-overview">
      <div class="card">
        <h2>Fleet</h2>
        <div id="overviewBody" class="mut">Loading…</div>
      </div>
    </div>
    <div id="view-projects" class="hide">
      <div class="card">
        <h2>Projects</h2>
        <div id="projectsBody" class="mut">Loading…</div>
      </div>
    </div>
    <div id="view-activity" class="hide">
      <div class="card">
        <h2>Activity — what's running now</h2>
        <div id="activityBody" class="mut">Loading…</div>
      </div>
    </div>
  </div>
</main>

<nav class="tabs hide" id="tabs">
  <button id="tab-overview" class="active" onclick="showTab('overview')">Overview</button>
  <button id="tab-projects" onclick="showTab('projects')">Projects</button>
  <button id="tab-activity" onclick="showTab('activity')">Activity</button>
</nav>

<script>
let TOKEN = localStorage.getItem("brainAdminToken") || "";
let CURRENT_TAB = "overview";
let BUSY = false;         // guard against a poll clobbering an in-progress tap/expand
let POLL_TIMER = null;
const POLL_MS = 18000;    // coarser than the desktop console's 7s — glance-and-close, not a monitor

async function api(path) {
  const r = await fetch(path, {headers:{"Authorization":"Bearer "+TOKEN}});
  if (r.status === 401) { logout("token rejected"); throw new Error("unauthorized"); }
  return r;
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmtTime(s){ return (s||"").replace("T"," ").slice(0,16); }
function badge(st){ return `<span class="badge" style="background:var(--${st},var(--mut))">${esc(st)}</span>`; }

function login(){ TOKEN = document.getElementById("token").value.trim();
  if(!TOKEN){ return; } localStorage.setItem("brainAdminToken", TOKEN); boot(); }
function logout(msg){
  TOKEN=""; localStorage.removeItem("brainAdminToken"); stopPolling();
  document.getElementById("app").classList.add("hide");
  document.getElementById("tabs").classList.add("hide");
  document.getElementById("login").classList.remove("hide");
  document.getElementById("logout").classList.add("hide");
  if(msg) document.getElementById("loginErr").textContent = msg;
}
document.getElementById("logout").onclick = () => logout("");

function showTab(name){
  CURRENT_TAB = name;
  for (const t of ["overview","projects","activity"]) {
    document.getElementById("view-"+t).classList.toggle("hide", t!==name);
    document.getElementById("tab-"+t).classList.toggle("active", t===name);
  }
  refreshCurrentTab();
}

async function boot(){
  await refreshCurrentTab();
  if (!TOKEN) return; // refreshCurrentTab's 401 path already called logout()
  document.getElementById("login").classList.add("hide");
  document.getElementById("app").classList.remove("hide");
  document.getElementById("tabs").classList.remove("hide");
  document.getElementById("logout").classList.remove("hide");
  startPolling();
}

async function refreshCurrentTab(){
  if (BUSY) return;
  try {
    if (CURRENT_TAB === "overview") await loadOverview();
    else if (CURRENT_TAB === "projects") await loadProjects();
    else await loadActivity();
    document.getElementById("stale").textContent = "";
  } catch(e) {
    document.getElementById("stale").textContent = "tracker unreachable";
  }
}

function startPolling(){
  stopPolling();
  POLL_TIMER = setInterval(refreshCurrentTab, POLL_MS);
}
function stopPolling(){ if (POLL_TIMER) { clearInterval(POLL_TIMER); POLL_TIMER = null; } }
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") stopPolling();
  else if (TOKEN) { refreshCurrentTab(); startPolling(); }
});

async function loadOverview(){
  const r = await api("/admin/tracker/overview");
  if (!r.ok) { const d = await r.json(); throw new Error(d.error||"HTTP "+r.status); }
  const d = await r.json();
  const counts = d.task_counts || {};
  const order = ["running","blocked","review","queued","dispatched","done","failed","cancelled"];
  const rows = order.filter(s => counts[s]).map(s =>
    `<span class="row">${badge(s)}<span class="mut">${counts[s]}</span></span>`).join("");
  document.getElementById("overviewBody").innerHTML =
    `<div><b>${d.projects}</b> project(s)</div><div class="donut-row">${rows || '<span class="empty">No directives yet.</span>'}</div>`;
}

let PROJECTS = [];

async function loadProjects(){
  const r = await api("/admin/tracker/projects");
  if (!r.ok) { const d = await r.json(); throw new Error(d.error||"HTTP "+r.status); }
  const d = await r.json();
  PROJECTS = d.projects || [];
  // data-idx (not the project name) drives the click handler — a project
  // name is Warden-controlled input and must never be interpolated into an
  // inline event handler string (see modules/tracker/panel.py's esc()/jsq()
  // comment: esc() alone doesn't stop a name from breaking out of both the
  // HTML attribute AND the JS string it sits in).
  document.getElementById("projectsBody").innerHTML = PROJECTS.length ? PROJECTS.map((p,i) =>
    `<div class="proj-row" data-idx="${i}" onclick="toggleProject(${i})">
       <div class="proj-name">${esc(p.name)}</div>
       <div class="mut">${esc(p.state)}${p.has_warden ? " · warden" : " · poller"}</div>
       <div id="proj-tasks-${i}"></div>
     </div>`).join("") : `<div class="empty">No projects registered.</div>`;
}

async function toggleProject(idx){
  const holder = document.getElementById("proj-tasks-"+idx);
  if (holder.innerHTML) { holder.innerHTML = ""; return; }
  const name = PROJECTS[idx].name;
  BUSY = true;
  try {
    const r = await api("/admin/tracker/tasks?project="+encodeURIComponent(name));
    if (!r.ok) { holder.innerHTML = `<div class="empty">tracker unreachable</div>`; return; }
    const d = await r.json();
    const tasks = d.tasks || [];
    holder.innerHTML = tasks.length ? tasks.map(t =>
      `<div class="directive-row"><div class="directive-title">${esc(t.title||t.task_id||("#"+t.id))}</div>
         <div class="row">${badge(t.status)}<span class="mut">${fmtTime(t.updated_at||t.created_at)}</span></div>
       </div>`).join("") : `<div class="empty">No directives.</div>`;
  } finally { BUSY = false; }
}

async function loadActivity(){
  const r = await api("/admin/tracker/activity");
  if (!r.ok) { const d = await r.json(); throw new Error(d.error||"HTTP "+r.status); }
  const d = await r.json();
  const rows = d.activity || [];
  document.getElementById("activityBody").innerHTML = rows.length ? rows.map(a =>
    `<div class="agent-card">
       <div class="row"><b>${esc(a.agent_slug)}</b><span class="mut">${esc(a.project)}</span></div>
       <div>${esc(a.directive ? a.directive.title||a.directive.task_id||"" : "")}</div>
       <div class="row">${badge(a.state)}<span class="mut">${esc(a.phase||"")} ${a.progress!=null?a.progress+"%":""}</span></div>
       ${a.blockers ? `<div class="mut">blocked: ${esc(a.blockers)}</div>` : ""}
     </div>`).join("") : `<div class="empty">Nothing running right now.</div>`;
}

if (TOKEN) boot();
</script>
</body>
</html>
"""
