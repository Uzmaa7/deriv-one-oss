import { useState } from "react";
import { ChevronDown, ChevronUp, Sparkles, Trash2 } from "lucide-react";
import SkillPicker, { type SkillAccess } from "@/components/computer/SkillPicker";
import type { ArtifactOption, FlowGraphNode } from "@/lib/flowsApi";

type FlowStepCardProps = {
  step: FlowGraphNode;
  index: number;
  /** context keys written by every earlier step — offered as insertable chips */
  priorWrites: string[];
  onChange: (next: FlowGraphNode) => void;
  onRemove: () => void;
  skills: SkillAccess[];
  artifactsBySlug: Record<string, ArtifactOption[]>;
};

function cfg(step: FlowGraphNode): Record<string, unknown> {
  return step.config ?? {};
}

function withCfg(step: FlowGraphNode, patch: Record<string, unknown>): FlowGraphNode {
  return { ...step, config: { ...cfg(step), ...patch } };
}

/** "alerts, tier" -> {writes: ["alerts","tier"], schema: {...}} — lets a user
 * name output fields without hand-writing JSON Schema; the engine still gets
 * a real output_schema + context_writes contract underneath. */
function parseOutputKeys(raw: string): { context_writes: string[]; output_schema: Record<string, unknown> } {
  const keys = raw
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
  const properties: Record<string, unknown> = {};
  for (const k of keys) properties[k] = { type: "string" };
  return { context_writes: keys, output_schema: { type: "object", properties } };
}

function ContextChips({ keys, onInsert }: { keys: string[]; onInsert: (token: string) => void }) {
  if (!keys.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        Insert from earlier steps:
      </span>
      {keys.map((k) => (
        <button
          key={k}
          type="button"
          onClick={() => onInsert(`{{context.${k}}}`)}
          className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[10px] text-slate-600 hover:border-[#FF444F]/40 hover:bg-[#FF444F]/5"
        >
          {`{{context.${k}}}`}
        </button>
      ))}
    </div>
  );
}

export default function FlowStepCard({
  step,
  index,
  priorWrites,
  onChange,
  onRemove,
  skills,
  artifactsBySlug,
}: FlowStepCardProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const c = cfg(step);
  const outputKeysRaw = Array.isArray(c.context_writes) ? (c.context_writes as string[]).join(", ") : "";
  const skillSlug = typeof c.skill_slug === "string" ? c.skill_slug : "";
  const artifactsForSkill = artifactsBySlug[skillSlug] ?? [];
  const mode = typeof c.mode === "string" ? c.mode : "agentic";

  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-900 text-[10px] font-bold text-white">
          {index + 1}
        </span>
        <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">
          <Sparkles className="h-3 w-3" /> Skill
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-slate-400">
          {skillSlug || "no skill chosen"}
        </span>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          {collapsed ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={onRemove}
          aria-label="Remove step"
          className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {!collapsed && (
        <div className="space-y-3 px-3 py-3">
          <>
              <div>
                <label className="mb-1 block text-[11px] font-semibold text-slate-600">Skill</label>
                <button
                  type="button"
                  onClick={() => setSkillPickerOpen(true)}
                  className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-left text-sm text-slate-700 hover:border-slate-300"
                >
                  {skillSlug || <span className="text-slate-400">Choose a skill…</span>}
                </button>
              </div>

              {skillSlug && (
                <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5">
                  <button
                    type="button"
                    disabled={artifactsForSkill.length === 0}
                    onClick={() => onChange(withCfg(step, { mode: "artifact" }))}
                    className={`flex-1 rounded-md px-2 py-1 text-[11px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
                      mode === "artifact" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"
                    }`}
                    title={artifactsForSkill.length === 0 ? "No approved artifact on this skill" : undefined}
                  >
                    Artifact (typed)
                  </button>
                  <button
                    type="button"
                    onClick={() => onChange(withCfg(step, { mode: "agentic" }))}
                    className={`flex-1 rounded-md px-2 py-1 text-[11px] font-semibold transition ${
                      mode === "agentic" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"
                    }`}
                  >
                    Agentic (prompt)
                  </button>
                </div>
              )}

              {mode === "artifact" ? (
                <>
                  <div>
                    <label className="mb-1 block text-[11px] font-semibold text-slate-600">Artifact</label>
                    <select
                      value={(c.artifact_name as string) || ""}
                      onChange={(e) => onChange(withCfg(step, { artifact_name: e.target.value }))}
                      className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm"
                    >
                      <option value="">Choose…</option>
                      {artifactsForSkill.map((a) => (
                        <option key={a.name} value={a.name}>
                          {a.name} — {a.summary}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-semibold text-slate-600">
                      Params (JSON — use {"{{context.key}}"} for earlier step output)
                    </label>
                    <ContextChips
                      keys={priorWrites}
                      onInsert={(token) =>
                        onChange(
                          withCfg(step, {
                            params_raw: `${(c.params_raw as string) ?? JSON.stringify(c.params ?? {}, null, 2)}${token}`,
                          }),
                        )
                      }
                    />
                    <textarea
                      rows={3}
                      value={(c.params_raw as string) ?? JSON.stringify(c.params ?? {}, null, 2)}
                      onChange={(e) => onChange(withCfg(step, { params_raw: e.target.value }))}
                      onBlur={(e) => {
                        try {
                          const parsed = JSON.parse(e.target.value || "{}");
                          onChange(withCfg(step, { params: parsed, params_raw: undefined }));
                        } catch {
                          // Leave params_raw in place — invalid JSON stays editable, not silently dropped.
                        }
                      }}
                      className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 font-mono text-xs"
                      placeholder='{"client_id": "{{context.client_id}}"}'
                    />
                  </div>
                </>
              ) : (
                <div>
                  <label className="mb-1 block text-[11px] font-semibold text-slate-600">Instructions</label>
                  <ContextChips
                    keys={priorWrites}
                    onInsert={(token) => onChange(withCfg(step, { instructions: `${(c.instructions as string) || ""} ${token}` }))}
                  />
                  <textarea
                    rows={3}
                    value={(c.instructions as string) || ""}
                    onChange={(e) => onChange(withCfg(step, { instructions: e.target.value }))}
                    className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm"
                    placeholder="Look up the tier for {{context.client_id}}."
                  />
                </div>
              )}
          </>

          <div>
            <label className="mb-1 block text-[11px] font-semibold text-slate-600">
              Output keys this step produces (comma separated, optional)
            </label>
            <input
              value={outputKeysRaw}
              onChange={(e) => onChange(withCfg(step, parseOutputKeys(e.target.value)))}
              className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 font-mono text-xs"
              placeholder="client_id, tier"
            />
          </div>
        </div>
      )}

      {skillPickerOpen && (
        <div className="relative h-80 border-t border-slate-100">
          <SkillPicker
            skills={skills}
            selected={skillSlug ? [skillSlug] : []}
            onToggle={(slug) => {
              onChange(withCfg(step, { skill_slug: slug }));
              setSkillPickerOpen(false);
            }}
            onClose={() => setSkillPickerOpen(false)}
          />
        </div>
      )}
    </div>
  );
}
