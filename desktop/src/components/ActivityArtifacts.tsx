import { useState } from "react";
import type { Bridge } from "../lib/bridge";
import type { ActivityItem } from "../lib/home";
import { Icon } from "./ui";

export function ActivityArtifacts({ api, item }: { api: Bridge; item: ActivityItem }) {
  const [error, setError] = useState("");
  if (!item.artifacts?.length) return null;
  return <>
    <div className="activity-artifacts" aria-label="Created files">
      {item.artifacts.map((artifact, index) => {
        const uploaded = artifact.delivery === "uploaded";
        const target = uploaded ? artifact.url || item.artifact_thread_url : artifact.local_path;
        const status = artifact.delivery === "upload_failed" ? "Upload failed" : uploaded ? "" : "Saved locally";
        const label = target ? `Open ${artifact.name}${uploaded ? " in Slack" : " locally"}` : `${artifact.name} · ${status || "Uploaded to Slack"}`;
        const content = <><Icon name={artifact.kind === "image" ? "image" : "file"} size={13} /><span>{artifact.name}</span>
          {status && <span className="activity-artifact-status">{status}</span>}{target && <Icon name="external" size={11} />}</>;
        return target ? <button key={index} className="activity-artifact" data-delivery={artifact.delivery} aria-label={label} title={label}
          onClick={() => { setError(""); void api.open(target).catch(() => setError("Couldn't open this file. It may have moved or been removed.")); }}>{content}</button>
          : <span key={index} className="activity-artifact" data-delivery={artifact.delivery} title={label}>{content}</span>;
      })}
    </div>
    {error && <p className="activity-artifact-error" role="alert">{error}</p>}
  </>;
}
