/**
 * SCR-5: Report Viewer — Deep Research history.
 */

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";

import {
  EmptyState,
  BackLink,
  Markdown,
  Skeleton,
  SourceListItem,
} from "../components";
import { getReport, getCitations, getAttempts } from "../api/research";
import { usePolling } from "../hooks/usePolling";
import { navigate, routes, type ReportTab } from "../router";
import { type Citation, type ResearchAttempt } from "../types";

interface ReportViewerProps {
  runId: string;
  initialTab?: ReportTab;
  initialAttemptNo?: number | null;
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

function safeMarkdownFilename(runId: string, attemptNo: number | null): string {
  const safeRunId = runId.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  const suffix = attemptNo ? `deep-research-${attemptNo}` : "report";
  return `${safeRunId || "research-run"}-${suffix}.md`;
}

function attemptSourceLabel(source?: string | null): string {
  if (source === "manual_upload") return "手動取り込み";
  if (source === "manual_chatgpt_rerun") return "ChatGPT手動rerun";
  return "API";
}

function downloadMarkdown(filename: string, markdown: string) {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ReportViewer({
  runId,
  initialTab = "research",
  initialAttemptNo = null,
}: ReportViewerProps) {
  const [viewMode, setViewMode] = useState<ReportTab>(initialTab);
  const [activeSourceType, setActiveSourceType] = useState<string>(SOURCE_TYPE_ALL);
  const [selectedAttemptNo, setSelectedAttemptNo] = useState<number | null>(
    initialAttemptNo,
  );
  const [attemptSelectionMode, setAttemptSelectionMode] = useState<"latest" | "manual">(
    initialAttemptNo ? "manual" : "latest",
  );
  const sourcePanelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setViewMode(initialTab);
    if (initialAttemptNo) {
      setSelectedAttemptNo(initialAttemptNo);
      setAttemptSelectionMode("manual");
    }
  }, [initialTab, initialAttemptNo]);

  useEffect(() => {
    setSelectedAttemptNo(initialAttemptNo);
    setAttemptSelectionMode(initialAttemptNo ? "manual" : "latest");
    setActiveSourceType(SOURCE_TYPE_ALL);
  }, [initialAttemptNo, runId]);

  const { data: report, loading: reportLoading } = usePolling({
    fetcher: (signal) => getReport(runId, signal),
    key: `report:${runId}`,
    interval: (data) => {
      if (data?.final_report) return null;
      return 15_000;
    },
  });

  const { data: citations } = usePolling({
    fetcher: (signal) => getCitations(runId, signal),
    key: `citations:${runId}`,
    interval: () => 30_000,
  });

  const { data: attempts, loading: attemptsLoading } = usePolling({
    fetcher: (signal) => getAttempts(runId, signal),
    key: `attempts:${runId}`,
    interval: () => 30_000,
  });

  const researchVersions = useMemo(
    () => buildResearchReportVersions(Array.isArray(attempts) ? attempts : []),
    [attempts],
  );

  useEffect(() => {
    if (attemptSelectionMode === "latest") {
      if (selectedAttemptNo !== null) {
        setSelectedAttemptNo(null);
      }
    }
  }, [attemptSelectionMode, selectedAttemptNo]);

  useEffect(() => {
    setActiveSourceType(SOURCE_TYPE_ALL);
  }, [viewMode, selectedAttemptNo]);

  const selectedAttempt =
    selectedAttemptNo === null
      ? researchVersions[researchVersions.length - 1] ?? null
      : researchVersions.find((version) => version.run_no === selectedAttemptNo) ?? null;

  const finalReportText = report?.final_report || report?.report || null;
  const displayMode = initialAttemptNo === null ? "final" : "attempt";
  const displayText =
    displayMode === "final" ? finalReportText : selectedAttempt?.report || null;
  const displayLoading =
    displayMode === "final"
      ? reportLoading && !report
      : attemptsLoading && researchVersions.length === 0;
  const currentCitations =
    displayMode === "attempt" && selectedAttempt?.citations
      ? selectedAttempt.citations
      : (Array.isArray(citations) ? citations : []);
  const sourceTypes = uniqueSourceTypes(currentCitations);
  const citationItems = currentCitations.map((citation, i) => ({
    citation,
    index: i + 1,
  }));
  const filteredCitationItems =
    activeSourceType === SOURCE_TYPE_ALL
      ? citationItems
      : citationItems.filter(({ citation }) => citation.source_type === activeSourceType);

