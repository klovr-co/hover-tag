// Browser-only DOM smoke checks, not Slack or pointer/keyboard acceptance tests.
// Run after opening .context/setup-workflow.html in an isolated agent-browser session:
// agent-browser --session tag-acceptance eval --stdin < docs/testing/setup-prototype-checks.js
(async () => {
  const results = [];
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const click = id => {
    const node = document.getElementById(id);
    assert(node, `Missing control: ${id}`);
    node.click();
  };
  const text = () => document.getElementById('screen').textContent;
  const reset = () => {
    clearTimeout(state.timer);
    Object.assign(state, {scene:'connect', workspace:'Example workspace', app:'OpenMax',
      selectedChannels:['team'], days:'30', agent:'Codex', done:0, timer:null});
    delete state.pendingFrom;
    delete state.appId;
    delete state.resume;
    render();
  };
  const appList = () => { click('authorize'); click('approved'); click('workspace-main'); };
  const link = () => { click('link-app'); document.getElementById('app-id').value = 'A0123456789'; document.getElementById('link-form').requestSubmit(); };
  const existing = () => { appList(); link(); click('existing-confirm'); };
  async function test(id, run) {
    reset();
    try { await run(); results.push({id, result:'PASS (prototype only)'}); }
    catch (error) { results.push({id, result:'FAIL', observed:error.message}); }
    finally { clearTimeout(state.timer); }
  }
  await test('F01', async () => {
    assert(document.getElementById('authorize'), 'Missing shared Connect Slack entry');
    assert(!document.getElementById('manual'), 'Duplicate existing-app entry on start screen');
    appList(); click('new-app'); click('create-approved');
    document.querySelector('[data-channel="product"]').click();
    document.getElementById('channel-form').requestSubmit();
    assert(state.scene === 'progress', 'Expected automatic progress');
    await new Promise(resolve => setTimeout(resolve, 5300));
    assert(state.scene === 'ready', 'Expected connection screen');
    assert(text().includes('#product') && text().includes('not verified yet'), 'Wrong destination or premature verification');
    click('reply'); assert(state.scene === 'verified', 'Expected observed-reply screen');
  });
  await test('F02', () => {
    existing(); assert(text().includes('A0123456789'), 'Selected app lost');
    document.querySelector('[data-channel="support"]').click();
    document.getElementById('channel-form').requestSubmit();
    assert(text().includes('A0123456789') && text().includes('#support'), 'App/channel lost');
  });
  await test('F03', () => {
    appList(); link(); click('existing-fix');
    assert(text().includes('Keep existing scopes'), 'No preservation guidance');
    click('check-existing'); click('existing-confirm');
    assert(state.scene === 'channel' && state.app === 'A0123456789', 'Repair lost app');
  });
  await test('F04', () => {
    existing(); document.querySelector('[data-channel="support"]').click();
    document.querySelector('[data-scene="blocked"]').click();
    click('exit'); click('resume');
    assert(state.scene === 'blocked' && state.app === 'A0123456789' && state.selectedChannels.includes('support') && state.selectedChannels.includes('team'), 'Resume lost state');
    click('retry'); assert(state.scene === 'progress', 'Retry did not continue');
  });
  await test('F05', () => {
    existing(); click('defaults');
    document.getElementById('days').value = '7';
    document.getElementById('agent').value = 'Claude';
    document.getElementById('defaults-form').requestSubmit();
    assert(state.days === '7' && state.agent === 'Claude' && text().includes('Claude'), 'Defaults lost');
    click('defaults'); assert(document.getElementById('agent').value === 'Claude', 'Reopened settings lost selection');
  });
  results.push({id:'F06',result:'BLOCKED',observed:'Static app list; no operator-role fixture or permission-aware discovery.'});
  await test('F07a', () => {
    appList(); click('new-app'); click('pending'); click('exit'); click('resume');
    assert(state.scene === 'pending', 'Approval wait lost');
    click('approved'); assert(state.scene === 'channel', 'Approval did not resume app setup');
  });
  await test('F07b', () => {
    appList(); click('new-app'); click('pending'); click('approved');
    document.querySelector('[data-scene="connect"]').click();
    click('authorize'); click('approved');
    assert(state.scene === 'workspace', `Expected workspace after fresh authorization; got ${state.scene}`);
  });
  results.push({id:'F08',result:'BLOCKED',observed:'No denied-app, inaccessible-channel, or empty-list state.'});
  await test('F07c authorization approval', () => {
    click('authorize'); click('pending'); click('exit'); click('resume'); click('approved');
    assert(state.scene === 'workspace', 'Authorization approval must lead to workspace');
    assert(!state.pendingFrom, 'Approval state should be consumed');
  });
  await test('M01 multi-channel and empty selection', () => {
    existing();
    const toggle = name => document.querySelector('[data-channel="'+name+'"]').click();
    toggle('product');
    assert(state.selectedChannels.length === 2 && text().includes('Use 2 channels'), 'Multiple selection missing');
    click('defaults');document.getElementById('defaults-form').requestSubmit();
    assert(state.selectedChannels.length === 2, 'Defaults lost channel selection');
    toggle('team');toggle('product');
    assert(document.querySelector('[type="submit"]').disabled, 'Empty selection not disabled');
    document.getElementById('channel-form').requestSubmit();
    assert(state.scene === 'channel', 'Empty selection advanced');
    toggle('support');document.getElementById('channel-form').requestSubmit();
    assert(state.scene === 'progress' && text().includes('#support') && !text().includes('#product'), 'Deselected channel persisted');
  });
  await test('X01 manual fallback', () => {
    appList(); link(); click('manual'); click('check-existing'); click('existing-confirm');
    assert(state.scene === 'channel', 'Manual branch cannot continue');
  });
  await test('X02 no color', () => {
    const toggle = document.getElementById('color');
    if (document.querySelector('.terminal').classList.contains('plain')) toggle.click();
    toggle.click(); assert(document.querySelector('.terminal').classList.contains('plain'), 'No-color toggle failed');
    assert(text().includes('Connect Slack'), 'Action missing without color'); toggle.click();
  });
  reset();
  return results;
})();
