import type { PipelineStepperProps, PipelineStep } from "./types";

const STEP_LABELS: Record<PipelineStep, string> = {
  brief: "ブリーフ作成",
  research: "調査",
  review: "レビュー",
  finalize: "最終化",
};

const STEPS: PipelineStep[] = ["brief", "research", "review", "finalize"];

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg className="pipeline-stepper__spinner" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  );
}

interface StepNodeProps {
  step: PipelineStep;
  state: "completed" | "active" | "pending";
  loopCount: number;
  maxIterations: number;
}

function StepNode({ step, state, loopCount, maxIterations }: StepNodeProps) {
  const isResearch = step === "research";
  // INVARIANT I-3: loop count ring visible on research node
  const showLoop = isResearch && loopCount >= 1;
  const loopIntense = loopCount >= 2;

  return (
    <div className={`pipeline-stepper__node pipeline-stepper__node--${state}`}>
      <div className="pipeline-stepper__circle">
        {state === "completed" && <CheckIcon />}
        {state === "active" && <SpinnerIcon />}
      </div>
      <span className="pipeline-stepper__label">
        {STEP_LABELS[step]}
        {isResearch && state === "active" && loopCount >= 1 && (
          <span className="pipeline-stepper__iter"> ({loopCount}/{maxIterations})</span>
        )}
      </span>
      {showLoop && (
        <span
          className={`pipeline-stepper__loop-badge${loopIntense ? " pipeline-stepper__loop-badge--intense" : ""}`}
          aria-label={`${loopCount}回目のループ`}
        >
          ↺ {loopCount}回目
        </span>
      )}
    </div>
  );
}

export function PipelineStepper({
  currentStep,
  loopCount,
  maxIterations,
  completedSteps,
}: PipelineStepperProps) {
  return (
    <nav className="pipeline-stepper" aria-label="パイプライン進行状況">
      {STEPS.map((step, i) => {
        const isCompleted = completedSteps.includes(step);
        const isActive = step === currentStep && !isCompleted;
        const state = isCompleted ? "completed" : isActive ? "active" : "pending";

        return (
          <div key={step} className="pipeline-stepper__item">
            <StepNode
              step={step}
              state={state}
              loopCount={loopCount}
              maxIterations={maxIterations}
            />
            {i < STEPS.length - 1 && (
              <div
                className={`pipeline-stepper__connector${isCompleted ? " pipeline-stepper__connector--done" : ""}`}
                aria-hidden="true"
              />
            )}
          </div>
        );
      })}
    </nav>
  );
}
