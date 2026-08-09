// Load .env.tracker from repo root
const fs = require('fs');
const envPath = require('path').join(__dirname, '..', '.env.tracker');
if (fs.existsSync(envPath)) {
  fs.readFileSync(envPath, 'utf8')
    .split('\n')
    .filter(l => l.trim() && !l.startsWith('#'))
    .forEach(l => {
      const [k, ...rest] = l.split('=');
      if (k && rest.length) process.env[k.trim()] = rest.join('=').trim();
    });
}

const { DatabaseSync } = require('node:sqlite');
const express = require('express');
const path = require('path');
const https = require('https');
const yaml = require('js-yaml');

const app = express();
const PORT = process.env.PORT || 3333;
const TRACKER_DIR = process.env.TRACKER_DIR || path.join(__dirname, '..', 'docs', 'tracker');
const TELEGRAM_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID || '';
const AGENT_RUNNER_URL = process.env.AGENT_RUNNER_URL || '';
const AGENTS_DIR = process.env.AGENTS_DIR || path.join(TRACKER_DIR, '..', '..', '.claude', 'agents');

// ── Database ──────────────────────────────────────────────────────────────────

const dataDir = path.join(__dirname, 'data');
fs.mkdirSync(dataDir, { recursive: true });

const db = new DatabaseSync(path.join(dataDir, 'tracker.db'));
db.exec(`PRAGMA journal_mode = WAL;`);
db.exec(`
  CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT    PRIMARY KEY,
    title       TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    type        TEXT    NOT NULL DEFAULT 'feature',
    priority    INTEGER DEFAULT 0,
    status      TEXT    DEFAULT 'pending',
    branch      TEXT    DEFAULT '',
    pr_url      TEXT    DEFAULT '',
    notes       TEXT    DEFAULT '',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE IF NOT EXISTS commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT    NOT NULL,
    payload      TEXT    DEFAULT '{}',
    source       TEXT    DEFAULT 'api',
    status       TEXT    DEFAULT 'pending',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME
  );

  CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    data       TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE IF NOT EXISTS research_qa (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    question    TEXT    NOT NULL,
    answer      TEXT    DEFAULT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    answered_at DATETIME
  );
`);

// ── Helpers ───────────────────────────────────────────────────────────────────

function slugify(str) {
  return str
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 40);
}

function uniqueId(title) {
  const base = slugify(title) || 'task';
  const existing = db.prepare('SELECT id FROM tasks WHERE id LIKE ?').all(`${base}%`);
  if (existing.length === 0) return base;
  const nums = existing
    .map(r => r.id.replace(base, '').replace(/^-/, '') || '0')
    .map(Number)
    .filter(n => !isNaN(n));
  return `${base}-${Math.max(...nums) + 1}`;
}

function now() {
  return new Date().toISOString();
}

// ── SSE broadcast ─────────────────────────────────────────────────────────────

const sseClients = new Set();

function broadcast(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClients) {
    try { res.write(msg); } catch (_) { sseClients.delete(res); }
  }
}

// ── Agent roster reader ───────────────────────────────────────────────────────

let _rosterCache = null;
let _rosterCacheTime = 0;
const ROSTER_CACHE_TTL = 60_000; // 60 seconds

function deriveTeam(slug, role) {
  if (/architect|lead/.test(role.toLowerCase())) return 'leadership';
  if (/security/.test(slug)) return 'crossfunc';
  if (/reviewer/.test(slug)) return 'crossfunc';
  if (/backend|infra/.test(slug)) return 'backend';
  if (/android|mobile|ios|frontend/.test(slug)) return 'mobile';
  return 'other';
}

function readAgentRoster() {
  if (_rosterCache && Date.now() - _rosterCacheTime < ROSTER_CACHE_TTL) return _rosterCache;

  const roster = [];
  if (!fs.existsSync(AGENTS_DIR)) {
    console.warn(`[tracker] agents dir not found: ${AGENTS_DIR}`);
    return roster;
  }

  const files = fs.readdirSync(AGENTS_DIR).filter(f => f.endsWith('.md'));
  for (const file of files) {
    try {
      const slug = file.replace(/\.md$/, '');
      const content = fs.readFileSync(path.join(AGENTS_DIR, file), 'utf8');

      const name = content.match(/\*\*Name\*\*:\s*(.+)/)?.[1]?.trim() || slug;
      const role = content.match(/\*\*Role\*\*:\s*(.+)/)?.[1]?.trim() || 'unknown';
      const model = content.match(/\*\*Model\*\*:\s*(.+)/)?.[1]?.trim() || 'unknown';
      const team = deriveTeam(slug, role);

      roster.push({ slug, name, role, model, team });
    } catch (_) {}
  }

  _rosterCache = roster;
  _rosterCacheTime = Date.now();
  return roster;
}

