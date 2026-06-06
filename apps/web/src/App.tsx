/**
 * App shell — sticky header, skip link, router switch, reviewer-id inline editor.
 *
 * Reviewer-scoped screens (review, dashboard queue) receive a prompt when no
 * reviewer id is set, rather than throwing an error.
 */

import { useState, useSyncExternalStore } from "react";

import { Link, navigate, routes, useRoute } from "./router";
import { getReviewerId, setReviewerId, clearReviewerId, subscribeReviewer } from "./reviewer";
import {
  NewResearch,
  Dashboard,
  RunMonitor,
  HumanReview,
  ReportViewer,
  AuditLog,
  Settings,
} from "./screens";
import "./App.css";

// ── Reviewer-ID indicator + inline editor ─────────────────────────────────────

function ReviewerIdControl() {
  const reviewerId = useSyncExternalStore(subscribeReviewer, getReviewerId);
  const [editing, setEditing] = useState(false);
  const [input, setInput] = useState("");

  function startEdit() {
    setInput(reviewerId ?? "");
    setEditing(true);
  }

  function handleSave() {
    const trimmed = input.trim();
    if (trimmed) {
      setReviewerId(trimmed);
    } else {
      clearReviewerId();
    }
    setEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") setEditing(false);
  }

  if (editing) {
    return (
      <div className="reviewer-editor" role="group" aria-label="レビュアーID設定">
        <input
          type="text"
          className="reviewer-editor-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="レビュアーIDを入力"
          aria-label="レビュアーID"
          autoFocus
        />
        <button type="button" className="reviewer-editor-save" onClick={handleSave}>
          保存
        </button>
        <button
          type="button"
          className="reviewer-editor-cancel"
          onClick={() => setEditing(false)}
        >
          キャンセル
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`reviewer-id-btn${reviewerId ? " reviewer-id-btn--set" : ""}`}
      onClick={startEdit}
      aria-label={reviewerId ? `レビュアーID: ${reviewerId}` : "レビュアーIDを設定"}
      title={reviewerId ? `レビュアーID: ${reviewerId}` : "レビュアーIDを設定"}
    >
      {reviewerId ? (
        <>
          <span className="reviewer-id-icon" aria-hidden="true">●</span>
          <span className="reviewer-id-label">{reviewerId}</span>
        </>
      ) : (
        <>
          <span className="reviewer-id-icon" aria-hidden="true">○</span>
          <span className="reviewer-id-label">レビュアーID</span>
        </>
      )}
    </button>
  );
}

// ── Not found ─────────────────────────────────────────────────────────────────

function NotFound() {
  return (
    <div className="not-found">
      <h1>ページが見つかりません</h1>
      <p>指定されたURLは存在しません。</p>
      <button
        type="button"
        className="btn-secondary"
        onClick={() => navigate(routes().dashboard)}
      >
        ダッシュボードへ戻る
      </button>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

export function App() {
  const route = useRoute();

  function renderScreen() {
    switch (route.name) {
      case "dashboard":
        return <Dashboard />;
      case "new":
        return <NewResearch />;
      case "monitor":
        return route.runId ? <RunMonitor runId={route.runId} /> : <NotFound />;
      case "review":
        return route.runId ? <HumanReview runId={route.runId} /> : <NotFound />;
      case "report":
        return route.runId ? <ReportViewer runId={route.runId} /> : <NotFound />;
      case "audit":
        return route.runId ? <AuditLog runId={route.runId} /> : <NotFound />;
      case "settings":
        return <Settings />;
      case "not-found":
      default:
        return <NotFound />;
    }
  }

  return (
    <>
      {/* Skip link for keyboard accessibility */}
      <a href="#main-content" className="skip-link">
        メインコンテンツへスキップ
      </a>

      {/* Sticky header */}
      <header className="app-header" role="banner">
        <div className="app-header-inner">
          <Link to={routes().dashboard} className="app-logo" aria-label="Deep Research Review Orchestrator ホーム">
            <span className="app-logo-mark" aria-hidden="true">◆</span>
            <span className="app-logo-text">Deep Research Review Orchestrator</span>
          </Link>

          <nav className="app-nav" aria-label="メインナビゲーション">
            <Link
              to={routes().dashboard}
              className={`nav-link${route.name === "dashboard" ? " nav-link--active" : ""}`}
            >
              ダッシュボード
            </Link>
            <Link
              to={routes().new}
              className={`nav-link${route.name === "new" ? " nav-link--active" : ""}`}
            >
              新規リサーチ
            </Link>
            <Link
              to={routes().settings}
              className={`nav-link${route.name === "settings" ? " nav-link--active" : ""}`}
            >
              設定
            </Link>
          </nav>

          <div className="app-header-controls">
            <ReviewerIdControl />
          </div>
        </div>
      </header>

      {/* Main content */}
      <main id="main-content" className="app-main">
        <div className="app-content">
          {renderScreen()}
        </div>
      </main>
    </>
  );
}
