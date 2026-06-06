/**
 * Minimal hash-based router (ui_plan.md A3 navigation structure).
 *
 * Deliberately dependency-free (no react-router) to keep the build self
 * contained, per the plan's note that new dependencies need approval. Hash
 * routing also means deep links survive a static file host with no rewrite
 * rules.
 *
 * Routes:
 *   #/                     Dashboard           (SCR-2)
 *   #/new                  New research        (SCR-1)
 *   #/runs/:id             Run monitor         (SCR-3)
 *   #/runs/:id/review      Human review        (SCR-4)
 *   #/runs/:id/report      Report viewer       (SCR-5)
 *   #/runs/:id/audit       Audit log           (SCR-6)
 *   #/settings             Settings            (SCR-7)
 */

/* eslint-disable react-refresh/only-export-components --
   This router module intentionally co-locates the <Link> component with the
   navigation helpers and the useRoute hook; fast-refresh granularity is not a
   concern for routing primitives. */
import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";

export type RouteName =
  | "dashboard"
  | "new"
  | "monitor"
  | "review"
  | "report"
  | "audit"
  | "settings"
  | "not-found";

export type ReportTab = "research" | "reviews";

export interface Route {
  name: RouteName;
  /** run id for run-scoped routes. */
  runId?: string;
  /** Optional report viewer tab. */
  reportTab?: ReportTab;
  path: string;
}

function parseReportTab(value: string | null): ReportTab {
  if (value === "reviews") return value;
  return "research";
}

function parseHash(hash: string): Route {
  const rawPath = hash.replace(/^#/, "") || "/";
  const [path, query = ""] = rawPath.split("?", 2);
  const params = new URLSearchParams(query);
  const segments = path.split("/").filter(Boolean);

  if (segments.length === 0) return { name: "dashboard", path };
  if (segments[0] === "new") return { name: "new", path };
  if (segments[0] === "settings") return { name: "settings", path };

  if (segments[0] === "runs" && segments[1]) {
    const runId = decodeURIComponent(segments[1]);
    const sub = segments[2];
    if (!sub) return { name: "monitor", runId, path };
    if (sub === "review") return { name: "review", runId, path };
    if (sub === "report") {
      return {
        name: "report",
        runId,
        reportTab: parseReportTab(params.get("tab")),
        path: rawPath,
      };
    }
    if (sub === "audit") return { name: "audit", runId, path };
  }

  return { name: "not-found", path };
}

export function navigate(path: string): void {
  const target = path.startsWith("#") ? path : `#${path}`;
  if (window.location.hash === target) return;
  window.location.hash = target;
}

export function routes() {
  return {
    dashboard: "/",
    new: "/new",
    monitor: (runId: string) => `/runs/${encodeURIComponent(runId)}`,
    review: (runId: string) => `/runs/${encodeURIComponent(runId)}/review`,
    report: (runId: string, tab: ReportTab = "research") => {
      const base = `/runs/${encodeURIComponent(runId)}/report`;
      return tab === "research" ? base : `${base}?tab=${tab}`;
    },
    audit: (runId: string) => `/runs/${encodeURIComponent(runId)}/audit`,
    settings: "/settings",
  };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    function onHashChange() {
      setRoute(parseHash(window.location.hash));
      window.scrollTo(0, 0);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return route;
}

interface LinkProps {
  to: string;
  className?: string;
  children: ReactNode;
  "aria-label"?: string;
}

export function Link({ to, className, children, ...rest }: LinkProps) {
  const onClick = useCallback(
    (event: MouseEvent<HTMLAnchorElement>) => {
      // Let modified clicks (new tab) behave normally.
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(to);
    },
    [to],
  );

  return (
    <a href={`#${to}`} className={className} onClick={onClick} {...rest}>
      {children}
    </a>
  );
}
