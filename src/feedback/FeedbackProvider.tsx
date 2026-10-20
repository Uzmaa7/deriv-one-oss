import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { apiFetch } from "@/lib/api";
import PickerOverlay from "./PickerOverlay";
import FeedbackPanel from "./FeedbackPanel";

interface FeedbackContextValue {
  isAdmin: boolean;
  annotateActive: boolean;
  toggleAnnotate: () => void;
  pickedElement: Element | null;
  setPicked: (el: Element | null) => void;
}

const FeedbackContext = createContext<FeedbackContextValue>({
  isAdmin: false,
  annotateActive: false,
  toggleAnnotate: () => {},
  pickedElement: null,
  setPicked: () => {},
});

export function useFeedback() {
  return useContext(FeedbackContext);
}

export default function FeedbackProvider({ children }: { children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState(false);
  const [annotateActive, setAnnotateActive] = useState(false);
  const [pickedElement, setPickedElement] = useState<Element | null>(null);
  const adminChecked = useRef(false);

  useEffect(() => {
    if (adminChecked.current) return;
    adminChecked.current = true;
    apiFetch("/api/me")
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.role === "admin") setIsAdmin(true); })
      .catch(() => {});
  }, []);

  const toggleAnnotate = useCallback(() => {
    setAnnotateActive(v => !v);
    setPickedElement(null);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "F") {
        e.preventDefault();
        toggleAnnotate();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggleAnnotate]);

  const setPicked = useCallback((el: Element | null) => {
    setPickedElement(el);
    if (el) setAnnotateActive(false);
  }, []);

  return (
    <FeedbackContext.Provider value={{ isAdmin, annotateActive, toggleAnnotate, pickedElement, setPicked }}>
      {children}
      {annotateActive && <PickerOverlay />}
      {pickedElement && <FeedbackPanel />}
    </FeedbackContext.Provider>
  );
}
