import { ShieldAlert } from "lucide-react";

/**
 * Badge for a skill's connected-system risk level (max_system_risk_level),
 * distinct from the skill's own manually-set ACL risk_level. Risk levels
 * are free text ("Critical", "High Risk", "Low risk", etc.) so this matches
 * loosely on keywords rather than an exact enum.
 */
function colorsFor(riskLevel: string): string {
  const v = riskLevel.toLowerCase();
  if (v.includes("critical")) return "text-red-700 bg-red-50 border-red-200";
  if (v.includes("high")) return "text-orange-700 bg-orange-50 border-orange-200";
  if (v.includes("medium") || v.includes("elevated")) return "text-amber-700 bg-amber-50 border-amber-200";
  if (v.includes("low")) return "text-emerald-700 bg-emerald-50 border-emerald-200";
  return "text-slate-600 bg-slate-100 border-slate-200";
}

export function SystemCriticalityBadge({
  riskLevel,
  className = "",
}: {
  riskLevel: string | null | undefined;
  className?: string;
}) {
  if (!riskLevel) return null;
  return (
    <span
      title={`Connected to a system rated "${riskLevel}" in the catalogue`}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border shrink-0 ${colorsFor(riskLevel)} ${className}`}
    >
      <ShieldAlert className="w-2.5 h-2.5" />
      {riskLevel}
    </span>
  );
}
