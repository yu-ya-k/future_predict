/**
 * SCR-6: Audit Log — full internal state viewer.
 *
 * One-shot fetch of getAudit (GAP-6). Six tabs:
 *  Attempts / Reviews / Tool calls / Citations / Cost / Human decisions
 *
 * Reviews tab MUST surface can_be_fixed_by_llm and requires_new_external_research
 * prominently (FlagChip per I-4).
 * Human decisions tab is reviewer-scoped — data comes from AuditResponse, no
 * extra header needed here (already included in the audit payload).
 * EmptyState per empty tab. Mono styling for ids.
 */

import { useState } from "react";

import {
  BackLink,
  EmptyState,
  FlagChip,
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
            {/* I-4: Surfaced prominently */}
            <FlagChip
              active={r.can_be_fixed_by_llm}
              label="LLMで修正可能"
              tone={r.can_be_fixed_by_llm ? "pass" : "neutral"}
            />
            <FlagChip
              active={r.requires_new_external_research}
              label="新たな外部調査が必要"
              tone={r.requires_new_external_research ? "deep" : "neutral"}
            />
          </div>
          <p className="audit-review-rationale">{r.rationale}</p>
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
