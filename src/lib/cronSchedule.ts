/** Helpers for automation schedule UX — backend still stores 5-field cron. */

export type CustomScheduleMode =
  | "daily"
  | "weekdays"
  | "weekly"
  | "hourly"
  | "interval";

export type CustomScheduleState = {
  mode: CustomScheduleMode;
  hour: number; // 0-23
  minute: number; // 0-59
  /** 0=Sun … 6=Sat (cron dow) */
  weekdays: number[];
  intervalMinutes: number;
};

export const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

export const INTERVAL_OPTIONS = [5, 10, 15, 30, 45] as const;

export function defaultCustomSchedule(): CustomScheduleState {
  return {
    mode: "daily",
    hour: 9,
    minute: 0,
    weekdays: [1, 2, 3, 4, 5],
    intervalMinutes: 15,
  };
}

export function isValidCron(cron: string): boolean {
  const parts = cron.trim().split(/\s+/);
  return parts.length === 5 && parts.every(p => p.length > 0);
}

export function customScheduleToCron(state: CustomScheduleState): string {
  const { mode, hour, minute, weekdays, intervalMinutes } = state;
  switch (mode) {
    case "interval":
      return `*/${intervalMinutes} * * * *`;
    case "hourly":
      return `${minute} * * * *`;
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekdays":
      return `${minute} ${hour} * * 1-5`;
    case "weekly": {
      const days = weekdays.length > 0 ? [...weekdays].sort((a, b) => a - b).join(",") : "1";
      return `${minute} ${hour} * * ${days}`;
    }
    default:
      return `${minute} ${hour} * * *`;
  }
}

/** Best-effort parse of common cron patterns into friendly builder state. */
export function cronToCustomSchedule(cron: string): CustomScheduleState | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [min, hour, dom, month, dow] = parts;
  if (dom !== "*" || month !== "*") return null;

  if (min.startsWith("*/")) {
    const n = Number(min.slice(2));
    if (!Number.isNaN(n) && n > 0 && hour === "*") {
      return { ...defaultCustomSchedule(), mode: "interval", intervalMinutes: n };
    }
  }

  if (hour === "*" && !min.startsWith("*/")) {
    const m = Number(min);
    if (!Number.isNaN(m)) {
      return { ...defaultCustomSchedule(), mode: "hourly", minute: m };
    }
  }

  const h = Number(hour);
  const m = Number(min);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;

  if (dow === "1-5") {
    return { mode: "weekdays", hour: h, minute: m, weekdays: [1, 2, 3, 4, 5], intervalMinutes: 15 };
  }
  if (dow === "*") {
    return { mode: "daily", hour: h, minute: m, weekdays: [1], intervalMinutes: 15 };
  }
  if (/^[\d,]+$/.test(dow)) {
    const weekdays = dow.split(",").map(Number).filter(n => !Number.isNaN(n) && n >= 0 && n <= 6);
    if (weekdays.length > 0) {
      return { mode: "weekly", hour: h, minute: m, weekdays, intervalMinutes: 15 };
    }
  }
  return null;
}

function formatTime12h(hour: number, minute: number): string {
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  const ampm = hour < 12 ? "AM" : "PM";
  const mm = minute.toString().padStart(2, "0");
  return `${h12}:${mm} ${ampm}`;
}

function weekdayList(days: number[]): string {
  const sorted = [...days].sort((a, b) => a - b);
  if (sorted.length === 5 && sorted.join() === "1,2,3,4,5") return "weekdays";
  if (sorted.length === 7) return "every day";
  return sorted.map(d => WEEKDAY_LABELS[d]).join(", ");
}

/** Human-readable label for display (presets + parsed custom). */
export function describeCron(cron: string, presetLabels?: Record<string, string>): string {
  if (presetLabels?.[cron]) return presetLabels[cron];

  const custom = cronToCustomSchedule(cron);
  if (custom) return describeCustomSchedule(custom);

  if (!isValidCron(cron)) return "Choose a time from the schedule options";

  return "Custom schedule";
}

export function describeCustomSchedule(state: CustomScheduleState): string {
  switch (state.mode) {
    case "interval":
      return `Every ${state.intervalMinutes} minutes`;
    case "hourly":
      return state.minute === 0
        ? "Every hour"
        : `Every hour at ${state.minute} minutes past`;
    case "daily":
      return `Every day at ${formatTime12h(state.hour, state.minute)}`;
    case "weekdays":
      return `Every weekday at ${formatTime12h(state.hour, state.minute)}`;
    case "weekly":
      return `Every ${weekdayList(state.weekdays)} at ${formatTime12h(state.hour, state.minute)}`;
    default:
      return customScheduleToCron(state);
  }
}
