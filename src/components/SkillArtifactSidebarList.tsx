import { useCallback, useEffect, useState } from "react";
import { FileCode2, Loader2 } from "lucide-react";
import { listSkillArtifacts, type ArtifactSummary } from "@/lib/skillArtifactsApi";

export function SkillArtifactSidebarList({
  slug,
  onSelect,
}: {
  slug: string;
  onSelect: (artifact: ArtifactSummary) => void;
}) {
  const [items, setItems] = useState<ArtifactSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listSkillArtifacts(slug)
      .then(setItems)
      .catch(e => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Loader2 className="w-3 h-3 animate-spin" /> Artifacts…
      </div>
    );
  }

  if (error) {
    return <p className="text-xs text-red-600">{error}</p>;
  }

  const activeItems = (() => {
    const latestByName = new Map<string, ArtifactSummary>();
    for (const a of items) {
      if (a.disabled) continue;
      const cur = latestByName.get(a.name);
      if (!cur || a.version > cur.version) latestByName.set(a.name, a);
    }
    return Array.from(latestByName.values()).sort((a, b) => a.name.localeCompare(b.name));
  })();

  if (activeItems.length === 0) {
    return <p className="text-xs text-slate-400">No published artifacts yet.</p>;
  }

  return (
    <ul className="space-y-1.5">
      {activeItems.map(a => (
        <li key={a.id}>
          <button
            type="button"
            onClick={() => onSelect(a)}
            className="w-full text-left rounded-lg border border-slate-200 bg-white px-3 py-2 hover:border-slate-300 hover:bg-slate-50 transition-colors"
          >
            <div className="flex items-center gap-2">
              <FileCode2 className="w-3.5 h-3.5 text-slate-400 shrink-0" />
              <span className="text-xs font-mono font-semibold text-slate-800 truncate">
                {a.name}
              </span>
              <span className="text-[10px] text-slate-400">v{a.version}</span>
            </div>
            {a.api_summary && (
              <p className="text-[11px] text-slate-500 mt-1 line-clamp-2 pl-5">{a.api_summary}</p>
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}
