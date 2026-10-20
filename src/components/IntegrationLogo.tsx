import type { Integration } from "@/src/data/integrations";

export default function IntegrationLogo({
  integration,
  className = "w-5 h-5",
}: {
  integration: Pick<Integration, "id" | "name" | "emoji" | "logoSrc">;
  className?: string;
}) {
  if (integration.logoSrc) {
    return (
      <img
        src={integration.logoSrc}
        alt=""
        aria-hidden
        className={`${className} object-contain shrink-0`}
      />
    );
  }

  return (
    <span className={`${className} inline-flex items-center justify-center shrink-0 text-base leading-none`} aria-hidden>
      {integration.emoji}
    </span>
  );
}
