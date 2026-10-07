// PROTOTYPE — throwaway, do not ship.
// Question: how should many saved connections look, and how does each Tag choose one?
// Two harnesses: Codex and Claude Code. Each connection belongs to one harness.
// Model: Settings holds any number of accounts (at most one ChatGPT sign-in and one Claude sign-in per Mac,
// because those use the CLI's global login). Each Tag uses one account + model.
// Three variants, switchable via ?variant=A|B|C. Mounted in Settings → AI connections AND in each Tag's Details tab.
// In-memory only; the "PROTOTYPE state" panel shows the per-Tag `tag NAME config set` commands it implies.
import { useEffect, useState, useSyncExternalStore } from "react";
import { Icon } from "./ui";
import { AgentMark } from "./AI";
import { PrototypeSwitcher, usePrototypeVariant } from "./PrototypeSwitcher";

type Backend = "codex" | "claude";
type Kind = "chatgpt" | "openai" | "azure" | "gateway" | "claude_signin" | "anthropic" | "claude_gateway";
type Account = { email?: string; favicon?: string; status?: "ok" | "expired"; id: string; name: string; kind: Kind; baseUrl: string; apiVersion: string; models: string; keyEnd: string; images: boolean; search: boolean };
type Use = { account: string; model: string };

const KIND: Record<Kind, { backend: Backend; title: string; detail: string; signIn?: boolean; url?: boolean }> = {
  chatgpt: { backend: "codex", title: "ChatGPT Subscription", detail: "Sign in with your ChatGPT plan.", signIn: true },
  openai: { backend: "codex", title: "OpenAI API key", detail: "Billed to an OpenAI API account." },
  azure: { backend: "codex", title: "Azure OpenAI", detail: "A Responses deployment in Azure.", url: true },
  gateway: { backend: "codex", title: "OpenAI-compatible endpoint", detail: "A gateway or proxy that speaks the OpenAI Responses API.", url: true },
  claude_signin: { backend: "claude", title: "Claude Subscription", detail: "Sign in with your Claude plan.", signIn: true },
  anthropic: { backend: "claude", title: "Anthropic API key", detail: "Billed to an Anthropic Console account." },
  claude_gateway: { backend: "claude", title: "Anthropic-compatible endpoint", detail: "A gateway or proxy that speaks the Anthropic API.", url: true },
};
const HARNESS: Record<Backend, string> = { codex: "Codex", claude: "Claude Code" };
const SIGNIN_MODELS: Partial<Record<Kind, string>> = { chatgpt: "gpt-5.5,gpt-5.5-mini", claude_signin: "claude-opus-5-5,claude-sonnet-5-5" };
const TAGS = [{ id: "t0klovr1-a0laun01", name: "launch" }, { id: "t0klovr1-a0rese02", name: "research" }, { id: "t0acme01-a0ops003", name: "ops" }];

