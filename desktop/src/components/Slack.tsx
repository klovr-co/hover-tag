// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// A miniature Slack window for sign-in: paste the line, send it, click Confirm.
// Always light, like Slack.
import jules from "../assets/art/jules.png";

/** The sign-in line in a message box, then Slack's Confirm dialog. */
export function SlackSendDemo({ line }: { line: string }) {
  const short = line.length > 24 ? `${line.slice(0, 21)}…` : line;
  return (
    <div className="slk" role="img" aria-label="Paste the line into a Slack message box, send it, then click Confirm.">
      <div className="ws-side"><b>Your workspace</b><span className="on"># general</span><span># launch</span><span># random</span></div>
      <div className="pane">
        <div className="ph"># general</div>
        <div className="pm"><img src={jules} alt="" /><div><b>Jules</b><div>Launch sync moved to 10:30</div></div></div>
        <div className="pc"><span style={{ flex: 1 }}>{short}</span>
          <span className="send"><svg width="9" height="9" viewBox="0 0 16 16" fill="#fff" aria-hidden="true"><path d="M1 8 15 1 11 15 8 9z" /></svg></span></div>
      </div>
      <div className="veil"><div className="modal"><b>Slack CLI Authentication</b><div className="ln" style={{ width: 140 }} /><div className="ln" style={{ width: 110 }} /><span className="cf">Confirm</span></div></div>
    </div>
  );
}
