const PIC_RE = /PIC:\s+@?[\w.-]+/i;
const TEAM_RE = /(?:Team Incharge|Owning Team|Team):\s+@?[\w.-]+/i;

/** Return a user-facing error, or null when the description is valid. */
export function validateAgentDescription(description: string | null | undefined): string | null {
  const text = (description ?? "").trim();
  if (!text) return "Description is required.";
  const hasPic = PIC_RE.test(text);
  const hasTeam = TEAM_RE.test(text);
  if (!hasPic && !hasTeam) {
    return (
      "Description must include a PIC contact (e.g. PIC: @username) " +
      "and an owning team (e.g. Team Incharge: @team-name)."
    );
  }
  if (!hasPic) {
    return "Description must include a PIC contact (e.g. PIC: @username).";
  }
  if (!hasTeam) {
    return "Description must include an owning team (e.g. Team Incharge: @team-name).";
  }
  const remaining = text.replace(PIC_RE, "").replace(TEAM_RE, "").trim();
  if (!remaining) {
    return "Description must explain what the agent does, not just PIC and Team contacts.";
  }
  return null;
}

/** Return a user-facing error, or null when the usecase description is valid. */
export function validateUsecaseDescription(
  usecaseDescription: string | null | undefined,
): string | null {
  const text = (usecaseDescription ?? "").trim();
  if (!text) return "Usecase description is required.";
  return null;
}