// ---------- tiny shared in-memory store, so Settings and Tag Details see the same accounts ----------
let state = {
  accounts: [
    { id: "a0", status: "expired", name: "Claude Subscription", email: "team@klovr.co", kind: "claude_signin", baseUrl: "", apiVersion: "", models: SIGNIN_MODELS.claude_signin!, keyEnd: "", images: false, search: false },
    { id: "a1", status: "ok", name: "ChatGPT Subscription", email: "team@klovr.co", kind: "chatgpt", baseUrl: "", apiVersion: "", models: SIGNIN_MODELS.chatgpt!, keyEnd: "", images: true, search: true },
    { id: "a2", name: "Azure prod", kind: "azure", baseUrl: "https://klovr.openai.azure.com/openai", apiVersion: "", models: "gpt-5-prod", keyEnd: "9f2c", images: false, search: false },
    { id: "a4", name: "Router gateway", kind: "gateway", baseUrl: "https://api.router.example/v1", apiVersion: "", models: "grok-4.7", keyEnd: "77aa", images: false, search: false },
    { id: "a5", name: "Office proxy", kind: "claude_gateway", baseUrl: "http://10.0.0.8:4000", apiVersion: "", models: "claude-sonnet-5-5", keyEnd: "c0de", images: false, search: false },
    { id: "a3", name: "Anthropic API", kind: "anthropic", baseUrl: "", apiVersion: "", models: "claude-opus-5-5,claude-haiku-4-5", keyEnd: "A1b2", images: false, search: false },
  ] as Account[],
  uses: { [TAGS[0].id]: { account: "a1", model: "gpt-5.5" }, [TAGS[1].id]: { account: "a3", model: "claude-opus-5-5" }, [TAGS[2].id]: { account: "a2", model: "gpt-5-prod" } } as Record<string, Use>,
};
const listeners = new Set<() => void>();
const store = {
  get: () => state,
  sub: (f: () => void) => { listeners.add(f); return () => listeners.delete(f); },
  set: (next: Partial<typeof state>) => { state = { ...state, ...next }; listeners.forEach((f) => f()); },
};
const useStore = () => useSyncExternalStore(store.sub, store.get);
const models = (a: Account) => a.models.split(",").map((m) => m.trim()).filter(Boolean);
const usedBy = (id: string) => TAGS.filter((t) => state.uses[t.id]?.account === id);
const takenSignIn = (k: Kind, except?: string) => !!KIND[k].signIn && state.accounts.some((a) => a.kind === k && a.id !== except);
const saveAccount = (a: Account) => store.set({ accounts: state.accounts.some((x) => x.id === a.id) ? state.accounts.map((x) => x.id === a.id ? a : x) : [...state.accounts, a] });
const removeAccount = (id: string) => store.set({ accounts: state.accounts.filter((a) => a.id !== id) });
const assign = (tag: string, use: Use) => store.set({ uses: { ...state.uses, [tag]: use } });
const Status = ({ a }: { a: Account }) => !KIND[a.kind].signIn ? null : a.status === "expired"
  ? <span style={{ color: "var(--amber, #b36b00)", fontWeight: 600 }}><Icon name="warn" size={11} /> Sign-in expired · </span>
  : <span style={{ color: "var(--green)", fontWeight: 600 }}>● Connected · </span>;
const Action = ({ a, edit }: { a: Account; edit: () => void }) => a.status === "expired"
  ? <button className="p-btn soft sm" onClick={() => saveAccount({ ...a, status: "ok" })}>Reconnect</button>
  : <button className="p-btn soft sm" onClick={edit}>{KIND[a.kind].signIn ? "Change" : "Edit"}</button>;
const summary = (a: Account) => [HARNESS[KIND[a.kind].backend], KIND[a.kind].title, a.kind === "azure" || a.kind === "gateway" || a.kind === "claude_gateway" ? host(a.baseUrl) : "",
  a.keyEnd ? `key ••${a.keyEnd}` : ""].filter(Boolean).join(" · ");
