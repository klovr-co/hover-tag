// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Opens the public pages where people can support Tag. Nothing is posted or
// starred on the user's behalf; each button just opens the page in a browser.
import type { Bridge } from "../lib/bridge";
import { Secondary } from "./ui";

export const REPO = "https://github.com/klovr-co/hover-tag";
const SLACK = "https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A";
const SHARE = "https://twitter.com/intent/tweet?text="
  + encodeURIComponent("I'm using Tag, a personal assistant that lives in Slack.") + "&url=" + encodeURIComponent(REPO);

export function CommunityLinks({ api }: { api: Bridge }) {
  return (
    <div className="stack gap-10">
      <div className="callout" style={{ fontWeight: 600 }}>Enjoying Tag?</div>
      <div className="row gap-8">
        <Secondary title="Star on GitHub" icon="star" onClick={() => void api.open(REPO)} />
        <Secondary title="Join Slack" icon="chat" onClick={() => void api.open(SLACK)} />
        <Secondary title="Share on X" icon="share" onClick={() => void api.open(SHARE)} />
      </div>
    </div>
  );
}
