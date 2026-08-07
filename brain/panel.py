# =============================================================================
# Brain admin panel — brain/panel.py
# =============================================================================
# WHAT: A single self-contained admin console (inline CSS + JS, no build step, no
#       external assets) served by Brain at GET /admin/panel. It lets the owner:
#         - mint / list agents (and see their delivery_addr)
#         - edit an agent's access-list (add/remove allow/deny rules) — e.g.
#           disable a module for Кузя with one click
#         - browse shared memory (facts) and the provenance feed (who wrote what)
#
# WHY aiohttp + inline HTML (not the plan's FastAPI + HTMX): this repo already
#       has exactly this pattern in app/tracker/panel.py — a service serves its
#       own single-file console, the admin token is typed into a login box, kept
#       in localStorage, and sent as the Bearer on every request. Following it
#       keeps Brain a single aiohttp server with no new dependency, and the
#       page holds no secrets. The plan's "FastAPI + HTMX хватит" was a
#       suggestion ("would suffice"), not a hard requirement.
#
# WHY the page holds no secrets: it calls Brain's admin JSON endpoints
#       (/admin/agents, /admin/tools, /admin/access, /admin/memory,
#       /admin/provenance), each guarded by the admin token. A 401 clears the
#       stored token and returns to the login box.
#
# HOW: BrainServer serves PANEL_HTML at /admin/panel. Open
#       http://localhost:8772/admin/panel and paste BRAIN_ADMIN_TOKEN.
# =============================================================================

