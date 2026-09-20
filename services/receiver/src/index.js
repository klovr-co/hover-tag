import { DurableObject } from 'cloudflare:workers';

export const OFFLINE_MESSAGE = 'Tag is offline. Start Tag on its host computer, then retry.';
const encoder = new TextEncoder();
const list = value => new Set((value || '').split(',').map(s => s.trim()).filter(Boolean));
const response = (body, status = 200) => Response.json(body, {status, headers: {'Cache-Control': 'no-store'}});
const deny = (status, error) => response({error}, status);
const hash = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value)))].map(n => n.toString(16).padStart(2, '0')).join('');
async function matches(value, expectedHash) {
  if (!value || !expectedHash) return false;
  const actual = await hash(value);
  let difference = actual.length ^ expectedHash.length;
  for (let i = 0; i < actual.length; i++) difference |= actual.charCodeAt(i) ^ expectedHash.charCodeAt(i);
  return difference === 0;
}
export async function seal(value, secret, app) {
  const key = await crypto.subtle.importKey('raw', Uint8Array.from(atob(secret), c => c.charCodeAt(0)), 'AES-GCM', false, ['encrypt']);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: encoder.encode(app)}, key, encoder.encode(JSON.stringify(value)));
  return {iv: [...iv], data: [...new Uint8Array(encrypted)]};
}
export async function unseal(value, secret, app) {
  const key = await crypto.subtle.importKey('raw', Uint8Array.from(atob(secret), c => c.charCodeAt(0)), 'AES-GCM', false, ['decrypt']);
  return JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name: 'AES-GCM', iv: new Uint8Array(value.iv), additionalData: encoder.encode(app)}, key, new Uint8Array(value.data))));
}
async function slack(method, token, args = {}) {
  const result = await fetch(`https://slack.com/api/${method}`, {
    method: 'POST', headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams(Object.entries(args).map(([key, value]) => [key, String(value)])).toString(), signal: AbortSignal.timeout(5000),
  });
  const body = await result.json();
  if (!result.ok || !body.ok) throw new Error(body.error === 'missing_scope' ? 'Slack permissions need updating; run tag setup' : 'Slack credential check failed');
  return body;
}
async function readJson(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error('Missing body');
  const chunks = []; let length = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    length += value.length;
    if (length > 16384) { await reader.cancel(); throw new Error('Body too large'); }
    chunks.push(value);
  }
  const all = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder().decode(all));
}
export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === '/health') return response({service: 'tag-receiver', version: 2});
    const route = /^\/v1\/apps\/(A[A-Z0-9]{2,30})\/(registration|connect)$/.exec(path);
    if (!route) return deny(404, 'Not found');
    if (!env.CREDENTIAL_KEY) return deny(503, 'Receiver is not configured');
    if (route[2] === 'registration' && request.method === 'PUT' && env.REGISTRATION_LIMITER) {
      const limit = await env.REGISTRATION_LIMITER.limit({key: request.headers.get('CF-Connecting-IP') || 'unknown'});
      if (!limit.success) return deny(429, 'Please retry registration later');
    }
    return env.RECEIVER.get(env.RECEIVER.idFromName(route[1])).fetch(request);
  },
};

