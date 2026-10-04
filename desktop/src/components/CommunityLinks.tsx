// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Opens the public pages where people can support Tag. Nothing is posted or
// starred on the user's behalf; each link just opens the page in a browser.
import type { Bridge } from "../lib/bridge";

export const REPO = "https://github.com/klovr-co/hover-tag";
const SLACK = "https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A";
const SHARE = "https://twitter.com/intent/tweet?text="
  + encodeURIComponent("I'm using Tag, a personal assistant that lives in Slack.") + "&url=" + encodeURIComponent(REPO);

/** Star, Join and Share as one line of links. */
export function CommunityLinks({ api }: { api: Bridge }) {
  return (
    <div className="community" style={{ marginLeft: -4 }}>
      <button onClick={() => void api.open(REPO)}>Star on GitHub</button>·
      <button onClick={() => void api.open(SLACK)}>Join Slack</button>·
      <button onClick={() => void api.open(SHARE)}>Share on X</button>
    </div>
  );
}
