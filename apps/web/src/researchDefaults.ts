export interface ResearchDefaults {
  max_deep_research_runs: number;
  max_llm_fix_runs: number;
  max_total_iterations: number;
  max_no_progress_rounds: number;
  max_cost_usd: number;
  max_total_tool_calls: number;
}

export const RESEARCH_DEFAULTS_STORAGE_KEY = "dro.defaults";

export const FACTORY_RESEARCH_DEFAULTS: ResearchDefaults = {
  max_deep_research_runs: 2,
  max_llm_fix_runs: 3,
  max_total_iterations: 5,
  max_no_progress_rounds: 2,
  max_cost_usd: 20.0,
  max_total_tool_calls: 120,
};

const LEGACY_FACTORY_RESEARCH_DEFAULTS: ResearchDefaults = {
  max_deep_research_runs: 3,
  max_llm_fix_runs: 3,
  max_total_iterations: 10,
  max_no_progress_rounds: 3,
  max_cost_usd: 5.0,
  max_total_tool_calls: 200,
};

export function loadResearchDefaults(): ResearchDefaults {
  try {
    const raw = localStorage.getItem(RESEARCH_DEFAULTS_STORAGE_KEY);
    if (!raw) return { ...FACTORY_RESEARCH_DEFAULTS };

    const parsed = JSON.parse(raw) as Partial<ResearchDefaults>;
    if (isLegacyFactoryDefaults(parsed)) {
      saveResearchDefaults(FACTORY_RESEARCH_DEFAULTS);
      return { ...FACTORY_RESEARCH_DEFAULTS };
    }

    return { ...FACTORY_RESEARCH_DEFAULTS, ...parsed };
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

function isLegacyFactoryDefaults(defaults: Partial<ResearchDefaults>): boolean {
  return (
    defaults.max_deep_research_runs === LEGACY_FACTORY_RESEARCH_DEFAULTS.max_deep_research_runs &&
    defaults.max_llm_fix_runs === LEGACY_FACTORY_RESEARCH_DEFAULTS.max_llm_fix_runs &&
    defaults.max_total_iterations === LEGACY_FACTORY_RESEARCH_DEFAULTS.max_total_iterations &&
    defaults.max_no_progress_rounds === LEGACY_FACTORY_RESEARCH_DEFAULTS.max_no_progress_rounds &&
    defaults.max_cost_usd === LEGACY_FACTORY_RESEARCH_DEFAULTS.max_cost_usd &&
    defaults.max_total_tool_calls === LEGACY_FACTORY_RESEARCH_DEFAULTS.max_total_tool_calls
  );
}
