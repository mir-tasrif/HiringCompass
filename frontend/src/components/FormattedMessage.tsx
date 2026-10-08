import React from "react";

function inlineText(text: string, keyPrefix: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    return part.startsWith("**") && part.endsWith("**")
      ? <strong key={key}>{part.slice(2, -2)}</strong>
      : <React.Fragment key={key}>{part}</React.Fragment>;
  });
}

// Render common assistant Markdown safely as React elements instead of showing its markers literally.
export default function FormattedMessage({ text }: { text: string }) {
  const blocks: Array<{ kind: "paragraph" | "list" | "ordered" | "heading"; lines: string[] }> = [];

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const bullet = /^[-*]\s+(.+)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.+)$/.exec(line);
    const heading = /^#{1,3}\s+(.+)$/.exec(line);
    if (bullet) {
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "list") previous.lines.push(bullet[1]);
      else blocks.push({ kind: "list", lines: [bullet[1]] });
    } else if (numbered) {
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "ordered") previous.lines.push(numbered[1]);
      else blocks.push({ kind: "ordered", lines: [numbered[1]] });
    } else if (heading) {
      blocks.push({ kind: "heading", lines: [heading[1].replace(/\*\*/g, "")] });
    } else {
      blocks.push({ kind: "paragraph", lines: [line] });
    }
  }

  return <div className="formatted-message">{blocks.map((block, index) => {
    if (block.kind === "list" || block.kind === "ordered") {
      const List = block.kind === "list" ? "ul" : "ol";
      return <List key={index}>{block.lines.map((line, itemIndex) => <li key={itemIndex}>{inlineText(line, `${index}-${itemIndex}`)}</li>)}</List>;
    }
    if (block.kind === "heading") return <h3 key={index}>{inlineText(block.lines[0], `${index}`)}</h3>;
    return <p key={index}>{inlineText(block.lines[0], `${index}`)}</p>;
  })}</div>;
}
