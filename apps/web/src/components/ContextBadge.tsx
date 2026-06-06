import type { ContextBadgeProps } from "./types";
import type { ContextClassification } from "../types";

interface BadgeConfig {
  label: string;
  mod: string;
  icon: "world" | "building" | "lock" | "layers";
}

const BADGE_CONFIG: Record<ContextClassification, BadgeConfig> = {
  public: { label: "public", mod: "info", icon: "world" },
  internal: { label: "internal", mod: "neutral", icon: "building" },
  confidential: { label: "confidential", mod: "neutral", icon: "lock" },
  mixed: { label: "mixed", mod: "llm", icon: "layers" },
};

function WorldIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

function BuildingIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="1" />
      <path d="M3 9h18M9 21V9" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

function LayersIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 17 12 22 22 17" />
      <polyline points="2 12 12 17 22 12" />
    </svg>
  );
}

const ICONS = {
  world: WorldIcon,
  building: BuildingIcon,
  lock: LockIcon,
  layers: LayersIcon,
};

export function ContextBadge({ context, showLock = false }: ContextBadgeProps) {
  const config = BADGE_CONFIG[context];
  const showLockIcon = context === "confidential" || showLock;
  const IconComponent = ICONS[config.icon];

  return (
    <span className={`context-badge context-badge--${config.mod}`}>
      <IconComponent />
      {config.label}
      {showLockIcon && context !== "confidential" && <LockIcon />}
    </span>
  );
}
