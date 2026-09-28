/**
 * Static display metadata for the skills bundled with this repository.
 *
 * This is presentation only — download counts, ratings and review blurbs that
 * make the catalogue look inhabited. Access control, versioning and the real
 * catalogue all come from the backend; nothing here grants or denies anything.
 *
 * Runtime-published skills have no entry here and fall back to synthesised
 * display data (see lib/SkillAccessContext.tsx).
 */

const _skillMarkdowns = import.meta.glob('./data/skills/*/skill.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

function md(slug: string): string {
  return _skillMarkdowns[`./data/skills/${slug}/skill.md`] ?? '';
}

export interface Skill {
  id: string;
  name: string;
  slug: string;
  description: string;
  publisher: string;
  version: string;
  /** "internal" = needs access to systems only this organisation has. "utility" = works anywhere. */
  scope: "internal" | "utility";
  tags: string[];
  verified: boolean;
  markdown: string;
  reviews: { id: string; user: string; rating: number; comment: string; date: string }[];
  /** Highest risk level among this skill's resolved connected-system links, if any. */
  maxSystemRiskLevel?: string | null;
}

export const MOCK_SKILLS: Skill[] = [
  {
    id: "announcement-card",
    name: "Announcement Card",
    slug: "announcement-card",
    description:
      "Generate a visual announcement card image from a short brief — headline, body, and branding — ready to post in chat or embed in a document.",
    publisher: "platform-team",
    version: "1.0.0",
    scope: "utility",
    tags: ["images", "design", "communications"],
    verified: true,
    markdown: md("announcement-card"),
    reviews: [
      { id: "r2", user: "Comms", rating: 5, comment: "Saves a round trip to design for routine announcements.", date: "2026-03-18" },
    ],
  },
  {
    id: "build-from-specs",
    name: "Build From Specs",
    slug: "build-from-specs",
    description:
      "Turn a written product specification into an implementation: read the spec, derive a feature checklist, build against it, and verify each item before reporting done.",
    publisher: "platform-team",
    version: "1.0.0",
    scope: "utility",
    tags: ["engineering", "specs", "workflow"],
    verified: true,
    markdown: md("build-from-specs"),
    reviews: [
      { id: "r3", user: "Engineering", rating: 4, comment: "Works best when the spec is already precise.", date: "2026-05-11" },
    ],
  },
  {
    id: "docx",
    name: "Word Documents",
    slug: "docx",
    description:
      "Create and edit Word documents — headings, tables, styles and images — from structured input or a natural-language brief.",
    publisher: "platform-team",
    version: "1.0.0",
    scope: "utility",
    tags: ["documents", "docx", "reporting"],
    verified: true,
    markdown: md("docx"),
    reviews: [
      { id: "r4", user: "Operations", rating: 5, comment: "Formatting survives the round trip, which is more than I expected.", date: "2026-02-27" },
    ],
  },
  {
    id: "pdf",
    name: "PDF Reports",
    slug: "pdf",
    description:
      "Build formatted PDF reports — branded headers and footers, tables, charts and page numbering — from query results or structured data.",
    publisher: "platform-team",
    version: "1.0.0",
    scope: "utility",
    tags: ["documents", "pdf", "reporting", "charts"],
    verified: true,
    markdown: md("pdf"),
    reviews: [
      { id: "r6", user: "Finance", rating: 5, comment: "Monthly reporting went from a morning to a prompt.", date: "2026-01-30" },
    ],
  },
];
