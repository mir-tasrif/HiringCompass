import React from "react";

const MARKDOWN_LINK = /(\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
const LINK_PARTS = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/;

// Keep the approved JD's plain-text layout while making generated form links clickable.
export default function FormattedJd({ text }: { text: string }) {
  return (
    <pre className="formatted-jd">{text.split(MARKDOWN_LINK).map((part, index) => {
      const match = LINK_PARTS.exec(part);
      return match
        ? <a key={index} href={match[2]} target="_blank" rel="noopener noreferrer">{match[1]}</a>
        : <React.Fragment key={index}>{part}</React.Fragment>;
    })}</pre>
  );
}
