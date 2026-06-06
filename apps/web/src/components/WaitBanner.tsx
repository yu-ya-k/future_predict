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
  estimatedRemainingMinutes,
  totalToolCalls,
  canLeave = true,
}: WaitBannerProps) {
  const remaining = Math.max(0, Math.round(estimatedRemainingMinutes));
  const elapsed = Math.round(elapsedMinutes);

  return (
    // INVARIANT I-2: always visible during long-running jobs
    <div className="wait-banner" aria-live="polite" aria-atomic="false" role="status">
      <LoaderIcon />
      <div className="wait-banner__body">
        <p className="wait-banner__main">
          Deep Research をバックグラウンド実行中。
          残り約{" "}
          <strong className="wait-banner__highlight">{remaining}分</strong>
          {totalToolCalls > 0 && (
            <>
              、これまでに{" "}
              <strong className="wait-banner__highlight">{totalToolCalls} 件</strong>
              の処理ステップ完了
            </>
          )}
          。
        </p>
        <p className="wait-banner__sub">
          経過時間: {elapsed}分
          {canLeave && (
            <> — 完了したら通知します。この画面は閉じても大丈夫です。</>
          )}
        </p>
      </div>
    </div>
  );
}