// ── Agent live status reader ─────────────────────────────────────────────────

function readLiveStatuses() {
  const result = {};
  if (!fs.existsSync(TRACKER_DIR)) return result;

  // Only read status from tasks that are currently active
  const activeTaskIds = new Set(
    db.prepare("SELECT id FROM tasks WHERE status IN ('in_progress', 'review', 'blocked')").all().map(t => t.id)
  );

  try {
    const taskIds = fs.readdirSync(TRACKER_DIR).filter(f => {
      try {
        if (!fs.statSync(path.join(TRACKER_DIR, f)).isDirectory()) return false;
        return activeTaskIds.size === 0 || activeTaskIds.has(f);
      } catch (_) { return false; }
    });

    for (const taskId of taskIds) {
      const statusDir = path.join(TRACKER_DIR, taskId, 'status');
      if (!fs.existsSync(statusDir)) continue;

      const files = fs.readdirSync(statusDir).filter(f => f.endsWith('.yml'));
      for (const file of files) {
        try {
          const filePath = path.join(statusDir, file);
          const stat = fs.statSync(filePath);
          const raw = fs.readFileSync(filePath, 'utf8');
          const parsed = yaml.load(raw);
          if (!parsed) continue;

          const slug = file.replace(/\.yml$/, '');
          const existing = result[slug];
          // Keep the most recently updated status per agent
          if (!existing || stat.mtimeMs > (existing._mtime || 0)) {
            result[slug] = { ...parsed, taskId, _mtime: stat.mtimeMs };
          }
        } catch (_) {}
      }
    }
  } catch (_) {}

  return result;
}

// ── Merged agents (roster + live) ────────────────────────────────────────────

const TEAM_ORDER = { leadership: 0, backend: 1, mobile: 2, crossfunc: 3, other: 4 };

function getAgentsFull() {
  const roster = readAgentRoster();
  const live = readLiveStatuses();

  const merged = roster.map(agent => {
    const liveData = live[agent.slug];
    if (liveData) {
      const { _mtime, ...rest } = liveData;
      return {
        ...agent,
        status: rest.status || 'unknown',
        taskId: rest.taskId || null,
        progress: rest.progress || null,
        blockers: rest.blockers || null,
        updatedAt: _mtime ? new Date(_mtime).toISOString() : null,
      };
    }
    return {
      ...agent,
      status: 'idle',
      taskId: null,
      progress: null,
      blockers: null,
      updatedAt: null,
    };
  });

  merged.sort((a, b) => (TEAM_ORDER[a.team] ?? 9) - (TEAM_ORDER[b.team] ?? 9));
  return merged;
}

let _lastAgentHash = '';
setInterval(() => {
  const agents = getAgentsFull();
  const hash = JSON.stringify(agents);
  if (hash !== _lastAgentHash) {
    _lastAgentHash = hash;
    broadcast('agents', agents);
  }
}, 2500);

// ── Telegram ──────────────────────────────────────────────────────────────────

