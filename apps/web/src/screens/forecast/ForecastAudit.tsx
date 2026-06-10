import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { EmptyState } from "../../components";
import { getForecastAudit } from "../../api/forecast";
import type { ForecastAuditResponse } from "../../types";
import { formatForecastError } from "./errors";

type AuditTab = "versions" | "probability" | "policy" | "reviews" | "simulation";

const TABS: Array<{ id: AuditTab; label: string }> = [
  { id: "versions", label: "バージョン" },
  { id: "probability", label: "確率" },
  { id: "policy", label: "ポリシー判断" },
  { id: "reviews", label: "レビュー" },
  { id: "simulation", label: "シミュレーション実行" },
];

const PROBABILITY_EVENT_TYPES = new Set([
  "probabilities_computed",
  "version_committed",
  "forecast_resolved",
]);

const EVENT_TYPE_LABELS: Record<string, string> = {
  probabilities_computed: "確率を計算",
  version_committed: "予測版を確定",
  forecast_resolved: "実績結果で解決",
};

const REVIEW_ACTION_LABELS: Record<string, string> = {
  approve_framing: "フレーミング承認",
  approve_phase_a_version: "PhaseAバージョン承認",
  approve_claim_target_links: "主張と結果の対応を承認",
  approve_private_data_use: "非公開情報の利用を承認",
  approve_probability_publication: "確率の公開を承認",
  override_probability_with_reason: "確率を理由付きで上書き",
  approve_external_report: "外部レポートを承認",
  approve_trusted_source: "信頼できる情報源を承認",
};

const POLICY_DECISION_LABELS: Record<string, string> = {
  allowed: "許可",
  blocked: "ブロック",
  needs_review: "要レビュー",
};

