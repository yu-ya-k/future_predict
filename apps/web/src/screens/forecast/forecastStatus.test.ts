import { describe, expect, it } from "vitest";

import type { ForecastStatus } from "../../types";
import {
  forecastStatusLabel,
  forecastStatusTone,
  localizePackStatus,
  PACK_STATUS_LABELS,
  type ForecastStatusTone,
} from "./forecastStatus";

describe("forecastStatus presentation helpers", () => {
  // The full ForecastStatus set with its expected Japanese label and pill tone.
  // Keep this in sync with FORECAST_STATUS_PRESENTATION in forecastStatus.ts.
  const cases: Array<{
    status: ForecastStatus;
    label: string;
    tone: ForecastStatusTone;
  }> = [
    { status: "framing_pending", label: "フレーミング待ち", tone: "human" },
    { status: "framing_approved", label: "フレーミング承認済み", tone: "info" },
    { status: "pack_running", label: "公開情報フェーズ", tone: "info" },
    { status: "evidence_ready", label: "証拠抽出済み", tone: "info" },
    { status: "scenarios_ready", label: "シナリオ生成済み", tone: "info" },
    { status: "draft_ready", label: "確率計算済み", tone: "info" },
    { status: "committed", label: "予測版確定済み", tone: "pass" },
    { status: "resolved", label: "解決済み", tone: "neutral" },
  ];

  it("covers every ForecastStatus value", () => {
    expect(cases).toHaveLength(8);
    expect(new Set(cases.map((entry) => entry.status)).size).toBe(8);
  });

  it.each(cases)(
    "labels and tones $status",
    ({ status, label, tone }) => {
      const rendered = forecastStatusLabel(status);
      expect(rendered).toBe(label);
      // Labels must be human Japanese, never the raw enum string.
      expect(rendered.length).toBeGreaterThan(0);
      expect(rendered).not.toBe(status);
      expect(forecastStatusTone(status)).toBe(tone);
    },
  );

  it("spot-checks the known label/tone mapping", () => {
    expect(forecastStatusTone("framing_pending")).toBe("human");
    expect(forecastStatusTone("committed")).toBe("pass");
    expect(forecastStatusTone("resolved")).toBe("neutral");
    // The middle pipeline states share the info tone.
    expect(forecastStatusTone("framing_approved")).toBe("info");
    expect(forecastStatusTone("pack_running")).toBe("info");
    expect(forecastStatusTone("evidence_ready")).toBe("info");
    expect(forecastStatusTone("scenarios_ready")).toBe("info");
    expect(forecastStatusTone("draft_ready")).toBe("info");
  });

  it("only emits real pill modifiers as tones", () => {
    const allowed: ForecastStatusTone[] = [
      "neutral",
      "info",
      "human",
      "pass",
      "error",
    ];
    for (const entry of cases) {
      expect(allowed).toContain(entry.tone);
    }
  });

  it("falls back to a loading label and neutral tone for undefined status", () => {
    expect(forecastStatusLabel(undefined)).toBe("読み込み中");
    expect(forecastStatusTone(undefined)).toBe("neutral");
  });
});

describe("localizePackStatus", () => {
  it("localizes every known pack status key", () => {
    const expected: Record<string, string> = {
      submitting: "サーバーに登録中",
      running: "実行中",
      completed: "完了",
      needs_human_review: "要確認",
      failed: "失敗",
      cancelled: "中断",
    };
    for (const [key, label] of Object.entries(PACK_STATUS_LABELS)) {
      expect(label).toBe(expected[key]);
      expect(localizePackStatus(key)).toBe(label);
    }
    // Guard against drift in the label table.
    expect(Object.keys(PACK_STATUS_LABELS).sort()).toEqual(
      Object.keys(expected).sort(),
    );
  });

  it("falls back to 未収集 for null, undefined, and unknown statuses", () => {
    expect(localizePackStatus(null)).toBe("未収集");
    expect(localizePackStatus(undefined)).toBe("未収集");
  });

  it("passes through an unrecognized non-empty status verbatim", () => {
    // Unknown but present statuses fall through to the raw value, not the default.
    expect(localizePackStatus("queued")).toBe("queued");
  });
});
