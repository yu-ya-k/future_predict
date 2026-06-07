import type { SourceListItemProps } from "./types";
import { toSafeHttpUrl } from "../utils/safeUrl";

export function SourceListItem({ citation, index }: SourceListItemProps) {
  const title = citation.title ?? citation.url ?? "(no title)";
  const url = citation.url;
  const safeUrl = toSafeHttpUrl(url);
  const sourceType = citation.source_type;
  const retrievedAt = citation.retrieved_at;

  let dateStr: string | null = null;
  if (retrievedAt) {
    try {
      dateStr = new Date(retrievedAt).toLocaleDateString("ja-JP", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      dateStr = retrievedAt;
    }
  }

  return (
    <div className="source-list-item">
      <span className="source-list-item__index" aria-label={`引用 ${index}`}>
        [{index}]
      </span>
      <div className="source-list-item__content">
        <div className="source-list-item__title-row">
          <span className="source-list-item__title" title={title}>
            {title}
          </span>
          {sourceType && (
            <span className="source-list-item__type-badge">{sourceType}</span>
          )}
        </div>
        {url && safeUrl && (
          <a
            className="source-list-item__url"
            href={safeUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`${title} を開く`}
          >
            {url}
          </a>
        )}
        {url && !safeUrl && <span className="source-list-item__url">{url}</span>}
        {dateStr && (
          <span className="source-list-item__date">{dateStr} 取得</span>
        )}
      </div>
    </div>
  );
}
