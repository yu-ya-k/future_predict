import type { ReactNode } from "react";
import type { MarkdownProps } from "./types";

/**
 * Minimal, dependency-free Markdown renderer.
 * Supports: # headings (h1-h3), paragraphs, unordered lists,
 * **bold**, *italic*, and [n] citation links.
 * Does NOT use dangerouslySetInnerHTML — all output is React elements.
 */

// ── Inline renderer ──────────────────────────────────────────────────────────

function renderInline(text: string, onCitationClick?: (n: number) => void): ReactNode[] {
  // Token types: citation [n], bold **x**, italic *x*, plain text
  const TOKEN_RE = /(\[\d+\]|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  const parts: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = TOKEN_RE.exec(text)) !== null) {
    // Push plain text before this match
    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }

    const token = match[1];

    if (token.startsWith("[") && token.endsWith("]")) {
      // Citation [n]
      const n = parseInt(token.slice(1, -1), 10);
      if (!isNaN(n) && onCitationClick) {
        parts.push(
          <button
            key={`cite-${match.index}`}
            className="markdown__citation"
            type="button"
            onClick={() => onCitationClick(n)}
            aria-label={`引用 ${n} へジャンプ`}
          >
            {token}
          </button>
        );
      } else {
        parts.push(token);
      }
    } else if (token.startsWith("**")) {
      // Bold
      const inner = token.slice(2, -2);
      parts.push(<strong key={`bold-${match.index}`}>{inner}</strong>);
    } else if (token.startsWith("*")) {
      // Italic
      const inner = token.slice(1, -1);
      parts.push(<em key={`em-${match.index}`}>{inner}</em>);
    } else {
      parts.push(token);
    }

    last = match.index + token.length;
  }

  // Remaining plain text
  if (last < text.length) {
    parts.push(text.slice(last));
  }

  return parts;
}

// ── Block renderer ────────────────────────────────────────────────────────────

function parseBlocks(source: string, onCitationClick?: (n: number) => void): ReactNode[] {
  const lines = source.split("\n");
  const nodes: ReactNode[] = [];
  let i = 0;
  let keyCounter = 0;

  const nextKey = () => `md-${keyCounter++}`;

  while (i < lines.length) {
    const line = lines[i];

    // Heading # / ## / ###
    const headingMatch = /^(#{1,3})\s+(.+)$/.exec(line);
    if (headingMatch) {
      const level = headingMatch[1].length as 1 | 2 | 3;
      const text = headingMatch[2];
      const Tag = (`h${level}`) as "h1" | "h2" | "h3";
      nodes.push(
        <Tag key={nextKey()} className={`markdown__h${level}`}>
          {renderInline(text, onCitationClick)}
        </Tag>
      );
      i++;
      continue;
    }

    // Unordered list (collect consecutive list lines)
    if (/^[-*+]\s/.test(line)) {
      const items: ReactNode[] = [];
      while (i < lines.length && /^[-*+]\s/.test(lines[i])) {
        const itemText = lines[i].replace(/^[-*+]\s/, "");
        items.push(
          <li key={i} className="markdown__li">
            {renderInline(itemText, onCitationClick)}
          </li>
        );
        i++;
      }
      nodes.push(
        <ul key={nextKey()} className="markdown__ul">
          {items}
        </ul>
      );
      continue;
    }

    // Blank line — skip
    if (line.trim() === "") {
      i++;
      continue;
    }

    // Paragraph — collect until blank line or heading
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^#{1,3}\s/.test(lines[i]) &&
      !/^[-*+]\s/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }

    if (paraLines.length > 0) {
      nodes.push(
        <p key={nextKey()} className="markdown__p">
          {renderInline(paraLines.join(" "), onCitationClick)}
        </p>
      );
    }
  }

  return nodes;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function Markdown({ source, onCitationClick }: MarkdownProps) {
  if (!source || source.trim() === "") {
    return <div className="markdown markdown--empty" />;
  }

  const blocks = parseBlocks(source, onCitationClick);

  return <article className="markdown">{blocks}</article>;
}
