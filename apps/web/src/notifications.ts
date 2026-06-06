/**
 * Browser completion notifications (ui_plan.md A6 / A8 Q-5).
 *
 * There is no server-side notification channel, so we use the browser
 * Notification API to satisfy the "leave and come back" requirement (I-2).
 * Permission is requested at run-start time; notifications fire when polling
 * observes a transition to `completed` or `needs_human_review`.
 */

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function notificationPermission(): NotificationPermission | "unsupported" {
  if (!notificationsSupported()) return "unsupported";
  return Notification.permission;
}

export async function requestNotificationPermission(): Promise<void> {
  if (!notificationsSupported()) return;
  if (Notification.permission === "default") {
    try {
      await Notification.requestPermission();
    } catch {
      /* some browsers reject the promiseless form; ignore */
    }
  }
}

export function notify(title: string, body: string): void {
  if (!notificationsSupported() || Notification.permission !== "granted") return;
  try {
    new Notification(title, { body, tag: "dro-run-update" });
  } catch {
    /* ignore (e.g. notifications disabled at OS level) */
  }
}
