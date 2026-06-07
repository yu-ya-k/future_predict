/**
 * Shared component prop contracts (ui_plan.md D3).
 *
 * This file is the interface boundary between the Visual Design Lead (who
 * implements the component bodies + CSS) and the Architecture/Integration Lead
 * (who consumes them in screens). Do not change a prop shape without updating
 * both sides. Each component lives in its own file under components/ and is
 * re-exported from components/index.ts.
 */

import type { ReactNode } from "react";
import type {
  Citation,
  HumanReviewAction,
  ReviewRecord,
  RunStatus,
  Verdict,
} from "../types";

// 3-1 StatusPill
export interface StatusPillProps {
  status: RunStatus;
  /** Force-disable progress animation (screenshots etc.). */
  staticMode?: boolean;
}

// 3-4 VerdictBadge
export interface VerdictBadgeProps {
  verdict: Verdict;
}

// 3-5 ScoreChip
export interface ScoreChipProps {
  score: number; // 0–100
  animate?: boolean;
}

// 3-6 CostMeter
export interface CostMeterProps {
  estimated: number;
  /** Compact bar for header (I-5). */
  compact?: boolean;
}

// 3-7 PipelineStepper
export type PipelineStep = "brief" | "research" | "review" | "finalize";
export interface PipelineStepperProps {
  currentStep: PipelineStep;
  loopCount: number;
  maxIterations: number;
  completedSteps: PipelineStep[];
}

// 3-8 ReviewHistoryItem
export interface ReviewHistoryItemProps {
  review: ReviewRecord;
  showTrend?: boolean;
  previousScore?: number;
}

// 3-9 MetricCard
export interface MetricCardProps {
  label: string;
  value: string | number;
  unit?: string;
  warn?: boolean;
  icon?: string; // Tabler icon name (optional)
}

// 3-10 DecisionButton
export type DecisionTone = "success" | "warning" | "danger" | "neutral";
export interface DecisionButtonProps {
  action?: HumanReviewAction;
  label: string;
  consequence?: string;
  tone: DecisionTone;
  costHint?: string;
  disabled?: boolean;
  guardMessage?: string;
  disabledReason?: string;
  /** Render as a full-width block (SCR-4) vs inline button. */
  block?: boolean;
  type?: "button" | "submit";
  onClick?: () => void;
}

// 3-11 WaitBanner
export interface WaitBannerProps {
  elapsedMinutes: number;
  startedAt?: string;
  totalToolCalls: number;
}

// 3-12 SourceListItem
export interface SourceListItemProps {
  citation: Citation;
  index: number; // citation number
}

// 3-13 EmptyState
export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  icon?: string;
}

// Extra — minimal Markdown renderer (SCR-4 / SCR-5; no new dependency)
export interface MarkdownProps {
  source: string;
  /** Render `[n]` as a clickable citation jump. */
  onCitationClick?: (index: number) => void;
}

// Extra — loading skeleton placeholder
export interface SkeletonProps {
  /** CSS width, e.g. "100%", "8rem". */
  width?: string;
  height?: string;
  /** Number of stacked lines. */
  lines?: number;
  className?: string;
}

// Extra — generic tone label chip (e.g. boolean review flags in SCR-6)
export interface FlagChipProps {
  active: boolean;
  label: string;
  tone?: "pass" | "deep" | "neutral";
}

export type { ReactNode };
