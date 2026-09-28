/**
 * Personalized home-page template content and department/persona helpers.
 */

import type { SkillAccessInfo } from "@/lib/SkillAccessContext";

export type HomeIcon =
  | "database"
  | "bell"
  | "file"
  | "bot"
  | "clock"
  | "link"
  | "rocket"
  | "grid"
  | "layers"
  | "key"
  | "sparkles";

export type HomeTabId =
  | "for-you"
  | "slack-agents"
  | "internal-apps"
  | "skills"
  | "data-reports";

export type DeptBucket =
  | "finance"
  | "compliance"
  | "marketing"
  | "engineering"
  | "hr"
  | "ops"
  | "generic";

export type Persona = "technical" | "non-technical";

export type TemplateAccess = "available" | "locked" | "neutral";

export interface HomeTemplate {
  id: string;
  title: string;
  description: string;
  long: string;
  steps: string[];
  icon: HomeIcon;
  /** When set, tier badge is derived from real ACL data. */
  skillSlug?: string;
  /** Deep link when user clicks Start in the preview modal. */
  href?: string;
  /** Prefill Workspace chat input. */
  workspacePrompt?: string;
  /** Skill slug to attach when opening Workspace. */
  workspaceSkillId?: string;
}

const TECHNICAL_DEPARTMENTS = new Set([
  "Engineering",
  "Data & Automation",
  "Apps & API",
  "AI - Product Experience",
  "Quants & Structuring",
  "Security",
  "Production Operations",
  "Centre of Excellence",
  "Automation & Risk",
]);

// Map a department name to a broad bucket used to pick starter prompts.
// Matched by pattern rather than by a fixed roster, so this keeps working for
// any organisation's department names instead of only one organisation's.
const DEPT_BUCKET_RULES: Array<{ bucket: DeptBucket; match: RegExp | string[] }> = [
  { bucket: "finance", match: /financ|account|payment|payable|treasur|tax|audit|billing|invoic/i },
  { bucket: "compliance", match: /compliance|legal|risk|regulat|aml|kyc/i },
  { bucket: "marketing", match: /marketing|brand|content|acquisition|growth|creative|comms/i },
  { bucket: "engineering", match: /engineer|data|platform|infrastructur|security|product|develop|quant/i },
  { bucket: "hr", match: /\bhr\b|people|talent|recruit|compensation|culture/i },
  { bucket: "ops", match: /operations|support|customer|partner|success|service/i },
];

export function isTechnicalDepartment(department: string | null | undefined): boolean {
  if (!department) return false;
  return TECHNICAL_DEPARTMENTS.has(department.trim());
}

export function resolveDeptBucket(department: string | null | undefined): DeptBucket {
  const dept = (department ?? "").trim();
  if (!dept) return "generic";
  for (const rule of DEPT_BUCKET_RULES) {
    if (Array.isArray(rule.match)) {
      if (rule.match.includes(dept)) return rule.bucket;
    }
  }
  return "generic";
}

