// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import type { Bridge } from "../lib/bridge";
import { parseJSON } from "../lib/protocol";
import { ErrorLine } from "./ui";

/** `tag NAME settings access --json`: two separate permissions, each with per-channel overrides. */
export interface AccessReport {
  inform: string;
  run: string;
  inform_options: { id: string; label: string }[];
  run_options: { id: string; label: string }[];
  channels: Record<string, { inform?: string; run?: string }>;
}

type Which = "inform" | "run";

/** Always named in full: never one "allow" for both. */
export const ACCESS_TEXT: Record<Which, { title: string; help: string }> = {
  inform: { title: "Who can give it information",
    help: "Answer its questions and add context. Used as information with their name; never runs anything." },
  run: { title: "Who can make it run work",
    help: "Start or add tasks, with this computer's files, tools and accounts." },
};

export function useAccess(api: Bridge, tag: string) {
  const [report, setReport] = useState<AccessReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const call = useCallback(async (args: string[]) => {
    const result = await api.tag([tag, "settings", "access", ...args, "--json"]);
    if (result.code !== 0) throw new Error(result.stderr.trim().split("\n").pop() || "Could not save who can work with it");
    setReport(parseJSON<AccessReport>(result.stdout));
  }, [api, tag]);
  useEffect(() => { call([]).catch(() => setReport(null)); }, [call]);
  /** A channel's level `default` clears its override. */
  const set = useCallback(async (which: Which, level: string, channel?: string) => {
    setBusy(true);
    setError("");
    try { await call([which, level, ...(channel ? [channel] : [])]); return true; }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { setBusy(false); }
  }, [call]);
  return { report, busy, error, set };
}

/** The Details rows; changes apply to the next message, without a restart. */
export function AccessRows({ api, tag, channels, say }: {
  api: Bridge; tag: string; channels: { id: string; name: string | null }[]; say: (text: string) => void;
}) {
  const access = useAccess(api, tag);
  const report = access.report;
  if (!report) return null;
  const pick = (which: Which, level: string, channel?: string) => void access.set(which, level, channel).then((ok) => {
    if (!ok) return;
    const label = level === "default" ? "the Tag's default" : options(which).find((o) => o.id === level)?.label ?? level;
    say(`${ACCESS_TEXT[which].title}${channel ? ` in ${channels.find((c) => c.id === channel)?.name ?? channel}` : ""}: ${label}`);
  });
  const options = (which: Which) => which === "inform" ? report.inform_options : report.run_options;
  return (
    <>
      {(["inform", "run"] as const).map((which) => (
        <AccessRow key={which} which={which}>
          <div className="segc" role="radiogroup" aria-label={ACCESS_TEXT[which].title}>
            {options(which).map((o) => (
              <button key={o.id} role="radio" aria-checked={report[which] === o.id} disabled={access.busy}
                onClick={() => report[which] !== o.id && pick(which, o.id)}>{o.label}</button>
            ))}
          </div>
        </AccessRow>
      ))}
      {channels.length > 0 && <>
        <dt>Per channel</dt>
        <dd className="mdd">
          <table className="access-tbl">
            <thead><tr><th>Channel</th><th>Information</th><th>Run work</th></tr></thead>
            <tbody>
              {channels.map((c) => (
                <tr key={c.id}>
                  <td>{c.name ? `#${c.name}` : c.id}</td>
                  {(["inform", "run"] as const).map((which) => (
                    <td key={which}>
                      <select className="field psel" aria-label={`${ACCESS_TEXT[which].title} in ${c.name ?? c.id}`} disabled={access.busy}
                        value={report.channels[c.id]?.[which] ?? "default"} onChange={(e) => pick(which, e.target.value, c.id)}>
                        <option value="default">Default</option>
                        {options(which).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                      </select>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </dd>
      </>}
      {access.error && <><dt /><dd><ErrorLine>{access.error}</ErrorLine></dd></>}
    </>
  );
}

function AccessRow({ which, children }: { which: Which; children: React.ReactNode }) {
  return (
    <>
      <dt>{ACCESS_TEXT[which].title}</dt>
      <dd className="mdd">
        {children}
        <p className="mcap">{ACCESS_TEXT[which].help}</p>
      </dd>
    </>
  );
}
