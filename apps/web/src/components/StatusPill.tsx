import type { StatusPillProps } from "./types";
import type { RunStatus } from "../types";

interface PillConfig {
  label: string;
  mod: string; // BEM modifier
  animate: "spin" | "pulse" | "none";
}

const PILL_CONFIG: Record<RunStatus, PillConfig> = {
  queued: { label: "待機中", mod: "neutral", animate: "none" },
  submitted: { label: "処理中", mod: "info", animate: "spin" },
  waiting_deep_research: { label: "調査中", mod: "info", animate: "pulse" },
  collecting: { label: "収集中", mod: "info", animate: "spin" },
  reviewing: { label: "レビュー中", mod: "info", animate: "spin" },
  needs_action: { label: "対応待ち", mod: "human", animate: "none" },
  needs_human_review: { label: "要対応", mod: "human", animate: "none" },
  completed: { label: "完了", mod: "pass", animate: "none" },
  cancelled: { label: "キャンセル", mod: "neutral", animate: "none" },
  failed: { label: "失敗", mod: "error", animate: "none" },
};

/** Spinner SVG — 16×16, decorative */
function SpinnerIcon() {
  return (
    <svg
      className="status-pill__icon status-pill__icon--spin"
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  );
}

/** Dot icon for static states that need a small marker */
function DotIcon({ mod }: { mod: string }) {
  if (mod === "pass") {
    return (
      <svg
        className="status-pill__icon"
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <polyline points="20 6 9 17 4 12" />
      </svg>
    );
  }
  if (mod === "error") {
    return (
      <svg
        className="status-pill__icon"
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    );
  }
  return null;
}

export function StatusPill({ status, staticMode = false }: StatusPillProps) {
  const config = PILL_CONFIG[status];
  const isPulsing = !staticMode && config.animate === "pulse";
  const isSpinning = !staticMode && config.animate === "spin";

  const classes = [
    "status-pill",
    `status-pill--${config.mod}`,
    isPulsing ? "status-pill--pulsing" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={classes}>
      {isSpinning && <SpinnerIcon />}
      {!isSpinning && <DotIcon mod={config.mod} />}
      {config.label}
    </span>
  );
}
