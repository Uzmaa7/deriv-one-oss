import { useState } from "react";
import { Bot, ChevronDown, ChevronUp, Key, Workflow, Zap } from "lucide-react";

const EXPANDED_KEY = "home-lingo-expanded";

function readExpanded(): boolean {
  try {
    const stored = localStorage.getItem(EXPANDED_KEY);
    return stored === null ? true : stored === "true";
  } catch {
    return true;
  }
}

function writeExpanded(value: boolean) {
  try {
    localStorage.setItem(EXPANDED_KEY, String(value));
  } catch {
    /* ignore */
  }
}

const TERMS = [
  {
    icon: Zap,
    label: "Skill",
    description: "A single automated task (e.g., sending a Slack message).",
  },
  {
    icon: Bot,
    label: "Agent",
    description: "An intelligent assistant with multiple skills and a role (e.g., Compliance Officer).",
  },
  {
    icon: Workflow,
    label: "Automation",
    description: "Workflow rules and triggers to make tasks run (e.g., daily stand-up summaries).",
  },
  {
    icon: Key,
    label: "Access tiers",
    description: "Tier 1 skills are yours automatically. Tier 2 skills need a quick request and approval first.",
  },
];

export default function AutomationLingo() {
  const [expanded, setExpanded] = useState(readExpanded);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    writeExpanded(next);
  };

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center justify-between gap-3 bg-white border border-slate-200 rounded-2xl px-5 py-3.5 shadow-sm hover:border-slate-300 transition-colors text-left"
      >
        <span className="text-sm text-slate-500">
          New to Automation? <span className="font-bold text-[#0E0E0E]">Learn the Lingo</span>
        </span>
        <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
      </button>
    );
  }

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <p className="text-sm font-semibold text-slate-500">New to Automation?</p>
          <h2 className="text-xl font-bold text-[#0E0E0E]">Learn the Lingo</h2>
        </div>
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors shrink-0"
        >
          <ChevronUp className="w-3.5 h-3.5" />
          Collapse
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {TERMS.map(({ icon: Icon, label, description }) => (
          <div key={label} className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-lg bg-[#FF444F]/10 border border-[#FF444F]/15 flex items-center justify-center shrink-0 text-[#FF444F]">
              <Icon className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-bold uppercase tracking-wide text-slate-900 mb-1">{label}</p>
              <p className="text-sm text-slate-500 leading-relaxed">{description}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
