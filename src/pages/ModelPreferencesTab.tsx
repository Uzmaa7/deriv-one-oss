import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { apiFetch } from "@/lib/api";

type ModelPreferencesResponse = {
  catalog_models: string[];
  enabled_models: string[];
  user_has_custom_selection: boolean;
  platform_allowlist: string[] | null;
  preferred_model: string | null;
  picker: { default?: string };
};

export default function ModelPreferencesTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalogModels, setCatalogModels] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preferred, setPreferred] = useState("");
  const [hasCustomSelection, setHasCustomSelection] = useState(false);
  const [platformAllowlist, setPlatformAllowlist] = useState<string[] | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/api/me/model-preferences");
      if (!res.ok) throw new Error("Could not load model preferences.");
      const data = (await res.json()) as ModelPreferencesResponse;
      setCatalogModels(data.catalog_models ?? []);
      const enabled = data.enabled_models ?? data.catalog_models ?? [];
      setSelected(new Set(enabled));
      setPreferred(data.preferred_model || data.picker?.default || enabled[0] || "");
      setHasCustomSelection(Boolean(data.user_has_custom_selection));
      setPlatformAllowlist(data.platform_allowlist);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load models.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleModels = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return catalogModels;
    return catalogModels.filter(id => id.toLowerCase().includes(q));
  }, [catalogModels, filter]);

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(catalogModels));
  const clearAll = () => setSelected(new Set());

  const save = async () => {
    if (selected.size === 0) {
      setError("Select at least one model.");
      return;
    }
    const enabled = catalogModels.filter(id => selected.has(id));
    const preferredModel = preferred && enabled.includes(preferred) ? preferred : enabled[0];
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch("/api/me/model-preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled_models: enabled,
          preferred_model: preferredModel,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Could not save model preferences.");
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch("/api/me/model-preferences", { method: "DELETE" });
      if (!res.ok) throw new Error("Could not reset model preferences.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reset failed.");
    } finally {
      setSaving(false);
    }
  };

  const enabledList = catalogModels.filter(id => selected.has(id));

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-8 py-12 space-y-6">
      <div>
        <h2 className="text-lg font-bold text-slate-900">Workspace models</h2>
        <p className="mt-1 text-sm text-slate-500">
          Choose which models appear in Workspace and Agents. Useful for LM Studio, Ollama, and
          other gateways that expose raw model ids instead of a LiteLLM catalog.
        </p>
        {platformAllowlist && platformAllowlist.length > 0 && (
          <p className="mt-2 text-xs text-slate-400">
            Platform allowlist is active ({platformAllowlist.length} model
            {platformAllowlist.length === 1 ? "" : "s"}).
          </p>
        )}
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {catalogModels.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-6 text-sm text-slate-500">
          No models were returned from your gateway. Check the model gateway URL in setup, then
          refresh.
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Filter models…"
              className="flex-1 min-w-[200px] text-sm px-3 py-2 border border-slate-200 rounded-lg"
            />
            <button
              type="button"
              onClick={selectAll}
              className="text-xs font-semibold text-slate-600 hover:text-slate-900"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={clearAll}
              className="text-xs font-semibold text-slate-600 hover:text-slate-900"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => void load()}
              className="inline-flex items-center gap-1 text-xs font-semibold text-slate-600 hover:text-slate-900"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </button>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white divide-y divide-slate-100 max-h-80 overflow-y-auto">
            {visibleModels.map(id => (
              <label
                key={id}
                className="flex items-center gap-3 px-4 py-3 text-sm cursor-pointer hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  checked={selected.has(id)}
                  onChange={() => toggle(id)}
                  className="rounded border-slate-300"
                />
                <span className="font-mono text-slate-800 break-all">{id}</span>
              </label>
            ))}
            {visibleModels.length === 0 && (
              <p className="px-4 py-6 text-sm text-slate-400">No models match your filter.</p>
            )}
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-600">Default model</label>
            <select
              value={preferred}
              onChange={e => setPreferred(e.target.value)}
              className="mt-1 w-full text-sm px-3 py-2 border border-slate-200 rounded-lg font-mono"
              disabled={enabledList.length === 0}
            >
              {enabledList.map(id => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-slate-400">
              Used when you start a new Workspace chat.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || selected.size === 0}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[#FF444F] text-white text-sm font-semibold hover:bg-[#e03d47] disabled:opacity-50"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save selection
            </button>
            {hasCustomSelection && (
              <button
                type="button"
                onClick={() => void reset()}
                disabled={saving}
                className="text-sm font-semibold text-slate-600 hover:text-slate-900"
              >
                Use all gateway models
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
