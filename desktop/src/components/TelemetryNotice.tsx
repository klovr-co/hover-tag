// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The first-run usage data notice, shown once per installation before the app
// records anything. Continuing turns it on, like the CLI's notice.
import { useState } from "react";
import type { Bridge } from "../lib/bridge";
import { USAGE_DATA_NEVER, USAGE_DATA_SUMMARY, type Telemetry } from "../lib/telemetry";
import keyArt from "../assets/art/tag-key.png";
import { CompactSky, ErrorLine, Icon, Primary, Secondary } from "./ui";

export function TelemetryNotice({ api, telemetry }: { api: Bridge; telemetry: Telemetry }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const notice = telemetry.status?.privacy_notice;
  const choose = async (on: boolean) => {
    setBusy(true);
    setError("");
    try { await telemetry.choose(on); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  return (
    <>
      <CompactSky title="Usage data" sub="One choice for the app and the tag command" />
      <div className="body roomy">
        <div className="title-row">
          <div>
            <div className="h2">Help support Tag's development</div>
            <p className="lead">{USAGE_DATA_SUMMARY}</p>
          </div>
          <img className="sprite" src={keyArt} alt="" />
        </div>
        <div className="facts">
          <div className="fact"><span className="fi"><Icon name="lock" size={16} /></span><span>{USAGE_DATA_NEVER}</span></div>
          <div className="fact"><span className="fi"><Icon name="user" size={16} /></span>
            <span>Usage data is on after this notice. Turn it off any time in Settings, or with <b>tag telemetry off</b>.</span></div>
        </div>
        {notice && <button className="link" style={{ alignSelf: "flex-start" }} onClick={() => void api.open(notice)}>Read the privacy notice</button>}
        {error && <>
          <ErrorLine>{error}</ErrorLine>
          <button className="link" style={{ alignSelf: "flex-start" }} onClick={telemetry.dismiss}>Decide later</button>
        </>}
        <div className="foot">
          <span className="spacer" />
          <Secondary title="Turn off" onClick={() => void choose(false)} disabled={busy} />
          <Primary title="Continue" onClick={() => void choose(true)} disabled={busy} autoFocus />
        </div>
      </div>
    </>
  );
}
