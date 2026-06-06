import { Link } from "../router";

interface BackLinkProps {
  to: string;
  label?: string;
}

export function BackLink({ to, label = "戻る" }: BackLinkProps) {
  return (
    <Link to={to} className="back-link" aria-label={label}>
      <span className="back-link-icon" aria-hidden="true">←</span>
      <span>{label}</span>
    </Link>
  );
}
