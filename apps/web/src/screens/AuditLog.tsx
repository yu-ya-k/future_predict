/**
 * SCR-6: Audit Log — full internal state viewer.
 *
 * One-shot fetch of getAudit (GAP-6). Six tabs:
 *  Attempts / Reviews / Tool calls / Citations / Cost / Human decisions
 *
 * Reviews tab surfaces vFinal item assessments and recommended actions.
 * Human decisions tab is reviewer-scoped — data comes from AuditResponse, no
 * extra header needed here (already included in the audit payload).
 * EmptyState per empty tab. Mono styling for ids.
 */

import { useState } from "react";

import {
  BackLink,
  EmptyState,
  ScoreChip,
  Skeleton,
  VerdictBadge,
} from "../components";
import { getAudit } from "../api/research";
import { usePolling } from "../hooks/usePolling";
import { routes } from "../router";
import type { AuditResponse } from "../types";

type TabId = "attempts" | "reviews" | "tool-calls" | "citations" | "cost" | "human-decisions";

const TABS: { id: TabId; label: string }[] = [
  { id: "attempts", label: "調査試行" },
  { id: "reviews", label: "レビュー" },
  { id: "tool-calls", label: "ツール呼び出し" },
  { id: "citations", label: "引用" },
  { id: "cost", label: "コスト" },
  { id: "human-decisions", label: "人間の判断" },
];

interface AuditLogProps {
  runId: string;
}

const ACTION_LABELS: Record<string, string> = {
  none: "対応なし",
  llm_patch: "LLM patch",
  verify: "Verify",
  targeted_rerun: "Targeted rerun",
  full_rerun: "Full rerun",
  human_review: "Human review",
  finalize_with_limitation: "制約付き完了",
  revise_items: "Item見直し",
};

const ITEM_STATUS_LABELS: Record<string, string> = {
  answered: "回答済み",
  partial: "一部回答",
  unanswered: "未回答",
  unverifiable: "確認不能",
  out_of_scope: "対象外",
};

const ITEM_SEVERITY_LABELS: Record<string, string> = {
  blocker: "Blocker",
  major: "Major",
  minor: "Minor",
};

function actionSummary(items: AuditResponse["reviews"][number]["item_assessments"]) {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.recommended_action, (counts.get(item.recommended_action) ?? 0) + 1);
  }
  return Array.from(counts.entries()).sort(([a], [b]) => a.localeCompare(b));
}

// ── Tab contents ──────────────────────────────────────────────────────────────