const host = (u: string) => { try { return new URL(u).host; } catch { return u; } };
// Safest-design icons: plain glyphs per connection type (no third-party logos), a fetched favicon only for
// custom gateways (mocked here), and a small harness badge using Tag's existing Codex / Claude Code icons.
// Pixelarticons 2.4.2 (MIT; src/assets/licenses/pixelarticons.txt): avatar-square, key-solid, cloud-server, server.
const GLYPH = {
  signin: { color: "#2f6fde", d: "M4 2h16v2H4zm0 18h16v2H4zM2 4h2v16H2zm18 0h2v16h-2zM6 18h2v2H6zm10 0h2v2h-2zm-8-2h8v2H8zm2-4h4v2h-4zM8 8h2v4H8zm2-2h4v2h-4zm4 2h2v4h-2z" },
  key: { color: "#b7790f", d: "M11 8H13V9H23V14H21V18H19V14H17V16H15V14H13V16H11V18H3V16H1V8H3V6H11V8ZM5 14H9V10H5V14Z" },
  cloud: { color: "#0f7fc9", d: "M20 6h-2v2h2V6Zm2 2h-2v4h2V8Zm-2 4H4v2h16v-2ZM4 8H2v4h2V8Zm4-2H4v2h4V6Zm8-4h-6v2h6V2Zm-6 2H8v2h2V4Zm0 4H8v2h2V8Zm8-4h-2v2h2V4Zm0 4h-2v2h2V8Zm-7 8h2v2h-2zm0 4h2v2h-2zm-7-2h7v2H4zm9 0h7v2h-7zm-2-4h2v2h-2z" },
  globe: { color: "#5b4fc4", d: "M6 7h4v2H6zm0 8h4v2H6zM2 5h2v14H2zm18 0h2v14h-2zM4 19h16v2H4zM4 3h16v2H4zm0 8h16v2H4z" },
};
const glyphFor = (k: Kind): keyof typeof GLYPH => KIND[k].signIn ? "signin" : k === "azure" ? "cloud" : KIND[k].url ? "globe" : "key";
const Logo = ({ k, favicon, badge = true }: { k: Kind; favicon?: string; badge?: boolean }) => {
  const g = GLYPH[glyphFor(k)];
  return <span style={{ position: "relative", width: 34, height: 34, flex: "none" }}>
    <span style={{ width: 34, height: 34, borderRadius: 9, display: "grid", placeItems: "center", color: g.color,
      background: favicon ? "var(--paper-2)" : `color-mix(in srgb, ${g.color} 13%, var(--paper-2))` }}>
      {favicon ? <img src={favicon} alt="" style={{ width: 20, height: 20, borderRadius: 4 }} />
        : <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" shapeRendering="crispEdges" aria-hidden="true"><path d={g.d} /></svg>}
    </span>
    {badge && <span style={{ position: "absolute", right: -5, bottom: -5, borderRadius: 5, display: "block", boxShadow: "0 0 0 2px var(--paper-2)" }}><AgentMark backend={KIND[k].backend} size={16} /></span>}
  </span>;
};

// ---------- shared account form (the fields are not what's being judged; layout around them is) ----------
const Label = ({ children, hint }: { children: React.ReactNode; hint?: string }) =>
  <div style={{ display: "flex", gap: 8, alignItems: "baseline", margin: "10px 2px 5px" }}>
    <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>{children}</span>{hint && <span className="meta" style={{ fontSize: 12 }}>{hint}</span>}</div>;
const Text = ({ value, set, placeholder, mono, type }: { value: string; set: (v: string) => void; placeholder: string; mono?: boolean; type?: string }) =>
  <input className="field" type={type} style={{ height: 36, fontSize: 14, fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined }}
    value={value} placeholder={placeholder} spellCheck={false} autoComplete="off" onChange={(e) => set(e.target.value)} />;
const Switch = ({ on, flip, label, sub }: { on: boolean; flip: () => void; label: string; sub: string }) =>
  <div className="conn" style={{ minHeight: 46 }}><span className="txt" style={{ flex: 1 }}><span className="label">{label}</span><span className="sub wrap">{sub}</span></span>
    <button className={on ? "sw on" : "sw"} role="switch" aria-checked={on} aria-label={label} onClick={flip}><i /></button></div>;

function AccountFields({ a, set }: { a: Account; set: (p: Partial<Account>) => void }) {
  const [key, setKey] = useState("");
  const k = KIND[a.kind];
  if (k.signIn) return <p className="mcap" style={{ marginTop: 8 }}>Opens your browser to sign in. </p>;
  return <>
    <Label>Name</Label><Text value={a.name} set={(name) => set({ name })} placeholder={k.title} />
    {k.url && <><Label hint={a.kind === "azure" ? "Ends in /openai" : "Full API base"}>Base URL</Label>
      <Text mono value={a.baseUrl} set={(baseUrl) => set({ baseUrl })} placeholder={a.kind === "azure" ? "https://YOUR_RESOURCE.openai.azure.com/openai" : "https://gateway.example/v1"} /></>}
    {a.kind === "azure" && <><Label hint="Leave empty for the v1 API">API version</Label><Text mono value={a.apiVersion} set={(apiVersion) => set({ apiVersion })} placeholder="2025-04-01-preview" /></>}
    <Label hint={a.kind === "azure" ? "Deployment names, comma-separated" : "Comma-separated"}>Models</Label>
    <Text mono value={a.models} set={(models) => set({ models })} placeholder={k.backend === "claude" ? "claude-sonnet-5-5" : "gpt-5.5"} />
    <Label hint="Stored on this Mac, never shown again">API key</Label>
    {a.keyEnd && !key ? <div style={{ display: "flex", alignItems: "center", gap: 10 }}><span className="meta" style={{ flex: 1 }}><Icon name="lock" size={12} /> Saved key ending {a.keyEnd}</span>
      <button className="p-btn soft sm" onClick={() => setKey(" ")}>Replace</button></div>
      : <Text type="password" value={key.trim()} set={(v) => { setKey(v || " "); set({ keyEnd: v.slice(-4) }); }} placeholder="Paste key" />}
    {(a.kind === "azure" || a.kind === "gateway") && <><Label hint="Off unless this endpoint supports them">Hosted tools</Label>
      <div className="card"><Switch on={a.images} flip={() => set({ images: !a.images })} label="Image generation" sub="Needs an image model on this endpoint." />
        <Switch on={a.search} flip={() => set({ search: !a.search })} label="Web search" sub="Or add an MCP search server." /></div></>}
  </>;
}
function KindPicker({ value, pick, except }: { value?: Kind; pick: (k: Kind) => void; except?: string }) {
  const [advanced, setAdvanced] = useState(false);
  const row = (k: Kind) => {
    const taken = takenSignIn(k, except);
    return <button key={k} className={value === k ? "opt sel" : "opt"} role="radio" aria-checked={value === k} disabled={taken} style={taken ? { opacity: .45 } : undefined} onClick={() => pick(k)}>
      <Logo k={k} /><span className="txt"><span className="label">{KIND[k].signIn ? `${HARNESS[KIND[k].backend]} · ${KIND[k].title}` : KIND[k].title}</span>
        <span className="sub wrap">{taken ? "Already connected." : KIND[k].detail}</span></span></button>;
  };
  const all = Object.keys(KIND) as Kind[];
  return <>
    <div className="card" role="radiogroup">{all.filter((k) => KIND[k].signIn).map(row)}</div>
    <button className="link" style={{ alignSelf: "flex-start", padding: 0 }} aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>
      <Icon name={advanced ? "chevdown" : "right"} size={11} /> Advanced: API key or custom endpoint</button>
    {advanced && (["codex", "claude"] as Backend[]).map((b) => <div key={b}><Label>{HARNESS[b]}</Label>
      <div className="card" role="radiogroup">{all.filter((k) => !KIND[k].signIn && KIND[k].backend === b).map(row)}</div></div>)}
  </>;
}
function AccountDialog({ initial, close }: { initial?: Account; close: () => void }) {
  const [a, setA] = useState<Account | null>(initial ?? null);
  const set = (p: Partial<Account>) => setA({ ...a!, ...p });
  const users = a ? usedBy(a.id) : [];
  return <div className="veil2" onClick={close}><div className="dlg" role="dialog" style={{ maxWidth: 480, maxHeight: "88vh", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
    <h3>{initial ? `Edit ${initial.name}` : a ? `Add ${KIND[a.kind].title}` : "Add a connection"}</h3>
    {!a ? <KindPicker pick={(kind) => setA({ id: `a${Date.now()}`, name: KIND[kind].signIn ? KIND[kind].title : "", kind, baseUrl: "", apiVersion: "", models: SIGNIN_MODELS[kind] ?? "", keyEnd: "", images: false, search: false })} />
      : <div><AccountFields a={a} set={set} /></div>}
    {initial && users.length > 0 && <div className="dlg-note"><Icon name="restart" /><span>{users.map((t) => t.name).join(", ")} restart{users.length === 1 ? "s" : ""} with the change.</span></div>}
    <div className="foot">{initial && <button className="p-btn quiet" style={{ color: "var(--red)" }} disabled={users.length > 0} title={users.length ? "Move its Tags to another connection first" : ""}
      onClick={() => { removeAccount(initial.id); close(); }}>Remove</button>}<span className="spacer" />
      <button className="p-btn quiet" onClick={close}>Cancel</button>
      {a && <button className="p-btn ink" onClick={() => { saveAccount({ ...a, name: a.name || KIND[a.kind].title }); close(); }}>{KIND[a.kind].signIn && !initial ? "Continue in browser" : "Save"}</button>}</div>
  </div></div>;
}

// ================= Variant A: flat account list + one grouped model menu per Tag =================
function SettingsA() {
  const { accounts } = useStore();
  const [editing, setEditing] = useState<Account | "new" | null>(null);
  const line = (a: Account) => a.status === "expired"
    ? <span style={{ color: "var(--amber, #b36b00)" }}>Sign-in expired{a.email ? ` · ${a.email}` : ""}</span> : a.email ? `Signed in as ${a.email}` : KIND[a.kind].title;
  const count = (a: Account) => { const n = usedBy(a.id).length;
    return <span className="meta" style={{ fontSize: 12.5, whiteSpace: "nowrap", color: n ? undefined : "var(--faint)" }}>{n ? `${n} Tag${n === 1 ? "" : "s"}` : "Not used"}</span>; };
  return <>
    <div className="sec-head" style={{ margin: "4px 2px 10px" }}><span className="spacer" />
      <button className="p-btn soft sm" onClick={() => setEditing("new")}><Icon name="plus" size={11} /> Add connection</button></div>
    {(["codex", "claude"] as Backend[]).map((b) => {
      const list = accounts.filter((a) => KIND[a.kind].backend === b);
      return <div key={b} className="section">
        <div className="sec-head" style={{ gap: 8 }}><AgentMark backend={b} size={18} /><h3>{HARNESS[b]}</h3></div>
        <div className="card">{list.length ? list.map((a) =>
          <div key={a.id} className="opt" role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={() => a.status !== "expired" && setEditing(a)}>
            <Logo k={a.kind} favicon={a.favicon} badge={false} />
            <span className="txt" style={{ flex: 1, minWidth: 0 }}><span className="label">{a.name}</span>
              <span className="sub" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{line(a)}</span></span>
            {a.status === "expired"
              ? <button className="p-btn ink sm" onClick={(e) => { e.stopPropagation(); saveAccount({ ...a, status: "ok" }); }}>Reconnect</button>
              : <>{count(a)}<span className="chev"><Icon name="right" /></span></>}
          </div>)
          : <div className="conn"><span className="meta">No {HARNESS[b]} connections yet.</span></div>}</div>
      </div>;
    })}
    <p className="mcap">Each Tag picks its model, and so its connection, in its Details tab.</p>
    {editing && <AccountDialog initial={editing === "new" ? undefined : editing} close={() => setEditing(null)} />}
  </>;
}
function TagA({ tag }: { tag: string }) {
  const { accounts, uses } = useStore();
  const use = uses[tag];
  return <div style={{ margin: "6px 0 10px" }}>
    <select className="field" style={{ height: 36, fontSize: 14 }} value={`${use?.account}|${use?.model}`}
      onChange={(e) => { const [account, model] = e.target.value.split("|"); assign(tag, { account, model }); }}>
      {accounts.map((a) => <optgroup key={a.id} label={a.name}>{models(a).map((m) => <option key={m} value={`${a.id}|${m}`}>{m}</option>)}</optgroup>)}
    </select>
    <p className="mcap" style={{ marginTop: 6 }}>Grouped by connection. Picking a model picks its connection and harness.</p>
  </div>;
}

// ================= Variant B: accounts grouped by agent, edited inline; Tag picks account then model =================
function SettingsB() {
  const { accounts } = useStore();
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState<Backend | null>(null);
  return <>{(["codex", "claude"] as Backend[]).map((b) => <div key={b} className="section">
    <div className="sec-head"><h3>{b === "codex" ? "Codex connections" : "Claude Code connections"}</h3><span className="spacer" />
      <button className="link" onClick={() => setAdding(adding === b ? null : b)}>{adding === b ? "Cancel" : "+ Add"}</button></div>
    {adding === b && <div style={{ marginBottom: 8 }}><KindPickerFor backend={b} done={() => setAdding(null)} /></div>}
    <div className="card">{accounts.filter((a) => KIND[a.kind].backend === b).map((a) => <div key={a.id}>
      <button className="opt" onClick={() => setOpen(open === a.id ? null : a.id)}><Logo k={a.kind} favicon={a.favicon} />
        <span className="txt" style={{ flex: 1 }}><span className="label">{a.name}</span><span className="sub"><Status a={a} />{summary(a)}</span></span>
        {a.status === "expired" && <span onClick={(e) => e.stopPropagation()}><Action a={a} edit={() => {}} /></span>}
        <span className="meta" style={{ fontSize: 12 }}>{usedBy(a.id).map((t) => t.name).join(", ") || "Unused"}</span>
        <span className="chev"><Icon name={open === a.id ? "chevdown" : "right"} /></span></button>
      {open === a.id && <div style={{ padding: "0 14px 14px" }}><AccountFields a={a} set={(p) => saveAccount({ ...a, ...p })} /></div>}
    </div>)}</div>
  </div>)}<p className="mcap">Edits save as you go. Tags using a connection restart when it changes.</p></>;
}
function KindPickerFor({ backend, done }: { backend: Backend; done: () => void }) {
  const [advanced, setAdvanced] = useState(false);
  return <div className="card">{(Object.keys(KIND) as Kind[]).filter((k) => KIND[k].backend === backend && (advanced || KIND[k].signIn)).map((k) => {
    const taken = takenSignIn(k);
    return <button key={k} className="opt" disabled={taken} style={taken ? { opacity: .45 } : undefined}
      onClick={() => { saveAccount({ id: `a${Date.now()}`, name: KIND[k].title, kind: k, baseUrl: "", apiVersion: "", models: SIGNIN_MODELS[k] ?? "", keyEnd: "", images: false, search: false }); done(); }}>
      <Logo k={k} /><span className="txt"><span className="label">{KIND[k].title}</span><span className="sub">{taken ? "Already connected." : KIND[k].detail}</span></span></button>;
  })}{!advanced && <button className="opt" onClick={() => setAdvanced(true)}><span className="meta">Advanced: API key or custom endpoint…</span></button>}</div>;
}
function TagB({ tag }: { tag: string }) {
  const { accounts, uses } = useStore();
  const use = uses[tag];
  const current = accounts.find((a) => a.id === use?.account);
  return <div style={{ margin: "6px 0 10px" }}>
    <div className="card" role="radiogroup" aria-label="Connection">{accounts.map((a) =>
      <button key={a.id} className={a.id === use?.account ? "opt sel" : "opt"} role="radio" aria-checked={a.id === use?.account} style={{ padding: "8px 12px" }}
        onClick={() => assign(tag, { account: a.id, model: models(a)[0] ?? "" })}>
        <span className={a.id === use?.account ? "radio on" : "radio"} style={{ marginLeft: 0 }} /><Logo k={a.kind} favicon={a.favicon} />
        <span className="txt"><span className="label">{a.name}</span><span className="sub">{KIND[a.kind].title}</span></span></button>)}</div>
    {current && <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}><span className="meta" style={{ width: 50 }}>Model</span>
      <select className="field" style={{ height: 34, fontSize: 14, flex: 1 }} value={use.model} onChange={(e) => assign(tag, { ...use, model: e.target.value })}>
        {models(current).map((m) => <option key={m}>{m}</option>)}</select></div>}
  </div>;
}

// ================= Variant C: assign Tags from Settings (accounts × Tags table); Tag Details only shows it =================
function SettingsC() {
  const { accounts, uses } = useStore();
  const [editing, setEditing] = useState<Account | "new" | null>(null);
  const cell: React.CSSProperties = { padding: "8px 6px", borderTop: "1px solid var(--line)", textAlign: "center" };
  return <div className="section">
    <div className="sec-head"><h3>Which Tag uses which connection</h3><span className="spacer" /><button className="p-btn soft sm" onClick={() => setEditing("new")}><Icon name="plus" size={11} /> Add connection</button></div>
    <div className="card" style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead><tr><th style={{ textAlign: "left", padding: "8px 12px" }}>Connection</th>{TAGS.map((t) => <th key={t.id} style={{ padding: 8 }}>{t.name}</th>)}</tr></thead>
      <tbody>{accounts.map((a) => <tr key={a.id}>
        <td style={{ ...cell, textAlign: "left", padding: "8px 12px" }}><button className="link" style={{ padding: 0, display: "flex", gap: 8, alignItems: "center" }} onClick={() => setEditing(a)}>
          <Logo k={a.kind} favicon={a.favicon} /><span style={{ textAlign: "left" }}><b>{a.name}</b><br /><span className="meta" style={{ fontSize: 11.5 }}><Status a={a} />{KIND[a.kind].title}</span></span></button>
          {a.status === "expired" && <div style={{ marginTop: 4 }}><Action a={a} edit={() => {}} /></div>}</td>
        {TAGS.map((t) => { const on = uses[t.id]?.account === a.id; return <td key={t.id} style={cell}>
          <button className={on ? "radio on" : "radio"} aria-label={`${t.name} uses ${a.name}`} style={{ margin: 0 }}
            onClick={() => assign(t.id, { account: a.id, model: on ? uses[t.id].model : models(a)[0] ?? "" })} />
          {on && <div className="meta" style={{ fontSize: 11 }}>{uses[t.id].model}</div>}</td>; })}
      </tr>)}</tbody></table></div>
    <p className="mcap">One dot per column: each Tag uses exactly one connection. Open a Tag to change its model.</p>
    {editing && <AccountDialog initial={editing === "new" ? undefined : editing} close={() => setEditing(null)} />}
  </div>;
}
function TagC({ tag, openAI }: { tag: string; openAI?: () => void }) {
  const { accounts, uses } = useStore();
  const use = uses[tag]; const a = accounts.find((x) => x.id === use?.account);
  return <div style={{ margin: "6px 0 10px" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>{a && <Logo k={a.kind} favicon={a.favicon} />}
      <span style={{ flex: 1 }}><b>{a?.name ?? "No connection"}</b><br /><span className="meta">{a ? summary(a) : ""}</span></span>
      <button className="link" onClick={openAI}>Change in Settings</button></div>
    {a && <select className="field" style={{ height: 34, fontSize: 14, marginTop: 8 }} value={use.model} onChange={(e) => assign(tag, { ...use, model: e.target.value })}>
      {models(a).map((m) => <option key={m}>{m}</option>)}</select>}
  </div>;
}

// ---------- mounts ----------
const VARIANTS = [{ key: "A", name: "Account list + grouped model menu" }, { key: "B", name: "Grouped by agent; Tag picks account" }, { key: "C", name: "Assign Tags from Settings" }];
function useVariant() {
  const [, rerender] = useState(0);
  useEffect(() => { const f = () => rerender((n) => n + 1); addEventListener("popstate", f); return () => removeEventListener("popstate", f); }, []);
  return usePrototypeVariant(VARIANTS.map((v) => v.key));
}
function commands(): string {
  return TAGS.map((t) => {
    const use = state.uses[t.id]; const a = state.accounts.find((x) => x.id === use?.account);
    if (!a) return `# ${t.name}: no account`;
    const p = KIND[a.kind].backend === "codex" ? "OPENTAG_CODEX_" : "OPENTAG_CLAUDE_";
    const lines = [`# ${t.name} → ${a.name}`];
    if (KIND[a.kind].signIn) lines.push(`tag ${t.name} config set ${p}AUTH inherit`);
    else {
      if (KIND[a.kind].url) lines.push(`tag ${t.name} config set ${p}BASE_URL ${a.baseUrl}`);
      if (a.kind === "azure" && a.apiVersion) lines.push(`tag ${t.name} config set ${p}AZURE_API_VERSION ${a.apiVersion}`);
      lines.push(`tag ${t.name} config set ${p}MODELS ${a.models}`, `tag ${t.name} config set ${p}API_KEY <key ••${a.keyEnd}>`);
      lines.push(`tag ${t.name} config set ${p}AUTH ${a.kind === "azure" ? "azure" : "api"}`);
    }
    lines.push(`tag ${t.name} config set OPENTAG_DEFAULT_MODEL ${KIND[a.kind].backend}:${use.model}`);
    return lines.join("\n");
  }).join("\n\n");
}
function StatePanel() {
  useStore();
  return <details className="section" style={{ marginBottom: 60 }}><summary className="meta" style={{ cursor: "pointer" }}>PROTOTYPE state: per-Tag commands</summary>
    <pre style={{ fontSize: 11, whiteSpace: "pre-wrap", background: "var(--fill)", padding: 10, borderRadius: 8 }}>{commands()}</pre></details>;
}

/** Mounted in Settings → AI connections. */
export function AccountsPrototype() {
  const v = useVariant();
  return <>{v === "A" && <SettingsA />}{v === "B" && <SettingsB />}{v === "C" && <SettingsC />}<StatePanel /><PrototypeSwitcher variants={VARIANTS} current={v} /></>;
}
/** Mounted in a Tag's Details tab, next to the real model row. Demo Tags without a mapping use the first sample Tag. */
export function TagAccountPrototype({ tag, openAI }: { tag: string; openAI?: () => void }) {
  const v = useVariant();
  const id = state.uses[tag] ? tag : TAGS[0].id;
  return <div style={{ border: "1.5px dashed var(--blue)", borderRadius: 10, padding: "6px 12px", margin: "8px 0" }}>
    <div className="meta" style={{ fontSize: 11, fontWeight: 700, letterSpacing: .4 }}>PROTOTYPE · CONNECTION FOR THIS TAG</div>
    {v === "A" && <TagA tag={id} />}{v === "B" && <TagB tag={id} />}{v === "C" && <TagC tag={id} openAI={openAI} />}
    <StatePanel /><PrototypeSwitcher variants={VARIANTS} current={v} />
  </div>;
}
