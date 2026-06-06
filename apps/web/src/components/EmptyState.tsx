import type { EmptyStateProps } from "./types";

function InboxIcon() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
      <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  );
}

export function EmptyState({ title, description, action, icon }: EmptyStateProps) {
  // icon is reserved for future Tabler icon rendering; currently renders a default inbox icon
  void icon;
  return (
    <div className="empty-state" role="status">
      <span className="empty-state__icon" aria-hidden="true">
        <InboxIcon />
      </span>
      <p className="empty-state__title">{title}</p>
      {description && (
        <p className="empty-state__description">{description}</p>
      )}
      {action && (
        <button
          type="button"
          className="empty-state__action"
          onClick={action.onClick}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