function sendTelegram(text) {
  return new Promise((resolve, reject) => {
    if (!TELEGRAM_TOKEN || !TELEGRAM_CHAT_ID) {
      console.warn('[telegram] not configured – skipping send');
      return resolve({ ok: false, reason: 'not_configured' });
    }
    const body = JSON.stringify({ chat_id: TELEGRAM_CHAT_ID, text, parse_mode: 'HTML' });
    const req = https.request({
      hostname: 'api.telegram.org',
      path: `/bot${TELEGRAM_TOKEN}/sendMessage`,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    }, res => {
      let raw = '';
      res.on('data', d => raw += d);
      res.on('end', () => resolve(JSON.parse(raw)));
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// ── Agent Runner webhook ──────────────────────────────────────────────────────

function triggerAgent(cmd) {
  if (!AGENT_RUNNER_URL) return;
  const payload = typeof cmd.payload === 'string' ? JSON.parse(cmd.payload || '{}') : (cmd.payload || {});
  const body = JSON.stringify({ type: cmd.type, payload, id: cmd.id });
  const url = new URL(`${AGENT_RUNNER_URL}/trigger`);
  const options = {
    hostname: url.hostname,
    port: url.port || 80,
    path: '/trigger',
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
  };
  const req = require('http').request(options, (res) => {
    let data = '';
    res.on('data', chunk => data += chunk);
    res.on('end', () => console.log(`[tracker] agent-runner: ${data.trim()}`));
  });
  req.on('error', (e) => console.error(`[tracker] agent-runner error: ${e.message}`));
  req.write(body);
  req.end();
}

// ── Middleware ────────────────────────────────────────────────────────────────

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── SSE endpoint ──────────────────────────────────────────────────────────────

app.get('/api/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  sseClients.add(res);

  // send current state immediately on connect
  res.write(`event: agents\ndata: ${JSON.stringify(getAgentsFull())}\n\n`);
  res.write(`event: tasks\ndata: ${JSON.stringify(db.prepare('SELECT * FROM tasks ORDER BY priority ASC, created_at ASC').all())}\n\n`);

  req.on('close', () => sseClients.delete(res));
});

// ── Tasks API ─────────────────────────────────────────────────────────────────

app.get('/api/tasks', (req, res) => {
  const { status } = req.query;
  const tasks = status
    ? db.prepare('SELECT * FROM tasks WHERE status = ? ORDER BY priority ASC, created_at ASC').all(status)
    : db.prepare('SELECT * FROM tasks ORDER BY priority ASC, created_at ASC').all();
  res.json(tasks);
});

app.get('/api/tasks/next', (req, res) => {
  const task = db.prepare(`
    SELECT * FROM tasks WHERE status = 'pending' ORDER BY priority ASC, created_at ASC LIMIT 1
  `).get();
  if (!task) return res.status(404).json({ error: 'no_pending_tasks' });
  res.json(task);
});

app.get('/api/tasks/:id', (req, res) => {
  const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);
  if (!task) return res.status(404).json({ error: 'not_found' });
  res.json(task);
});

app.post('/api/tasks', (req, res) => {
  const { title, description = '', type = 'feature', priority, notes = '' } = req.body;
  if (!title) return res.status(400).json({ error: 'title required' });

  const id = uniqueId(title);
  // default priority = max + 1 (end of queue)
  const maxPriority = db.prepare('SELECT MAX(priority) as m FROM tasks').get()?.m ?? -1;
  const finalPriority = priority !== undefined ? priority : maxPriority + 1;

  db.prepare(`
    INSERT INTO tasks (id, title, description, type, priority, notes)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(id, title, description, type, finalPriority, notes);

  const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(id);
  const allTasks = db.prepare('SELECT * FROM tasks ORDER BY priority ASC, created_at ASC').all();
  const position = allTasks.filter(t => t.status === 'pending').findIndex(t => t.id === id) + 1;
  broadcast('tasks', allTasks);
  res.status(201).json({ ...task, position });
});

app.put('/api/tasks/:id', (req, res) => {
  const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);
  if (!task) return res.status(404).json({ error: 'not_found' });

  const allowed = ['title', 'description', 'type', 'status', 'branch', 'pr_url', 'notes'];
  const fields = [];
  const values = [];
  for (const key of allowed) {
    if (req.body[key] !== undefined) {
      fields.push(`${key} = ?`);
      values.push(req.body[key]);
    }
  }
  if (fields.length === 0) return res.status(400).json({ error: 'nothing to update' });

  fields.push('updated_at = ?');
  values.push(now());
  values.push(req.params.id);

  db.prepare(`UPDATE tasks SET ${fields.join(', ')} WHERE id = ?`).run(...values);

  const updated = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);
  broadcast('tasks', db.prepare('SELECT * FROM tasks ORDER BY priority ASC, created_at ASC').all());
  res.json(updated);
});

// Reorder: set explicit priority values for a sorted list of IDs
app.put('/api/tasks/reorder', (req, res) => {
  const { ids } = req.body; // ordered array of task IDs
  if (!Array.isArray(ids)) return res.status(400).json({ error: 'ids must be array' });

  const update = db.prepare('UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?');
  db.exec('BEGIN');
  try {
    ids.forEach((id, index) => update.run(index, now(), id));
    db.exec('COMMIT');
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }

  broadcast('tasks', db.prepare('SELECT * FROM tasks ORDER BY priority ASC, created_at ASC').all());
  res.json({ ok: true });
});

app.delete('/api/tasks/:id', (req, res) => {
  const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);
  if (!task) return res.status(404).json({ error: 'not_found' });
  db.prepare('DELETE FROM tasks WHERE id = ?').run(req.params.id);
  broadcast('tasks', db.prepare('SELECT * FROM tasks ORDER BY priority ASC, created_at ASC').all());
  res.json({ ok: true });
});

// ── Commands API ──────────────────────────────────────────────────────────────

// External systems (telegram bot, curl) post commands here
app.post('/api/commands', (req, res) => {
  const { type, payload = {}, source = 'api' } = req.body;
  if (!type) return res.status(400).json({ error: 'type required' });

  const result = db.prepare(
    'INSERT INTO commands (type, payload, source) VALUES (?, ?, ?)'
  ).run(type, JSON.stringify(payload), source);

  const cmd = db.prepare('SELECT * FROM commands WHERE id = ?').get(result.lastInsertRowid);
  broadcast('command', cmd);
  if (cmd.type !== 'btw') triggerAgent(cmd);
  res.status(201).json(cmd);
});

// Claude polls this to get the next instruction
app.get('/api/commands/next', (req, res) => {
  const cmd = db.prepare(`
    SELECT * FROM commands WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1
  `).get();
  if (!cmd) return res.status(404).json({ error: 'no_pending_commands' });
  res.json(cmd);
});

app.get('/api/commands', (req, res) => {
  const limit = parseInt(req.query.limit) || 50;
  const cmds = db.prepare('SELECT * FROM commands ORDER BY created_at DESC LIMIT ?').all(limit);
  res.json(cmds);
});

app.put('/api/commands/:id/ack', (req, res) => {
  const { status = 'processing' } = req.body;
  db.prepare(
    'UPDATE commands SET status = ?, processed_at = ? WHERE id = ?'
  ).run(status, now(), req.params.id);
  const cmd = db.prepare('SELECT * FROM commands WHERE id = ?').get(req.params.id);
  res.json(cmd);
});

// ── Notifications API ─────────────────────────────────────────────────────────

app.post('/api/notify', async (req, res) => {
  const { message } = req.body;
  if (!message) return res.status(400).json({ error: 'message required' });

  db.prepare("INSERT INTO events (kind, data) VALUES ('notify', ?)").run(
    JSON.stringify({ message })
  );
  broadcast('notify', { message, time: now() });

  try {
    const result = await sendTelegram(message);
    res.json({ ok: true, telegram: result });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ── Agents status API ─────────────────────────────────────────────────────────

app.get('/api/agents', (req, res) => {
  res.json(getAgentsFull());
});

// ── Agent Runner status proxy ─────────────────────────────────────────────────

app.get('/api/runner/status', (req, res) => {
  if (!AGENT_RUNNER_URL) return res.json({ busy: false, configured: false });
  const url = new URL(`${AGENT_RUNNER_URL}/status`);
  const options = { hostname: url.hostname, port: url.port || 80, path: '/status', method: 'GET' };
  require('http').request(options, (r) => {
    let data = '';
    r.on('data', chunk => data += chunk);
    r.on('end', () => {
      try { res.json({ ...JSON.parse(data), configured: true }); }
      catch (_) { res.json({ busy: false, configured: true, error: 'parse error' }); }
    });
  }).on('error', () => res.json({ busy: false, configured: true, error: 'unreachable' })).end();
});

// ── Research Q&A API ─────────────────────────────────────────────────────────

// Research agent posts a question — sent to Telegram, stored for polling
app.post('/api/research/ask', async (req, res) => {
  const { session_id, question } = req.body;
  if (!session_id || !question) return res.status(400).json({ error: 'session_id and question required' });

  const result = db.prepare(
    'INSERT INTO research_qa (session_id, question) VALUES (?, ?)'
  ).run(session_id, question);

  const qa = db.prepare('SELECT * FROM research_qa WHERE id = ?').get(result.lastInsertRowid);

  try {
    await sendTelegram(`🔬 <b>Researcher's question:</b>\n\n${question}\n\n<i>Reply with text in this chat.</i>`);
  } catch (err) {
    console.error('[tracker] research ask telegram error:', err.message);
  }

  res.status(201).json(qa);
});

// Research agent polls for answer
app.get('/api/research/answer/:id', (req, res) => {
  const qa = db.prepare('SELECT * FROM research_qa WHERE id = ?').get(req.params.id);
  if (!qa) return res.status(404).json({ error: 'not_found' });
  if (!qa.answer) return res.status(404).json({ error: 'not_answered_yet' });
  res.json(qa);
});

// Bot posts user's answer
app.post('/api/research/answer/:id', (req, res) => {
  const { answer } = req.body;
  if (!answer) return res.status(400).json({ error: 'answer required' });

  db.prepare(
    'UPDATE research_qa SET answer = ?, answered_at = ? WHERE id = ?'
  ).run(answer, now(), req.params.id);

  const qa = db.prepare('SELECT * FROM research_qa WHERE id = ?').get(req.params.id);
  res.json(qa);
});

// Bot checks if there's an unanswered research question
app.get('/api/research/pending', (req, res) => {
  const qa = db.prepare(
    'SELECT * FROM research_qa WHERE answer IS NULL ORDER BY created_at DESC LIMIT 1'
  ).get();
  if (!qa) return res.status(404).json({ error: 'no_pending' });
  res.json(qa);
});

// ── Health ────────────────────────────────────────────────────────────────────

app.get('/api/health', (req, res) => {
  res.json({ ok: true, time: now(), telegram: !!(TELEGRAM_TOKEN && TELEGRAM_CHAT_ID) });
});

// ── Start ─────────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`\n  YourProject Tracker running at http://localhost:${PORT}\n`);
  console.log(`  Telegram: ${TELEGRAM_TOKEN ? '✓ configured' : '✗ not configured (.env.tracker)'}`);
  console.log(`  Tracker dir: ${TRACKER_DIR}\n`);
});
