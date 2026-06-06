import type { SkeletonProps } from "./types";

export function Skeleton({
  width = "100%",
  height = "1rem",
  lines = 1,
  className,
}: SkeletonProps) {
  return (
    <span
      className={["skeleton", className].filter(Boolean).join(" ")}
      aria-hidden="true"
      aria-label="読み込み中"
    >
      {Array.from({ length: lines }, (_, i) => (
        <span
          key={i}
          className="skeleton__line"
          style={{
            width: i === lines - 1 && lines > 1 ? `calc(${width} * 0.7)` : width,
            height,
          }}
        />
      ))}
    </span>
  );
}
