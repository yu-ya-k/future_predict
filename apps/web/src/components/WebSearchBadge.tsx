import type { WebSearchBadgeProps } from "./types";

const TOOLTIP_ID = "web-search-disabled-reason";

function SearchIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function SearchOffIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
      <line x1="2" y1="2" x2="22" y2="22" />
    </svg>
  );
}

export function WebSearchBadge({
  webSearchAllowed,
  context,
  showReason = false,
  size = "sm",
}: WebSearchBadgeProps) {
  // INVARIANT I-1: disabled state must ONLY use --neutral tokens
  // Mixed context with web search allowed gets --llm (still not --pass/--info)
  let mod: string;
  let label: string;

  if (!webSearchAllowed) {
    // Always neutral regardless of context — never reads as enabled
    mod = "neutral";
    label = "Web Search 無効";
  } else if (context === "mixed") {
    // public claim only — warm amber to signal partial/conditional
    mod = "llm";
    label = "public claim のみ";
  } else {
    // fully enabled (public context)
    mod = "pass";
    label = "Web Search 有効";
  }

  const sizeClass = size === "lg" ? "web-search-badge--lg" : "";
  const describedBy = !webSearchAllowed && showReason ? TOOLTIP_ID : undefined;

  return (
    <span
      className={`web-search-badge web-search-badge--${mod} ${sizeClass}`.trim()}
      aria-describedby={describedBy}
      title={!webSearchAllowed && showReason ? "機密区分により無効" : undefined}
    >
      {webSearchAllowed ? <SearchIcon /> : <SearchOffIcon />}
      {label}
      {!webSearchAllowed && showReason && (
        <span id={TOOLTIP_ID} className="web-search-badge__reason" role="tooltip">
          機密区分により無効
        </span>
      )}
    </span>
  );
}
