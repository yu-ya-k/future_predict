/**
 * SCR-5: Report Viewer — final/latest report with citations.
 *
 * - getReport: uses final_report ?? report; null → empty/loading state.
 * - getCitations: right-column source list with source_type filter tabs.
 * - getReviews: quality badge row (VerdictBadge + ScoreChip + iterations).
 * - Export: MD download (Blob) + PDF via window.print (client-side, per 整合注記).
 * - Warnings banner from report.warnings.
 * - onCitationClick scrolls the right column.
 */

import { useRef } from "react";

import {
  EmptyState,
  Markdown,
  ScoreChip,
  Skeleton,
  SourceListItem,
  VerdictBadge,
} from "../components";
import { getReport, getCitations, getReviews } from "../api/research";
import { usePolling } from "../hooks/usePolling";
import { type Citation } from "../types";
import { useState } from "react";

interface ReportViewerProps {
  runId: string;
}

const SOURCE_TYPE_ALL = "all";

function uniqueSourceTypes(citations: Citation[]): string[] {
  const types = new Set<string>();
  for (const c of citations) {
    if (c.source_type) types.add(c.source_type);
  }
  return Array.from(types).sort();
}

export function ReportViewer({ runId }: ReportViewerProps) {
  const [activeSourceType, setActiveSourceType] = useState<string>(SOURCE_TYPE_ALL);
  const sourcePanelRef = useRef<HTMLDivElement | null>(null);

  // ── Report ──────────────────────────────────────────────────────────────────

  const { data: report, loading: reportLoading } = usePolling({
    fetcher: (signal) => getReport(runId, signal),
    interval: (data) => {
      // Stop polling once we have a final_report
      if (data?.final_report) return null;
      return 15_000;
    },
  });

  // ── Citations ───────────────────────────────────────────────────────────────

  const { data: citations } = usePolling({
    fetcher: (signal) => getCitations(runId, signal),
    interval: () => 30_000,
  });

  // ── Reviews (for quality row) ───────────────────────────────────────────────

  const { data: reviews } = usePolling({
    fetcher: (signal) => getReviews(runId, signal),
    interval: () => 30_000,
  });

  // ── Derived ─────────────────────────────────────────────────────────────────

  const reportText = report?.final_report ?? report?.report ?? null;
  const allCitations: Citation[] = citations ?? [];
  const latestReview = reviews && reviews.length > 0
    ? reviews.reduce((a, b) => (a.review_no > b.review_no ? a : b))
    : null;
  const totalReviews = reviews?.length ?? 0;

  const sourceTypes = uniqueSourceTypes(allCitations);
  const filteredCitations =
    activeSourceType === SOURCE_TYPE_ALL
      ? allCitations
      : allCitations.filter((c) => c.source_type === activeSourceType);

  // Scroll right column to nth citation
  function handleCitationClick(index: number) {
    if (!sourcePanelRef.current) return;
    const items = sourcePanelRef.current.querySelectorAll("[data-citation-index]");
    const target = items[index - 1]; // 1-based index
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  // ── Export helpers ───────────────────────────────────────────────────────────

  function handleMdDownload() {
    if (!reportText) return;
    const blob = new Blob([reportText], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report-${runId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handlePdfPrint() {
    window.print();
  }

  return (
    <div className="screen-report">
      {/* ── Header ──────────────────────────────────────── */}
      <header className="report-header">
        <div className="report-header-left">
          <h1 className="screen-title">レポート</h1>
          <p className="report-run-id">{runId}</p>
        </div>

        {/* Quality badge row */}
        {latestReview && (
          <div className="report-quality-row" aria-label="品質スコア">
            <VerdictBadge verdict={latestReview.verdict} />
            <ScoreChip score={latestReview.score} />
            <span className="report-iteration-count">
              レビュー {totalReviews}回
            </span>
          </div>
        )}

        <div className="report-export-buttons">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={handleMdDownload}
            disabled={!reportText}
          >
            MD ダウンロード
          </button>
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={handlePdfPrint}
          >
            PDF 印刷
          </button>
        </div>
      </header>

      {/* ── Warnings ────────────────────────────────────── */}
      {report?.warnings && report.warnings.length > 0 && (
        <div className="report-warnings" role="alert">
          <strong>警告:</strong>
          <ul>
            {report.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Two-column layout ────────────────────────────── */}
      <div className="report-layout">
        {/* Left: Markdown body */}
        <main className="report-body" aria-label="レポート本文">
          {reportLoading && !report ? (
            <div className="report-loading">
              <Skeleton width="80%" height="24px" />
              <Skeleton width="100%" height="16px" lines={6} />
              <Skeleton width="60%" height="16px" />
            </div>
          ) : reportText ? (
            <Markdown source={reportText} onCitationClick={handleCitationClick} />
          ) : (
            <EmptyState
              title="レポートがまだありません"
              description="runが完了すると、ここにレポートが表示されます。"
              icon="ti-file-text"
            />
          )}
        </main>

        {/* Right: Citations */}
        <aside className="report-sources" aria-label="引用ソース一覧">
          <div className="sources-header">
            <h2 className="sources-title">引用ソース</h2>

            {/* Source type filter tabs */}
            {sourceTypes.length > 0 && (
              <div className="source-type-tabs" role="tablist" aria-label="ソースタイプ">
                <button
                  role="tab"
                  aria-selected={activeSourceType === SOURCE_TYPE_ALL}
                  className={`source-type-tab${activeSourceType === SOURCE_TYPE_ALL ? " source-type-tab--active" : ""}`}
                  onClick={() => setActiveSourceType(SOURCE_TYPE_ALL)}
                >
                  すべて ({allCitations.length})
                </button>
                {sourceTypes.map((type) => {
                  const count = allCitations.filter((c) => c.source_type === type).length;
                  return (
                    <button
                      key={type}
                      role="tab"
                      aria-selected={activeSourceType === type}
                      className={`source-type-tab${activeSourceType === type ? " source-type-tab--active" : ""}`}
                      onClick={() => setActiveSourceType(type)}
                    >
                      {type} ({count})
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="sources-list" ref={sourcePanelRef}>
            {filteredCitations.length === 0 ? (
              <EmptyState
                title="引用ソースなし"
                description="このフィルターに一致するソースはありません。"
              />
            ) : (
              filteredCitations.map((citation, i) => (
                <div
                  key={i}
                  data-citation-index={i + 1}
                >
                  <SourceListItem citation={citation} index={i + 1} />
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