export class TagReceiver extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.pending = new Map(); this.slackSocket = null; this.connecting = null;
    this.generation = 0; this.slackReady = false;
    ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, route TEXT NOT NULL, created INTEGER NOT NULL, channel TEXT, thread TEXT, attempts INTEGER NOT NULL DEFAULT 0)');
    ctx.blockConcurrencyWhile(async () => { this.registration = await ctx.storage.get('registration'); });
  }
  async credentials(registration = this.registration) {
    return unseal(registration.credentials, this.env.CREDENTIAL_KEY, registration.app);
  }
  async fetch(request) {
    const path = new URL(request.url).pathname;
    const app = path.split('/')[3];
    const token = (request.headers.get('Authorization') || '').replace(/^Bearer /, '');
    if (!/^[A-Za-z0-9_-]{32,128}$/.test(token)) return deny(401, 'Unauthorized');
    if (path.endsWith('/registration') && request.method === 'PUT') {
      return this.ctx.blockConcurrencyWhile(async () => {
        if (this.registration && !await matches(token, this.registration.owner)) return deny(403, 'This app is registered to another Tag installation');
        let data;
        try { data = await readJson(request); } catch { return deny(400, 'Invalid registration'); }
        if (!/^T[A-Z0-9]+$/.test(data.team || '') || !/^xapp-/.test(data.app_token || '') || !/^xoxb-/.test(data.bot_token || '') ||
            !['selected', 'invited'].includes(data.policy) || !Array.isArray(data.users) || !data.users.length || data.users.length > 100 ||
            !data.users.every(id => /^[UW][A-Z0-9]+$/.test(id)) || !Array.isArray(data.channels) || data.channels.length > 1000 ||
            !data.channels.every(id => /^[CG][A-Z0-9]+$/.test(id)) || (data.policy === 'selected' && !data.channels.length)) return deny(400, 'Invalid registration');
        if (this.registration && this.registration.team !== data.team) return deny(409, 'Remove the existing workspace registration first');
        try {
          const identity = await slack('auth.test', data.bot_token);
          if (identity.team_id !== data.team || !identity.bot_id) return deny(403, 'Wrong Slack workspace');
          const bot = await slack('bots.info', data.bot_token, {bot: identity.bot_id});
          if (bot.bot?.app_id !== app) return deny(403, 'Wrong Slack app');
          await slack('apps.connections.open', data.app_token);
        } catch (error) { return deny(400, error.message); }
        const credentialHash = await hash(data.app_token + '\n' + data.bot_token);
        const changed = this.registration?.credentialHash !== credentialHash;
        const registration = {app, team: data.team, owner: await hash(token), users: data.users, channels: data.channels, policy: data.policy,
          credentialHash, active: this.registration?.active || false,
          credentials: await seal({app_token: data.app_token, bot_token: data.bot_token}, this.env.CREDENTIAL_KEY, app)};
        await this.ctx.storage.put('registration', registration);
        this.registration = registration;
        if (changed) this.disconnectSlack();
        if (registration.active) await this.schedule(100);
        return response({registered: true, app, team: data.team});
      });
    }
    if (!this.registration && path.endsWith('/registration') && request.method === 'DELETE') return response({removed: true});
    if (!this.registration || this.registration.app !== app || !await matches(token, this.registration.owner)) return deny(401, 'Unauthorized');
    if (path.endsWith('/registration')) {
      if (request.method === 'GET') return response({app, team: this.registration.team, connected: this.slackReady, registered: true});
      if (request.method === 'DELETE') {
        return this.ctx.blockConcurrencyWhile(async () => {
          this.disconnectSlack(); this.registration = null;
          for (const ws of this.ctx.getWebSockets()) ws.close(1000, 'Registration removed');
          for (const pending of this.pending.values()) pending.resolve(null);
          await this.ctx.storage.delete('registration');
          await this.ctx.storage.deleteAlarm();
          this.ctx.storage.sql.exec('DELETE FROM deliveries');
          return response({removed: true});
        });
      }
      return deny(405, 'Method not allowed');
    }
    if (request.method !== 'GET' || request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return deny(426, 'WebSocket required');
    for (const ws of this.ctx.getWebSockets()) {
      if (await this.request(ws, {type: 'probe'})) return deny(409, 'Another Tag host is connected');
      ws.close(1000, 'Connection expired');
    }
    if (this.ctx.getWebSockets().some(ws => ws.readyState === WebSocket.OPEN)) return deny(409, 'Another Tag host is connected');
    const registration = this.registration;
    if (!registration.active) {
      registration.active = true;
      await this.ctx.storage.put('registration', registration);
    }
    await this.schedule(1000);
    try { await this.ensureSlack(); } catch { return deny(503, 'Slack is reconnecting; check credentials in tag setup'); }
    if (this.registration !== registration || !this.slackReady) return deny(503, 'Registration changed; reconnect');
    if (this.ctx.getWebSockets().some(ws => ws.readyState === WebSocket.OPEN)) return deny(409, 'Another Tag host is connected');
    const [client, server] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(server);
    server.send(JSON.stringify({type: 'hello', team: registration.team, app}));
    return new Response(null, {status: 101, webSocket: client});
  }
  disconnectSlack() {
    this.generation++; this.slackReady = false;
    this.slackSocket?.close(1000, 'Reconnecting'); this.slackSocket = null;
  }
  async schedule(delay = 100) {
    const deadline = Date.now() + delay;
    if ((await this.ctx.storage.getAlarm() ?? Infinity) > deadline) await this.ctx.storage.setAlarm(deadline);
  }
  async ensureSlack() {
    if (!this.registration?.active || this.slackReady) return;
    if (this.connecting) return this.connecting;
    this.connecting = this.openSlack();
    try { await this.connecting; } finally { this.connecting = null; }
  }
  async openSlack() {
    const generation = this.generation;
    const registration = this.registration;
    const credentials = await this.credentials(registration);
    const result = await slack('apps.connections.open', credentials.app_token);
    const url = new URL(result.url);
    if (url.protocol !== 'wss:' || !(url.hostname === 'slack.com' || url.hostname.endsWith('.slack.com'))) throw new Error('Invalid Slack socket URL');
    url.protocol = 'https:';
    const connection = await fetch(url, {headers: {Upgrade: 'websocket'}, signal: AbortSignal.timeout(5000)});
    const ws = connection.webSocket;
    if (!ws) throw new Error('Slack connection unavailable');
    ws.accept();
    if (generation !== this.generation || this.registration !== registration) { ws.close(); return; }
    this.slackSocket = ws;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { ws.close(); reject(new Error('Slack handshake timeout')); }, 5000);
      ws.addEventListener('message', event => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (generation !== this.generation || this.slackSocket !== ws) return;
        if (message.type === 'hello') {
          clearTimeout(timer);
          if (message.connection_info?.app_id !== registration.app) { ws.close(); reject(new Error('Wrong Slack app token')); return; }
          this.slackReady = true; resolve(); return;
        }
        if (message.type === 'disconnect') { this.disconnectSlack(); this.ctx.waitUntil(this.schedule(100)); return; }
        if (this.slackReady && message.envelope_id) this.ctx.waitUntil(this.envelope(ws, message).catch(() => console.error('Slack envelope processing failed')));
      });
      const closed = () => {
        clearTimeout(timer);
        if (this.slackSocket === ws) { this.slackSocket = null; this.slackReady = false; }
        if (this.registration?.active) this.ctx.waitUntil(this.schedule(1000));
        reject(new Error('Slack connection closed'));
      };
      ws.addEventListener('close', closed); ws.addEventListener('error', closed);
    });
  }
  request(ws, message, timeout = 900) {
    const id = crypto.randomUUID();
    return new Promise(resolve => {
      const timer = setTimeout(() => { this.pending.delete(id); resolve(null); }, timeout);
      this.pending.set(id, {ws, resolve: value => { clearTimeout(timer); this.pending.delete(id); resolve(value); }});
      try { ws.send(JSON.stringify({...message, id})); } catch { this.pending.get(id)?.resolve(null); }
    });
  }
  async allowed(body) {
    const r = this.registration;
    if (!r || (body.team_id || body.team?.id) !== r.team || body.api_app_id !== r.app) return false;
    const event = body.event;
    if (!r.users.includes(event?.user || body.user?.id)) return false;
    const channel = event?.channel || body.channel?.id || body.container?.channel_id;
    if (channel) {
      if (r.policy === 'selected') return r.channels.includes(channel);
      // Membership is checked live, including while the local computer is off.
      const credentials = await this.credentials(r);
      try { return Boolean((await slack('conversations.info', credentials.bot_token, {channel})).channel?.is_member); }
      catch { return false; }
    }
    return event?.type === 'app_home_opened' || body.type === 'view_submission';
  }
  async envelope(socket, envelope) {
    const body = envelope.payload;
    const ack = payload => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({envelope_id: envelope.envelope_id, ...(payload ? {payload} : {})}));
    };
    if (!body || !await this.allowed(body)) { ack(); return; }
    const interactive = envelope.type === 'interactive';
    const event = body.event;
    if (!interactive && (!body.event_id || !['app_mention', 'app_home_opened', 'agent_session_stopped'].includes(event?.type))) { ack(); return; }
    if (event?.type === 'app_mention' && (!event.ts || event.bot_id || event.subtype)) { ack(); return; }
    const result = await this.deliver(body, interactive);
    if (interactive && result) {
      let payload;
      try { payload = JSON.parse(result.body || '{}'); } catch { payload = {text: result.body}; }
      ack(envelope.accepts_response_payload ? payload : undefined);
    } else ack();
  }
  async deliver(body, interactive) {
    const eventId = interactive ? null : body.event_id;
    const sql = this.ctx.storage.sql;
    const seen = () => eventId && sql.exec('SELECT id FROM deliveries WHERE id = ?', eventId).toArray().length;
    if (seen()) return;
    const socket = this.ctx.getWebSockets().find(ws => ws.readyState === WebSocket.OPEN);
    const reachable = socket && await this.request(socket, {type: 'probe'});
    if (!this.registration || seen()) return;
    if (reachable) {
      if (eventId) sql.exec('INSERT INTO deliveries (id, route, created) VALUES (?, ?, ?)', eventId, 'local', Date.now());
      const result = await this.request(socket, {type: 'request', body}, 1700);
      return interactive ? result : undefined;
    }
    socket?.close(1000, 'Connection expired');
    if (interactive) return {body: JSON.stringify({response_type: 'ephemeral', text: OFFLINE_MESSAGE})};
    const mention = body.event?.type === 'app_mention';
    sql.exec('INSERT INTO deliveries (id, route, created, channel, thread) VALUES (?, ?, ?, ?, ?)', eventId, mention ? 'offline' : 'ignored', Date.now(), mention ? body.event.channel : null, mention ? (body.event.thread_ts || body.event.ts) : null);
    await this.schedule();
  }
  webSocketMessage(ws, message) {
    let body;
    try { body = JSON.parse(message); } catch { ws.close(1003, 'Invalid JSON'); return; }
    if (body.type === 'ping') { ws.send(JSON.stringify({type: 'pong', connected: this.slackReady})); return; }
    const pending = this.pending.get(body.id);
    if (pending?.ws === ws && body.type === 'response') pending.resolve(body);
  }
  webSocketClose(ws) {
    for (const pending of this.pending.values()) if (pending.ws === ws) pending.resolve(null);
    ws.close(1000, 'Disconnected');
  }
  webSocketError(ws) { this.webSocketClose(ws); }
  async alarm() {
    if (!this.registration?.active) return;
    // Durable watchdog reconnects even after eviction or deployment, with the host off.
    await this.schedule(20000);
    try { await this.ensureSlack(); } catch { console.error('Slack receiver is reconnecting'); }
    const registration = this.registration;
    if (!registration?.active) return;
    const sql = this.ctx.storage.sql;
    const credentials = await this.credentials(registration);
    for (const row of sql.exec("SELECT * FROM deliveries WHERE route = 'offline' AND attempts < 6 LIMIT 10").toArray()) {
      if (registration !== this.registration) return;
      try {
        await slack('chat.postMessage', credentials.bot_token, {channel: row.channel, thread_ts: row.thread, text: OFFLINE_MESSAGE, unfurl_links: false, unfurl_media: false});
        sql.exec("UPDATE deliveries SET route = 'replied' WHERE id = ?", row.id);
      } catch {
        sql.exec('UPDATE deliveries SET attempts = attempts + 1 WHERE id = ?', row.id);
        console.error('Offline reminder delivery failed', row.id);
      }
    }
    sql.exec('DELETE FROM deliveries WHERE created < ?', Date.now() - 86400000);
  }
}
