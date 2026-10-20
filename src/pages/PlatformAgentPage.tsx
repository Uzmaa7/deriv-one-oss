import { useEffect, useState } from "react";
import { useBranding } from "@/lib/branding";
import {
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Hash,
  Loader2,
  MessageSquare,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useSkillList } from "@/lib/SkillAccessContext";

// ── Types ─────────────────────────────────────────────────────────────────────

interface PlatformAgent {
  id: string;
  name: string;
  description: string | null;
  status: string;
  slack_app_id: string | null;
  slack_bot_user_id: string | null;
  slack_team_id: string | null;
  skill_ids: string[];
  channel_fallback_behavior: string;
  created_at: string | null;
}

interface ChannelConfig {
  id: string;
  agent_id: string;
  channel_id: string;
  channel_name: string | null;
  channel_topic: string | null;
  channel_type: string | null;
  display_name: string | null;
  soul_text: string | null;
  soul_generated_at: string | null;
  skill_ids: string[];
  guardrails: Array<{ id: string; type: string; enabled: boolean; text?: string }> | null;
  access_policy: { mode: "open" | "allowlist"; emails: string[] };
  memory_scope: "channel" | "global" | "user";
  join_behavior: {
    on_join: "full_intro" | "silent" | "custom";
    custom_message?: string;
    proactivity: "reactive" | "ambient";
  };
  join_status: "pending" | "done" | "error";
  is_active: boolean;
  channel_metadata?: {
    auto_excluded_critical_skills?: string[];
    is_private?: boolean;
    num_members?: number;
  } | null;
  created_at: string;
  updated_at: string;
}

interface ChannelMemory {
  id: string;
  key: string;
  value: string;
  scope: string;
  channel_id: string | null;
  source: string;
  updated_at: string;
}

type Tab = "channels" | "interactions" | "memory" | "settings";

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleDateString("en-GB", {
      day: "numeric", month: "short", year: "numeric",
    });
  } catch {
    return iso;
  }
}

function channelTypeBadge(type: string | null) {
  if (type === "BROADCAST") return <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-rose-50 border border-rose-200 text-rose-700">BROADCAST</span>;
  if (type === "PERSONAL") return <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-violet-50 border border-violet-200 text-violet-700">PERSONAL</span>;
  return <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-emerald-50 border border-emerald-200 text-emerald-700">TEAM</span>;
}

function joinStatusBadge(status: string) {
  if (status === "pending") return <span className="flex items-center gap-1 text-[10px] font-semibold text-amber-600"><Loader2 className="w-3 h-3 animate-spin" />Pending</span>;
  if (status === "error") return <span className="text-[10px] font-semibold text-red-600">Error</span>;
  return null;
}

function soulStatusBadge(cfg: ChannelConfig) {
  if (!cfg.soul_text) return <span className="text-[10px] text-slate-400 italic">No soul yet</span>;
  if (cfg.soul_generated_at) return <span className="text-[10px] text-emerald-600 font-semibold">Auto-generated</span>;
  return <span className="text-[10px] text-blue-600 font-semibold">Edited</span>;
}

// ── Channel config drawer ─────────────────────────────────────────────────────

interface DrawerProps {
  cfg: ChannelConfig;
  agentId: string;
  onClose: () => void;
  onSaved: (updated: ChannelConfig) => void;
  onDeleted: (channelId: string) => void;
  availableSkills: Array<{ id: string; name: string }>;
}

