import { useEffect, useState } from "react";
import {
  INTERVAL_OPTIONS,
  WEEKDAY_LABELS,
  cronToCustomSchedule,
  customScheduleToCron,
  defaultCustomSchedule,
  describeCron,
  describeCustomSchedule,
  type CustomScheduleState,
} from "../lib/cronSchedule";

export const SCHEDULE_PRESETS = [
  { label: "Every 15 minutes", cron: "*/15 * * * *" },
  { label: "Every 30 minutes", cron: "*/30 * * * *" },
  { label: "Every hour",       cron: "0 * * * *" },
  { label: "Daily at 9am",     cron: "0 9 * * *" },
  { label: "Daily at 6pm",     cron: "0 18 * * *" },
  { label: "Weekdays at 9am",  cron: "0 9 * * 1-5" },
  { label: "Custom",           cron: "custom" },
] as const;

const PRESET_LABEL_BY_CRON = Object.fromEntries(
  SCHEDULE_PRESETS.filter(p => p.cron !== "custom").map(p => [p.cron, p.label]),
);

export { describeCron };

function TimeSelectors({
  hour,
  minute,
  onChange,
}: {
  hour: number;
  minute: number;
  onChange: (hour: number, minute: number) => void;
}) {
  const timeValue = `${hour.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}`;

  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-slate-500 shrink-0" htmlFor="schedule-time">
        Time
      </label>
      <input
        id="schedule-time"
        type="time"
        step={60}
        value={timeValue}
        onChange={e => {
          const [h, m] = e.target.value.split(":").map(Number);
          if (!Number.isNaN(h) && !Number.isNaN(m)) onChange(h, m);
        }}
        className="text-xs px-2 py-1.5 border border-slate-200 rounded-lg bg-white focus:outline-none focus:border-slate-400"
      />
    </div>
  );
}

function CustomScheduleBuilder({
  value,
  onChange,
}: {
  value: string;
  onChange: (cron: string) => void;
}) {
  const [custom, setCustom] = useState<CustomScheduleState>(
    () => cronToCustomSchedule(value) ?? defaultCustomSchedule(),
  );

  useEffect(() => {
    const next = customScheduleToCron(custom);
    if (next !== value) onChange(next);
  }, [custom]); // eslint-disable-line react-hooks/exhaustive-deps

  const setMode = (mode: CustomScheduleState["mode"]) => {
    setCustom(prev => ({ ...prev, mode }));
  };

  const toggleWeekday = (day: number) => {
    setCustom(prev => {
      const has = prev.weekdays.includes(day);
      const weekdays = has
        ? prev.weekdays.filter(d => d !== day)
        : [...prev.weekdays, day];
      return { ...prev, weekdays: weekdays.length ? weekdays : [day] };
    });
  };

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div>
        <p className="text-xs font-medium text-slate-600 mb-1.5">How often?</p>
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              ["daily", "Every day"],
              ["weekdays", "Weekdays"],
              ["weekly", "Specific days"],
              ["hourly", "Every hour"],
              ["interval", "Every N min"],
            ] as const
          ).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              onClick={() => setMode(mode)}
              className={`text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
                custom.mode === mode
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 border-slate-200 hover:border-slate-400"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {(custom.mode === "daily" || custom.mode === "weekdays" || custom.mode === "weekly") && (
        <TimeSelectors
          hour={custom.hour}
          minute={custom.minute}
          onChange={(hour, minute) => setCustom(prev => ({ ...prev, hour, minute }))}
        />
      )}

      {custom.mode === "weekly" && (
        <div className="flex flex-wrap gap-1">
          {WEEKDAY_LABELS.map((label, day) => (
            <button
              key={label}
              type="button"
              onClick={() => toggleWeekday(day)}
              className={`text-[10px] px-2 py-1 rounded-md border transition-colors ${
                custom.weekdays.includes(day)
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-600 border-slate-200 hover:border-slate-400"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {custom.mode === "hourly" && (
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500 shrink-0" htmlFor="schedule-hourly-minute">
            At minute
          </label>
          <input
            id="schedule-hourly-minute"
            type="number"
            min={0}
            max={59}
            value={custom.minute}
            onChange={e => {
              const m = Math.min(59, Math.max(0, Number(e.target.value) || 0));
              setCustom(prev => ({ ...prev, minute: m }));
            }}
            className="w-16 text-xs px-2 py-1.5 border border-slate-200 rounded-lg bg-white focus:outline-none focus:border-slate-400"
          />
          <span className="text-xs text-slate-500">past each hour (0–59)</span>
        </div>
      )}

      {custom.mode === "interval" && (
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500">Every</label>
          <select
            value={custom.intervalMinutes}
            onChange={e => setCustom(prev => ({ ...prev, intervalMinutes: Number(e.target.value) }))}
            className="text-xs px-2 py-1.5 border border-slate-200 rounded-lg bg-white"
          >
            {INTERVAL_OPTIONS.map(n => (
              <option key={n} value={n}>{n} minutes</option>
            ))}
          </select>
        </div>
      )}

      <div className="rounded-md bg-white border border-slate-200 px-3 py-2">
        <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-0.5">Runs</p>
        <p className="text-sm text-slate-800">{describeCustomSchedule(custom)}</p>
      </div>
    </div>
  );
}

export default function SchedulePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (cron: string) => void;
}) {
  const isPreset = SCHEDULE_PRESETS.some(p => p.cron === value && p.cron !== "custom");
  const [showCustom, setShowCustom] = useState(!isPreset);

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-1.5">
        {SCHEDULE_PRESETS.map(p => {
          const active = p.cron === "custom" ? showCustom : (!showCustom && value === p.cron);
          return (
            <button
              key={p.cron}
              type="button"
              onClick={() => {
                if (p.cron === "custom") {
                  setShowCustom(true);
                  if (isPreset) onChange("0 9 * * *");
                } else {
                  setShowCustom(false);
                  onChange(p.cron);
                }
              }}
              className={`text-left text-xs px-3 py-2 rounded-lg border transition-colors ${
                active
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 border-slate-200 hover:border-slate-400"
              }`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {showCustom && (
        <CustomScheduleBuilder value={value} onChange={onChange} />
      )}
      {value && !showCustom && (
        <p className="text-xs text-slate-600 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
          {describeCron(value, PRESET_LABEL_BY_CRON)}
        </p>
      )}
    </div>
  );
}
