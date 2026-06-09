import type { CSSProperties } from "react";

export type ForecastFlowStatus =
  | "done"
  | "active"
  | "submitting"
  | "blocked"
  | "available"
  | "pending";

export type ForecastFlowTone =
  | "brief"
  | "research"
  | "review"
  | "finalize"
  | "verify";

export interface ForecastFlowNode {
  id: string;
  title: string;
  meta: string;
  status: ForecastFlowStatus;
  statusLabel?: string;
  tone: ForecastFlowTone;
}

const FORECAST_FLOW_STATUS_LABEL: Record<ForecastFlowStatus, string> = {
  done: "完了",
  active: "実行中",
  submitting: "登録中",
  blocked: "要対応",
  available: "次に実行",
  pending: "待機",
};

export function ForecastFlowProgress({
  heading,
  summary,
  nodes,
  label = "Forecast作成フロー",
  layout = "grid",
  columns = 4,
}: {
  heading: string;
  summary: string;
  nodes: ForecastFlowNode[];
  label?: string;
  layout?: "grid" | "wrapped" | "timeline";
  columns?: number;
}) {
  const doneCount = nodes.filter((node) => node.status === "done").length;
  const currentNode = nodes.find((node) => node.status === "active");
  const submittingNode = nodes.find((node) => node.status === "submitting");
  const blockedNode = nodes.find((node) => node.status === "blocked");
  const availableNode = nodes.find((node) => node.status === "available");
  const statusLabelFor = (node: ForecastFlowNode) =>
    node.statusLabel ?? FORECAST_FLOW_STATUS_LABEL[node.status];
  const statusText = `${heading}。${doneCount}/${nodes.length}完了。${
    currentNode
      ? `現在実行中: ${currentNode.title}。`
      : submittingNode
        ? `${statusLabelFor(submittingNode)}: ${submittingNode.title}。`
        : blockedNode
          ? `対応が必要: ${blockedNode.title}。`
      : availableNode
        ? `次に実行: ${availableNode.title}。`
        : ""
  }`;
  const headingId = `forecast-flow-${nodes.map((node) => node.id).join("-")}-heading`;
  const columnCount = Math.max(1, Math.trunc(columns));
  const trackStyle = {
    "--forecast-flow-columns": String(columnCount),
  } as CSSProperties;

  return (
    <section
      className={`forecast-flow-progress forecast-flow-progress--${layout}`}
      aria-labelledby={headingId}
    >
      <p className="sr-only" aria-live="polite" role="status">
        {statusText}
      </p>
      <div className="forecast-flow-header">
        <div>
          <h3 id={headingId}>{heading}</h3>
          <p>{summary}</p>
        </div>
        <span className="forecast-flow-summary">
          {doneCount}/{nodes.length} 完了
        </span>
      </div>
      <ol className="forecast-flow-track" style={trackStyle} aria-label={label}>
        {nodes.map((node, index) => {
          const isWrapBreak =
            layout === "wrapped" && (index + 1) % columnCount === 0;
          return (
            <li className="forecast-flow-item" key={node.id}>
              <article
                className={[
                  "execution-dag-node",
                  "forecast-flow-node",
                  `execution-dag-node--${node.status}`,
                  `execution-dag-node--${node.tone}`,
                ].join(" ")}
              >
                <div className="execution-dag-node-topline">
                  <span className="execution-dag-dot" aria-hidden="true" />
                  <span className="execution-dag-state">
                    {statusLabelFor(node)}
                  </span>
                </div>
                <h4 className="execution-dag-title">{node.title}</h4>
                <p className="execution-dag-meta">{node.meta}</p>
              </article>
              {index < nodes.length - 1 && (
                <span
                  className={[
                    "forecast-flow-edge",
                    `forecast-flow-edge--${nodes[index + 1].status}`,
                    isWrapBreak ? "forecast-flow-edge--wrap-break" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
