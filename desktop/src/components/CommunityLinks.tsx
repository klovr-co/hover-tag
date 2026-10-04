// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Opens the public pages where people can support Tag. Nothing is posted or
// starred on the user's behalf; each link just opens the page in a browser.
import type { Bridge } from "../lib/bridge";
import slackLogo from "../../../assets/branding/slack-icon.svg";

// GitHub and X marks from Bootstrap Icons (MIT; src/assets/licenses/bootstrap-icons.txt).
const BRAND_PATHS = {
  github: "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8",
  x: "M12.6.75h2.454l-5.36 6.142L16 15.25h-4.937l-3.867-5.07-4.425 5.07H.316l5.733-6.57L0 .75h5.063l3.495 4.633L12.601.75Zm-.86 13.028h1.36L4.323 2.145H2.865z",
};

function CommunityLogo({ brand }: { brand: keyof typeof BRAND_PATHS | "slack" }) {
  return brand === "slack"
    ? <img className="community-logo" src={slackLogo} width={14} height={14} alt="" />
    : <svg className="community-logo" width={14} height={14} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false">
      <path d={BRAND_PATHS[brand]} />
    </svg>;
}

export const REPO = "https://github.com/klovr-co/hover-tag";
const SLACK = "https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A";
const SHARE = "https://twitter.com/intent/tweet?text="
  + encodeURIComponent("I'm using Tag, a personal assistant that lives in Slack.") + "&url=" + encodeURIComponent(REPO);

/** Star, Join and Share as one line of links. */
export function CommunityLinks({ api }: { api: Bridge }) {
  return (
    <div className="community" style={{ marginLeft: -4 }}>
      <button onClick={() => void api.open(REPO)}><CommunityLogo brand="github" />Star on GitHub</button><span aria-hidden="true">·</span>
      <button onClick={() => void api.open(SLACK)}><CommunityLogo brand="slack" />Join Slack community</button><span aria-hidden="true">·</span>
      <button onClick={() => void api.open(SHARE)}><CommunityLogo brand="x" />Share on X</button>
    </div>
  );
}
