// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The Tag's answers and people's messages as Markdown. Builds React elements, never HTML,
// so a message can't inject markup; links open through the app, never inside the window.
import { Fragment, type ReactNode } from "react";

type Open = (url: string) => void;

const SAFE_URL = /^(https?:\/\/|mailto:)[^\s<>"]+$/i;
const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

/** A Markdown document as React elements. */
export function Markdown({ text, open, className = "md" }: { text: string; open: Open; className?: string }) {
  return <div className={className}>{blocks(text.replace(/\r\n?/g, "\n").split("\n"), open, "b")}</div>;
}

const isTableRow = (line: string) => line.includes("|") && line.trim() !== "|";
const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));

function blocks(lines: string[], open: Open, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let i = 0;
  const id = () => `${key}${out.length}`;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      out.push(<pre key={id()} className="md-code"><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      const Tag = `h${level}` as "h3";
      out.push(<Tag key={id()}>{inline(heading[2], open, id())}</Tag>);
      i++;
      continue;
    }
    if (RULE.test(line)) { out.push(<hr key={id()} />); i++; continue; }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && lines[i].trim() && QUOTE.test(lines[i])) body.push(QUOTE.exec(lines[i++])![1]);
      out.push(<blockquote key={id()}>{blocks(body, open, `${id()}q`)}</blockquote>);
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const head = cells(line);
      const align = cells(lines[i + 1]).map((cell) => cell.endsWith(":") ? (cell.startsWith(":") ? "center" : "right") : undefined);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && isTableRow(lines[i])) rows.push(cells(lines[i++]));
      const k = id();
      out.push(
        <div key={k} className="md-table"><table>
          <thead><tr>{head.map((cell, c) => <th key={c} style={{ textAlign: align[c] }}>{inline(cell, open, `${k}h${c}`)}</th>)}</tr></thead>
          <tbody>{rows.map((row, r) => <tr key={r}>{head.map((_, c) => <td key={c} style={{ textAlign: align[c] }}>{inline(row[c] ?? "", open, `${k}${r}.${c}`)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
      continue;
    }
    const item = ITEM.exec(line);
    if (item) {
      const indent = item[1].length;
      const ordered = /\d/.test(item[2]);
      const items: { first: string; rest: string[] }[] = [];
      while (i < lines.length) {
        const next = ITEM.exec(lines[i]);
        if (next && next[1].length <= indent + 1 && /\d/.test(next[2]) === ordered) {
          items.push({ first: next[3], rest: [] });
          i++;
        } else if (items.length && lines[i].trim() && (/^\s/.test(lines[i]) || !ITEM.exec(lines[i]) && !blockStart(lines[i]))) {
          // Indented lines (a nested list or a continued item) belong to the item above.
          items[items.length - 1].rest.push(lines[i].replace(new RegExp(`^\\s{0,${indent + 4}}`), ""));
          i++;
        } else break;
      }
      const start = ordered ? parseInt(item[2], 10) : undefined;
      const k = id();
      const List = ordered ? "ol" : "ul";
      out.push(
        <List key={k} start={start !== 1 ? start : undefined}>
          {items.map((entry, n) => {
            const task = /^\[([ xX])\]\s+(.*)$/.exec(entry.first);
            const nested = entry.rest.some((rest) => ITEM.test(rest) || blockStart(rest));
            const head = task ? task[2] : entry.first;
            return (
              <li key={n} className={task ? `md-task${task[1] !== " " ? " done" : ""}` : undefined}>
                {task && <span className="md-check" aria-label={task[1] !== " " ? "Done" : "Not done"} role="img" />}
                {nested ? <>{inline(head, open, `${k}${n}`)}{blocks(entry.rest, open, `${k}${n}n`)}</>
                  : inline([head, ...entry.rest].join("\n"), open, `${k}${n}`)}
              </li>
            );
          })}
        </List>,
      );
      continue;
    }
    const body: string[] = [];
    while (i < lines.length && lines[i].trim() && !blockStart(lines[i]) && !(isTableRow(lines[i]) && TABLE_SEP.test(lines[i + 1] ?? ""))) body.push(lines[i++]);
    out.push(<p key={id()}>{inline(body.join("\n"), open, id())}</p>);
  }
  return out;
}

function blockStart(line: string) {
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line);
}

// Inline: code first (nothing inside it is formatted), then links, then emphasis.
const INLINE = /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)|\[([^\]\n]+)\]\(([^)\s]+)\)|<((?:https?:\/\/|mailto:)[^>\s]+)>|((?:https?:\/\/)[^\s<>()]+[^\s<>().,;:!?'"*_~])|(\*\*|__)(?=\S)([\s\S]*?\S)\7|(~~)(?=\S)([\s\S]*?\S)~~|(\*|_)(?=[^\s*_])([\s\S]*?[^\s*_])\11(?![\w*])|\n/g;

export function inline(text: string, open: Open, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  const k = () => `${key}.${n++}`;
  const link = (url: string, label: ReactNode) => SAFE_URL.test(url)
    ? <a key={k()} href={url} onClick={(event) => { event.preventDefault(); open(url); }} title={url}>{label}</a>
    : <Fragment key={k()}>{label}</Fragment>;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    // `snake_case_words` aren't emphasis: an underscore inside a word stays literal.
    if (match[11] === "_" && at > 0 && /\w/.test(text[at - 1])) continue;
    if (at > last) out.push(unescape(text.slice(last, at)));
    last = at + match[0].length;
    if (match[1]) out.push(<code key={k()}>{match[2]}</code>);
    else if (match[3]) out.push(link(match[4], inline(match[3], open, k())));
    else if (match[5]) out.push(link(match[5], match[5]));
    else if (match[6]) out.push(link(match[6], match[6]));
    else if (match[7]) out.push(<strong key={k()}>{inline(match[8], open, k())}</strong>);
    else if (match[9]) out.push(<del key={k()}>{inline(match[10], open, k())}</del>);
    else if (match[11]) out.push(<em key={k()}>{inline(match[12], open, k())}</em>);
    else out.push(<br key={k()} />);
  }
  if (last < text.length) out.push(unescape(text.slice(last)));
  return out;
}

const unescape = (text: string) => text.replace(/\\([\\`*_{}[\]()#+\-.!|~>])/g, "$1");
