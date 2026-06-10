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
 *   #/forecasts            Forecast dashboard
 *   #/forecasts/new        New forecast
 *   #/forecasts/:id        Forecast detail
 *   #/forecasts/:id/audit  Forecast audit
 */

/* eslint-disable react-refresh/only-export-components --
   This router module intentionally co-locates the <Link> component with the
   navigation helpers and the useRoute hook; fast-refresh granularity is not a
   concern for routing primitives. */
import {
  useCallback,
  useEffect,
  useState,
  type AriaAttributes,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
} from "react";

export type RouteName =
  | "dashboard"
  | "new"
  | "monitor"
  | "review"
  | "report"
  | "audit"
  | "settings"
  | "forecasts"
  | "forecast-new"
  | "forecast-detail"
  | "forecast-audit"
  | "not-found";

export type ReportTab = "research";

export interface Route {
  name: RouteName;
  /** run id for run-scoped routes. */
  runId?: string;
  /** Optional report viewer tab. */
  reportTab?: ReportTab;
  /** Optional Deep Research attempt selected from a report URL. */
  reportAttemptNo?: number | null;
  /** Optional audit log tab selected from an audit URL. */
  auditTab?: string;
  /** Optional review number selected from an audit URL. */
  auditReviewNo?: number | null;
  /** Optional canonical path for legacy route compatibility. */
  redirectTo?: string;
  /** forecast id for forecast-scoped routes. */
  forecastId?: string;
  path: string;
}

function parseHash(hash: string): Route {
  const rawPath = hash.replace(/^#/, "") || "/";
  const [path, query = ""] = rawPath.split("?", 2);
  const params = new URLSearchParams(query);
  const segments = path.split("/").filter(Boolean);

  if (segments.length === 0) return { name: "dashboard", path };
  if (segments[0] === "new" && segments.length === 1) return { name: "new", path };
  if (segments[0] === "settings" && segments.length === 1) {
    return { name: "settings", path };
  }
  if (segments[0] === "forecasts") {
    if (segments.length === 1) return { name: "forecasts", path };
    if (segments[1] === "new" && segments.length === 2) {
      return { name: "forecast-new", path };
    }
    const forecastId = decodeURIComponent(segments[1]);
    if (segments.length === 3 && segments[2] === "audit") {
      return { name: "forecast-audit", forecastId, path };
    }
    if (segments.length === 2) return { name: "forecast-detail", forecastId, path };
  }

  if (segments[0] === "runs" && segments[1]) {
    const runId = decodeURIComponent(segments[1]);
    const sub = segments[2];
    if (!sub) return { name: "monitor", runId, path };
    if (sub === "review") return { name: "review", runId, path };
    if (sub === "report") {
      if (params.get("tab") === "reviews") {
        const auditReviewNo = positiveIntParam(params, "review");
        const auditParams = new URLSearchParams({ tab: "reviews" });
        if (auditReviewNo) auditParams.set("review", String(auditReviewNo));
        return {
          name: "audit",
          runId,
          auditTab: "reviews",
          auditReviewNo,
          redirectTo: `/runs/${encodeURIComponent(runId)}/audit?${auditParams.toString()}`,
          path: rawPath,
        };
      }
      return {
        name: "report",
        runId,
        reportTab: "research",
        reportAttemptNo: positiveIntParam(params, "attempt"),
        path: rawPath,
      };
    }
    if (sub === "audit") {
      return {
        name: "audit",
        runId,
        auditTab: params.get("tab") ?? undefined,
        auditReviewNo: positiveIntParam(params, "review"),
        path: rawPath,
      };
    }
  }

  return { name: "not-found", path };
}

function positiveIntParam(params: URLSearchParams, name: string): number | null {
  const raw = params.get(name);
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) return null;
  return value;
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
    report: (runId: string, options?: { attempt?: number }) => {
      if (!options?.attempt) return `/runs/${encodeURIComponent(runId)}/report`;
      const params = new URLSearchParams({ tab: "research" });
      if (options?.attempt) params.set("attempt", String(options.attempt));
      return `/runs/${encodeURIComponent(runId)}/report?${params.toString()}`;
    },
    audit: (runId: string, options?: { tab?: string; review?: number }) => {
      const params = new URLSearchParams();
      if (options?.tab) params.set("tab", options.tab);
      if (options?.review) params.set("review", String(options.review));
      const query = params.toString();
      return `/runs/${encodeURIComponent(runId)}/audit${query ? `?${query}` : ""}`;
    },
    settings: "/settings",
    forecasts: "/forecasts",
    forecastNew: "/forecasts/new",
    forecastDetail: (forecastId: string) => `/forecasts/${encodeURIComponent(forecastId)}`,
    forecastAudit: (forecastId: string) =>
      `/forecasts/${encodeURIComponent(forecastId)}/audit`,
  };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    if (!route.redirectTo) return;
    const target = `#${route.redirectTo}`;
    if (window.location.hash === target) return;
    window.history.replaceState(null, "", target);
    setRoute(parseHash(window.location.hash));
  }, [route.redirectTo]);

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
  style?: CSSProperties;
  children: ReactNode;
  "aria-label"?: string;
  "aria-current"?: AriaAttributes["aria-current"];
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