function AttemptsTab({ data }: { data: AuditResponse }) {
  if (data.attempts.length === 0) {
    return <EmptyState title="調査試行なし" description="まだ調査試行がありません。" />;
  }
  return (
    <div className="audit-table-wrap">
      <table className="audit-table">
        <thead>
          <tr>
            <th>#</th>
            <th>ステータス</th>
            <th>モデル</th>
            <th>レスポンスID</th>
            <th>エラー</th>
          </tr>
        </thead>
        <tbody>
          {data.attempts.map((a) => (
            <tr key={a.run_no}>
              <td className="mono">{a.run_no}</td>
              <td>{a.status}</td>
              <td className="mono">{a.model}</td>
              <td className="mono">{a.response_id ?? "—"}</td>
              <td>{a.error ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewsTab({ data }: { data: AuditResponse }) {
  if (data.reviews.length === 0) {
    return <EmptyState title="レビューなし" description="まだレビューがありません。" />;
  }
  return (
    <div className="reviews-tab-list">
      {data.reviews.map((r) => (
        <div key={r.review_no} className="audit-review-item">
          <div className="audit-review-header">
            <span className="mono audit-review-no">#{r.review_no}</span>
            <VerdictBadge verdict={r.verdict} />
            <ScoreChip score={r.score} />
            {actionSummary(r.item_assessments).map(([action, count]) => (
              <span key={action} className="audit-action-chip">
                {ACTION_LABELS[action] ?? action} {count}
              </span>
            ))}
            {r.item_assessments.length === 0 && (
              <span className="audit-action-chip audit-action-chip--empty">
                Item assessmentなし
              </span>
            )}
          </div>
          <p className="audit-review-rationale">{r.rationale}</p>
          {r.item_assessments.length > 0 && (
            <details className="audit-review-items" open>
              <summary>ResearchItem評価 ({r.item_assessments.length}件)</summary>
              <div className="audit-review-item-list">
                {r.item_assessments.map((item) => (
                  <div key={item.item_id} className="audit-review-item-row">
                    <div className="audit-review-item-main">
                      <span className="mono">{item.item_id}</span>
                      <span>{ITEM_STATUS_LABELS[item.status] ?? item.status}</span>
                      <span>{ITEM_SEVERITY_LABELS[item.severity] ?? item.severity}</span>
                      <span>{ACTION_LABELS[item.recommended_action] ?? item.recommended_action}</span>
                    </div>
                    <p>{item.rationale}</p>
                  </div>
                ))}
              </div>
            </details>
          )}
          {r.gaps.length > 0 && (
            <details className="audit-review-gaps">
              <summary>ギャップ ({r.gaps.length}件)</summary>
              <ul>
                {r.gaps.map((gap, i) => (
                  <li key={i}>{gap}</li>
                ))}
              </ul>
            </details>
          )}
          {r.reviewer_response_id && (
            <p className="audit-review-response-id mono">{r.reviewer_response_id}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function ToolCallsTab({ data }: { data: AuditResponse }) {
  if (data.tool_calls.length === 0) {
    return (
      <EmptyState title="ツール呼び出しなし" description="ツール呼び出しの記録がありません。" />
    );
  }
  return (
    <div className="audit-table-wrap">
      <table className="audit-table">
        <thead>
          <tr>
            <th>タイプ</th>
            <th>ステータス</th>
            <th>クエリ / URL</th>
            <th>所要時間 (ms)</th>
            <th>ステップ</th>
          </tr>
        </thead>
        <tbody>
          {data.tool_calls.map((tc, i) => (
            <tr key={i}>
              <td className="mono">{tc.type}</td>
              <td>{tc.status ?? "—"}</td>
              <td className="audit-truncate">{tc.query ?? tc.url ?? "—"}</td>
              <td className="mono">{tc.duration_ms != null ? tc.duration_ms : "—"}</td>
              <td className="mono">{tc.step ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CitationsTab({ data }: { data: AuditResponse }) {
  if (data.citations.length === 0) {
    return <EmptyState title="引用なし" description="引用の記録がありません。" />;
  }
  return (
    <div className="audit-table-wrap">
      <table className="audit-table">
        <thead>
          <tr>
            <th>#</th>
            <th>タイトル</th>
            <th>URL</th>
            <th>ソースタイプ</th>
          </tr>
        </thead>
        <tbody>
          {data.citations.map((c, i) => (
            <tr key={i}>
              <td className="mono">{i + 1}</td>
              <td>{c.title ?? "—"}</td>
              <td>
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noopener noreferrer" className="audit-link">
                    {c.url}
                  </a>
                ) : (
                  "—"
                )}
              </td>
              <td>{c.source_type ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CostTab({ data }: { data: AuditResponse }) {
  if (data.cost_events.length === 0) {
    return <EmptyState title="コストイベントなし" description="コスト記録がありません。" />;
  }
  const total = data.cost_events.reduce((s, e) => s + e.estimated_cost_usd, 0);
  return (
    <div>
      <p className="audit-cost-total">
        合計推定コスト: <strong>${total.toFixed(4)}</strong>
      </p>
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th>ステップ</th>
              <th>モデル</th>
              <th>入力トークン</th>
              <th>出力トークン</th>
              <th>ツール呼び出し</th>
              <th>コスト (USD)</th>
              <th>日時</th>
            </tr>
          </thead>
          <tbody>
            {data.cost_events.map((e, i) => (
              <tr key={i}>
                <td className="mono">{e.step}</td>
                <td className="mono">{e.model}</td>
                <td className="mono">{e.input_tokens.toLocaleString()}</td>
                <td className="mono">{e.output_tokens.toLocaleString()}</td>
                <td className="mono">{e.tool_calls}</td>
                <td className="mono">${e.estimated_cost_usd.toFixed(4)}</td>
                <td className="audit-date">{e.created_at ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HumanDecisionsTab({ data }: { data: AuditResponse }) {
  if (data.human_decisions.length === 0) {
    return (
      <EmptyState
        title="人間の判断なし"
        description="まだ人間による判断が記録されていません。"
      />
    );
  }
  return (
    <div className="audit-table-wrap">
      <table className="audit-table">
        <thead>
          <tr>
            <th>#</th>
            <th>アクション</th>
            <th>コメント</th>
            <th>日時</th>
          </tr>
        </thead>
        <tbody>
          {data.human_decisions.map((d) => (
            <tr key={d.decision_no}>
              <td className="mono">{d.decision_no}</td>
              <td>{d.action}</td>
              <td>{d.comment ?? "—"}</td>
              <td className="audit-date">{d.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── AuditLog ──────────────────────────────────────────────────────────────────

export function AuditLog({ runId }: AuditLogProps) {
  const [activeTab, setActiveTab] = useState<TabId>("attempts");

  const { data, loading, error, refetch } = usePolling<AuditResponse>({
    fetcher: (signal) => getAudit(runId, signal),
    key: `audit:${runId}`,
    // One-shot fetch (GAP-6: audit is append-only; refetch on tab change if needed)
    interval: () => null,
  });

  if (loading && !data) {
    return (
      <div className="screen-audit">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <div className="audit-skeleton">
          <Skeleton width="50%" height="28px" />
          <Skeleton width="100%" height="40px" />
          <Skeleton width="100%" height="300px" />
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="screen-audit">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <div className="audit-error" role="alert">
          <p>監査ログの取得に失敗しました。</p>
          <button type="button" className="btn-secondary" onClick={refetch}>
            再試行
          </button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="screen-audit">
      <header className="audit-header">
        <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
        <h1 className="screen-title">監査ログ</h1>
        <p className="audit-run-id mono">{runId}</p>
      </header>

      {/* ── Tab bar ─────────────────────────────────────── */}
      <div className="audit-tabs" role="tablist" aria-label="監査ログタブ">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`audit-panel-${tab.id}`}
            id={`audit-tab-${tab.id}`}
            className={`audit-tab${activeTab === tab.id ? " audit-tab--active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab panels ──────────────────────────────────── */}
      <div
        id={`audit-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`audit-tab-${activeTab}`}
        className="audit-panel"
      >
        {activeTab === "attempts" && <AttemptsTab data={data} />}
        {activeTab === "reviews" && <ReviewsTab data={data} />}
        {activeTab === "tool-calls" && <ToolCallsTab data={data} />}
        {activeTab === "citations" && <CitationsTab data={data} />}
        {activeTab === "cost" && <CostTab data={data} />}
        {activeTab === "human-decisions" && <HumanDecisionsTab data={data} />}
      </div>
    </div>
  );
}
