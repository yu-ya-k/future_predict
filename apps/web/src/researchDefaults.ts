import { OPTION_BOUNDS } from "./types";

export interface ResearchDefaults {
  max_targeted_rerun_runs: number;
  max_full_rerun_runs: number;
  max_llm_patch_runs: number;
  max_verification_runs: number;
  max_total_iterations: number;
  max_total_tool_calls: number;
}

export const RESEARCH_DEFAULTS_STORAGE_KEY = "dro.defaults";

export const FACTORY_RESEARCH_DEFAULTS: ResearchDefaults = {
  max_targeted_rerun_runs: 2,
  max_full_rerun_runs: 1,
  max_llm_patch_runs: 3,
  max_verification_runs: 3,
  max_total_iterations: 5,
  max_total_tool_calls: 120,
};

const STALE_SAVED_FACTORY_DEFAULTS: ResearchDefaults = {
  max_targeted_rerun_runs: 3,
  max_full_rerun_runs: 1,
  max_llm_patch_runs: 3,
  max_verification_runs: 3,
  max_total_iterations: 10,
  max_total_tool_calls: 200,
};

const RESEARCH_DEFAULT_KEYS = [
  "max_targeted_rerun_runs",
  "max_full_rerun_runs",
  "max_llm_patch_runs",
  "max_verification_runs",
  "max_total_iterations",
  "max_total_tool_calls",
] as const satisfies readonly (keyof ResearchDefaults)[];

export function loadResearchDefaults(): ResearchDefaults {
  try {
    const raw = localStorage.getItem(RESEARCH_DEFAULTS_STORAGE_KEY);
    if (!raw) return { ...FACTORY_RESEARCH_DEFAULTS };

    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) return { ...FACTORY_RESEARCH_DEFAULTS };

    if (isStaleSavedFactoryDefaults(parsed)) {
      saveResearchDefaults(FACTORY_RESEARCH_DEFAULTS);
      return { ...FACTORY_RESEARCH_DEFAULTS };
    }

    return normalizeResearchDefaults(parsed);
  } catch {
    return { ...FACTORY_RESEARCH_DEFAULTS };
  }
}

export function saveResearchDefaults(defaults: ResearchDefaults): void {
  try {
    localStorage.setItem(
      RESEARCH_DEFAULTS_STORAGE_KEY,
      JSON.stringify(normalizeResearchDefaults(defaults)),
    );
  } catch {
    /* ignore storage errors */
  }
}

export function normalizeResearchDefaultValue(
  key: keyof ResearchDefaults,
  value: unknown,
): number {
  const fallback = FACTORY_RESEARCH_DEFAULTS[key];
  const numeric =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : fallback;
  const finite = Number.isFinite(numeric) ? numeric : fallback;
  const integer = Math.trunc(finite);
  const bounds = OPTION_BOUNDS[key];
  return Math.min(Math.max(integer, bounds.min), bounds.max);
}

export function normalizeResearchDefaults(
  defaults: Partial<Record<keyof ResearchDefaults, unknown>>,
): ResearchDefaults {
  return RESEARCH_DEFAULT_KEYS.reduce((normalized, key) => {
    normalized[key] = normalizeResearchDefaultValue(key, defaults[key]);
    return normalized;
  }, {} as ResearchDefaults);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStaleSavedFactoryDefaults(defaults: Partial<Record<keyof ResearchDefaults, unknown>>): boolean {
  return (
    defaults.max_targeted_rerun_runs === STALE_SAVED_FACTORY_DEFAULTS.max_targeted_rerun_runs &&
    defaults.max_full_rerun_runs === STALE_SAVED_FACTORY_DEFAULTS.max_full_rerun_runs &&
    defaults.max_llm_patch_runs === STALE_SAVED_FACTORY_DEFAULTS.max_llm_patch_runs &&
    defaults.max_verification_runs === STALE_SAVED_FACTORY_DEFAULTS.max_verification_runs &&
    defaults.max_total_iterations === STALE_SAVED_FACTORY_DEFAULTS.max_total_iterations &&
    defaults.max_total_tool_calls === STALE_SAVED_FACTORY_DEFAULTS.max_total_tool_calls
  );
}
