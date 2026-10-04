import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { Bridge } from "../lib/bridge";
import type { ActivityDetail, ActivityItem } from "../lib/home";
import { parseJSON } from "../lib/protocol";
import { Icon } from "./ui";

const states: Record<string, string> = { running: "Running", completed: "Finished", failed: "Failed",
  declined: "Declined", interrupted: "Stopped", unknown: "Outcome unknown" };

/** Load one local record on demand. Keep finished details across feed refreshes. */
/** Header chip that opens a run's details: step count, plus the outcome when it needs attention. */
export function StepsToggle({ item, open, controls, onToggle }: { item: ActivityItem; open: boolean; controls: string; onToggle: () => void }) {
  const steps = item.step_count ?? 0;
  const outcome = item.kind === "failed" ? "Failed" : item.kind === "working" ? "Working" : "";
  if (!item.run_id || (!steps && !outcome)) return null;
  const count = steps ? `${steps} ${steps === 1 ? "step" : "steps"}` : "";
  const text = [outcome, count].filter(Boolean).join(" · ");
  return <span className="activity-steps-wrap">· <button className={`activity-steps${item.kind === "failed" ? " activity-steps-failed" : ""}`} aria-expanded={open} aria-controls={controls}
    onClick={onToggle}>
    {item.kind === "failed" && <Icon name="warn" size={12} />}{text}<Icon name="right" size={11} />
  </button></span>;
}

export function ActivityDetails({ api, tag, item, label, children, open: controlledOpen, id: controlledId }: {
  api: Bridge; tag: string; item: ActivityItem; label?: string; children?: ReactNode;
  /** When set, the caller renders the toggle (see StepsToggle) and owns the open state. */
  open?: boolean; id?: string;
}) {
  const ownId = useId();
  const id = controlledId ?? ownId;
  const [ownOpen, setOpen] = useState(false);
  const controlled = controlledOpen !== undefined;
  const open = controlled ? controlledOpen : ownOpen;
  const [detail, setDetail] = useState<ActivityDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const version = `${item.kind}:${item.at}`;
  const loaded = useRef("");
  const pending = useRef(false);
  const load = async () => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true); setError("");
    try {
      const result = await api.tag([tag, "logs", "--activity", item.run_id!, "--json"]);
      const response = parseJSON<{ ok: boolean; activity?: ActivityDetail; error?: string }>(result.stdout);
      if (result.code !== 0 || !response.ok || !response.activity) throw new Error(response.error || "Saved details are unavailable.");
      setDetail(response.activity);
      loaded.current = version;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load saved details. Try again.");
    } finally { pending.current = false; setBusy(false); }
  };
  useEffect(() => {
    if (open && !busy && !error && loaded.current !== version) void load();
  }, [open, version, busy, error]);
  if (!item.run_id) return <>{children}</>;
  if (controlled && !open && !children) return null;
  const hasActions = !controlled || !!children;
  const slackURL = detail && /^T[A-Z0-9]+$/.test(detail.team) && /^[CDG][A-Z0-9]+$/.test(detail.channel)
    && /^\d+\.\d+$/.test(detail.thread_ts)
    ? `slack://channel?team=${encodeURIComponent(detail.team)}&id=${encodeURIComponent(detail.channel)}&message=${encodeURIComponent(detail.thread_ts)}` : null;
  return (
    <div className="activity-details">
      {hasActions && <div className="activity-actions">
        {children}
        {!controlled && <button className={`activity-toggle${label ? " activity-toggle-labeled" : ""}`} aria-label={label || (open ? "Hide details" : "View details")} title={open ? "Hide details" : "View details"} aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          {label && <span>{label}</span>}
          <Icon name="right" size={12} />
        </button>}
        {!controlled && open && slackURL && <button className="activity-thread" aria-label="Open thread in Slack" title="Open thread in Slack" onClick={() => void api.open(slackURL)}><Icon name="external" size={12} /></button>}
      </div>}
      {open && <div id={id} className={`activity-detail-body${controlled ? " activity-detail-panel" : ""}`}>
        {busy && <p role="status">Loading saved details…</p>}
        {error && <p role="alert">{error} <button className="link" onClick={() => void load()}>Try again</button></p>}
        {detail && <>
          {detail.error && <details className="activity-error">
            <summary><Icon name="warn" size={14} /><span>Error report</span><span className="activity-reference">{detail.error.reference}</span><Icon name="right" size={12} /></summary>
            <pre>{detail.error.text}</pre>
          </details>}
          {detail.outcome === "failed" && !detail.error && <p>No matching error report is available for this request.</p>}
          {!detail.events.length && <p>No tool activity was recorded for this request.</p>}
          {!!detail.events.length && <ol className="activity-timeline" aria-label="Request steps">
            {detail.events.map((event, index) => {
              const hasDetails = !!(event.details.tool?.trim() || event.details.input?.trim() || event.details.output?.trim());
              const Step = hasDetails ? "details" : "div";
              const Heading = hasDetails ? "summary" : "div";
              return <li key={index} data-status={event.status}>
              <span className="activity-step-mark"><Icon name={event.status === "completed" ? "check" : event.status === "failed" ? "close" : event.status === "running" ? "play" : "stop"} size={11} /></span>
              <Step className="activity-step">
                <Heading className="activity-step-heading">
                  <span className="activity-step-label">{event.label.replace(/…$/, "")}</span>
                  <span className="activity-step-status">{states[event.status] ?? event.status}</span>
                  {hasDetails && <Icon name="right" size={12} />}
                </Heading>
                {hasDetails && <div className="activity-step-content">
                  {event.details.tool && <code className="activity-tool">{event.details.tool}</code>}
                  {(event.details.input?.trim() || event.details.output?.trim()) && <>
                    <dl>
                      {event.details.input?.trim() && <><dt>Input</dt><dd><pre>{event.details.input}</pre></dd></>}
                      {event.details.output?.trim() && <><dt>Result</dt><dd><pre>{event.details.output}</pre></dd></>}
                    </dl>
                    <span className="activity-preview-note">Saved preview · shortened and redacted</span>
                  </>}
                </div>}
              </Step>
            </li>;
            })}
          </ol>}
          {detail.omitted > 0 && <p>{detail.omitted} later steps omitted.</p>}
          {(controlled && slackURL || item.kind === "working" || (detail.outcome === "failed" && !detail.error)) && <div className="activity-detail-footer">
            {controlled && slackURL && <button className="activity-refresh" onClick={() => void api.open(slackURL)}>Open thread in Slack<Icon name="external" size={12} /></button>}
            {(item.kind === "working" || (detail.outcome === "failed" && !detail.error)) && <button className="activity-refresh" disabled={busy} onClick={() => void load()}><Icon name="refresh" size={12} />Refresh details</button>}
          </div>}
        </>}
      </div>}
    </div>
  );
}
