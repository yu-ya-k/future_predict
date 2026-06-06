import type { WaitBannerProps } from "./types";

function LoaderIcon() {
  return (
    <svg
      className="wait-banner__loader"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  );
}

export function WaitBanner({
  elapsedMinutes,
  startedAt,
  totalToolCalls,
}: WaitBannerProps) {
  const elapsed = Math.round(elapsedMinutes);
  const startedLabel = formatStartedAt(startedAt);

  return (
    // INVARIANT I-2: always visible during long-running jobs
    <div className="wait-banner" aria-live="polite" aria-atomic="false" role="status">
      <LoaderIcon />
      <div className="wait-banner__body">
        <p className="wait-banner__main">
          Deep Research をバックグラウンド実行中。
          {totalToolCalls > 0 && (
            <>
              {" "}これまでに{" "}
              <strong className="wait-banner__highlight">{totalToolCalls} 件</strong>
              の処理ステップが完了しています。
            </>
          )}
        </p>
        <p className="wait-banner__sub">
          今回の経過時間: {elapsed}分
          {startedLabel && (
            <> ・ 開始時刻: {startedLabel}</>
          )}
        </p>
      </div>
    </div>
  );
}

function formatStartedAt(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