export function greetingTime(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

export function firstName(name: string | null | undefined): string {
  if (!name?.trim()) return "there";
  return name.trim().split(/\s+/)[0] ?? "there";
}

export function computeSkillStats(bySlug: Map<string, SkillAccessInfo>) {
  let available = 0;
  let toRequest = 0;
  for (const info of bySlug.values()) {
    if (info.accessible) available += 1;
    else if (info.can_request) toRequest += 1;
  }
  return { available, toRequest };
}

export function resolveTemplateAccess(
  template: HomeTemplate,
  bySlug: Map<string, SkillAccessInfo>,
): TemplateAccess {
  if (!template.skillSlug) return "neutral";
  const info = bySlug.get(template.skillSlug);
  if (!info) return "neutral";
  if (info.accessible) return "available";
  if (info.can_request) return "locked";
  return "neutral";
}

function humanizeSlug(slug: string): string {
  return slug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Skills that make poor "For you" picks — still usable elsewhere.
 *  Add your own slugs here; unknown slugs are simply ignored. */
const LOW_PRIORITY_SKILL_SLUGS = new Set([
  "announcement-card",
  "pdf",
]);

const STARTER_PROMPTS: Record<string, string> = {
  "announcement-card": "Make an announcement card for our next release",
  "build-from-specs": "Help me implement this feature from the product spec",
  docx: "Turn these notes into a formatted Word document",
  pdf: "Build a PDF report from last month's numbers",
};

function inferSkillIcon(slug: string): HomeIcon {
  if (slug.includes("bq") || slug.includes("sql") || slug.includes("query")) return "database";
  if (slug.includes("alert") || slug.includes("monitor")) return "bell";
  if (slug.includes("agent") || slug.includes("route")) return "bot";
  if (slug.includes("build") || slug.includes("spec")) return "rocket";
  return "layers";
}

function truncateAtWord(text: string, maxLen: number): string {
  const s = text.trim();
  if (s.length <= maxLen) return s;
  const slice = s.slice(0, maxLen);
  const lastSpace = slice.lastIndexOf(" ");
  const cut = lastSpace > maxLen * 0.55 ? slice.slice(0, lastSpace) : slice;
  return `${cut.trimEnd()}…`;
}

function firstSentence(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return "";
  const match = trimmed.match(/^[^.!?\n]+[.!?]?/);
  return (match?.[0] ?? trimmed).trim();
}

function shortDisplayName(name: string): string {
  return name.replace(/\s+skill$/i, "").trim();
}

/** One-line card blurb — never the full skill description. */
function shortSkillBlurb(description: string, displayName: string): string {
  const shortName = shortDisplayName(displayName);
  const first = firstSentence(description);
  if (first) return truncateAtWord(first, 88);
  return `Query and automate with ${shortName}.`;
}

/** Short ask-bar chip — target ~50 chars, always a single sentence. */
function askChipForSkill(slug: string, displayName: string, description: string): string {
  if (STARTER_PROMPTS[slug]) return STARTER_PROMPTS[slug];

  const shortName = shortDisplayName(displayName);
  const slugL = slug.toLowerCase();

  if (slugL.includes("sql") || slugL.includes("query") || slugL.includes("database")) {
    return `Query ${shortName} for last week's numbers`;
  }
  if (slugL.includes("crm") || slugL.includes("backoffice")) return "Look up a record in your CRM";
  if (slugL.includes("audit")) return "List open audit engagements";
  if (slugL.includes("workflow")) return "Check workflow status";
  if (slugL.includes("slack")) return "Search my Slack messages";
  if (slugL.includes("github")) return "Summarize recent PR activity";
  if (slugL.includes("monitor") || slugL.includes("observability")) {
    return "Check recent error spikes in monitoring";
  }
  if (slugL.includes("api")) return `Look something up with ${shortName}`;

  const first = firstSentence(description);
  if (first) return truncateAtWord(first, 52);
  return `Help me with ${shortName}`;
}

function workspacePromptForSkill(slug: string, displayName: string, description: string): string {
  return askChipForSkill(slug, displayName, description);
}

function scoreSkillForUser(
  info: SkillAccessInfo,
  department: string,
): number {
  let score = 0;
  if (info.accessible) score += 100;
  else if (info.can_request) score += 25;
  if (info.tier1) score += 20;
  if (info.owning_department && department && info.owning_department === department) score += 15;
  if (department && info.departments.includes(department)) score += 8;
  if (info.departments.includes("*")) score += 2;
  if (LOW_PRIORITY_SKILL_SLUGS.has(info.slug)) score -= 40;
  return score;
}

function skillToTemplate(
  slug: string,
  info: SkillAccessInfo,
  displayName: string,
  locked: boolean,
): HomeTemplate {
  const shortName = shortDisplayName(displayName);
  const blurb = shortSkillBlurb(info.description, displayName);
  const steps = locked
    ? [
        "Request access from the Skills page with a short justification",
        "Once granted, attach the skill in Workspace from the + menu",
        "Ask your question in plain English and iterate on the results",
      ]
    : [
        `Open Workspace and attach ${shortName} from the + menu`,
        "Ask your question — include dates, entities, or metrics if you know them",
        "Review results, export if needed, or follow up in the same thread",
      ];

  const base: HomeTemplate = {
    id: `skill-${slug}${locked ? "-locked" : ""}`,
    title: locked ? `Request ${shortName}` : shortName,
    description: locked ? `${blurb} Request access to use it.` : blurb,
    long: info.description.trim() || blurb,
    steps,
    icon: inferSkillIcon(slug),
    skillSlug: slug,
  };

  if (locked) {
    return { ...base, href: `/skills/${slug}` };
  }

  return {
    ...base,
    workspacePrompt: workspacePromptForSkill(slug, displayName, info.description),
    workspaceSkillId: slug,
  };
}

const GENERIC_FOR_YOU: HomeTemplate[] = [
  {
    id: "gen-workspace",
    title: "Ask {{APP_NAME}} in Workspace",
    description: "Start a chat with memory, tools, and inline file rendering.",
    long: "Workspace is your AI chat. Attach a skill when you need governed company data.",
    steps: [
      "Type your question in the ask bar or open Workspace",
      "Add a skill from the + menu if your question needs company data",
      "Review the answer and iterate",
    ],
    icon: "sparkles",
    href: "/workspace",
  },
  {
    id: "gen-skills-browse",
    title: "Browse skills for your department",
    description: "Discover pre-built workflows with governed data access.",
    long: "Skills extend {{APP_NAME}} with department-specific tools and datasets.",
    steps: [
      "Open the Skills page",
      "Filter by your department or search by keyword",
      "Request access or start using Tier 1 skills immediately",
    ],
    icon: "layers",
    href: "/skills",
  },
  {
    id: "gen-agent",
    title: "Deploy a Slack agent",
    description: "Create an agent, assign skills, and connect it to Slack.",
    long: "Agents run on {{APP_NAME}} with identity and ACL checks on every hop.",
    steps: [
      "Create the agent and assign skills at /agents/new",
      "Connect Slack from Integrations if you have not already",
      "Activate — it responds to @mentions immediately",
    ],
    icon: "bot",
    href: "/agents/new",
  },
];

/** Build "For you" cards from the user's real skill ACL — no hardcoded fake slugs. */
export function buildForYouTemplates(
  bySlug: Map<string, SkillAccessInfo>,
  skillNames: Map<string, string>,
  department: string | null | undefined,
): HomeTemplate[] {
  const dept = (department ?? "").trim();

  const ranked = [...bySlug.entries()]
    .map(([slug, info]) => ({
      slug,
      info,
      score: scoreSkillForUser(info, dept),
    }))
    .filter(({ info }) => info.accessible || info.can_request)
    .sort((a, b) => b.score - a.score);

  const accessible = ranked.filter(({ info }) => info.accessible);
  const requestable = ranked.filter(({ info }) => !info.accessible && info.can_request);

  const picks: Array<{ slug: string; info: SkillAccessInfo; locked: boolean }> = [];
  for (const { slug, info } of accessible.slice(0, 3)) {
    picks.push({ slug, info, locked: false });
  }
  if (picks.length < 3) {
    for (const { slug, info } of requestable.slice(0, 3 - picks.length)) {
      picks.push({ slug, info, locked: true });
    }
  }

  if (picks.length === 0) {
    return GENERIC_FOR_YOU;
  }

  return picks.map(({ slug, info, locked }) =>
    skillToTemplate(slug, info, skillNames.get(slug) ?? humanizeSlug(slug), locked),
  );
}

const GENERIC_ASK_SUGGESTIONS = [
  "Summarize this PDF for me",
  "Draft a Word doc report",
  "Help me draft an email to my team",
];

/** Ask-bar chips from accessible skills the user actually has. */
export function buildAskSuggestionsFromSkills(
  bySlug: Map<string, SkillAccessInfo>,
  skillNames: Map<string, string>,
  department: string | null | undefined,
): string[] {
  const dept = (department ?? "").trim();

  const accessible = [...bySlug.values()]
    .filter((info) => info.accessible)
    .sort((a, b) => scoreSkillForUser(b, dept) - scoreSkillForUser(a, dept));

  const fromSkills = accessible
    .slice(0, 3)
    .map((info) => {
      const name = skillNames.get(info.slug) ?? humanizeSlug(info.slug);
      return workspacePromptForSkill(info.slug, name, info.description);
    });

  const merged = [...fromSkills];
  for (const generic of GENERIC_ASK_SUGGESTIONS) {
    if (merged.length >= 3) break;
    if (!merged.includes(generic)) merged.push(generic);
  }
  return merged.slice(0, 3);
}


function staticTemplates(persona: Persona): Record<Exclude<HomeTabId, "for-you">, HomeTemplate[]> {
  const tech = persona === "technical";
  return {
    "slack-agents": [
      {
        id: "slack-deploy",
        title: "Deploy a Slack agent in 5 minutes",
        description: tech
          ? "Create the agent, configure the Slack app, paste the bot token — no deploy pipeline."
          : "Create a Slack bot that answers @mentions using your team's skills — no servers needed.",
        long: "Create the agent on {{APP_NAME}}, point a Slack app at your events relay, paste the bot token — activate.",
        steps: [
          "Create the agent and assign it skills",
          "Configure the Slack app and paste the bot token",
          "Activate — it responds to @mentions immediately",
        ],
        icon: "bot",
        href: "/agents/new",
      },
      {
        id: "slack-digest",
        title: "Auto-summarize a channel every morning",
        description: "Point an agent at a channel and give it a schedule — it posts a clean summary before the day starts.",
        long: "Schedules an agent to read the last 24 hours and post a summary to Slack.",
        steps: [
          "Pick the channel to watch",
          "Set the time to post",
          "Review the first digest and adjust the prompt",
        ],
        icon: "clock",
        href: "/automations",
      },
      {
        id: "slack-handoff",
        title: "Let an agent call another agent",
        description: "Connect agents so complex questions route to the right specialist automatically.",
        long: "Connect agents in the agent detail page. Identity and access checks travel with every hop.",
        steps: [
          "Open the agent you want to extend",
          "Connect it to another agent",
          "Test a question that needs both",
        ],
        icon: "link",
        href: "/agents",
      },
    ],
    "internal-apps": [
      {
        id: "apps-spreadsheet",
        title: "Turn a spreadsheet into a tool",
        description: tech
          ? "Describe the workflow in Lab — {{APP_NAME}} scaffolds a real app file by file."
          : "Describe what your spreadsheet does — Lab builds a shareable app for your team.",
        long: "Describe what the spreadsheet does in chat — {{APP_NAME}} Lab builds a real app file by file.",
        steps: [
          "Describe the workflow in Lab chat",
          "Watch it get built file by file",
          "Share it privately or with the workspace",
        ],
        icon: "rocket",
        href: "/lab",
      },
      {
        id: "apps-status",
        title: "Build a status page for your team",
        description: "Ask Lab to scaffold a status page with a live URL at your deployment /apps.",
        long: "Lab scaffolds the UI and data source, then gives you a shareable link.",
        steps: [
          "Describe what should be on the page",
          "Preview it instantly",
          "Share with specific people or the whole workspace",
        ],
        icon: "grid",
        href: "/lab",
      },
    ],
    skills: [
      {
        id: "skills-publish",
        title: "Publish a skill from a SKILL.md",
        description: "Upload a SKILL.md — the AI security reviewer scans it before it goes live.",
        long: "Go to Skills → Publish and upload a SKILL.md.",
        steps: [
          "Upload your SKILL.md",
          "Review the auto-filled details",
          "Submit — it is approved or flagged automatically",
        ],
        icon: "layers",
        href: "/publish",
      },
      {
        id: "skills-create",
        title: "Create a skill from API docs",
        description: "The AI Skill Creator fetches docs and writes frontmatter, patterns, and Python examples.",
        long: "Paste an API docs URL or upload a reference file.",
        steps: [
          "Paste an API docs URL or upload a reference file",
          "Review the generated SKILL.md",
          "Publish — security review runs automatically",
        ],
        icon: "sparkles",
        href: "/create-skill",
      },
      {
        id: "skills-secrets",
        title: "Add a secret to an existing skill",
        description: "Secrets are stored as opaque references — raw values never touch the database.",
        long: "Add a named secret during publish or from the skill's settings.",
        steps: [
          "Open the skill's settings",
          "Add a named secret, e.g. IAM_API_KEY",
          "Reference it in code as an environment variable",
        ],
        icon: "key",
        href: "/skills",
      },
    ],
    "data-reports": [
      {
        id: "data-word",
        title: "Generate a Word doc report",
        description: "Ask for a report the way you would ask a person — review inline before downloading.",
        long: "{{APP_NAME}} drafts reports with python-docx and renders them inline.",
        steps: [
          "Describe the report you need in Workspace",
          "Review it rendered inline",
          "Download the .docx or keep iterating",
        ],
        icon: "file",
        workspacePrompt: "Draft a Word doc report summarising last month's key metrics",
      },
      {
        id: "data-trace",
        title: "Get a live trace of a multi-tool task",
        description: "Watch a step-by-step timeline as {{APP_NAME}} chains multiple tools.",
        long: "For anything that chains multiple tools, {{APP_NAME}} shows a live timeline.",
        steps: [
          "Ask a question that needs more than one tool",
          "Watch the step-by-step timeline appear",
          "Open any step to see its raw result",
        ],
        icon: "layers",
        workspacePrompt: "Analyse last week's signup funnel and draft a summary with charts",
      },
      {
        id: "data-models",
        title: "Compare models on one query",
        description: tech
          ? "Pick a model from the Workspace menu or let auto-routing choose by complexity."
          : "{{APP_NAME}} routes simple questions to fast models automatically — or pick one yourself.",
        long: "Open the model menu in Workspace and compare answers side by side.",
        steps: [
          "Ask your question as usual in Workspace",
          "Or open the model menu and pick one directly",
          "Compare answers if you like",
        ],
        icon: "sparkles",
        href: "/workspace",
      },
    ],
  };
}

const STATIC = {
  technical: staticTemplates("technical"),
  "non-technical": staticTemplates("non-technical"),
};

export function getCategoryTemplates(
  tab: Exclude<HomeTabId, "for-you">,
  persona: Persona,
): HomeTemplate[] {
  return STATIC[persona][tab];
}

export const HOME_TABS: { id: HomeTabId; label: string }[] = [
  { id: "for-you", label: "For you" },
  { id: "slack-agents", label: "Slack agents" },
  { id: "internal-apps", label: "Internal apps" },
  { id: "skills", label: "Skills" },
  { id: "data-reports", label: "Data & reports" },
];
