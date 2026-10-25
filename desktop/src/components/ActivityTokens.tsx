import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ActivityItem } from "../lib/home";

export function ActivityTokens({ usage }: { usage: NonNullable<ActivityItem["usage"]> }) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const show = () => { clearTimeout(timer.current); setOpen(true); };
  const hide = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (document.activeElement !== trigger.current) setOpen(false);
    }, 120);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") { clearTimeout(timer.current); setOpen(false); }
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [open]);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = trigger.current?.getBoundingClientRect();
      const box = tooltip.current?.getBoundingClientRect();
      if (!anchor || !box) return;
      setPosition({
        left: Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8)),
        top: anchor.bottom + box.height + 8 < window.innerHeight ? anchor.bottom + 6 : Math.max(8, anchor.top - box.height - 6),
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open]);
  return <>
    <button ref={trigger} className="activity-tokens" type="button" aria-label={`${usage.total_tokens.toLocaleString()} tokens, show breakdown`}
      aria-describedby={open ? id : undefined} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
      onClick={show}>
      · {new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(usage.total_tokens)} tokens
    </button>
    {open && createPortal(<div ref={tooltip} id={id} role="tooltip" className="activity-token-tooltip" style={position}
      onMouseEnter={show} onMouseLeave={hide}>
      <strong>Token usage</strong>
      <dl>
        <dt>Input</dt><dd>{usage.input_tokens.toLocaleString()}</dd>
        {usage.cache_read_input_tokens != null && <><dt className="token-subset">Cached input read</dt><dd>{usage.cache_read_input_tokens.toLocaleString()}</dd></>}
        {usage.cache_creation_input_tokens != null && <><dt className="token-subset">Cache written</dt><dd>{usage.cache_creation_input_tokens.toLocaleString()}</dd></>}
        <dt>Output</dt><dd>{usage.output_tokens.toLocaleString()}</dd>
        {usage.reasoning_output_tokens != null && <><dt className="token-subset">Reasoning</dt><dd>{usage.reasoning_output_tokens.toLocaleString()}</dd></>}
        <dt className="token-total">Total</dt><dd className="token-total">{usage.total_tokens.toLocaleString()}</dd>
      </dl>
      <p>Cache counts are included in input; reasoning is included in output. Extra breakdowns appear when reported.</p>
      <p>Reply and tool work only. The summary pass is excluded.</p>
    </div>, document.body)}
  </>;
}
