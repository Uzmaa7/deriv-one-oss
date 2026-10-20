import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { Loader2, Search, X } from "lucide-react";
import { apiFetch } from "@/lib/api";

export interface EmployeeResult {
  email: string;
  name: string;
  job_title: string;
  department: string;
  status: string;
}

const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$/i;

function parseEmails(text: string): string[] {
  return [...new Set(
    text
      .split(/[,;\s]+/)
      .map(e => e.trim().toLowerCase())
      .filter(Boolean),
  )];
}

export function employeeFromEmail(email: string): EmployeeResult {
  return { email, name: email, job_title: "", department: "", status: "" };
}

function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

interface Props {
  selected: EmployeeResult[];
  onChange: (next: EmployeeResult[]) => void;
  disabled?: boolean;
  placeholder?: string;
}

/**
 * Employee multi-select: search-as-you-type against the org directory
 * (GET /api/employees/search, same endpoint AdminGrantModal uses) with a
 * suggestion dropdown, plus a comma/semicolon-separated paste fallback for
 * bulk entry. Selected employees render as removable chips.
 */
export default function EmployeePicker({ selected, onChange, disabled, placeholder }: Props) {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<EmployeeResult[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [directoryHint, setDirectoryHint] = useState<string | null>(null);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const debouncedQuery = useDebounce(query, 350);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedEmails = new Set(selected.map(e => e.email.toLowerCase()));

  useEffect(() => {
    const q = debouncedQuery.trim();
    if (q.length < 2) {
      setResults([]);
      setSearchError(null);
      setDirectoryHint(null);
      return;
    }
    let cancelled = false;
    setSearching(true);
    setSearchError(null);
    apiFetch(`/api/employees/search?q=${encodeURIComponent(q)}&limit=8`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`Search failed (${r.status})`))))
      .then(d => {
        if (cancelled) return;
        setResults(d.employees ?? []);
        setDirectoryHint(d.directory_hint ?? null);
        setSearching(false);
      })
      .catch(err => {
        if (cancelled) return;
        setSearchError(err instanceof Error ? err.message : "Search unavailable.");
        setSearching(false);
      });
    return () => { cancelled = true; };
  }, [debouncedQuery]);

  const handleSelect = useCallback((emp: EmployeeResult) => {
    if (!selected.some(e => e.email.toLowerCase() === emp.email.toLowerCase())) {
      onChange([...selected, emp]);
    }
    setQuery("");
    setResults([]);
    setPasteError(null);
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [selected, onChange]);

  const handleRemove = useCallback((email: string) => {
    onChange(selected.filter(e => e.email.toLowerCase() !== email.toLowerCase()));
  }, [selected, onChange]);

  const addEmailsFromText = useCallback((text: string): boolean => {
    const emails = parseEmails(text);
    if (emails.length === 0) return false;

    const invalid = emails.filter(e => !EMAIL_RE.test(e));
    if (invalid.length > 0) {
      setPasteError(`Invalid email: ${invalid[0]}`);
      return true;
    }

    const existing = new Set(selected.map(e => e.email.toLowerCase()));
    const additions = emails.filter(e => !existing.has(e)).map(employeeFromEmail);
    if (additions.length) onChange([...selected, ...additions]);

    setQuery("");
    setResults([]);
    setPasteError(null);
    setTimeout(() => inputRef.current?.focus(), 50);
    return true;
  }, [selected, onChange]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      const text = query.trim();
      if (text.includes(",") || text.includes(";") || EMAIL_RE.test(text)) {
        e.preventDefault();
        addEmailsFromText(text);
      }
    }
  };

  const handlePaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const pasted = e.clipboardData.getData("text");
    if (/[,;\s]/.test(pasted) && pasted.includes("@")) {
      e.preventDefault();
      addEmailsFromText(pasted);
    }
  };

  const filteredResults = results.filter(emp => !selectedEmails.has(emp.email.toLowerCase()));

  return (
    <div>
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {selected.map(emp => (
            <span
              key={emp.email}
              className="inline-flex items-center gap-1 pl-2 pr-1 py-1 bg-slate-100 border border-slate-200 rounded-lg text-xs max-w-full"
            >
              <span className="font-semibold text-slate-800 truncate">{emp.name || emp.email}</span>
              <span className="text-slate-400 font-mono truncate hidden sm:inline">{emp.email}</span>
              <button
                type="button"
                onClick={() => handleRemove(emp.email)}
                disabled={disabled}
                className="p-0.5 text-slate-400 hover:text-slate-700 rounded disabled:opacity-50"
                aria-label={`Remove ${emp.email}`}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={placeholder ?? "Search name/email, or paste a@x.com, b@x.com…"}
          autoComplete="off"
          disabled={disabled}
          className="w-full text-sm pl-8 pr-8 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 placeholder:text-slate-400 disabled:opacity-50"
        />
        {query && (
          <button
            type="button"
            onClick={() => { setQuery(""); setResults([]); }}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {filteredResults.length > 0 && (
        <div className="mt-1 border border-slate-200 rounded-lg overflow-hidden shadow-lg bg-white max-h-48 overflow-y-auto">
          {filteredResults.map(emp => (
            <button
              key={emp.email}
              type="button"
              onClick={() => handleSelect(emp)}
              className="w-full px-3 py-2.5 flex items-start gap-3 hover:bg-slate-50 text-left border-b border-slate-100 last:border-0 transition-colors"
            >
              <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center shrink-0 mt-0.5">
                <span className="text-[10px] font-bold text-indigo-700">
                  {(emp.name || emp.email).charAt(0).toUpperCase()}
                </span>
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-800 truncate">{emp.name || emp.email}</div>
                <div className="text-xs text-slate-400 truncate">{emp.email}</div>
                {emp.department && (
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {emp.department}{emp.job_title ? ` · ${emp.job_title}` : ""}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      {searching && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-400">
          <Loader2 className="w-3 h-3 animate-spin" /> Searching org directory…
        </div>
      )}
      {!searching && debouncedQuery.trim().length >= 2 && filteredResults.length === 0 && !searchError && (
        <p className="mt-2 text-xs text-slate-400">
          {results.length > 0
            ? "All matching employees are already selected."
            : directoryHint ?? "No matching employees found. You can paste email addresses directly."}
        </p>
      )}
      {searchError && <p className="mt-2 text-xs text-red-600">{searchError}</p>}
      {pasteError && <p className="mt-2 text-xs text-red-600">{pasteError}</p>}
    </div>
  );
}
