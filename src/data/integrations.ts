export type CredentialFieldType = "token" | "url" | "text" | "multiline" | "password";

export interface CredentialField {
  slug: string;
  label: string;
  type: CredentialFieldType;
  placeholder: string;
  hint?: string;
  required: boolean;
}

export interface Integration {
  id: string;
  name: string;
  emoji: string;
  /** Brand logo served from /public (preferred over emoji in UI). */
  logoSrc?: string;
  description: string;
  credentials: CredentialField[];
  docsUrl?: string;
  /** When true, connection is handled via an OAuth redirect instead of a token-paste form. */
  oauthFlow?: boolean;
  /**
   * The backend endpoint to POST to in order to start the OAuth redirect.
   * Required when oauthFlow is true. e.g. "/api/slack-user-oauth/start"
   */
  oauthStartEndpoint?: string;
  /**
   * For OAuth integrations: the secret slugs managed by the OAuth flow.
   * Used for isConnected() checks and disconnect (since credentials: [] is empty).
   */
  oauthSecretSlugs?: string[];
}

/** True when all required credential / OAuth secrets exist in the vault. */
export function isIntegrationConnected(
  integration: Integration,
  vaultSlugs: ReadonlySet<string> | Record<string, unknown>,
): boolean {
  const has = (slug: string) =>
    vaultSlugs instanceof Set ? vaultSlugs.has(slug) : slug in vaultSlugs;
  if (integration.oauthSecretSlugs?.length) {
    return integration.oauthSecretSlugs.every(has);
  }
  return integration.credentials.filter((c) => c.required).every((c) => has(c.slug));
}

export const HOME_SETUP_INTEGRATION_IDS = ["slack", "google-workspace"] as const;

