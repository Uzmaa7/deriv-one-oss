/**
 * SkillAccessContext — loaded once at app startup, provides access data
 * to all components without repeated /api/skills calls.
 *
 * useSkillList() is the single source of truth for which skills exist.
 * It merges the live /api/skills response with static display metadata
 * (verified badge, reviews) for pre-built skills, and synthesises
 * display data for runtime-published skills. Nothing else should read
 * MOCK_SKILLS directly.
 */

import { createContext, useContext, useEffect, useState, useMemo, type ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { MOCK_SKILLS, type Skill } from "../src/data";

export interface SkillSystemLink {
  integration_tag: string;
  system_name: string | null;
  risk_level: string | null;
  match_confidence: "exact" | "fuzzy" | "content" | "unmatched";
  source: "integration_tag" | "skill_content";
  direction: "read" | "write" | "read_write" | null;
}

export interface SkillAccessInfo {
  slug: string;
  accessible: boolean;
  tier1: boolean;
  tier2: boolean;
  can_request: boolean;
  pending_request_id: string | null;
  expires_at: string | null;
  risk_level: string;
  cross_dept_policy: string;
  departments: string[];
  integrations: string[];
  description: string;
  name: string;
  owning_department: string;
  is_owner: boolean;
  is_collaborator: boolean;
  can_manage: boolean;
  has_secrets: boolean;
  system_links: SkillSystemLink[];
  max_system_risk_level: string | null;
}

interface SkillAccessState {
  loading: boolean;
  error: string | null;
  /** Slugs the current user can actually activate */
  accessibleSlugs: Set<string>;
  /** Full access detail per slug */
  bySlug: Map<string, SkillAccessInfo>;
  /** True if the current user is a platform admin */
  isAdmin: boolean;
  /** True if the current user is a platform admin (hub admin group, not executive override) */
  isPlatformAdmin: boolean;
  /** Directory profile for the signed-in person */
  employee: {
    email: string;
    name: string | null;
    department: string | null;
    role: string | null;
    iam_resolved: boolean;
    is_admin?: boolean;
    is_platform_admin?: boolean;
  } | null;
  /** Force a refresh (e.g. after a request is submitted) */
  refresh: () => void;
}

const SkillAccessContext = createContext<SkillAccessState>({
  loading: true,
  error: null,
  accessibleSlugs: new Set(),
  bySlug: new Map(),
  isAdmin: false,
  isPlatformAdmin: false,
  employee: null,
  refresh: () => {},
});

export function SkillAccessProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accessibleSlugs, setAccessibleSlugs] = useState<Set<string>>(new Set());
  const [bySlug, setBySlug] = useState<Map<string, SkillAccessInfo>>(new Map());
  const [isAdmin, setIsAdmin] = useState(false);
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [employee, setEmployee] = useState<SkillAccessState["employee"]>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    apiFetch("/api/skills")
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(data => {
        if (cancelled) return;
        const skills: SkillAccessInfo[] = data.skills ?? [];
        const slugSet = new Set(skills.filter(s => s.accessible).map(s => s.slug));
        const map = new Map(skills.map(s => [s.slug, s]));
        setAccessibleSlugs(slugSet);
        setBySlug(map);
        setIsAdmin(data.employee?.is_admin ?? false);
        setIsPlatformAdmin(data.employee?.is_platform_admin ?? false);
        setEmployee(data.employee ?? null);
        setError(null);
      })
      .catch(e => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [tick]);

  return (
    <SkillAccessContext.Provider
      value={{
        loading,
        error,
        accessibleSlugs,
        bySlug,
        isAdmin,
        isPlatformAdmin,
        employee,
        refresh: () => setTick(t => t + 1),
      }}
    >
      {children}
    </SkillAccessContext.Provider>
  );
}

export function useSkillAccess() {
  return useContext(SkillAccessContext);
}

// ── Static display metadata lookup (downloads, rating, verified, reviews) ────
// Keyed by slug; used only for supplementary UI numbers on pre-built skills.
const _mockBySlug = new Map(MOCK_SKILLS.map(s => [s.slug, s]));

function _humanizeSlug(slug: string): string {
  return slug.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

/**
 * Returns the full merged skill list sourced from /api/skills.
 * Pre-built skills are enriched with display metadata (name, tags, verified badge,
 * reviews) from the static bundle; runtime-published skills get synthesised
 * display values. This hook is the SINGLE authoritative skill list for all UI.
 */
export function useSkillList(): Skill[] {
  const { bySlug } = useSkillAccess();
  return useMemo(() => {
    const result: Skill[] = [];
    bySlug.forEach((info, slug) => {
      const mock = _mockBySlug.get(slug);
      if (mock) {
        result.push({
          ...mock,
          name: info.name || mock.name,
          maxSystemRiskLevel: info.max_system_risk_level,
        });
      } else {
        result.push({
          id: slug,
          slug,
          name: info.name || _humanizeSlug(slug),
          description: info.description || slug,
          publisher: info.owning_department || "community",
          version: "1.0.0",
          scope: "internal",
          tags: [],
          verified: false,
          markdown: "",
          reviews: [],
          maxSystemRiskLevel: info.max_system_risk_level,
        });
      }
    });
    return result;
  }, [bySlug]);
}
