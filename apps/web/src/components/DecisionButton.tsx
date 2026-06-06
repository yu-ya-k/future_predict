import type { DecisionButtonProps } from "./types";

export function DecisionButton({
  action,
  label,
  consequence,
  tone,
  costHint,
  disabled = false,
  guardMessage,
  block = false,
  type = "button",
  onClick,
}: DecisionButtonProps) {
  const classes = [
    "decision-button",
    `decision-button--${tone}`,
    block ? "decision-button--block" : "",
    disabled ? "decision-button--disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={`decision-button-wrap${block ? " decision-button-wrap--block" : ""}`}>
      <button
        className={classes}
        type={type}
        disabled={disabled}
        onClick={disabled ? undefined : onClick}
        aria-disabled={disabled}
        data-action={action}
      >
        <span className="decision-button__label">{label}</span>
        {(consequence || costHint) && (
          <span className="decision-button__meta">
            {consequence && (
              <span className="decision-button__consequence">{consequence}</span>
            )}
            {costHint && (
              <span className="decision-button__cost-hint">{costHint}</span>
            )}
          </span>
        )}
      </button>
      {disabled && guardMessage && (
        <p className="decision-button__guard" role="alert">
          {guardMessage}
        </p>
      )}
    </div>
  );
}
