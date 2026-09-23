/**
 * Minimal renderer for training module markdown. Supports the subset the
 * content uses: # and ## headings, - bullet lists, 1. numbered lists, and
 * paragraphs. Text is rendered as text, never as HTML.
 */
import React from "react";

export type Block =
  | { kind: "h1" | "h2" | "p"; text: string }
  | { kind: "ul" | "ol"; items: string[] };

const BULLET = /^- /;
const NUMBERED = /^\d+\. /;

export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ kind: "p", text: paragraph.join(" ") });
    paragraph = [];
  };
  const pushItem = (kind: "ul" | "ol", text: string) => {
    const last = blocks[blocks.length - 1];
    if (last && last.kind === kind) last.items.push(text);
    else blocks.push({ kind, items: [text] });
  };

  for (const raw of source.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
    } else if (line.startsWith("## ")) {
      flushParagraph();
      blocks.push({ kind: "h2", text: line.slice(3) });
    } else if (line.startsWith("# ")) {
      flushParagraph();
      blocks.push({ kind: "h1", text: line.slice(2) });
    } else if (BULLET.test(line)) {
      flushParagraph();
      pushItem("ul", line.replace(BULLET, ""));
    } else if (NUMBERED.test(line)) {
      flushParagraph();
      pushItem("ol", line.replace(NUMBERED, ""));
    } else {
      paragraph.push(line);
    }
  }
  flushParagraph();
  return blocks;
}

function renderBlock(block: Block, key: number): React.ReactElement {
  switch (block.kind) {
    case "h1":
      return (
        <h2 key={key} className="font-display text-3xl font-bold uppercase leading-tight text-white">
          {block.text}
        </h2>
      );
    case "h2":
      return (
        <h3 key={key} className="pt-3 font-display text-xl font-bold uppercase text-brand">
          {block.text}
        </h3>
      );
    case "p":
      return <p key={key}>{block.text}</p>;
    case "ul":
      return (
        <ul key={key} className="list-disc space-y-1.5 pl-6 marker:text-brand">
          {block.items.map((item, i) => <li key={i}>{item}</li>)}
        </ul>
      );
    case "ol":
      return (
        <ol key={key} className="list-decimal space-y-1.5 pl-6 marker:font-bold marker:text-brand">
          {block.items.map((item, i) => <li key={i}>{item}</li>)}
        </ol>
      );
  }
}

export default function Markdown({ source }: { source: string }): React.ReactElement {
  return (
    <div className="space-y-3 text-lg leading-relaxed text-slate-200">{parseMarkdown(source).map(renderBlock)}</div>
  );
}