PANEL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brain Admin</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e6e9ef; --mut:#8b93a7;
          --accent:#5b9dff; --danger:#ff6b6b; --ok:#3ddc97; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 system-ui,sans-serif; }
  header { padding:14px 20px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:12px; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .sp { flex:1; }
  main { max-width:1000px; margin:0 auto; padding:20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px; margin-bottom:18px; }
  .card h2 { margin:0 0 12px; font-size:14px; color:var(--mut); text-transform:uppercase;
             letter-spacing:.04em; }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--mut); font-weight:500; }
  input,select,button { font:inherit; color:var(--fg); background:var(--bg);
    border:1px solid var(--line); border-radius:7px; padding:7px 10px; }
  button { cursor:pointer; background:var(--accent); border-color:var(--accent); color:#fff; }
  button.ghost { background:transparent; color:var(--fg); }
  button.danger { background:transparent; border-color:var(--danger); color:var(--danger); }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .mut { color:var(--mut); }
  .tag { display:inline-block; padding:1px 7px; border-radius:20px; font-size:12px;
         border:1px solid var(--line); }
  .deny { color:var(--danger); border-color:var(--danger); }
  .allow { color:var(--ok); border-color:var(--ok); }
  #login { max-width:420px; margin:60px auto; }
  .hide { display:none; }
  code { background:var(--bg); padding:1px 5px; border-radius:5px; }
</style>
</head>
<body>
<header>
  <h1>🧠 Brain Admin</h1><span class="sp"></span>
  <span id="who" class="mut"></span>
  <button id="logout" class="ghost hide">Log out</button>
</header>

<main>
  <div id="login" class="card">
    <h2>Admin token</h2>
    <div class="row">
      <input id="token" type="password" placeholder="BRAIN_ADMIN_TOKEN" style="flex:1">
      <button onclick="login()">Enter</button>
    </div>
    <p id="loginErr" class="mut"></p>
  </div>

  <div id="app" class="hide">
    <div class="card">
      <h2>Agents</h2>
      <table><thead><tr><th>id</th><th>slug</th><th>delivery_addr</th></tr></thead>
        <tbody id="agents"></tbody></table>
      <div class="row" style="margin-top:12px">
        <input id="newSlug" placeholder="new agent slug (e.g. kuzya)">
        <input id="newAddr" placeholder="delivery_addr (optional)" style="flex:1">
        <button onclick="mint()">Mint agent</button>
      </div>
      <p id="minted" class="mut"></p>
    </div>

    <div class="card">
      <h2>Access-list</h2>
      <div class="row">
        <label>Agent</label>
        <select id="accAgent" onchange="loadAccess()"></select>
        <span class="mut">allow-by-default; add a rule to carve an exception</span>
      </div>
      <table style="margin-top:10px"><thead>
        <tr><th>rule</th><th>module</th><th>tool</th><th>effect</th><th></th></tr></thead>
        <tbody id="rules"></tbody></table>
      <div class="row" style="margin-top:12px">
        <select id="ruleModule"><option value="">(any module)</option></select>
        <input id="ruleTool" placeholder="tool (blank = whole module)">
        <select id="ruleAllowed">
          <option value="false">deny</option><option value="true">allow</option>
        </select>
        <button onclick="addRule()">Add rule</button>
      </div>
    </div>

    <div class="card">
      <h2>Profile</h2>
      <div id="profile" class="mut">—</div>
    </div>

    <div class="card">
      <h2>Reminders</h2>
      <table><thead>
        <tr><th>id</th><th>when (due_at)</th><th>tz</th><th>text</th><th>recurrence</th><th>status</th><th></th></tr></thead>
        <tbody id="reminders"></tbody></table>
    </div>

    <div class="card">
      <h2>Shared memory (facts)</h2>
      <table><thead><tr><th>id</th><th>fact</th><th>by agent</th></tr></thead>
        <tbody id="facts"></tbody></table>
    </div>

    <div class="card">
      <h2>Provenance (who wrote what)</h2>
      <table><thead><tr><th>when</th><th>agent</th><th>action</th><th>entity</th><th>content</th></tr></thead>
        <tbody id="prov"></tbody></table>
    </div>

    <div class="card">
      <h2>Backups</h2>
      <div class="row" style="margin-bottom:10px">
        <button id="backupBtn" onclick="doBackup()">Backup now</button>
        <span id="backupMsg" class="mut"></span>
      </div>
      <table><thead><tr><th>when</th><th>size</th><th>key</th><th>restore</th></tr></thead>
        <tbody id="backups"></tbody></table>
      <p class="mut">Restore is destructive and runs from the host:
        <code>scripts/restore.sh &lt;key&gt; &lt;age-identity-file&gt;</code> —
        it stops the agents, restores the whole cluster, and restarts.</p>
    </div>
  </div>
</main>

<script>
let TOKEN = localStorage.getItem("brainAdminToken") || "";
let AGENTS = [];

async function api(path, opts={}) {
  const r = await fetch(path, {...opts, headers:{
    "Authorization":"Bearer "+TOKEN, "Content-Type":"application/json", ...(opts.headers||{})}});
  if (r.status === 401) { logout("token rejected"); throw new Error("unauthorized"); }
  return r;
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function login(){ TOKEN = document.getElementById("token").value.trim();
  if(!TOKEN){ return; } localStorage.setItem("brainAdminToken", TOKEN); boot(); }
function logout(msg){ TOKEN=""; localStorage.removeItem("brainAdminToken");
  document.getElementById("app").classList.add("hide");
  document.getElementById("login").classList.remove("hide");
  document.getElementById("logout").classList.add("hide");
  document.getElementById("who").textContent="";
  if(msg) document.getElementById("loginErr").textContent=msg; }
document.getElementById("logout").onclick=()=>logout("");

async function boot(){
  try {
    await loadAgents(); await loadTools(); await loadProfile(); await loadReminders();
    await loadMemory(); await loadProvenance(); await loadBackups();
    document.getElementById("login").classList.add("hide");
    document.getElementById("app").classList.remove("hide");
    document.getElementById("logout").classList.remove("hide");
    document.getElementById("who").textContent = AGENTS.length + " agents";
  } catch(e) { /* logout already handled 401 */ }
}

async function loadAgents(){
  const d = await (await api("/admin/agents")).json();
  AGENTS = d.agents||[];
  document.getElementById("agents").innerHTML = AGENTS.map(a=>
    `<tr><td>${a.id}</td><td>${esc(a.slug)}</td><td class="mut">${esc(a.delivery_addr||"—")}</td></tr>`).join("");
  const sel = document.getElementById("accAgent");
  sel.innerHTML = AGENTS.map(a=>`<option value="${a.id}">${esc(a.slug)}</option>`).join("");
  if(AGENTS.length) loadAccess();
}
async function mint(){
  const slug = document.getElementById("newSlug").value.trim();
  const delivery_addr = document.getElementById("newAddr").value.trim();
  if(!slug) return;
  const r = await api("/admin/agents",{method:"POST",body:JSON.stringify({slug,delivery_addr})});
  const d = await r.json();
  if(!r.ok){ document.getElementById("minted").textContent = "Error: "+(d.error||r.status); return; }
  document.getElementById("minted").innerHTML =
    `Minted <b>${esc(d.slug)}</b> — token (shown once): <code>${esc(d.token)}</code>`;
  document.getElementById("newSlug").value=""; document.getElementById("newAddr").value="";
  loadAgents();
}
async function loadTools(){
  const d = await (await api("/admin/tools")).json();
  const modules = [...new Set((d.tools||[]).map(t=>t.module).filter(Boolean))];
  document.getElementById("ruleModule").innerHTML =
    `<option value="">(any module)</option>` + modules.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join("");
}
async function loadAccess(){
  const aid = document.getElementById("accAgent").value;
  const d = await (await api("/admin/access?agent_id="+encodeURIComponent(aid))).json();
  document.getElementById("rules").innerHTML = (d.rules||[]).map(r=>
    `<tr><td>#${r.id}</td><td>${esc(r.module||"*")}</td><td>${esc(r.tool||"*")}</td>`+
    `<td><span class="tag ${r.allowed?"allow":"deny"}">${r.allowed?"allow":"deny"}</span></td>`+
    `<td><button class="danger" onclick="delRule(${r.id})">remove</button></td></tr>`).join("")
    || `<tr><td colspan="5" class="mut">No rules — this agent can call everything.</td></tr>`;
}
async function addRule(){
  const agent_id = parseInt(document.getElementById("accAgent").value,10);
  const module = document.getElementById("ruleModule").value;
  const tool = document.getElementById("ruleTool").value.trim();
  const allowed = document.getElementById("ruleAllowed").value === "true";
  await api("/admin/access",{method:"POST",body:JSON.stringify({agent_id,module,tool,allowed})});
  document.getElementById("ruleTool").value=""; loadAccess();
}
async function delRule(id){ await api("/admin/access/"+id,{method:"DELETE"}); loadAccess(); }

async function loadMemory(){
  const d = await (await api("/admin/memory")).json();
  const byId = Object.fromEntries(AGENTS.map(a=>[a.id,a.slug]));
  document.getElementById("facts").innerHTML = (d.facts||[]).map(f=>
    `<tr><td>${f.id}</td><td>${esc(f.content)}</td><td class="mut">${esc(byId[f.agent_id]||"—")}</td></tr>`).join("")
    || `<tr><td colspan="3" class="mut">No facts yet.</td></tr>`;
}
async function loadProfile(){
  const d = await (await api("/admin/profile")).json();
  const p = d.profile;
  document.getElementById("profile").innerHTML = p
    ? `timezone: <b>${esc(p.timezone||"—")}</b> &nbsp; home: ${esc(p.home_location||"—")}`
    : `<span class="deny">No profile set — naive reminder times fall back to the server default.</span>`;
}
async function loadReminders(){
  const d = await (await api("/admin/reminders")).json();
  const byId = Object.fromEntries(AGENTS.map(a=>[a.id,a.slug]));
  document.getElementById("reminders").innerHTML = (d.reminders||[]).map(r=>
    `<tr><td>${r.id}</td><td>${esc((r.due_at||"").replace("T"," "))}</td>`+
    `<td class="mut">${esc(r.tz||"—")}</td>`+
    // A self-note wakes the agent for a whole turn instead of being relayed,
    // so it must not look like an ordinary reminder in this list.
    `<td>${esc(r.text)}${r.audience==="agent"?` <span class="tag">note to self</span>`:""}</td>`+
    `<td class="mut">${esc(r.recurrence)}</td>`+
    `<td><span class="tag ${r.is_done?"":"allow"}">${r.is_done?"done":"pending"}</span></td>`+
    `<td>${r.is_done?"":`<button class="danger" onclick="cancelReminder(${r.id})">cancel</button>`}</td></tr>`).join("")
    || `<tr><td colspan="7" class="mut">No reminders.</td></tr>`;
}
async function cancelReminder(id){ await api("/admin/reminders/"+id,{method:"DELETE"}); loadReminders(); }

async function loadProvenance(){
  const d = await (await api("/admin/provenance")).json();
  const byId = Object.fromEntries(AGENTS.map(a=>[a.id,a.slug]));
  document.getElementById("prov").innerHTML = (d.changes||[]).map(c=>
    `<tr><td class="mut">${esc((c.at||"").replace("T"," ").slice(0,16))}</td>`+
    `<td>${esc(byId[c.agent_id]||c.agent_id||"—")}</td><td>${esc(c.action)}</td>`+
    `<td>${esc(c.entity)}#${c.entity_id}</td><td class="mut">${esc(c.new||c.old||"")}</td></tr>`).join("")
    || `<tr><td colspan="5" class="mut">No changes recorded yet.</td></tr>`;
}

function fmtSize(n){ if(n==null) return "—"; const u=["B","KB","MB","GB"]; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++;} return n.toFixed(1)+u[i]; }
async function loadBackups(){
  const r = await api("/admin/backups"); const d = await r.json();
  if(!r.ok){ document.getElementById("backups").innerHTML = `<tr><td colspan="4" class="mut">${esc(d.error||("HTTP "+r.status))}</td></tr>`; return; }
  document.getElementById("backups").innerHTML = (d.backups||[]).map(b=>
    `<tr><td class="mut">${esc((b.last_modified||"").replace("T"," ").slice(0,16))}</td>`+
    `<td>${fmtSize(b.size)}</td><td><code>${esc(b.key)}</code></td>`+
    `<td class="mut">restore.sh ${esc(b.key)} &lt;key-file&gt;</td></tr>`).join("")
    || `<tr><td colspan="4" class="mut">No backups yet.</td></tr>`;
}
async function doBackup(){
  const btn=document.getElementById("backupBtn"), msg=document.getElementById("backupMsg");
  btn.disabled=true; msg.textContent="Backing up… (dump → encrypt → upload)";
  try{
    const r=await api("/admin/backups",{method:"POST"}); const d=await r.json();
    msg.textContent = r.ok ? ("Done: "+(d.key||"")) : ("Error: "+(d.error||r.status));
    await loadBackups();
  }catch(e){ msg.textContent="Error: "+e; }
  finally{ btn.disabled=false; }
}

if(TOKEN) boot();
</script>
</body>
</html>
"""
