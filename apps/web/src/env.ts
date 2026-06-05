function getApiBaseUrl() {
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;

  if (!apiBaseUrl) {
    throw new Error("VITE_API_BASE_URL is required.");
  }

  return apiBaseUrl;
}

export const env = {
  get apiBaseUrl() {
    return getApiBaseUrl();
  },
} as const;