  const exportText = displayText;

  function handleAttemptSelect(runNo: number) {
    setSelectedAttemptNo(runNo);
    setAttemptSelectionMode("manual");
    navigate(routes().report(runId, { attempt: runNo }));
  }

  function handleCitationClick(index: number) {
    if (!sourcePanelRef.current) return;
    const target = sourcePanelRef.current.querySelector(
      `[data-citation-index="${index}"]`,
    );
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function handleMdDownload() {
    if (!exportText) return;
    downloadMarkdown(
      safeMarkdownFilename(
        runId,
        displayMode === "attempt" ? selectedAttempt?.run_no ?? null : null,
      ),
      exportText,
    );
  }

  return (
    <div className="screen-report">
      <header className="report-header">
        <div className="report-header-left">
          <BackLink to={routes().monitor(runId)} label="Runへ戻る" />
          <h1 className="screen-title">レポート履歴</h1>
          <p className="report-run-id">{runId}</p>
        </div>

        <div className="report-export-buttons">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={handleMdDownload}
            disabled={!exportText}
          >
            MD ダウンロード
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

      <div className="report-layout">
        <main
          className="report-body"
          aria-label={displayMode === "final" ? "最終レポート" : "Deep Research出力"}
        >
          {displayLoading ? (
            <div className="report-loading">
              <Skeleton width="80%" height="24px" />
              <Skeleton width="100%" height="16px" lines={6} />
              <Skeleton width="60%" height="16px" />
            </div>
          ) : displayText ? (
            <Markdown source={displayText} onCitationClick={handleCitationClick} />
          ) : displayMode === "attempt" ? (
            <EmptyState
              title={
                selectedAttemptNo
                  ? `Deep Research ${selectedAttemptNo}回目の出力なし`
                  : "Deep Research出力なし"
              }
              description={
                selectedAttemptNo
                  ? "この試行はまだ取得中か、履歴がまだ同期されていません。"
                  : "完了済みのDeep Research出力がまだありません。"
              }
              icon="ti-file-text"
            />
          ) : (
            <EmptyState
              title="レポートなし"
              description="表示できるレポートがまだありません。"
              icon="ti-file-text"
            />
          )}

          {displayMode === "attempt" && selectedAttempt && (
            <section className="report-history-detail" aria-label="Deep Research詳細">
              <h2 className="report-history-title">Deep Research {selectedAttempt.run_no}回目</h2>
              <dl className="report-history-meta">
                <div>
                  <dt>Status</dt>
                  <dd>{selectedAttempt.status}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>{attemptSourceLabel(selectedAttempt.source)}</dd>
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
              title:
                version.source === "manual_upload"
                  ? `${version.run_no}回目 手動取り込み`
                  : version.source === "manual_chatgpt_rerun"
                    ? `${version.run_no}回目 ChatGPT手動rerun`
                  : `${version.run_no}回目`,
              meta: `${version.status} / ${version.model}`,
            }))}
            selectedId={displayMode === "attempt" ? selectedAttempt?.run_no ?? null : null}
            onSelect={handleAttemptSelect}
            emptyTitle="履歴なし"
            emptyDescription="Deep Researchの履歴がまだありません。"
          />
          <SourcesAside
            title={displayMode === "final" ? "最終レポートの引用ソース" : "選択版の引用ソース"}
            citations={filteredCitationItems}
            allCitations={currentCitations}
            sourceTypes={sourceTypes}
            activeSourceType={activeSourceType}
            onSourceTypeChange={setActiveSourceType}
            sourcePanelRef={sourcePanelRef}
            embedded
          />
        </aside>
      </div>
    </div>
  );
}

interface SourcesAsideProps {
  title: string;
  citations: Array<{ citation: Citation; index: number }>;
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
          citations.map(({ citation, index }) => (
            <div key={index} data-citation-index={index}>
              <SourceListItem citation={citation} index={index} />
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
