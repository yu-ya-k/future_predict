/**
 * SCR-5: Report Viewer — Deep Research and review history.
 */

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";

import {
  EmptyState,
  BackLink,
  Markdown,
  ScoreChip,
  Skeleton,
  SourceListItem,
  VerdictBadge,
} from "../components";
import { getReport, getCitations, getReviews, getAttempts } from "../api/research";
import { usePolling } from "../hooks/usePolling";
import { routes, type ReportTab } from "../router";
import { type Citation, type ResearchAttempt, type ReviewRecord } from "../types";

interface ReportViewerProps {
  runId: string;
  initialTab?: ReportTab;
}

interface ResearchReportVersion extends ResearchAttempt {
  records: ResearchAttempt[];
}

const SOURCE_TYPE_ALL = "all";
function uniqueSourceTypes(citations: Citation[]): string[] {
  const types = new Set<string>();
  for (const c of citations) {
    if (c.source_type) types.add(c.source_type);
  }
  return Array.from(types).sort();
}

function buildResearchReportVersions(attempts: ResearchAttempt[]): ResearchReportVersion[] {
  const grouped = new Map<number, ResearchAttempt[]>();
  for (const attempt of attempts) {
    const group = grouped.get(attempt.run_no) ?? [];
    group.push(attempt);
    grouped.set(attempt.run_no, group);
  }

  return Array.from(grouped.entries())
    .sort(([a], [b]) => a - b)
    .map(([runNo, records]) => {
      const prompt = records.find((record) => record.prompt.trim())?.prompt ?? "";
      const output =
        [...records].reverse().find((record) => record.report.trim()) ??
        records[records.length - 1];
      return {
        ...output,
        run_no: runNo,
        prompt: prompt || output.prompt,
        records,
      };
    });
}

function formatReviewMarkdown(review: ReviewRecord | null): string | null {
  if (!review) return null;
  const lines = [
    `# レビュー #${review.review_no}`,
    "",
    `- Verdict: ${review.verdict}`,
    `- Score: ${review.score}`,
    `- Recommended route: ${review.recommended_route}`,
    `- Reviewer confidence: ${review.reviewer_confidence}%`,
    "",
    "## Rationale",
    review.rationale,
    "",
    "## Gaps",
    ...(review.gaps.length > 0 ? review.gaps.map((gap) => `- ${gap}`) : ["- なし"]),
    "",
    "## Factuality Concerns",
    ...(
      review.factuality_concerns.length > 0
        ? review.factuality_concerns.map((concern) => `- ${concern}`)
        : ["- なし"]
    ),
    "",
    "## Source Quality Concerns",
    ...(
      review.source_quality_concerns.length > 0
        ? review.source_quality_concerns.map((concern) => `- ${concern}`)
        : ["- なし"]
    ),
    "",
    "## Next Instructions",
    review.next_instructions ?? "なし",
  ];
  return lines.join("\n");
}

