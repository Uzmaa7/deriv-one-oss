/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional baked-in Cf Access application JWT (docker dev only — prefer Tunnel). */
  readonly VITE_AUTH_TOKEN?: string;
  /** Slack Events API Gateway base URL (no trailing slash). Staging builds set this. */
  readonly VITE_SLACK_GATEWAY_BASE?: string;
  /** HMAC attestation header value for platform UI API calls (not present in Lab scaffolds). */
  readonly VITE_PLATFORM_CLIENT_TOKEN?: string;
}
