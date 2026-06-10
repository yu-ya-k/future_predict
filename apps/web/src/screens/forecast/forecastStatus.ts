/**
 * Shared Forecast status presentation helpers.
 *
 * Centralises the Japanese label and the semantic `status-pill` modifier for
 * every ForecastStatus so the dashboard, detail header and any future surface
 * render the same colour and wording. Keep this the single source of truth —
 * do not re-derive status labels inline.
 */

import type { ForecastStatus } from "../../types";

/** Semantic pill modifier (matches `.status-pill--*` in components.css). */
export type ForecastStatusTone = "neutral" | "info" | "human" | "pass" | "error";

interface ForecastStatusPresentation {
  label: string;
  tone: ForecastStatusTone;
}

const FORECAST_STATUS_PRESENTATION: Record<ForecastStatus, ForecastStatusPresentation> = {
  framing_pending: { label: "フレーミング待ち", tone: "human" },
  framing_approved: { label: "フレーミング承認済み", tone: "info" },
  pack_running: { label: "公開情報フェーズ", tone: "info" },
  evidence_ready: { label: "証拠抽出済み", tone: "info" },
  scenarios_ready: { label: "シナリオ生成済み", tone: "info" },
  draft_ready: { label: "確率計算済み", tone: "info" },
  committed: { label: "予測版確定済み", tone: "pass" },
  resolved: { label: "解決済み", tone: "neutral" },
};

/** Human-readable Japanese label for a forecast status. */
export function forecastStatusLabel(status: ForecastStatus | undefined): string {
  if (status && status in FORECAST_STATUS_PRESENTATION) {
    return FORECAST_STATUS_PRESENTATION[status].label;
  }
  return "読み込み中";
}

/** Semantic tone (pill modifier) for a forecast status. */
export function forecastStatusTone(status: ForecastStatus | undefined): ForecastStatusTone {
  if (status && status in FORECAST_STATUS_PRESENTATION) {
    return FORECAST_STATUS_PRESENTATION[status].tone;
  }
  return "neutral";
}

export const PACK_STATUS_LABELS: Record<string, string> = {
  submitting: "サーバーに登録中",
  running: "実行中",
  completed: "完了",
  needs_human_review: "要確認",
  failed: "失敗",
  cancelled: "中断",
};

/** Localize a research pack status. Missing status falls back to "未収集". */
export function localizePackStatus(status: string | null | undefined): string {
  if (status === null || status === undefined) return "未収集";
  return PACK_STATUS_LABELS[status] ?? status;
}