export function ReportViewer({ runId, initialTab = "research" }: ReportViewerProps) {
  const [viewMode, setViewMode] = useState<ReportTab>(initialTab);
  const [activeSourceType, setActiveSourceType] = useState<string>(SOURCE_TYPE_ALL);
  const [selectedAttemptNo, setSelectedAttemptNo] = useState<number | null>(null);
  const [selectedReviewNo, setSelectedReviewNo] = useState<number | null>(null);
  const [attemptSelectionMode, setAttemptSelectionMode] = useState<"latest" | "manual">("latest");
  const [reviewSelectionMode, setReviewSelectionMode] = useState<"latest" | "manual">("latest");
  const sourcePanelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setViewMode(initialTab);
  }, [initialTab]);

  useEffect(() => {
    setSelectedAttemptNo(null);
    setSelectedReviewNo(null);
    setAttemptSelectionMode("latest");
    setReviewSelectionMode("latest");
    setActiveSourceType(SOURCE_TYPE_ALL);
  }, [runId]);

  const { data: report, loading: reportLoading } = usePolling({
    fetcher: (signal) => getReport(runId, signal),
    interval: (data) => {
      if (data?.final_report) return null;
      return 15_000;
    },
  });

  const { data: citations } = usePolling({
    fetcher: (signal) => getCitations(runId, signal),
    interval: () => 30_000,
  });

  const { data: reviews } = usePolling({
    fetcher: (signal) => getReviews(runId, signal),
    interval: () => 30_000,
  });

  const { data: attempts } = usePolling({
    fetcher: (signal) => getAttempts(runId, signal),
    interval: () => 30_000,
  });

  const sortedReviews = useMemo(
    () => (
      Array.isArray(reviews)
        ? [...reviews].sort((a, b) => a.review_no - b.review_no)
        : []
    ),
    [reviews],
  );
  const latestReview = sortedReviews.length > 0 ? sortedReviews[sortedReviews.length - 1] : null;
  const researchVersions = useMemo(
    () => buildResearchReportVersions(Array.isArray(attempts) ? attempts : []),
    [attempts],
  );
  const latestAttemptNo =
    researchVersions.length > 0 ? researchVersions[researchVersions.length - 1].run_no : null;
  const latestReviewNo = latestReview?.review_no ?? null;

  useEffect(() => {
    if (researchVersions.length === 0) {
      setSelectedAttemptNo(null);
      return;
    }
    if (attemptSelectionMode === "latest") {
      if (selectedAttemptNo !== latestAttemptNo) {
        setSelectedAttemptNo(latestAttemptNo);
      }
      return;
    }
    if (!researchVersions.some((version) => version.run_no === selectedAttemptNo)) {
      setSelectedAttemptNo(latestAttemptNo);
      setAttemptSelectionMode("latest");
    }
  }, [attemptSelectionMode, latestAttemptNo, researchVersions, selectedAttemptNo]);

  useEffect(() => {
    if (sortedReviews.length === 0) {
      setSelectedReviewNo(null);
      return;
    }
    if (reviewSelectionMode === "latest") {
      if (selectedReviewNo !== latestReviewNo) {
        setSelectedReviewNo(latestReviewNo);
      }
      return;
    }
    if (!sortedReviews.some((review) => review.review_no === selectedReviewNo)) {
      setSelectedReviewNo(latestReviewNo);
      setReviewSelectionMode("latest");
    }
  }, [latestReviewNo, reviewSelectionMode, selectedReviewNo, sortedReviews]);

  useEffect(() => {
    setActiveSourceType(SOURCE_TYPE_ALL);
  }, [viewMode, selectedAttemptNo]);

  const selectedAttempt =
    researchVersions.find((version) => version.run_no === selectedAttemptNo) ??
    researchVersions[researchVersions.length - 1] ??
    null;
  const selectedReview =
    sortedReviews.find((review) => review.review_no === selectedReviewNo) ??
    latestReview;

  const currentCitations = selectedAttempt?.citations ?? (Array.isArray(citations) ? citations : []);
  const sourceTypes = uniqueSourceTypes(currentCitations);
  const filteredCitations =
    activeSourceType === SOURCE_TYPE_ALL
      ? currentCitations
      : currentCitations.filter((c) => c.source_type === activeSourceType);

  const exportText =
    viewMode === "reviews"
      ? formatReviewMarkdown(selectedReview)
      : selectedAttempt?.report || null;

  function handleAttemptSelect(runNo: number) {
    setSelectedAttemptNo(runNo);
    setAttemptSelectionMode(runNo === latestAttemptNo ? "latest" : "manual");
  }

  function handleReviewSelect(reviewNo: number) {
    setSelectedReviewNo(reviewNo);
    setReviewSelectionMode(reviewNo === latestReviewNo ? "latest" : "manual");
  }

  function handleCitationClick(index: number) {
    if (!sourcePanelRef.current) return;
    const items = sourcePanelRef.current.querySelectorAll("[data-citation-index]");
    const target = items[index - 1];
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function handleMdDownload() {
    if (!exportText) return;
    const blob = new Blob([exportText], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${viewMode}-${runId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handlePdfPrint() {
    window.print();
  }

  return (
    <div className="screen-report">
      <header className="report-header">
        <div className="report-header-left">
          <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
          <h1 className="screen-title">
            {viewMode === "reviews" ? "レビュー内容" : "レポート履歴"}
          </h1>
          <p className="report-run-id">{runId}</p>
        </div>

        {viewMode === "reviews" && latestReview && (
          <div className="report-quality-row" aria-label="品質スコア">
            <VerdictBadge verdict={latestReview.verdict} />
            <ScoreChip score={latestReview.score} />
            <span className="report-iteration-count">
              レビュー {sortedReviews.length}回
            </span>
          </div>
        )}

        <div className="report-export-buttons">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={handleMdDownload}
            disabled={!exportText}
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

      {viewMode === "research" && (
        <div className="report-layout">
          <main className="report-body" aria-label="Deep Research出力">
            {reportLoading && !report && researchVersions.length === 0 ? (
              <div className="report-loading">
                <Skeleton width="80%" height="24px" />
                <Skeleton width="100%" height="16px" lines={6} />
                <Skeleton width="60%" height="16px" />
              </div>
            ) : selectedAttempt?.report ? (
              <Markdown source={selectedAttempt.report} onCitationClick={handleCitationClick} />
            ) : (
              <EmptyState
                title="Deep Research出力なし"
                description="完了済みのDeep Research出力がまだありません。"
                icon="ti-file-text"
              />
            )}

            {selectedAttempt && (
              <section className="report-history-detail" aria-label="Deep Research詳細">
                <h2 className="report-history-title">Deep Research {selectedAttempt.run_no}回目</h2>
                <dl className="report-history-meta">
                  <div>
                    <dt>Status</dt>
                    <dd>{selectedAttempt.status}</dd>
                  </div>
                  <div>
                    <dt>Model</dt>
                    <dd>{selectedAttempt.model}</dd>
                  </div>
                  {selectedAttempt.response_id && (
                    <div>
                      <dt>Response</dt>
                      <dd className="mono">{selectedAttempt.response_id}</dd>
                    </div>
                  )}
                </dl>
                {selectedAttempt.error && (
                  <div className="report-history-error" role="alert">
                    {selectedAttempt.error}
                  </div>
                )}
                <details className="report-history-prompt">
                  <summary>Deep Researchへの指示内容</summary>
                  <pre>{selectedAttempt.prompt}</pre>
                </details>
              </section>
            )}
          </main>

          <aside className="report-sources" aria-label="Deep Research履歴一覧">
            <h2 className="sources-title">Deep Research履歴</h2>
            <VersionList
              versions={researchVersions.map((version) => ({
                id: version.run_no,
                title: `${version.run_no}回目`,
                meta: `${version.status} / ${version.model}`,
              }))}
              selectedId={selectedAttempt?.run_no ?? null}
              onSelect={handleAttemptSelect}
              emptyTitle="履歴なし"
              emptyDescription="Deep Researchの履歴がまだありません。"
            />
            <SourcesAside
              title="選択版の引用ソース"
              citations={filteredCitations}
              allCitations={currentCitations}
              sourceTypes={sourceTypes}
              activeSourceType={activeSourceType}
              onSourceTypeChange={setActiveSourceType}
              sourcePanelRef={sourcePanelRef}
              embedded
            />
          </aside>
        </div>
      )}

      {viewMode === "reviews" && (
        <div className="report-layout">
          <main className="report-body" aria-label="レビュー内容">
            {selectedReview ? (
              <ReviewDetail review={selectedReview} />
            ) : (
              <EmptyState
                title="レビュー履歴なし"
                description="レビューが完了すると、ここに表示されます。"
                icon="ti-clipboard-check"
              />
            )}
          </main>

          <aside className="report-sources" aria-label="レビュー履歴一覧">
            <h2 className="sources-title">レビュー履歴</h2>
            <VersionList
              versions={sortedReviews.map((review) => ({
                id: review.review_no,
                title: `${review.review_no}回目`,
                meta: `${review.verdict} / ${review.score}`,
              }))}
              selectedId={selectedReview?.review_no ?? null}
              onSelect={handleReviewSelect}
              emptyTitle="レビュー履歴なし"
              emptyDescription="レビューが完了すると、ここに表示されます。"
            />
          </aside>
        </div>
      )}
    </div>
  );
}

interface SourcesAsideProps {
  title: string;
  citations: Citation[];
  allCitations: Citation[];
  sourceTypes: string[];
  activeSourceType: string;
  onSourceTypeChange: (sourceType: string) => void;
  sourcePanelRef: RefObject<HTMLDivElement | null>;
  embedded?: boolean;
}

function SourcesAside({
  title,
  citations,
  allCitations,
  sourceTypes,
  activeSourceType,
  onSourceTypeChange,
  sourcePanelRef,
  embedded = false,
}: SourcesAsideProps) {
  return (
    <aside
      className={embedded ? "report-history-sources" : "report-sources"}
      aria-label={title}
    >
      <div className="sources-header">
        <h2 className="sources-title">{title}</h2>

        {sourceTypes.length > 0 && (
          <div className="source-type-tabs" role="tablist" aria-label="ソースタイプ">
            <button
              role="tab"
              aria-selected={activeSourceType === SOURCE_TYPE_ALL}
              className={`source-type-tab${activeSourceType === SOURCE_TYPE_ALL ? " source-type-tab--active" : ""}`}
              onClick={() => onSourceTypeChange(SOURCE_TYPE_ALL)}
            >
              すべて ({allCitations.length})
            </button>
            {sourceTypes.map((type) => (
              <button
                key={type}
                role="tab"
                aria-selected={activeSourceType === type}
                className={`source-type-tab${activeSourceType === type ? " source-type-tab--active" : ""}`}
                onClick={() => onSourceTypeChange(type)}
              >
                {type} ({allCitations.filter((c) => c.source_type === type).length})
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="sources-list" ref={sourcePanelRef}>
        {citations.length === 0 ? (
          <EmptyState
            title="引用ソースなし"
            description="このフィルターに一致するソースはありません。"
          />
        ) : (
          citations.map((citation, i) => (
            <div key={i} data-citation-index={i + 1}>
              <SourceListItem citation={citation} index={i + 1} />
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

interface VersionListProps {
  versions: Array<{ id: number; title: string; meta: string }>;
  selectedId: number | null;
  onSelect: (id: number) => void;
  emptyTitle: string;
  emptyDescription: string;
}

function VersionList({
  versions,
  selectedId,
  onSelect,
  emptyTitle,
  emptyDescription,
}: VersionListProps) {
  if (versions.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="report-version-list">
      {[...versions].reverse().map((version) => (
        <button
          key={version.id}
          type="button"
          className={`report-version-button${selectedId === version.id ? " report-version-button--active" : ""}`}
          onClick={() => onSelect(version.id)}
        >
          <span>{version.title}</span>
          <span>{version.meta}</span>
        </button>
      ))}
    </div>
  );
}

function ReviewDetail({ review }: { review: ReviewRecord }) {
  return (
    <article className="report-review-detail">
      <div className="report-review-detail-header">
        <div>
          <h2 className="report-history-title">レビュー {review.review_no}回目</h2>
          {review.reviewer_response_id && (
            <p className="report-history-response mono">{review.reviewer_response_id}</p>
          )}
        </div>
        <div className="report-quality-row">
          <VerdictBadge verdict={review.verdict} />
          <ScoreChip score={review.score} />
        </div>
      </div>

      <dl className="report-history-meta">
        <div>
          <dt>Recommended route</dt>
          <dd>{review.recommended_route}</dd>
        </div>
        <div>
          <dt>Goal achieved</dt>
          <dd>{review.goal_achieved ? "yes" : "no"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{review.reviewer_confidence}%</dd>
        </div>
        {review.report_hash && (
          <div>
            <dt>Report hash</dt>
            <dd className="mono">{review.report_hash}</dd>
          </div>
        )}
      </dl>

      <section className="report-review-section">
        <h3>Rationale</h3>
        <p>{review.rationale}</p>
      </section>
      <ReviewList title="未解決のギャップ" items={review.gaps} />
      <ReviewList title="事実確認の懸念" items={review.factuality_concerns} />
      <ReviewList title="ソース品質の懸念" items={review.source_quality_concerns} />
      <ReviewList title="高リスクフラグ" items={review.high_risk_flags} />
      <section className="report-review-section">
        <h3>次回実行への改善点</h3>
        <p>{review.next_instructions ?? "なし"}</p>
      </section>
    </article>
  );
}

function ReviewList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="report-review-section">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p>なし</p>
      ) : (
        <ul>
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
