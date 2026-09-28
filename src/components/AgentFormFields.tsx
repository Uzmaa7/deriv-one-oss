import { Hash, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { validateAgentDescription, validateUsecaseDescription } from "@/lib/agentDescription";
import {
  SLACK_MAX_APP_NAME,
  SLACK_MIN_LONG_DESCRIPTION,
  validateSlackAgentName,
  validateSlackLongDescription,
} from "@/lib/slackLimits";
import { AvailableSkill, InferredSkill, SOUL_TEMPLATES, TriggerMode, validateAgentName } from "../data/agents";
import { useSkillList } from "@/lib/SkillAccessContext";
import SchedulePicker from "./SchedulePicker";
import { useSkillAccess } from "@/lib/SkillAccessContext";
import { modelDisplayLabel } from "@/components/composer/shared";

export interface AgentFormState {
  name: string;
  description: string;
  usecase_description: string;
  avatar_url: string | null;
  slack_app_id: string;
  skill_ids: string[];
  channel_ids: string[];
  trigger_mode: TriggerMode;
  soul_text: string;
  /** Canonical model slug, or null for platform default (env MODEL). */
  llm_model: string | null;
  schedule_enabled: boolean;
  schedule_cron: string;
  schedule_prompt: string;
  schedule_output_channel: string;
}

export const emptyAgentForm = (): AgentFormState => ({
  name: "",
  description: "",
  usecase_description: "",
  avatar_url: null,
  slack_app_id: "",
  skill_ids: [],
  channel_ids: [],
  trigger_mode: "mention",
  soul_text: "",
  llm_model: null,
  schedule_enabled: false,
  schedule_cron: "0 9 * * *",
  schedule_prompt: "",
  schedule_output_channel: "",
});

export function agentModelLabel(slug: string | null | undefined): string {
  if (!slug) return "Platform default";
  return slug;
}

export default function AgentFormFields({
  form,
  onChange,
  disableSlackAppId = false,
  hideSlackAppId = false,
  showSchedule = true,
  agentId,
  slackLimitsMode = false,
}: {
  form: AgentFormState;
  onChange: (next: AgentFormState) => void;
  disableSlackAppId?: boolean;
  hideSlackAppId?: boolean;
  showSchedule?: boolean;
  /** When set (editing an existing agent), enables the per-channel "All messages" toggle. */
  agentId?: string;
  /** OAuth auto-create registration: enforce Slack manifest limits on name + description. */
  slackLimitsMode?: boolean;
}) {
  const [skills, setSkills] = useState<AvailableSkill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const { accessibleSlugs, loading: accessLoading } = useSkillAccess();
  const mockSkills = useSkillList();

  // Auto skill inference state.
  const [inferring, setInferring] = useState(false);
  const [suggestionSlugs, setSuggestionSlugs] = useState<Set<string>>(new Set());
  const [inferError, setInferError] = useState<string | null>(null);
  // True once the user manually toggles a skill — suppresses auto-run-on-blur so we
  // never stomp a deliberate choice. Reset after a successful inference run.
  const userEditedSkills = useRef(false);
  const lastInferredDesc = useRef("");

  const runInference = async () => {
    const desc = form.description.trim();
    if (!desc || inferring) return;
    setInferring(true);
    setInferError(null);
    try {
      const res = await apiFetch("/api/agents/infer-skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: form.name.trim(), description: desc }),
      });
      const data: { skills: InferredSkill[] } = await res.json();
      const slugs = new Set<string>((data.skills ?? []).map(s => s.slug));
      setSuggestionSlugs(slugs);
      lastInferredDesc.current = desc;
      userEditedSkills.current = false;
      // Merge inferred skills into the selection (union — never uncheck user picks).
      const merged = Array.from(new Set([...form.skill_ids, ...slugs]));
      if (merged.length !== form.skill_ids.length) {
        set("skill_ids", merged);
      }
    } catch {
      setInferError("Couldn't suggest skills — pick manually.");
    } finally {
      setInferring(false);
    }
  };

  useEffect(() => {
    apiFetch("/api/agents/available-skills")
      .then(r => r.json())
      .then((data: AvailableSkill[]) => {
        setSkills(Array.isArray(data) ? data : []);
        setSkillsLoading(false);
      })
      .catch(() => {
        setSkills([]);
        setSkillsLoading(false);
      });
  }, []);

  useEffect(() => {
    apiFetch("/api/models")
      .then(async (res) => {
        if (!res.ok) return;
        const data = await res.json();
        const ids = Array.isArray(data.models)
          ? data.models.map((m: string | { id: string }) => typeof m === "string" ? m : m.id)
          : [];
        if (ids.length > 0) setModelOptions(ids);
      })
      .catch(() => { /* keep empty — platform default still works */ });
  }, []);

  const set = <K extends keyof AgentFormState>(k: K, v: AgentFormState[K]) =>
    onChange({ ...form, [k]: v });

  const toggleSkill = (slug: string) => {
    userEditedSkills.current = true;
    if (form.skill_ids.includes(slug)) {
      set("skill_ids", form.skill_ids.filter(s => s !== slug));
    } else {
      set("skill_ids", [...form.skill_ids, slug]);
    }
  };

  // ── Per-channel "All messages" (ambient) mode — edit view only ─────────────
  const normalizeChannelKey = (ch: string) => ch.trim().replace(/^#/, "").toLowerCase();
  const [ambientState, setAmbientState] = useState<Record<string, { enabled: boolean; busy: boolean; error?: string }>>({});

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    apiFetch(`/api/agents/${agentId}/channel-configs`)
      .then(r => r.json())
      .then((data: { channel_configs?: Array<{ channel_id: string; channel_name: string | null; join_behavior?: { proactivity?: string } }> }) => {
        if (cancelled) return;
        const byKey = new Map<string, boolean>();
        for (const cfg of data.channel_configs ?? []) {
          const enabled = cfg.join_behavior?.proactivity === "ambient";
          if (cfg.channel_id) byKey.set(normalizeChannelKey(cfg.channel_id), enabled);
          if (cfg.channel_name) byKey.set(normalizeChannelKey(cfg.channel_name), enabled);
        }
        setAmbientState(prev => {
          const next = { ...prev };
          for (const ch of form.channel_ids) {
            const key = normalizeChannelKey(ch);
            if (byKey.has(key)) next[key] = { enabled: byKey.get(key)!, busy: false };
          }
          return next;
        });
      })
      .catch(() => {
        // Best-effort — toggles simply default to "off" if this fails.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  const toggleAmbient = async (channel: string) => {
    if (!agentId) return;
    const key = normalizeChannelKey(channel);
    const current = ambientState[key]?.enabled ?? false;
    setAmbientState(prev => ({ ...prev, [key]: { enabled: current, busy: true } }));
    try {
      const res = await apiFetch(`/api/agents/${agentId}/channels/ambient`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_ref: channel, enabled: !current }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || "Failed to update channel.");
      }
      setAmbientState(prev => ({ ...prev, [key]: { enabled: !current, busy: false } }));
    } catch (err) {
      setAmbientState(prev => ({
        ...prev,
        [key]: { enabled: current, busy: false, error: err instanceof Error ? err.message : "Failed to update channel." },
      }));
    }
  };

  const addChannel = (raw: string) => {
    const trimmed = raw.trim().replace(/,$/, "");
    if (!trimmed) return;
    if (form.channel_ids.includes(trimmed)) return;
    set("channel_ids", [...form.channel_ids, trimmed]);
  };

  const removeChannel = (ch: string) =>
    set("channel_ids", form.channel_ids.filter(c => c !== ch));

  const nameError = form.name.trim()
    ? (slackLimitsMode ? validateSlackAgentName(form.name) : validateAgentName(form.name))
    : null;
  const [descTouched, setDescTouched] = useState(false);
  const [usecaseTouched, setUsecaseTouched] = useState(false);
  const descriptionError = descTouched ? validateAgentDescription(form.description) : null;
  const slackDescError = slackLimitsMode && descTouched && !descriptionError
    ? validateSlackLongDescription(form.description)
    : null;
  const usecaseError = usecaseTouched ? validateUsecaseDescription(form.usecase_description) : null;
  const descLen = form.description.trim().length;
  const nameLen = form.name.trim().length;

  return (
    <div className="space-y-6">
      <Section title="Identity">
        <Field label="Agent name">
          <input
            required
            type="text"
            value={form.name}
            onChange={e => set("name", e.target.value)}
            placeholder={slackLimitsMode ? "treasury-ops-bot" : "Treasury Rex"}
            maxLength={slackLimitsMode ? SLACK_MAX_APP_NAME : 120}
            aria-invalid={Boolean(nameError)}
            className={`w-full text-sm px-3 py-2 border rounded-lg focus:outline-none ${
              nameError
                ? "border-red-400 focus:border-red-500"
                : "border-slate-200 focus:border-slate-400"
            }`}
          />
          {slackLimitsMode && (
            <p className={`text-[11px] mt-1 ${nameLen > SLACK_MAX_APP_NAME ? "text-red-600" : "text-slate-400"}`}>
              {nameLen}/{SLACK_MAX_APP_NAME} — Slack app names are limited to 35 characters; use lowercase letters,
              numbers, hyphens, underscores, or periods only.
            </p>
          )}
          {nameError && (
            <p className="text-[11px] text-red-600 mt-1.5 leading-relaxed">{nameError}</p>
          )}
        </Field>

        <Field
          label="Description"
          hint={
            slackLimitsMode
              ? "Required. Explain what the agent does (min 175 characters for Slack), then include PIC: @owner and Team Incharge: @your-team."
              : "Required. Explain what the agent does, then include PIC: @owner and Team Incharge: @your-team."
          }
        >
          <textarea
            required
            value={form.description}
            onChange={e => set("description", e.target.value)}
            onBlur={() => {
              setDescTouched(true);
              const desc = form.description.trim();
              // Auto-suggest on blur only if the description changed since the last
              // run and the user hasn't manually toggled a skill since then.
              if (
                desc &&
                desc !== lastInferredDesc.current &&
                !userEditedSkills.current &&
                !inferring
              ) {
                runInference();
              }
            }}
            placeholder={
              "What does this agent do? Who is it for?\n" +
              "PIC: @owner\n" +
              "Team Incharge: @your-team"
            }
            maxLength={1000}
            rows={4}
            aria-invalid={Boolean(descriptionError || slackDescError)}
            className={`w-full text-sm px-3 py-2 border rounded-lg focus:outline-none resize-none leading-relaxed ${
              descriptionError || slackDescError
                ? "border-red-400 focus:border-red-500"
                : "border-slate-200 focus:border-slate-400"
            }`}
          />
          {slackLimitsMode && (
            <p className={`text-[11px] mt-1 ${descLen < SLACK_MIN_LONG_DESCRIPTION ? "text-red-600" : "text-slate-400"}`}>
              {descLen}/{SLACK_MIN_LONG_DESCRIPTION} minimum for Slack app creation
            </p>
          )}
          {descriptionError && (
            <p className="text-[11px] text-red-600 mt-1.5 leading-relaxed">{descriptionError}</p>
          )}
          {!descriptionError && slackDescError && (
            <p className="text-[11px] text-red-600 mt-1.5 leading-relaxed">{slackDescError}</p>
          )}
        </Field>

        <Field
          label="Usecase description"
          hint="Required. Describe the exact task and where it runs (public channel / private channel / DM) — this drives which Slack permissions get requested. Be specific; vague descriptions get fewer scopes."
        >
          <textarea
            required
            value={form.usecase_description}
            onChange={e => set("usecase_description", e.target.value)}
            onBlur={() => setUsecaseTouched(true)}
            placeholder={
              "What are you trying to achieve with this agent?\n" +
              'e.g. "Posts daily P&L summaries to #treasury-ops only" or\n' +
              '"Answers HR policy questions via DM, no channel access needed"'
            }
            maxLength={1000}
            rows={4}
            aria-invalid={Boolean(usecaseError)}
            className={`w-full text-sm px-3 py-2 border rounded-lg focus:outline-none resize-none leading-relaxed ${
              usecaseError
                ? "border-red-400 focus:border-red-500"
                : "border-slate-200 focus:border-slate-400"
            }`}
          />
          {usecaseError && (
            <p className="text-[11px] text-red-600 mt-1.5 leading-relaxed">{usecaseError}</p>
          )}
        </Field>

        {!hideSlackAppId && (
          <Field
            label="Slack App ID"
            hint="Find this on api.slack.com/apps under the app's Basic Information page."
          >
            <input
              required={!hideSlackAppId}
              type="text"
              value={form.slack_app_id}
              onChange={e => set("slack_app_id", e.target.value.trim())}
              placeholder="A0ALU7541JM"
              disabled={disableSlackAppId}
              maxLength={32}
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg font-mono focus:outline-none focus:border-slate-400 disabled:bg-slate-50 disabled:text-slate-500"
            />
          </Field>
        )}
      </Section>

      <Section
        title="Skills"
        hint="Skills you don't have access to are not shown. Cross-department access lands with the Identity model in a later phase."
      >
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={runInference}
            disabled={inferring || !form.description.trim()}
            className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-500" />
            {inferring ? "Suggesting…" : "Suggest skills"}
          </button>
          {suggestionSlugs.size > 0 && (
            <span className="text-[11px] text-slate-500">
              {suggestionSlugs.size} suggested from description
            </span>
          )}
          {inferError && <span className="text-[11px] text-red-500">{inferError}</span>}
        </div>

        {skillsLoading ? (
          <p className="text-xs text-slate-400">Loading available skills...</p>
        ) : skills.length === 0 ? (
          <p className="text-xs text-slate-400">No skills are available to this account.</p>
        ) : (
          <div className="grid sm:grid-cols-2 gap-2">
            {skills.filter(s => accessLoading || accessibleSlugs.has(s.slug)).map(s => {
              const meta = mockSkills.find(m => m.slug === s.slug);
              const checked = form.skill_ids.includes(s.slug);
              const suggested = suggestionSlugs.has(s.slug);
              return (
                <label
                  key={s.slug}
                  className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${checked
                      ? "bg-slate-900 text-white border-slate-900"
                      : "bg-white border-slate-200 hover:border-slate-400"
                    }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSkill(s.slug)}
                    className="mt-0.5 shrink-0"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <p className="text-xs font-semibold truncate">{meta?.name ?? s.slug}</p>
                      {suggested && (
                        <span
                          className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${checked
                              ? "bg-amber-500/30 text-amber-200"
                              : "bg-amber-500/20 text-amber-700"
                            }`}
                          title="Inferred from the agent description"
                        >
                          ✓ suggested
                        </span>
                      )}
                    </div>
                    <p
                      className={`text-[11px] mt-0.5 line-clamp-2 ${checked ? "text-slate-300" : "text-slate-500"
                        }`}
                    >
                      {meta?.description?.slice(0, 110) ?? "—"}
                    </p>
                  </div>
                </label>
              );
            })}
          </div>
        )}
      </Section>

      <Section
        title="Model"
        hint="Which model this agent uses for Slack replies. Leave blank to use the platform default (MODEL)."
      >
        <Field label="Reply model">
          <select
            value={form.llm_model ?? ""}
            onChange={(e) => set("llm_model", e.target.value ? e.target.value : null)}
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 bg-white"
          >
            <option value="">Platform default</option>
            {modelOptions.map((slug) => (
              <option key={slug} value={slug}>
                {modelDisplayLabel(slug)}
              </option>
            ))}
          </select>
        </Field>
      </Section>

      <Section title="Channels & trigger">
        <Field
          label="Channels"
          hint={
            agentId
              ? "Where the agent will listen for activity. Use the channel name (#treasury-ops) or its Slack id (C012AB3CD). Toggle \"All messages\" to have the agent watch every message in a channel — each one still passes through a fast router before the agent replies, so it won't respond to unrelated chatter."
              : "Where the agent will listen for activity. Use the channel name (#treasury-ops) or its Slack id (C012AB3CD). Save the agent first to enable \"All messages\" mode per channel."
          }
        >
          <ChannelChips
            value={form.channel_ids}
            onAdd={addChannel}
            onRemove={removeChannel}
            ambientState={agentId ? ambientState : undefined}
            onToggleAmbient={agentId ? toggleAmbient : undefined}
            normalizeChannelKey={normalizeChannelKey}
          />
        </Field>

        <Field label="Trigger">
          <div className="flex gap-2 flex-wrap">
            {(["mention", "dm", "both"] as const).map(mode => (
              <button
                key={mode}
                type="button"
                onClick={() => set("trigger_mode", mode)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${form.trigger_mode === mode
                    ? "bg-slate-900 text-white border-slate-900"
                    : "bg-white text-slate-700 border-slate-200 hover:border-slate-400"
                  }`}
              >
                {mode === "mention" ? "@mention" : mode === "dm" ? "Direct message" : "Both"}
              </button>
            ))}
          </div>
        </Field>
      </Section>

      <Section
        title="Personality / Soul"
        hint="Free-form system prompt the agent always uses. Templates are quick starting points; edit freely."
      >
        <div className="flex flex-wrap gap-1.5 mb-2">
          {SOUL_TEMPLATES.map(t => (
            <button
              key={t.id}
              type="button"
              onClick={() => set("soul_text", t.text)}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-700 hover:border-slate-400 transition-colors"
            >
              {t.label}
            </button>
          ))}
          {form.soul_text && (
            <button
              type="button"
              onClick={() => set("soul_text", "")}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-red-600 hover:border-red-200 transition-colors"
            >
              Clear
            </button>
          )}
        </div>
        <textarea
          value={form.soul_text}
          onChange={e => set("soul_text", e.target.value)}
          placeholder="Professional and concise. Always includes data sources..."
          rows={5}
          maxLength={8000}
          className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 leading-relaxed font-mono"
        />
      </Section>

      {showSchedule && (
        <Section
          title="Recurring task (optional)"
          hint="Configure a recurring task for this agent."
        >
          <label className="flex items-center gap-2 text-sm text-slate-700 mb-3">
            <input
              type="checkbox"
              checked={form.schedule_enabled}
              onChange={e => set("schedule_enabled", e.target.checked)}
            />
            Add a scheduled task to this agent
          </label>

          {form.schedule_enabled && (
            <div className="space-y-3 pl-6 border-l-2 border-slate-100 ml-1.5">
              <Field label="Prompt">
                <textarea
                  value={form.schedule_prompt}
                  onChange={e => set("schedule_prompt", e.target.value)}
                  placeholder="Daily allocation summary by entity"
                  rows={2}
                  maxLength={4000}
                  className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 resize-none"
                />
              </Field>
              <Field label="Schedule">
                <SchedulePicker
                  value={form.schedule_cron}
                  onChange={v => set("schedule_cron", v)}
                />
              </Field>
              <Field label="Output channel">
                <input
                  type="text"
                  value={form.schedule_output_channel}
                  onChange={e => set("schedule_output_channel", e.target.value)}
                  placeholder="#treasury-ops"
                  maxLength={120}
                  className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
                />
              </Field>
            </div>
          )}
        </Section>
      )}
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
      <header>
        <h3 className="text-sm font-bold text-slate-900">{title}</h3>
        {hint && <p className="text-xs text-slate-500 mt-1 leading-relaxed">{hint}</p>}
      </header>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
        {label}
      </label>
      {children}
      {hint && <p className="text-[11px] text-slate-400 mt-1 leading-relaxed">{hint}</p>}
    </div>
  );
}

function ChannelChips({
  value,
  onAdd,
  onRemove,
  ambientState,
  onToggleAmbient,
  normalizeChannelKey,
}: {
  value: string[];
  onAdd: (ch: string) => void;
  onRemove: (ch: string) => void;
  /** When provided, renders an "All messages" toggle per channel. */
  ambientState?: Record<string, { enabled: boolean; busy: boolean; error?: string }>;
  onToggleAmbient?: (ch: string) => void;
  normalizeChannelKey?: (ch: string) => string;
}) {
  const [draft, setDraft] = useState("");
  const showAmbientToggle = Boolean(ambientState && onToggleAmbient && normalizeChannelKey);
  return (
    <div className="space-y-2">
      {value.length > 0 && (
        showAmbientToggle ? (
          <div className="space-y-1.5">
            {value.map(ch => {
              const key = normalizeChannelKey!(ch);
              const state = ambientState![key];
              return (
                <div
                  key={ch}
                  className="flex items-center justify-between gap-2 text-xs bg-slate-50 border border-slate-200 rounded-lg pl-2 pr-2 py-1.5"
                >
                  <span className="inline-flex items-center gap-1 text-slate-700 min-w-0">
                    <Hash className="w-3 h-3 text-slate-400 shrink-0" />
                    <span className="truncate">{ch.replace(/^#/, "")}</span>
                    <button
                      type="button"
                      onClick={() => onRemove(ch)}
                      className="ml-0.5 p-0.5 rounded-full hover:bg-slate-200 text-slate-500 shrink-0"
                      aria-label={`Remove ${ch}`}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                  <label className="inline-flex items-center gap-1.5 shrink-0 cursor-pointer select-none">
                    {state?.error && (
                      <span className="text-[10px] text-red-500" title={state.error}>⚠</span>
                    )}
                    <span className="text-[11px] text-slate-500">All messages</span>
                    <input
                      type="checkbox"
                      checked={Boolean(state?.enabled)}
                      disabled={Boolean(state?.busy)}
                      onChange={() => onToggleAmbient!(ch)}
                      className="w-3.5 h-3.5"
                    />
                  </label>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {value.map(ch => (
              <span
                key={ch}
                className="inline-flex items-center gap-1 text-xs bg-slate-100 text-slate-700 border border-slate-200 rounded-full pl-2 pr-1 py-0.5"
              >
                <Hash className="w-3 h-3 text-slate-400" />
                {ch.replace(/^#/, "")}
                <button
                  type="button"
                  onClick={() => onRemove(ch)}
                  className="ml-0.5 p-0.5 rounded-full hover:bg-slate-200 text-slate-500"
                  aria-label={`Remove ${ch}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
        )
      )}
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              onAdd(draft);
              setDraft("");
            }
          }}
          placeholder="#treasury-ops"
          maxLength={80}
          className="flex-1 text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
        />
        <button
          type="button"
          onClick={() => {
            onAdd(draft);
            setDraft("");
          }}
          className="text-xs font-medium px-3 py-2 border border-slate-200 rounded-lg text-slate-700 hover:bg-slate-50 transition-colors"
        >
          Add
        </button>
      </div>
    </div>
  );
}