const DATE_FORMATTER = new Intl.DateTimeFormat("ja-JP", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function formatAuditDate(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return DATE_FORMATTER.format(date);
}

function asString(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string") return value.length > 0 ? value : "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function labelFor(map: Record<string, string>, value: unknown): string {
  if (typeof value === "string" && value in map) return map[value];
  return asString(value);
}

function DetailsJson({ value }: { value: unknown }) {
  return (
    <details className="audit-review-items">
      <summary>詳細(JSON)を表示</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function VersionsPanel({ audit }: { audit: ForecastAuditResponse | null }) {
  const versions = audit?.versions ?? [];
  if (versions.length === 0) {
    return (
      <EmptyState
        title="バージョン記録はまだありません"
        description="予測版を確定すると、ここに履歴が表示されます。"
      />
    );
  }
  return (
    <>
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th scope="col">バージョンID</th>
              <th scope="col">推定セットID</th>
              <th scope="col">入力スナップショットハッシュ</th>
              <th scope="col">確定日時</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((version, index) => (
              <tr key={asString(version.version_id) + index}>
                <td className="mono">{asString(version.version_id)}</td>
                <td className="mono">{asString(version.estimate_set_id)}</td>
                <td className="mono audit-truncate">
                  {asString(version.input_snapshot_hash)}
                </td>
                <td className="audit-date">{formatAuditDate(version.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <DetailsJson value={versions} />
    </>
  );
}

function ProbabilityPanel({ audit }: { audit: ForecastAuditResponse | null }) {
  const events = (audit?.events ?? []).filter((event) =>
    PROBABILITY_EVENT_TYPES.has(event.event_type),
  );
  if (events.length === 0) {
    return (
      <EmptyState
        title="確率関連の記録はまだありません"
        description="確率の計算・版確定・解決を行うと、ここに履歴が表示されます。"
      />
    );
  }
  return (
    <>
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th scope="col">種別</th>
              <th scope="col">日時</th>
              <th scope="col">要約</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.event_id}>
                <td>{labelFor(EVENT_TYPE_LABELS, event.event_type)}</td>
                <td className="audit-date">{formatAuditDate(event.created_at)}</td>
                <td className="audit-truncate">{JSON.stringify(event.event_json)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <DetailsJson value={events} />
    </>
  );
}

function PolicyPanel({ audit }: { audit: ForecastAuditResponse | null }) {
  const decisions = audit?.policy_decisions ?? [];
  if (decisions.length === 0) {
    return (
      <EmptyState
        title="ポリシー判断の記録はまだありません"
        description="公開情報の収集を行うと、ポリシー判断がここに記録されます。"
      />
    );
  }
  return (
    <>
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th scope="col">プロファイル</th>
              <th scope="col">判断</th>
              <th scope="col">ステータス</th>
              <th scope="col">理由</th>
              <th scope="col">日時</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((decision, index) => (
              <tr key={asString(decision.policy_decision_id) + index}>
                <td className="mono">{asString(decision.profile)}</td>
                <td>{labelFor(POLICY_DECISION_LABELS, decision.decision)}</td>
                <td>{asString(decision.status)}</td>
                <td className="audit-truncate">{asString(decision.reason)}</td>
                <td className="audit-date">{formatAuditDate(decision.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <DetailsJson value={decisions} />
    </>
  );
}

function ReviewsPanel({ audit }: { audit: ForecastAuditResponse | null }) {
  const reviews = audit?.reviews ?? [];
  if (reviews.length === 0) {
    return (
      <EmptyState
        title="レビュー記録はまだありません"
        description="承認・差し戻しを行うと、ここに履歴が表示されます。"
      />
    );
  }
  return (
    <>
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th scope="col">アクション</th>
              <th scope="col">日時</th>
              <th scope="col">コメント</th>
              <th scope="col">レビュアー</th>
            </tr>
          </thead>
          <tbody>
            {reviews.map((review, index) => (
              <tr key={asString(review.review_id) + index}>
                <td>{labelFor(REVIEW_ACTION_LABELS, review.action)}</td>
                <td className="audit-date">{formatAuditDate(review.created_at)}</td>
                <td className="audit-truncate">{asString(review.comment)}</td>
                <td>{asString(review.reviewer)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <DetailsJson value={reviews} />
    </>
  );
}

export function ForecastAudit({ forecastId }: { forecastId: string }) {
  const [audit, setAudit] = useState<ForecastAuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<AuditTab>("versions");
  const tabRefs = useRef<Record<AuditTab, HTMLButtonElement | null>>({
    versions: null,
    probability: null,
    policy: null,
    reviews: null,
    simulation: null,
  });

  const load = useCallback(() => {
    setError(null);
    void getForecastAudit(forecastId)
      .then(setAudit)
      .catch((err) => setError(formatForecastError(err)));
  }, [forecastId]);

  useEffect(() => {
    load();
  }, [load]);

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    let nextIndex: number;
    if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = TABS.length - 1;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const currentIndex = TABS.findIndex((tab) => tab.id === activeTab);
      const delta = event.key === "ArrowRight" ? 1 : -1;
      nextIndex = (currentIndex + delta + TABS.length) % TABS.length;
    } else {
      return;
    }
    event.preventDefault();
    const nextTab = TABS[nextIndex].id;
    setActiveTab(nextTab);
    tabRefs.current[nextTab]?.focus();
  }

  return (
    <section className="screen">
      <div className="screen-header">
        <div>
          <h1>Forecast監査</h1>
          <p className="screen-subtitle">{forecastId}</p>
        </div>
      </div>
      {error && (
        <div className="alert alert-error" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          <p>{error}</p>
          <button type="button" className="btn-secondary" onClick={load}>
            再読み込み
          </button>
        </div>
      )}
      <div className="form-panel">
        <div className="audit-tabs" role="tablist" aria-label="Forecast audit tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`forecast-audit-tab-${tab.id}`}
              aria-selected={activeTab === tab.id}
              aria-controls={`forecast-audit-panel-${tab.id}`}
              tabIndex={activeTab === tab.id ? 0 : -1}
              ref={(node) => {
                tabRefs.current[tab.id] = node;
              }}
              className={`audit-tab${activeTab === tab.id ? " audit-tab--active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={handleTabKeyDown}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {TABS.map((tab) => (
          <div
            key={tab.id}
            id={`forecast-audit-panel-${tab.id}`}
            role="tabpanel"
            aria-labelledby={`forecast-audit-tab-${tab.id}`}
            tabIndex={0}
            className="audit-panel"
            hidden={tab.id !== activeTab}
          >
            {tab.id === "versions" && (
              <>
                <h2>バージョン</h2>
                <VersionsPanel audit={audit} />
              </>
            )}
            {tab.id === "probability" && (
              <>
                <h2>確率</h2>
                <ProbabilityPanel audit={audit} />
              </>
            )}
            {tab.id === "policy" && (
              <>
                <h2>ポリシー判断</h2>
                <PolicyPanel audit={audit} />
              </>
            )}
            {tab.id === "reviews" && (
              <>
                <h2>レビュー</h2>
                <ReviewsPanel audit={audit} />
              </>
            )}
            {tab.id === "simulation" && (
              <>
                <h2>シミュレーション実行</h2>
                <p className="muted">Phase Cで利用します。</p>
              </>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