function ChannelConfigDrawer({ cfg, agentId: _agentId, onClose, onSaved, onDeleted, availableSkills }: DrawerProps) {
  const branding = useBranding();
  const [displayName, setDisplayName] = useState(cfg.display_name ?? "");
  const [soulText, setSoulText] = useState(cfg.soul_text ?? "");
  const [skillIds, setSkillIds] = useState<string[]>(cfg.skill_ids ?? []);
  const [memScope, setMemScope] = useState<"channel" | "global" | "user">(cfg.memory_scope ?? "channel");
  const [accessMode, setAccessMode] = useState<"open" | "allowlist">(cfg.access_policy?.mode ?? "open");
  const [allowEmails, setAllowEmails] = useState<string[]>(cfg.access_policy?.emails ?? []);
  const [emailInput, setEmailInput] = useState("");
  const [onJoin, setOnJoin] = useState(cfg.join_behavior?.on_join ?? "full_intro");
  const [customMsg, setCustomMsg] = useState(cfg.join_behavior?.custom_message ?? "");
  const [isActive, setIsActive] = useState(cfg.is_active);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openSections, setOpenSections] = useState({ identity: true, personality: false, skills: false, access: false, memory: false });

  const toggleSection = (s: keyof typeof openSections) => setOpenSections(v => ({ ...v, [s]: !v[s] }));

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const body = {
        display_name: displayName || null,
        soul_text: soulText || null,
        skill_ids: skillIds.length > 0 ? skillIds : null,
        memory_scope: memScope,
        access_policy: { mode: accessMode, emails: allowEmails },
        join_behavior: { on_join: onJoin, custom_message: customMsg || undefined, proactivity: "reactive" },
        is_active: isActive,
      };
      const res = await apiFetch(`/api/platform-agent/channel-configs/${cfg.channel_id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = await res.json();
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleRegenSoul = async () => {
    setRegenerating(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/platform-agent/channel-configs/${cfg.channel_id}/regenerate-soul`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = await res.json();
      setSoulText(updated.soul_text ?? "");
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Regeneration failed");
    } finally {
      setRegenerating(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Remove channel config for #${cfg.channel_name ?? cfg.channel_id}?`)) return;
    setDeleting(true);
    try {
      await apiFetch(`/api/platform-agent/channel-configs/${cfg.channel_id}`, { method: "DELETE" });
      onDeleted(cfg.channel_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  };

  const addEmail = () => {
    const e = emailInput.trim().toLowerCase();
    if (e && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e) && !allowEmails.includes(e)) {
      setAllowEmails(v => [...v, e]);
    }
    setEmailInput("");
  };

  const Section = ({ id, title, icon: Icon, children }: { id: keyof typeof openSections; title: string; icon: React.ElementType; children: React.ReactNode }) => (
    <div className="border-b border-slate-100 last:border-0">
      <button
        type="button"
        className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-slate-50 transition-colors"
        onClick={() => toggleSection(id)}
      >
        <div className="flex items-center gap-2">
          <Icon className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-xs font-semibold text-slate-700">{title}</span>
        </div>
        {openSections[id] ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
      </button>
      {openSections[id] && <div className="px-5 pb-4 space-y-3">{children}</div>}
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-slate-900/20" onClick={onClose} />
      <div className="w-[480px] bg-white shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <div>
            <div className="flex items-center gap-2">
              <Hash className="w-4 h-4 text-slate-400" />
              <span className="text-sm font-bold text-slate-900">{cfg.channel_name ?? cfg.channel_id}</span>
              {channelTypeBadge(cfg.channel_type)}
            </div>
            {cfg.channel_topic && <p className="text-xs text-slate-500 mt-0.5 ml-6 truncate">{cfg.channel_topic}</p>}
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors">
            <X className="w-4 h-4 text-slate-500" />
          </button>
        </div>

        {/* Sections */}
        <div className="flex-1 overflow-y-auto">
          {/* Identity */}
          <Section id="identity" title="Identity" icon={Sparkles}>
            <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Display Name</label>
            <input
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder={`Same as agent name`}
              maxLength={120}
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
            />

            <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mt-3 mb-1">On Join</label>
            <div className="flex rounded-lg border border-slate-200 overflow-hidden text-xs font-semibold">
              {(["full_intro", "silent", "custom"] as const).map(v => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setOnJoin(v)}
                  className={`flex-1 py-2 transition-colors ${onJoin === v ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"}`}
                >
                  {v === "full_intro" ? "Full intro" : v === "silent" ? "Silent" : "Custom"}
                </button>
              ))}
            </div>
            {onJoin === "custom" && (
              <textarea
                value={customMsg}
                onChange={e => setCustomMsg(e.target.value)}
                rows={3}
                placeholder="Welcome message text…"
                className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg mt-2 focus:outline-none focus:border-slate-400 resize-none"
              />
            )}

            <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mt-3 mb-1">Active</label>
            <button
              type="button"
              onClick={() => setIsActive(v => !v)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${isActive ? "bg-emerald-500" : "bg-slate-200"}`}
            >
              <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${isActive ? "translate-x-4" : "translate-x-1"}`} />
            </button>
          </Section>

          {/* Personality */}
          <Section id="personality" title="Personality" icon={Brain}>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Soul</label>
              <button
                type="button"
                onClick={handleRegenSoul}
                disabled={regenerating}
                className="flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-700 disabled:opacity-50"
              >
                {regenerating ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                Regenerate
              </button>
            </div>
            {cfg.soul_generated_at && (
              <p className="text-[10px] text-slate-400 mb-1">Generated {fmtDate(cfg.soul_generated_at)}</p>
            )}
            <textarea
              value={soulText}
              onChange={e => setSoulText(e.target.value)}
              rows={8}
              maxLength={8000}
              placeholder="Soul not yet generated — will be created automatically when the bot first joins."
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 resize-y font-mono text-xs leading-relaxed"
            />
            <p className="text-[10px] text-slate-400 mt-1">{soulText.length}/8000 characters</p>
          </Section>

          {/* Skills */}
          <Section id="skills" title="Skills" icon={Zap}>
            <p className="text-[11px] text-slate-500 mb-2">
              Leave empty to inherit the agent's global skill set. Users without access to a skill will be told to request it on {branding.app_name}.
            </p>
            {(cfg.channel_metadata?.auto_excluded_critical_skills?.length ?? 0) > 0 && (
              <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-2 mb-2">
                {cfg.channel_metadata!.auto_excluded_critical_skills!.length} sensitive skill(s) were
                hidden automatically because this is a public channel. You can re-enable them below if needed.
              </p>
            )}
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {availableSkills.map(skill => {
                const selected = skillIds.includes(skill.id);
                return (
                  <label key={skill.id} className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={e => {
                        if (e.target.checked) setSkillIds(v => [...v, skill.id]);
                        else setSkillIds(v => v.filter(s => s !== skill.id));
                      }}
                      className="rounded accent-[#FF444F]"
                    />
                    <span className="text-xs text-slate-700">{skill.name}</span>
                  </label>
                );
              })}
              {availableSkills.length === 0 && <p className="text-xs text-slate-400 italic">No skills available</p>}
            </div>
            {skillIds.length > 0 && (
              <button type="button" onClick={() => setSkillIds([])} className="text-[11px] text-slate-400 hover:text-slate-600 mt-1">
                Clear selection (inherit global)
              </button>
            )}
          </Section>

          {/* Access */}
          <Section id="access" title="Access" icon={ShieldCheck}>
            <div className="flex rounded-lg border border-slate-200 overflow-hidden text-xs font-semibold mb-3">
              {(["open", "allowlist"] as const).map(v => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setAccessMode(v)}
                  className={`flex-1 py-2 transition-colors ${accessMode === v ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"}`}
                >
                  {v === "open" ? "Open" : "Allowlist"}
                </button>
              ))}
            </div>
            {accessMode === "allowlist" && (
              <div>
                <div className="flex gap-2 mb-2">
                  <input
                    value={emailInput}
                    onChange={e => setEmailInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addEmail(); } }}
                    placeholder="email@example.com"
                    type="email"
                    className="flex-1 text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
                  />
                  <button type="button" onClick={addEmail} className="px-3 py-1.5 text-xs font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-700">
                    Add
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {allowEmails.map(e => (
                    <span key={e} className="flex items-center gap-1 text-xs bg-slate-100 rounded-full px-2.5 py-0.5">
                      {e}
                      <button type="button" onClick={() => setAllowEmails(v => v.filter(x => x !== e))} className="text-slate-400 hover:text-red-500">
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                  {allowEmails.length === 0 && <p className="text-xs text-slate-400 italic">No emails added</p>}
                </div>
              </div>
            )}
          </Section>

          {/* Memory */}
          <Section id="memory" title="Memory" icon={Brain}>
            <p className="text-[11px] text-slate-500 mb-2">Controls how memories are stored and retrieved for interactions in this channel.</p>
            <div className="space-y-1.5">
              {(["channel", "global", "user"] as const).map(v => (
                <label key={v} className="flex items-center gap-2.5 cursor-pointer">
                  <input type="radio" name="memscope" value={v} checked={memScope === v} onChange={() => setMemScope(v)} className="accent-[#FF444F]" />
                  <div>
                    <p className="text-xs font-semibold text-slate-700">
                      {v === "channel" ? "Channel (isolated)" : v === "global" ? "Global (shared across channels)" : "Per-user"}
                    </p>
                    <p className="text-[10px] text-slate-400">
                      {v === "channel" ? "Memories only from this channel" : v === "global" ? "Shared memory across all channels" : "Per-user memories only"}
                    </p>
                  </div>
                </label>
              ))}
            </div>
          </Section>
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 px-5 py-4">
          {error && <p className="text-xs text-red-500 mb-2">{error}</p>}
          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 text-xs text-red-500 hover:text-red-700 disabled:opacity-50 transition-colors"
            >
              {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
              Remove channel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 text-sm font-semibold bg-[#FF444F] hover:bg-[#e63d48] text-white px-4 py-2 rounded-lg disabled:opacity-50 transition-colors"
            >
              {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Save changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Channels tab ──────────────────────────────────────────────────────────────

function ChannelsTab({ agentId }: { agentId: string }) {
  const [configs, setConfigs] = useState<ChannelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCfg, setSelectedCfg] = useState<ChannelConfig | null>(null);
  const availableSkillsForChannels = useSkillList();

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/api/platform-agent/channel-configs");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConfigs(data.channel_configs ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  if (loading) return (
    <div className="flex items-center justify-center py-20">
      <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
    </div>
  );

  if (error) return (
    <div className="flex flex-col items-center py-20 text-center">
      <p className="text-sm text-red-500">{error}</p>
      <button onClick={load} className="mt-3 text-xs text-slate-500 hover:text-slate-700">Retry</button>
    </div>
  );

  if (configs.length === 0) return (
    <div className="flex flex-col items-center py-20 text-center">
      <Hash className="w-8 h-8 text-slate-300 mb-3" />
      <p className="text-sm font-semibold text-slate-700">No channels yet</p>
      <p className="text-xs text-slate-500 mt-1 max-w-sm">
        Add this bot to a Slack channel and it will appear here automatically. You can also add a channel config manually.
      </p>
    </div>
  );

  return (
    <div className="divide-y divide-slate-100">
      {configs.map(cfg => (
        <div key={cfg.channel_id} className="flex items-center gap-4 px-6 py-4 hover:bg-slate-50 transition-colors">
          <Hash className="w-4 h-4 text-slate-400 shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-slate-900 truncate">
                #{cfg.channel_name ?? cfg.channel_id}
              </span>
              {channelTypeBadge(cfg.channel_type)}
              {joinStatusBadge(cfg.join_status)}
            </div>
            <div className="flex items-center gap-3 mt-1 flex-wrap">
              {soulStatusBadge(cfg)}
              <span className="text-[10px] text-slate-400">
                {cfg.skill_ids?.length > 0 ? `${cfg.skill_ids.length} skill${cfg.skill_ids.length !== 1 ? "s" : ""}` : "Global skills"}
              </span>
              <span className={`text-[10px] font-semibold ${cfg.access_policy?.mode === "allowlist" ? "text-amber-600" : "text-slate-400"}`}>
                {cfg.access_policy?.mode === "allowlist"
                  ? `Allowlist · ${cfg.access_policy.emails.length}`
                  : "Open"}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <button
              type="button"
              onClick={() => setSelectedCfg(cfg)}
              className="text-xs font-semibold text-slate-600 hover:text-slate-900 border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 transition-colors"
            >
              Configure
            </button>
          </div>
        </div>
      ))}

      {selectedCfg && (
        <ChannelConfigDrawer
          cfg={selectedCfg}
          agentId={agentId}
          onClose={() => setSelectedCfg(null)}
          onSaved={(updated) => {
            setConfigs(prev => prev.map(c => c.channel_id === updated.channel_id ? updated : c));
            setSelectedCfg(updated);
          }}
          onDeleted={(channelId) => {
            setConfigs(prev => prev.filter(c => c.channel_id !== channelId));
            setSelectedCfg(null);
          }}
          availableSkills={(availableSkillsForChannels ?? []).map(s => ({ id: s.slug ?? (s as { id?: string }).id ?? "", name: s.name ?? s.slug ?? "" }))}
        />
      )}
    </div>
  );
}

// ── Memory tab ────────────────────────────────────────────────────────────────

function MemoryTab({ agentId }: { agentId: string }) {
  const [channelFilter, setChannelFilter] = useState<string>("all");
  const [channels, setChannels] = useState<Array<{ channel_id: string; channel_name: string | null }>>([]);
  const [memories, setMemories] = useState<ChannelMemory[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiFetch("/api/platform-agent/channel-configs").then(r => r.json()).then(d => {
      setChannels((d.channel_configs ?? []).map((c: ChannelConfig) => ({ channel_id: c.channel_id, channel_name: c.channel_name })));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (channelFilter === "all") {
      setMemories([]);
      return;
    }
    setLoading(true);
    apiFetch(`/api/platform-agent/channel-configs/${channelFilter}/memories`)
      .then(r => r.json())
      .then(d => setMemories(d.memories ?? []))
      .catch(() => setMemories([]))
      .finally(() => setLoading(false));
  }, [channelFilter]);

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-4">
        <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Channel</label>
        <select
          value={channelFilter}
          onChange={e => setChannelFilter(e.target.value)}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-slate-400"
        >
          <option value="all">All channels</option>
          {channels.map(c => (
            <option key={c.channel_id} value={c.channel_id}>
              #{c.channel_name ?? c.channel_id}
            </option>
          ))}
        </select>
      </div>

      {channelFilter === "all" ? (
        <div className="flex flex-col items-center py-16 text-center">
          <Brain className="w-7 h-7 text-slate-300 mb-2" />
          <p className="text-sm text-slate-500">Select a channel to view its memories</p>
        </div>
      ) : loading ? (
        <div className="flex justify-center py-12"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div>
      ) : memories.length === 0 ? (
        <div className="flex flex-col items-center py-16 text-center">
          <Brain className="w-7 h-7 text-slate-300 mb-2" />
          <p className="text-sm text-slate-500">No memories for this channel yet</p>
        </div>
      ) : (
        <div className="space-y-2">
          {memories.map(m => (
            <div key={m.id} className="bg-white border border-slate-200 rounded-xl px-4 py-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold text-slate-700">{m.key}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-slate-400">{m.source}</span>
                  <span className="text-[10px] text-slate-400">{fmtDate(m.updated_at)}</span>
                </div>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">{m.value}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Interactions tab ──────────────────────────────────────────────────────────

function InteractionsTab({ agentId }: { agentId: string }) {
  const [channelFilter, setChannelFilter] = useState<string>("all");
  const [channels, setChannels] = useState<Array<{ channel_id: string; channel_name: string | null }>>([]);
  const [interactions, setInteractions] = useState<Array<{ id: string; invoker_email: string; channel_id: string | null; user_message: string; assistant_message: string; created_at: string }>>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch("/api/platform-agent/channel-configs").then(r => r.json()).then(d => {
      setChannels((d.channel_configs ?? []).map((c: ChannelConfig) => ({ channel_id: c.channel_id, channel_name: c.channel_name })));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    const params = channelFilter !== "all" ? `?channel_id=${encodeURIComponent(channelFilter)}&limit=50` : "?limit=50";
    apiFetch(`/api/agents/${agentId}/interactions${params}`)
      .then(r => r.json())
      .then(d => setInteractions(d.items ?? []))
      .catch(() => setInteractions([]))
      .finally(() => setLoading(false));
  }, [agentId, channelFilter]);

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-4">
        <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Channel</label>
        <select
          value={channelFilter}
          onChange={e => setChannelFilter(e.target.value)}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-slate-400"
        >
          <option value="all">All channels</option>
          {channels.map(c => (
            <option key={c.channel_id} value={c.channel_id}>
              #{c.channel_name ?? c.channel_id}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div>
      ) : interactions.length === 0 ? (
        <div className="flex flex-col items-center py-16 text-center">
          <MessageSquare className="w-7 h-7 text-slate-300 mb-2" />
          <p className="text-sm text-slate-500">No interactions yet</p>
        </div>
      ) : (
        <div className="space-y-3">
          {interactions.map(i => (
            <div key={i.id} className="bg-white border border-slate-200 rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-700">{i.invoker_email}</span>
                <div className="flex items-center gap-2 text-[10px] text-slate-400">
                  {i.channel_id && <span>#{i.channel_id}</span>}
                  <span>{fmtDate(i.created_at)}</span>
                </div>
              </div>
              <p className="text-xs text-slate-600 mb-2 line-clamp-2">{i.user_message}</p>
              <p className="text-xs text-slate-500 italic line-clamp-2">{i.assistant_message}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Settings tab ──────────────────────────────────────────────────────────────

function SettingsTab({ agent, onUpdated }: { agent: PlatformAgent; onUpdated: (a: PlatformAgent) => void }) {
  const isConnected = !!agent.slack_app_id;
  // Where Slack should send events. Defaults to this deployment's own origin;
  // set VITE_SLACK_GATEWAY_BASE when a relay sits in front of the application.
  const GATEWAY_BASE = `${(
    import.meta.env.VITE_SLACK_GATEWAY_BASE || window.location.origin
  ).replace(/\/$/, "")}/slack/agent`;
  const availableSkills = useSkillList();

  const [appId, setAppId] = useState("");
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [teamId, setTeamId] = useState("");
  const [botUserId, setBotUserId] = useState("");
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Skills state
  const [skillIds, setSkillIds] = useState<string[]>(agent.skill_ids ?? []);
  const [savingSkills, setSavingSkills] = useState(false);
  const [skillsSaved, setSkillsSaved] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);

  const liveAppId = appId.trim() || agent.slack_app_id || "";
  const gatewayUrl = liveAppId ? `${GATEWAY_BASE}/${liveAppId}` : null;

  const copyUrl = async () => {
    if (!gatewayUrl) return;
    await navigator.clipboard.writeText(gatewayUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSave = async () => {
    setError(null);
    if (!appId.trim()) { setError("Slack App ID is required."); return; }
    if (!botToken.trim()) { setError("Bot token is required."); return; }
    if (!signingSecret.trim()) { setError("Signing secret is required."); return; }
    setSaving(true);
    try {
      const r = await apiFetch("/api/platform-agent/slack-credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slack_app_id: appId.trim(),
          bot_token: botToken.trim(),
          signing_secret: signingSecret.trim(),
          slack_team_id: teamId.trim() || undefined,
          slack_bot_user_id: botUserId.trim() || undefined,
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error((d as { detail?: string }).detail ?? `HTTP ${r.status}`);
      }
      const updated = await r.json();
      onUpdated({ ...agent, slack_app_id: updated.slack_app_id, slack_team_id: updated.slack_team_id, slack_bot_user_id: updated.slack_bot_user_id });
      setBotToken("");
      setSigningSecret("");
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save credentials.");
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm("Clear all Slack credentials from this agent?")) return;
    setDisconnecting(true);
    setError(null);
    try {
      const r = await apiFetch("/api/platform-agent/slack-credentials", { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onUpdated({ ...agent, slack_app_id: null, slack_team_id: null, slack_bot_user_id: null });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to disconnect.");
    } finally {
      setDisconnecting(false);
    }
  };

  const handleSaveSkills = async () => {
    setSavingSkills(true);
    setSkillsError(null);
    try {
      const r = await apiFetch("/api/platform-agent", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_ids: skillIds }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error((d as { detail?: string }).detail ?? `HTTP ${r.status}`);
      }
      const updated = await r.json();
      onUpdated({ ...agent, skill_ids: updated.skill_ids ?? skillIds });
      setSkillsSaved(true);
      setTimeout(() => setSkillsSaved(false), 3000);
    } catch (e) {
      setSkillsError(e instanceof Error ? e.message : "Failed to save skills.");
    } finally {
      setSavingSkills(false);
    }
  };

  return (
    <div className="p-6 max-w-2xl space-y-6">

      {/* Connection status */}
      <div className={`rounded-xl border px-5 py-4 flex items-start gap-4 ${isConnected ? "bg-emerald-50 border-emerald-200" : "bg-amber-50 border-amber-200"}`}>
        <div className={`mt-0.5 w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${isConnected ? "bg-emerald-100" : "bg-amber-100"}`}>
          {isConnected
            ? <Check className="w-4 h-4 text-emerald-600" />
            : <Zap className="w-4 h-4 text-amber-600" />}
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${isConnected ? "text-emerald-800" : "text-amber-800"}`}>
            {isConnected ? "Slack connected" : "Slack not connected"}
          </p>
          {isConnected ? (
            <div className="mt-1 space-y-0.5">
              <p className="text-xs text-emerald-700 font-mono">App: {agent.slack_app_id}</p>
              {agent.slack_team_id && <p className="text-xs text-emerald-700 font-mono">Workspace: {agent.slack_team_id}</p>}
              {agent.slack_bot_user_id && <p className="text-xs text-emerald-700 font-mono">Bot user: {agent.slack_bot_user_id}</p>}
            </div>
          ) : (
            <p className="text-xs text-amber-700 mt-0.5">Enter your Slack app credentials below to connect.</p>
          )}
        </div>
        {isConnected && (
          <button
            type="button"
            onClick={handleDisconnect}
            disabled={disconnecting}
            className="shrink-0 text-xs font-semibold text-red-600 hover:text-red-700 disabled:opacity-50"
          >
            {disconnecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Disconnect"}
          </button>
        )}
      </div>

      {/* Credentials form */}
      <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
        <div className="px-5 py-4">
          <h3 className="text-sm font-semibold text-slate-800 mb-0.5">Slack App Credentials</h3>
          <p className="text-xs text-slate-500">
            Find these in your{" "}
            <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
              Slack API app settings
            </a>
            . Bot token and signing secret are stored encrypted in the vault — never logged.
          </p>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">App ID <span className="text-red-500">*</span></label>
            <input
              value={appId}
              onChange={e => setAppId(e.target.value)}
              placeholder="A0123456789"
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:outline-none focus:border-slate-400"
            />
            <p className="text-[11px] text-slate-400 mt-1">From Settings → Basic Information → App ID</p>
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">Bot Token <span className="text-red-500">*</span></label>
            <input
              type="password"
              value={botToken}
              onChange={e => setBotToken(e.target.value)}
              placeholder="xoxb-…"
              autoComplete="off"
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:outline-none focus:border-slate-400"
            />
            <p className="text-[11px] text-slate-400 mt-1">From OAuth & Permissions → Bot User OAuth Token</p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">Signing Secret <span className="text-red-500">*</span></label>
            <input
              type="password"
              value={signingSecret}
              onChange={e => setSigningSecret(e.target.value)}
              placeholder="••••••••••••"
              autoComplete="off"
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:outline-none focus:border-slate-400"
            />
            <p className="text-[11px] text-slate-400 mt-1">From Settings → Basic Information → Signing Secret</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">Workspace ID <span className="text-slate-400 font-normal">(optional)</span></label>
              <input
                value={teamId}
                onChange={e => setTeamId(e.target.value)}
                placeholder="T0123456789"
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:outline-none focus:border-slate-400"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">Bot User ID <span className="text-slate-400 font-normal">(optional)</span></label>
              <input
                value={botUserId}
                onChange={e => setBotUserId(e.target.value)}
                placeholder="U0123456789"
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:outline-none focus:border-slate-400"
              />
            </div>
          </div>
        </div>

        <div className="px-5 py-4 flex items-center justify-between">
          {error && <p className="text-xs text-red-600">{error}</p>}
          {saved && !error && <p className="text-xs text-emerald-600 flex items-center gap-1"><Check className="w-3 h-3" />Credentials saved</p>}
          {!error && !saved && <span />}
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-slate-900 text-white text-xs font-semibold rounded-lg hover:bg-slate-700 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
            {isConnected ? "Update credentials" : "Save & connect"}
          </button>
        </div>
      </div>
      {/* Gateway / Event Subscriptions URL */}
      <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
        <div className="px-5 py-4">
          <h3 className="text-sm font-semibold text-slate-800 mb-0.5">Slack Event Subscriptions URL</h3>
          <p className="text-xs text-slate-500">
            Paste this into your Slack app under <span className="font-semibold">Event Subscriptions → Request URL</span>. Same gateway used by all agents.
          </p>
        </div>
        <div className="px-5 py-4">
          {gatewayUrl ? (
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs font-mono text-slate-700 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 break-all">
                {gatewayUrl}
              </code>
              <button
                type="button"
                onClick={copyUrl}
                className="shrink-0 flex items-center gap-1.5 px-3 py-2 text-xs font-semibold border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <RefreshCw className="w-3.5 h-3.5 text-slate-500" />}
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
          ) : (
            <p className="text-xs text-slate-400 italic">Enter the App ID above to generate the URL.</p>
          )}
        </div>
        {gatewayUrl && (
          <div className="px-5 py-3 bg-slate-50 rounded-b-xl">
            <p className="text-[11px] text-slate-500">
              <span className="font-semibold">Required bot events:</span>{" "}
              <code className="font-mono">app_mention</code>, <code className="font-mono">message.im</code>, <code className="font-mono">member_joined_channel</code>
            </p>
          </div>
        )}
      </div>

      {/* Global Skills */}
      <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
        <div className="px-5 py-4">
          <h3 className="text-sm font-semibold text-slate-800 mb-0.5">Global Skills</h3>
          <p className="text-xs text-slate-500">
            Skills available to the agent across all channels. Per-channel overrides can be set in each channel's config.
            Skill secrets (API keys, tokens) are pulled from the vault automatically.
          </p>
        </div>
        <div className="px-5 py-4 space-y-2">
          {(availableSkills ?? []).length === 0 ? (
            <p className="text-xs text-slate-400 italic">No published skills found.</p>
          ) : (
            (availableSkills ?? []).map(skill => {
              const slug = skill.slug ?? (skill as { id?: string }).id ?? "";
              const selected = skillIds.includes(slug);
              return (
                <label key={slug} className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={e => {
                      if (e.target.checked) setSkillIds(v => [...v, slug]);
                      else setSkillIds(v => v.filter(s => s !== slug));
                    }}
                    className="rounded border-slate-300 text-slate-900"
                  />
                  <span className="text-xs font-medium text-slate-700">{skill.name ?? slug}</span>
                  <span className="text-[10px] text-slate-400 font-mono">{slug}</span>
                </label>
              );
            })
          )}
        </div>
        <div className="px-5 py-4 flex items-center justify-between">
          {skillsError && <p className="text-xs text-red-600">{skillsError}</p>}
          {skillsSaved && !skillsError && <p className="text-xs text-emerald-600 flex items-center gap-1"><Check className="w-3 h-3" />Skills saved</p>}
          {!skillsError && !skillsSaved && (
            <p className="text-xs text-slate-400">{skillIds.length} skill{skillIds.length !== 1 ? "s" : ""} selected</p>
          )}
          <button
            type="button"
            onClick={handleSaveSkills}
            disabled={savingSkills}
            className="flex items-center gap-2 px-4 py-2 bg-slate-900 text-white text-xs font-semibold rounded-lg hover:bg-slate-700 disabled:opacity-50"
          >
            {savingSkills ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            Save skills
          </button>
        </div>
      </div>
    </div>
  );
}


// ── Main page ─────────────────────────────────────────────────────────────────

export default function PlatformAgentPage() {
  const branding = useBranding();
  const [agent, setAgent] = useState<PlatformAgent | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("channels");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiFetch("/api/platform-agent")
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => setAgent(d))
      .catch(e => setError(e instanceof Error ? e.message : "Failed to load platform agent"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
    </div>
  );

  if (error || !agent) return (
    <div className="flex flex-col items-center justify-center h-full text-center p-8">
      <Sparkles className="w-10 h-10 text-slate-300 mb-3" />
      <p className="text-base font-semibold text-slate-700">Platform Agent</p>
      <p className="text-sm text-slate-500 mt-1">{error ?? "Platform agent not found."}</p>
      <p className="text-xs text-slate-400 mt-2 max-w-sm">
        The platform agent is created automatically at startup. If this error persists, check the server logs.
      </p>
    </div>
  );

  const tabClass = (tab: Tab) =>
    `shrink-0 flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
      activeTab === tab ? "bg-slate-900 text-white" : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
    }`;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 px-6 py-4 shrink-0">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#FF444F] to-[#FF8C42] flex items-center justify-center shrink-0">
              <Sparkles className="w-4.5 h-4.5 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-bold text-slate-900">{agent.name}</h1>
                <span className="px-2 py-0.5 text-[10px] font-bold rounded-full bg-violet-50 border border-violet-200 text-violet-700 uppercase tracking-wider">
                  Platform Agent
                </span>
                <span className={`px-2 py-0.5 text-[10px] font-bold rounded-full uppercase tracking-wider ${
                  agent.status === "active"
                    ? "bg-emerald-50 border border-emerald-200 text-emerald-700"
                    : "bg-slate-100 border border-slate-200 text-slate-500"
                }`}>
                  {agent.status}
                </span>
              </div>
              {agent.description && (
                <p className="text-xs text-slate-500 mt-0.5">{agent.description}</p>
              )}
            </div>
          </div>
          <div className="text-right">
            <p className="text-[10px] text-slate-400">Admin-managed · Users cannot create versions of this agent</p>
            {agent.slack_app_id ? (
              <p className="text-[10px] font-mono text-emerald-600 mt-0.5">● Connected · App: {agent.slack_app_id}</p>
            ) : (
              <button type="button" onClick={() => setActiveTab("settings")} className="text-[10px] text-amber-600 hover:underline mt-0.5">
                ⚠ Slack not connected — click to configure
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="mt-4 flex gap-1 bg-slate-50 rounded-xl p-1 w-fit">
          <button type="button" onClick={() => setActiveTab("channels")} className={tabClass("channels")}>
            <Hash className="w-3.5 h-3.5" />
            Channels
          </button>
          <button type="button" onClick={() => setActiveTab("interactions")} className={tabClass("interactions")}>
            <MessageSquare className="w-3.5 h-3.5" />
            Interactions
          </button>
          <button type="button" onClick={() => setActiveTab("memory")} className={tabClass("memory")}>
            <Brain className="w-3.5 h-3.5" />
            Memory
          </button>
          <button type="button" onClick={() => setActiveTab("settings")} className={tabClass("settings")}>
            <Settings className="w-3.5 h-3.5" />
            Settings
          </button>
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto bg-slate-50">
        {activeTab === "channels" && <ChannelsTab agentId={agent.id} />}
        {activeTab === "interactions" && <InteractionsTab agentId={agent.id} />}
        {activeTab === "memory" && <MemoryTab agentId={agent.id} />}
        {activeTab === "settings" && <SettingsTab agent={agent} onUpdated={setAgent} />}
      </div>
    </div>
  );
}
