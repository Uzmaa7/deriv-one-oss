'use strict';

/** Slack app manifest limits for OAuth auto-create registration. */

export const SLACK_MAX_APP_NAME = 35;
export const SLACK_MIN_LONG_DESCRIPTION = 175;
export const SLACK_MAX_LONG_DESCRIPTION = 4000;
export const SLACK_AVATAR_MAX_BYTES = 2 * 1024 * 1024;

export const AVATAR_ALLOWED_MIME_TYPES = [
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
] as const;

export const AVATAR_REQUIRED_MSG =
  'Upload an agent photo before continuing. JPEG, PNG, GIF, or WebP, up to 2 MB. It becomes the Slack app icon.';

const SLACK_BOT_DISPLAY_NAME_RE = /^[a-z0-9](?:[a-z0-9\-_.]*[a-z0-9])?$/;

/** Slack OAuth auto-create: max 35 chars + bot display charset. */
export function validateSlackAgentName(name: string | null | undefined): string | null {
  const trimmed = (name ?? '').trim();
  if (!trimmed) return 'Agent name is required.';
  const n = trimmed.length;
  if (n > SLACK_MAX_APP_NAME) {
    return (
      `Agent name must be 35 characters or fewer for Slack app creation (currently ${n}/${SLACK_MAX_APP_NAME}). ` +
      'Shorten the name and try again.'
    );
  }
  if (!SLACK_BOT_DISPLAY_NAME_RE.test(trimmed.toLowerCase())) {
    return (
      'Slack bot names can only contain lowercase letters, numbers, hyphens, underscores, and periods — ' +
      'no spaces or special characters. Example: treasury-ops-bot. Update the agent name to match.'
    );
  }
  return null;
}

/** Description-only — maps to Slack display_information.long_description. */
export function validateSlackLongDescription(description: string | null | undefined): string | null {
  const text = (description ?? '').trim();
  const n = text.length;
  if (n < SLACK_MIN_LONG_DESCRIPTION) {
    return (
      `Description must be at least ${SLACK_MIN_LONG_DESCRIPTION} characters for Slack app creation ` +
      `(currently ${n}/${SLACK_MIN_LONG_DESCRIPTION}). Expand what the agent does, and keep your PIC ` +
      'and Team Incharge contacts in this field.'
    );
  }
  if (n > SLACK_MAX_LONG_DESCRIPTION) {
    return (
      `Description must be at most ${SLACK_MAX_LONG_DESCRIPTION} characters for Slack ` +
      `(currently ${n}/${SLACK_MAX_LONG_DESCRIPTION}). Shorten the Description field.`
    );
  }
  return null;
}

export function avatarTooLargeMessage(sizeBytes: number): string {
  const sizeMb = (sizeBytes / (1024 * 1024)).toFixed(1);
  return (
    `This image is ${sizeMb} MB. Slack app icons must be 2 MB or smaller. ` +
    'Compress the image or choose a smaller file, then upload again.'
  );
}

export function avatarWrongTypeMessage(mimeOrExt: string): string {
  return (
    `Unsupported image type: ${mimeOrExt}. Upload a JPEG, PNG, GIF, or WebP file up to 2 MB.`
  );
}

export function validateAvatarFile(file: File): string | null {
  if (!AVATAR_ALLOWED_MIME_TYPES.includes(file.type as typeof AVATAR_ALLOWED_MIME_TYPES[number])) {
    const label = file.type || file.name.split('.').pop() || 'unknown';
    return avatarWrongTypeMessage(label);
  }
  if (file.size > SLACK_AVATAR_MAX_BYTES) {
    return avatarTooLargeMessage(file.size);
  }
  return null;
}
