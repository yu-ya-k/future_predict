import { useId } from "react";

import type { DecisionButtonProps } from "./types";

export function DecisionButton({
  action,
  label,
  consequence,
  tone,
  costHint,
  disabled = false,
  guardMessage,
  disabledReason,
  block = false,
  type = "button",
  onClick,
}: DecisionButtonProps) {
  const descriptionBaseId = useId();
  const guardId = guardMessage ? `${descriptionBaseId}-guard` : undefined;
  const blockedId =
    disabled && disabledReason ? `${descriptionBaseId}-blocked` : undefined;
  const describedBy = [guardId, blockedId].filter(Boolean).join(" ") || undefined;
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
        aria-describedby={describedBy}
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
      {guardMessage && (
        <p id={guardId} className="decision-button__guard" role="note">
          {guardMessage}
        </p>
      )}
      {disabled && disabledReason && (
        <p id={blockedId} className="decision-button__blocked">
          {disabledReason}
        </p>
      )}
    </div>
  );
}
