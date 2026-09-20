import { env, SELF, runInDurableObject } from 'cloudflare:test';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { OFFLINE_MESSAGE, unseal } from '../src/index.js';
const token = 'test-relay-token-that-is-long-enough';
const otherToken = 'different-owner-token-that-is-long-enough';
const sockets = [];
const stub = (app = 'ATEST') => env.RECEIVER.get(env.RECEIVER.idFromName(app));
const config = {team: 'TTEST', app_token: 'xapp-test', bot_token: 'xoxb-test', users: ['UTEST'], channels: ['CTEST'], policy: 'selected', direct_messages: true};
const path = (app, action = 'registration') => `https://test/v1/apps/${app}/${action}`;
const mention = (extra = {}) => ({type: 'event_callback', event_id: crypto.randomUUID(), team_id: 'TTEST', api_app_id: 'ATEST', event: {type: 'app_mention', user: 'UTEST', channel: 'CTEST', ts: '123.456', thread_ts: '100.001', text: '<@BOT> help'}, ...extra});
const directMessage = (extra = {}) => ({type: 'event_callback', event_id: crypto.randomUUID(), team_id: 'TTEST', api_app_id: 'ATEST', event: {type: 'message', channel_type: 'im', user: 'UTEST', channel: 'DTEST', ts: '123.456', text: 'help'}, ...extra});
async function register(app = 'ATEST', options = {}) {
  return runInDurableObject(stub(app), async instance => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, requestOptions) => {
      expect(requestOptions.headers['Content-Type']).toBe('application/x-www-form-urlencoded');
      if (String(url).endsWith('bots.info')) expect(new URLSearchParams(requestOptions.body).get('bot')).toBe('BTEST');
      if (String(url).endsWith('auth.test')) return Response.json({ok: true, team_id: options.team || 'TTEST', bot_id: 'BTEST'});
      if (String(url).endsWith('bots.info')) return Response.json({ok: true, bot: {app_id: options.botApp || app}});
      return Response.json({ok: true, url: 'wss://wss-primary.slack.com/link?ticket=test'});
    });
    try {
      return await instance.fetch(new Request(path(app), {method: 'PUT', headers: {Authorization: `Bearer ${options.token || token}`}, body: JSON.stringify({...config, ...options.data})}));
    } finally { fetcher.mockRestore(); }
  });
}
async function activate(app = 'ATEST') {
  await runInDurableObject(stub(app), async (instance, ctx) => {
    instance.registration.active = true;
    await ctx.storage.put('registration', instance.registration);
    instance.slackReady = true;
  });
}
async function connect(app = 'ATEST', respond = true) {
  await activate(app);
  const response = await SELF.fetch(path(app, 'connect'), {headers: {Upgrade: 'websocket', Authorization: `Bearer ${token}`}});
  expect(response.status).toBe(101);
  const ws = response.webSocket; ws.accept(); sockets.push(ws);
  const requests = [];
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (respond && message.type === 'probe') ws.send(JSON.stringify({type: 'response', id: message.id}));
    if (message.type === 'request') {
      requests.push(message.body);
      ws.send(JSON.stringify({type: 'response', id: message.id, status: 200, body: JSON.stringify({response_action: 'errors', errors: {model: 'Choose a model'}})}));
    }
  });
  return {ws, requests};
}
async function envelope(body, app = 'ATEST', interactive = false) {
  return runInDurableObject(stub(app), async instance => {
    const acknowledgements = [];
    const socket = {readyState: WebSocket.OPEN, send: text => acknowledgements.push(JSON.parse(text))};
    await instance.envelope(socket, {envelope_id: 'envelope', payload: body, type: interactive ? 'interactive' : 'events_api', accepts_response_payload: true});
    return acknowledgements;
  });
}
beforeEach(async () => {
  for (const app of ['ATEST', 'AOTHER']) await runInDurableObject(stub(app), async (instance, ctx) => {
    instance.disconnectSlack(); instance.registration = null;
    for (const ws of ctx.getWebSockets()) ws.close();
    ctx.storage.sql.exec('DELETE FROM deliveries');
    await ctx.storage.delete('registration'); await ctx.storage.deleteAlarm();
  });
});
afterEach(() => { for (const ws of sockets.splice(0)) ws.close(); vi.restoreAllMocks(); });
it('verifies bot app and workspace before storing registration', async () => {
  expect((await register('ATEST', {team: 'TOTHER'})).status).toBe(403);
  expect((await register('ATEST', {botApp: 'AOTHER'})).status).toBe(403);
  expect(await runInDurableObject(stub(), (_, ctx) => ctx.storage.get('registration'))).toBeUndefined();
  expect((await register()).status).toBe(200);
});
it('encrypts per-app credentials and never returns them to registration callers', async () => {
  const result = await (await register()).json();
  expect(result).toEqual({registered: true, app: 'ATEST', team: 'TTEST'});
  const stored = await runInDurableObject(stub(), (_, ctx) => ctx.storage.get('registration'));
  expect(JSON.stringify(stored)).not.toContain('xoxb-test');
  expect(JSON.stringify(stored)).not.toContain(token);
  expect(await unseal(stored.credentials, env.CREDENTIAL_KEY, 'ATEST')).toEqual({app_token: 'xapp-test', bot_token: 'xoxb-test'});
  await expect(unseal(stored.credentials, env.CREDENTIAL_KEY, 'AOTHER')).rejects.toThrow();
});
it('allows interrupted registration retries but rejects a different owner', async () => {
  await register(); expect((await register()).status).toBe(200);
  expect((await register('ATEST', {token: otherToken})).status).toBe(403);
  for (const method of ['GET', 'DELETE']) expect((await SELF.fetch(path('ATEST'), {method, headers: {Authorization: `Bearer ${otherToken}`}})).status).toBe(401);
});
it('isolates different apps in the same workspace', async () => {
  await register(); await register('AOTHER', {token: otherToken});
  expect((await SELF.fetch(path('AOTHER'), {headers: {Authorization: `Bearer ${token}`}})).status).toBe(401);
  await envelope(mention({api_app_id: 'AOTHER'}));
  expect(await runInDurableObject(stub(), (_, ctx) => ctx.storage.sql.exec('SELECT * FROM deliveries').toArray())).toHaveLength(0);
});
it('ignores unauthorized users and channels and wrong workspaces', async () => {
  await register();
  const body = mention(); body.event.user = 'UOTHER'; await envelope(body);
  body.event.user = 'UTEST'; body.event.channel = 'COTHER'; await envelope(body);
  await envelope(mention({team_id: 'TOTHER'}));
  expect(await runInDurableObject(stub(), (_, ctx) => ctx.storage.sql.exec('SELECT * FROM deliveries').toArray())).toHaveLength(0);
});
it('forwards authorized direct messages independently of channel policy', async () => {
  await register(); const {requests} = await connect();
  await envelope(directMessage());
  expect(requests).toHaveLength(1);
  expect(requests[0].event.channel).toBe('DTEST');
});
it('ignores direct messages when disabled or sent by an unauthorized user', async () => {
  await register('ATEST', {data: {direct_messages: false}}); await activate();
  await envelope(directMessage());
  const body = directMessage(); body.event.user = 'UOTHER'; await envelope(body);
  expect(await runInDurableObject(stub(), (_, ctx) => ctx.storage.sql.exec('SELECT * FROM deliveries').toArray())).toHaveLength(0);
});
it('deduplicates offline mentions and posts once in the original thread', async () => {
  await register(); await activate(); const body = mention();
  await envelope(body); await envelope(body);
  await runInDurableObject(stub(), async (instance, ctx) => {
    expect(ctx.storage.sql.exec('SELECT * FROM deliveries').toArray()).toHaveLength(1);
    const post = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ok: true}));
    try {
      await instance.alarm(); await instance.alarm();
      expect(post).toHaveBeenCalledTimes(1);
      expect(Object.fromEntries(new URLSearchParams(post.mock.calls[0][1].body))).toMatchObject({channel: 'CTEST', thread_ts: '100.001', text: OFFLINE_MESSAGE});
    } finally { post.mockRestore(); }
  });
});
it('forwards online events once and preserves interactive responses', async () => {
  await register(); const {requests} = await connect(); const body = mention();
  await Promise.all([envelope(body), envelope(body)]); expect(requests).toHaveLength(1);
  const action = {type: 'view_submission', api_app_id: 'ATEST', team: {id: 'TTEST'}, user: {id: 'UTEST'}, view: {}};
  const ack = await envelope(action, 'ATEST', true);
  expect(ack[0].payload).toEqual({response_action: 'errors', errors: {model: 'Choose a model'}});
});
it('detects stale local sockets without queuing work on reconnect', async () => {
  await register(); const {requests} = await connect('ATEST', false);
  await envelope(mention()); expect(requests).toHaveLength(0);
  expect(await runInDurableObject(stub(), (_, ctx) => ctx.storage.sql.exec('SELECT route FROM deliveries').one().route)).toBe('offline');
});
it('rejects a competing local host', async () => {
  await register(); await connect();
  expect((await SELF.fetch(path('ATEST', 'connect'), {headers: {Upgrade: 'websocket', Authorization: `Bearer ${token}`}})).status).toBe(409);
});
it('checks current membership for invited-channel policy, even while offline', async () => {
  await register('ATEST', {data: {policy: 'invited', channels: []}});
  await runInDurableObject(stub(), async instance => {
    const api = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(Response.json({ok: true, channel: {is_member: true}})).mockResolvedValueOnce(Response.json({ok: true, channel: {is_member: false}}));
    try { expect(await instance.allowed(mention())).toBe(true); expect(await instance.allowed(mention())).toBe(false); }
    finally { api.mockRestore(); }
  });
});
it('removes credentials, metadata, alarm and access; removal retries are safe', async () => {
  await register(); await connect(); await envelope(mention());
  const remove = () => SELF.fetch(path('ATEST'), {method: 'DELETE', headers: {Authorization: `Bearer ${token}`}});
  expect((await remove()).status).toBe(200); expect((await remove()).status).toBe(200);
  await runInDurableObject(stub(), async (instance, ctx) => {
    expect(instance.registration).toBeNull();
    expect(await ctx.storage.get('registration')).toBeUndefined(); expect(await ctx.storage.getAlarm()).toBeNull();
    expect(ctx.storage.sql.exec('SELECT * FROM deliveries').toArray()).toHaveLength(0);
  });
  expect((await SELF.fetch(path('ATEST', 'connect'), {headers: {Upgrade: 'websocket', Authorization: `Bearer ${token}`}})).status).toBe(401);
});
it('retries an offline post after Slack rejects it', async () => {
  await register(); await activate(); await envelope(mention());
  await runInDurableObject(stub(), async (instance, ctx) => {
    const post = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ok: false, error: 'ratelimited'}));
    try { await instance.alarm(); expect(ctx.storage.sql.exec('SELECT route, attempts FROM deliveries').one()).toEqual({route: 'offline', attempts: 1}); }
    finally { post.mockRestore(); }
  });
});
it('verifies the Slack socket hello before marking the receiver connected', async () => {
  await register();
  await runInDurableObject(stub(), async instance => {
    instance.registration.active = true;
    const servers = [];
    const api = vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
      if (String(url).endsWith('apps.connections.open')) return Response.json({ok: true, url: 'wss://wss-primary.slack.com/link?ticket=test'});
      const [client, server] = Object.values(new WebSocketPair()); server.accept(); servers.push(server);
      setTimeout(() => server.send(JSON.stringify({type: 'hello', connection_info: {app_id: 'ATEST'}})), 5);
      return new Response(null, {status: 101, webSocket: client});
    });
    try { await instance.ensureSlack(); expect(instance.slackReady).toBe(true); }
    finally { instance.registration.active = false; instance.disconnectSlack(); for (const ws of servers) ws.close(); api.mockRestore(); }
  });
});
it('rejects a socket token for a different app', async () => {
  await register();
  await runInDurableObject(stub(), async instance => {
    instance.registration.active = true;
    let server;
    const api = vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
      if (String(url).endsWith('apps.connections.open')) return Response.json({ok: true, url: 'wss://wss-primary.slack.com/link?ticket=test'});
      const pair = new WebSocketPair(); server = pair[1]; server.accept();
      setTimeout(() => server.send(JSON.stringify({type: 'hello', connection_info: {app_id: 'AOTHER'}})), 5);
      return new Response(null, {status: 101, webSocket: pair[0]});
    });
    try { await expect(instance.ensureSlack()).rejects.toThrow('Wrong Slack app token'); expect(instance.slackReady).toBe(false); }
    finally { instance.registration.active = false; instance.disconnectSlack(); server?.close(); api.mockRestore(); }
  });
});
it('does not replay or send an offline reply after local acceptance and disconnect', async () => {
  await register(); await activate();
  const response = await SELF.fetch(path('ATEST', 'connect'), {headers: {Upgrade: 'websocket', Authorization: `Bearer ${token}`}});
  const ws = response.webSocket; ws.accept(); sockets.push(ws);
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.type === 'probe') ws.send(JSON.stringify({type: 'response', id: message.id}));
    if (message.type === 'request') ws.close();
  });
  const body = mention(); await envelope(body); await envelope(body);
  expect(await runInDurableObject(stub(), (_, ctx) => ctx.storage.sql.exec('SELECT route FROM deliveries').one().route)).toBe('local');
});
