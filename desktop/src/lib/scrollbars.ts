// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Scrollbars that float over the content, like macOS with a trackpad, whatever
// the Mac's scroll bar setting. Native scrollbars are hidden in CSS; one thumb
// shows for whatever is scrolling, fades out after a moment, and can be dragged.

const INSET = 3;
const MIN = 28;
const LINGER = 900;

/** Where the thumb sits along a track of `track` pixels, or null when nothing scrolls. */
export function thumbFor(scrollTop: number, scrollHeight: number, clientHeight: number, track: number) {
  const range = scrollHeight - clientHeight;
  if (range <= 1 || track <= 0) return null;
  const size = Math.min(track, Math.max(MIN, (track * clientHeight) / scrollHeight));
  const offset = ((track - size) * Math.min(Math.max(scrollTop, 0), range)) / range;
  return { size, offset };
}

export function installOverlayScrollbars(doc: Document = document): () => void {
  const win = doc.defaultView!;
  const thumb = doc.createElement("div");
  thumb.className = "oscroll";
  thumb.setAttribute("aria-hidden", "true");
  doc.body.appendChild(thumb);

  let target: Element | null = null;
  let hide = 0;
  let drag: { y: number; top: number; ratio: number } | null = null;

  const frame = (el: Element) => (el === doc.scrollingElement
    ? { top: 0, bottom: win.innerHeight, right: win.innerWidth }
    : el.getBoundingClientRect());

  const place = (el: Element) => {
    const box = frame(el);
    const track = box.bottom - box.top - INSET * 2;
    const at = thumbFor(el.scrollTop, el.scrollHeight, el.clientHeight, track);
    if (!at) return false;
    target = el;
    thumb.classList.toggle("on-sky", el === doc.scrollingElement || !!el.closest(".logbox"));
    thumb.style.top = `${box.top + INSET + at.offset}px`;
    thumb.style.height = `${at.size}px`;
    thumb.style.left = `${box.right - INSET}px`;
    return true;
  };

  const show = () => {
    thumb.classList.add("shown");
    win.clearTimeout(hide);
    hide = win.setTimeout(() => { if (!drag && !thumb.matches(":hover")) thumb.classList.remove("shown"); }, LINGER);
  };

  const onScroll = (e: Event) => {
    const el = e.target === doc ? doc.scrollingElement : e.target;
    if (el instanceof Element && place(el)) show();
  };
  const onDown = (e: PointerEvent) => {
    if (!target) return;
    const box = frame(target);
    const track = box.bottom - box.top - INSET * 2;
    const at = thumbFor(target.scrollTop, target.scrollHeight, target.clientHeight, track);
    if (!at) return;
    e.preventDefault();
    thumb.setPointerCapture(e.pointerId);
    drag = { y: e.clientY, top: target.scrollTop, ratio: (target.scrollHeight - target.clientHeight) / (track - at.size) };
    thumb.classList.add("dragging");
  };
  const onMove = (e: PointerEvent) => {
    if (drag && target) target.scrollTop = drag.top + (e.clientY - drag.y) * drag.ratio;
  };
  const onUp = () => { drag = null; thumb.classList.remove("dragging"); show(); };
  const onResize = () => { win.clearTimeout(hide); thumb.classList.remove("shown"); };

  doc.addEventListener("scroll", onScroll, { capture: true, passive: true });
  thumb.addEventListener("pointerdown", onDown);
  thumb.addEventListener("pointermove", onMove);
  thumb.addEventListener("pointerup", onUp);
  thumb.addEventListener("pointercancel", onUp);
  thumb.addEventListener("pointerleave", () => { if (!drag) show(); });
  win.addEventListener("resize", onResize);
  return () => {
    win.clearTimeout(hide);
    doc.removeEventListener("scroll", onScroll, { capture: true });
    win.removeEventListener("resize", onResize);
    thumb.remove();
  };
}
