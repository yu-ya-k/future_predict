const STORAGE_KEY = "dro.researchApiKey";

export function getResearchApiKey(): string | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)?.trim();
    return value ? value : null;
  } catch {
    return null;
  }
}

export function saveResearchApiKey(value: string): void {
  const normalized = value.trim();
  try {
    if (normalized) {
      localStorage.setItem(STORAGE_KEY, normalized);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* localStorage may be unavailable in restricted browser contexts */
  }
}

export function clearResearchApiKey(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* localStorage may be unavailable in restricted browser contexts */
  }
}
