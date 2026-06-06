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

export function loadResearchDefaults(): ResearchDefaults {
  try {
    const raw = localStorage.getItem(RESEARCH_DEFAULTS_STORAGE_KEY);
    if (!raw) return { ...FACTORY_RESEARCH_DEFAULTS };

    const parsed = JSON.parse(raw) as Partial<ResearchDefaults>;
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
    localStorage.setItem(RESEARCH_DEFAULTS_STORAGE_KEY, JSON.stringify(defaults));
  } catch {
    /* ignore storage errors */
  }
}

function isStaleSavedFactoryDefaults(defaults: Partial<ResearchDefaults>): boolean {
  return (
    defaults.max_targeted_rerun_runs === STALE_SAVED_FACTORY_DEFAULTS.max_targeted_rerun_runs &&
    defaults.max_full_rerun_runs === STALE_SAVED_FACTORY_DEFAULTS.max_full_rerun_runs &&
    defaults.max_llm_patch_runs === STALE_SAVED_FACTORY_DEFAULTS.max_llm_patch_runs &&
    defaults.max_verification_runs === STALE_SAVED_FACTORY_DEFAULTS.max_verification_runs &&
    defaults.max_total_iterations === STALE_SAVED_FACTORY_DEFAULTS.max_total_iterations &&
    defaults.max_total_tool_calls === STALE_SAVED_FACTORY_DEFAULTS.max_total_tool_calls
  );
}

function normalizeResearchDefaults(defaults: Partial<ResearchDefaults>): ResearchDefaults {
  return {
    max_targeted_rerun_runs:
      defaults.max_targeted_rerun_runs ??
      FACTORY_RESEARCH_DEFAULTS.max_targeted_rerun_runs,
    max_full_rerun_runs:
      defaults.max_full_rerun_runs ?? FACTORY_RESEARCH_DEFAULTS.max_full_rerun_runs,
    max_llm_patch_runs:
      defaults.max_llm_patch_runs ?? FACTORY_RESEARCH_DEFAULTS.max_llm_patch_runs,
    max_verification_runs:
      defaults.max_verification_runs ?? FACTORY_RESEARCH_DEFAULTS.max_verification_runs,
    max_total_iterations:
      defaults.max_total_iterations ?? FACTORY_RESEARCH_DEFAULTS.max_total_iterations,
    max_total_tool_calls:
      defaults.max_total_tool_calls ?? FACTORY_RESEARCH_DEFAULTS.max_total_tool_calls,
  };
}
