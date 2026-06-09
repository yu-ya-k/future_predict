export type ClipboardCopyResult = "success" | "fallback_success" | "failed";

export async function copyTextToClipboard(text: string): Promise<ClipboardCopyResult> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return "success";
    } catch {
      // Embedded browsers may expose Clipboard API but still reject writes.
    }
  }

  const activeElement =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);

  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    if (document.execCommand?.("copy")) {
      return "fallback_success";
    }
    return "failed";
  } catch {
    return "failed";
  } finally {
    textarea.remove();
    activeElement?.focus();
  }
}