export const INTEGRATIONS: Integration[] = [
  {
    id: "slack",
    name: "Slack",
    emoji: "💬",
    logoSrc: "/integration-logos/slack.svg",
    description:
      "Search messages, access channels, read threads, and stay connected with your team's communications. Connects as you — with access to your private channels and DMs.",
    oauthFlow: true,
    oauthStartEndpoint: "/api/slack-user-oauth/start",
    oauthSecretSlugs: ["SLACK_USER_TOKEN"],
    credentials: [],
  },
  {
    id: "github",
    name: "GitHub",
    emoji: "🐙",
    description:
      "Browse repositories, read code, manage pull requests and issues. Uses a personal access token or fine-grained token scoped to the repos you need.",
    docsUrl: "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens",
    credentials: [
      {
        slug: "GITHUB_TOKEN",
        label: "Personal access token",
        type: "token",
        placeholder: "ghp_… or github_pat_…",
        hint: "Create a fine-grained token at GitHub › Settings › Developer settings › Personal access tokens.",
        required: true,
      },
    ],
  },
  {
    id: "datadog",
    name: "Datadog",
    emoji: "🐕",
    description:
      "Monitor infrastructure, query logs, and surface alerts. Give your agent full observability across your production environment.",
    docsUrl: "https://docs.datadoghq.com/api/latest/authentication/",
    credentials: [
      {
        slug: "DD_API_KEY",
        label: "API key",
        type: "token",
        placeholder: "e.g. 1a2b3c4d…",
        hint: "Found at Datadog › Organization Settings › API Keys.",
        required: true,
      },
      {
        slug: "DD_APP_KEY",
        label: "Application key",
        type: "token",
        placeholder: "e.g. 5e6f7a8b…",
        hint: "Found at Datadog › Organization Settings › Application Keys.",
        required: true,
      },
      {
        slug: "DD_SITE",
        label: "Datadog site",
        type: "url",
        placeholder: "datadoghq.com",
        hint: "Your Datadog site (e.g. datadoghq.com, datadoghq.eu, us3.datadoghq.com).",
        required: false,
      },
    ],
  },
  {
    id: "notion",
    name: "Notion",
    emoji: "📄",
    description:
      "Access and edit Notion pages, databases, and workspaces. Search content, create new documents, and keep your knowledge base in sync.",
    docsUrl: "https://developers.notion.com/docs/authorization",
    credentials: [
      {
        slug: "NOTION_API_KEY",
        label: "Internal integration token",
        type: "token",
        placeholder: "secret_…",
        hint: "Create an integration at notion.so/my-integrations and copy the Internal Integration Token.",
        required: true,
      },
    ],
  },
  {
    id: "figma",
    name: "Figma",
    emoji: "🎨",
    description:
      "Browse design files, inspect components, and extract design tokens. Bridge the gap between design and engineering workflows.",
    docsUrl: "https://www.figma.com/developers/api#access-tokens",
    credentials: [
      {
        slug: "FIGMA_ACCESS_TOKEN",
        label: "Personal access token",
        type: "token",
        placeholder: "figd_…",
        hint: "Generate at Figma › Account Settings › Security › Personal access tokens.",
        required: true,
      },
    ],
  },
  {
    id: "linear",
    name: "Linear",
    emoji: "📐",
    description:
      "Query issues, manage projects, and track engineering work. Create and update tickets directly from your agent workflow.",
    docsUrl: "https://developers.linear.app/docs/graphql/working-with-the-graphql-api#personal-api-keys",
    credentials: [
      {
        slug: "LINEAR_API_KEY",
        label: "Personal API key",
        type: "token",
        placeholder: "lin_api_…",
        hint: "Generate at Linear › Settings › API › Personal API keys.",
        required: true,
      },
    ],
  },
  {
    id: "snowflake",
    name: "Snowflake",
    emoji: "❄️",
    description:
      "Run SQL queries against your data warehouse. Analyze business metrics, generate reports, and pull structured data directly into conversations.",
    docsUrl: "https://docs.snowflake.com/en/user-guide/admin-account-identifier",
    credentials: [
      {
        slug: "SNOWFLAKE_ACCOUNT",
        label: "Account identifier",
        type: "text",
        placeholder: "xy12345.us-east-1",
        hint: "Your Snowflake account identifier (org-account or legacy format).",
        required: true,
      },
      {
        slug: "SNOWFLAKE_USER",
        label: "Username",
        type: "text",
        placeholder: "john.doe@example.com",
        required: true,
      },
      {
        slug: "SNOWFLAKE_PASSWORD",
        label: "Password",
        type: "password",
        placeholder: "••••••••",
        required: true,
      },
      {
        slug: "SNOWFLAKE_DATABASE",
        label: "Database",
        type: "text",
        placeholder: "MY_DB",
        required: true,
      },
      {
        slug: "SNOWFLAKE_WAREHOUSE",
        label: "Warehouse",
        type: "text",
        placeholder: "COMPUTE_WH",
        required: false,
      },
    ],
  },
  {
    id: "postgres",
    name: "Postgres",
    emoji: "🐘",
    description:
      "Query PostgreSQL databases for operational data. Supports complex queries with full schema introspection.",
    credentials: [
      {
        slug: "POSTGRES_URL",
        label: "Connection URL",
        type: "token",
        placeholder: "host:5432/dbname (full URL with user and password)",
        hint: "Full connection string including credentials. Never share this.",
        required: true,
      },
    ],
  },
  {
    id: "salesforce",
    name: "Salesforce",
    emoji: "☁️",
    description:
      "Access CRM data including leads, opportunities, accounts, and contacts. Enrich sales workflows and customer context.",
    docsUrl: "https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/quickstart_oauth.htm",
    credentials: [
      {
        slug: "SF_INSTANCE_URL",
        label: "Instance URL",
        type: "url",
        placeholder: "https://yourorg.salesforce.com",
        required: true,
      },
      {
        slug: "SF_CLIENT_ID",
        label: "Connected app client ID",
        type: "text",
        placeholder: "3MVG9…",
        required: true,
      },
      {
        slug: "SF_CLIENT_SECRET",
        label: "Connected app client secret",
        type: "token",
        placeholder: "••••••••",
        required: true,
      },
    ],
  },
  {
    id: "gong",
    name: "Gong",
    emoji: "🔔",
    description:
      "Access call recordings, transcripts, and insights. Pull context from customer conversations to inform follow-ups and strategy.",
    docsUrl: "https://us-11515.app.gong.io/settings/api/overview",
    credentials: [
      {
        slug: "GONG_ACCESS_KEY",
        label: "Access key",
        type: "text",
        placeholder: "e.g. a1b2c3…",
        hint: "Generated at Gong › Settings › API.",
        required: true,
      },
      {
        slug: "GONG_ACCESS_SECRET",
        label: "Access key secret",
        type: "token",
        placeholder: "••••••••",
        required: true,
      },
    ],
  },
  {
    id: "google-workspace",
    name: "Google Workspace",
    emoji: "🔵",
    logoSrc: "/integration-logos/google-workspace.svg",
    description:
      "Access Gmail, Calendar, and Drive as yourself. Read emails, check schedules, and pull documents directly into your agent context. Connects via Google OAuth — no service account required.",
    oauthFlow: true,
    oauthStartEndpoint: "/api/google-user-oauth/start",
    oauthSecretSlugs: ["GOOGLE_OAUTH_CREDENTIALS"],
    credentials: [],
  },
  {
    id: "jira",
    name: "Jira",
    emoji: "🔷",
    description:
      "Track issues, manage sprints, and query project boards. Integrates with your existing Atlassian workflows.",
    docsUrl: "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/",
    credentials: [
      {
        slug: "JIRA_URL",
        label: "Jira base URL",
        type: "url",
        placeholder: "https://yourorg.atlassian.net",
        required: true,
      },
      {
        slug: "JIRA_EMAIL",
        label: "Account email",
        type: "text",
        placeholder: "you@example.com",
        required: true,
      },
      {
        slug: "JIRA_API_TOKEN",
        label: "API token",
        type: "token",
        placeholder: "ATATT3xFfGF0…",
        hint: "Generate at id.atlassian.com › Manage your account › Security › API tokens.",
        required: true,
      },
    ],
  },
  {
    id: "confluence",
    name: "Confluence",
    emoji: "📚",
    description:
      "Search and read Confluence pages. Surface internal documentation and runbooks in your agent conversations.",
    docsUrl: "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/",
    credentials: [
      {
        slug: "CONFLUENCE_URL",
        label: "Confluence base URL",
        type: "url",
        placeholder: "https://yourorg.atlassian.net/wiki",
        required: true,
      },
      {
        slug: "CONFLUENCE_EMAIL",
        label: "Account email",
        type: "text",
        placeholder: "you@example.com",
        required: true,
      },
      {
        slug: "CONFLUENCE_API_TOKEN",
        label: "API token",
        type: "token",
        placeholder: "ATATT3xFfGF0…",
        hint: "Generate at id.atlassian.com › Manage your account › Security › API tokens.",
        required: true,
      },
    ],
  },
  {
    id: "pagerduty",
    name: "PagerDuty",
    emoji: "🚨",
    description:
      "Read on-call schedules, acknowledge incidents, and manage escalations. Keep your agent aware of live production issues.",
    docsUrl: "https://developer.pagerduty.com/docs/rest-api-v2/authentication/",
    credentials: [
      {
        slug: "PAGERDUTY_API_TOKEN",
        label: "REST API key",
        type: "token",
        placeholder: "u+…",
        hint: "Create a User API key at PagerDuty › Integrations › API Access Keys.",
        required: true,
      },
    ],
  },
];
