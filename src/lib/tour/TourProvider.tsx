'use strict';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useLocation } from 'react-router-dom';

import { apiFetch } from '@/lib/api';

import { CURRENT_TOUR_VERSION } from './tourConfig';
import { runTour } from './runTour';
import { shouldStartTour } from './shouldStartTour';

import './tour.css';

type SessionFields = {
  last_login_at: string | null;
  last_seen_tour_version: string | null;
};

type TourContextValue = {
  sessionReady: boolean;
  startTour: (force?: boolean) => void;
  maybeAutoStartTour: () => void;
};

const TourContext = createContext<TourContextValue | null>(null);

export function useTour(): TourContextValue {
  const ctx = useContext(TourContext);
  if (!ctx) {
    throw new Error('useTour must be used within TourProvider');
  }
  return ctx;
}

function waitForTourTargets(maxAttempts = 24): Promise<boolean> {
  return new Promise((resolve) => {
    let attempts = 0;
    const tick = () => {
      const hasTarget = Boolean(
        document.querySelector("[data-tour='prompt-bar']")
        || document.querySelector("[data-tour='nav-skills']"),
      );
      if (hasTarget || attempts >= maxAttempts) {
        resolve(hasTarget);
        return;
      }
      attempts += 1;
      requestAnimationFrame(tick);
    };
    tick();
  });
}

export function TourProvider({ children }: { children: ReactNode }) {
  const [sessionReady, setSessionReady] = useState(false);
  const lastLoginAtRef = useRef<string | null>(null);
  const lastSeenTourVersionRef = useRef<string | null>(null);
  const tourRunningRef = useRef(false);
  const destroyTourRef = useRef<(() => void) | null>(null);
  const startGenerationRef = useRef(0);
  const location = useLocation();
  const pathnameRef = useRef(location.pathname);

  const stopActiveTour = useCallback(() => {
    startGenerationRef.current += 1;
    destroyTourRef.current?.();
    destroyTourRef.current = null;
    tourRunningRef.current = false;
  }, []);

  // The tour's steps are only meaningful while sitting on "/" — several of
  // them spotlight sidebar links, which stay fully clickable underneath the
  // overlay. Clicking one navigates away without ever hitting Skip/Finish,
  // so without this the overlay is left stuck on screen forever. Treat any
  // route change during an active tour as an implicit exit.
  useEffect(() => {
    if (pathnameRef.current === location.pathname) return;
    pathnameRef.current = location.pathname;
    if (tourRunningRef.current) {
      stopActiveTour();
    }
  }, [location.pathname, stopActiveTour]);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/me/session', { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) throw new Error('session failed');
        return res.json() as Promise<SessionFields>;
      })
      .then((data) => {
        if (cancelled) return;
        lastLoginAtRef.current = data.last_login_at ?? null;
        lastSeenTourVersionRef.current = data.last_seen_tour_version ?? null;
        setSessionReady(true);
      })
      .catch(() => {
        if (!cancelled) setSessionReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => {
    stopActiveTour();
  }, [stopActiveTour]);

  const startTour = useCallback((force = false) => {
    if (tourRunningRef.current && !force) return;

    if (
      !force
      && !shouldStartTour(lastSeenTourVersionRef.current, lastLoginAtRef.current)
    ) {
      return;
    }

    if (force) {
      stopActiveTour();
    }

    // Mark running synchronously — Strict Mode / rapid effect re-runs can call
    // startTour twice before waitForTourTargets resolves, which leaves two
    // driver.js instances and stacked popovers (e.g. Skills + Integrations).
    const generation = ++startGenerationRef.current;
    tourRunningRef.current = true;

    void (async () => {
      const hasTargets = await waitForTourTargets();
      if (generation !== startGenerationRef.current) {
        tourRunningRef.current = false;
        return;
      }
      if (!hasTargets) {
        tourRunningRef.current = false;
        return;
      }

      destroyTourRef.current?.();
      destroyTourRef.current = runTour(() => {
        if (generation !== startGenerationRef.current) return;
        tourRunningRef.current = false;
        lastSeenTourVersionRef.current = CURRENT_TOUR_VERSION;
      });
    })();
  }, [stopActiveTour]);

  const maybeAutoStartTour = useCallback(() => {
    if (!sessionReady) return;
    startTour(false);
  }, [sessionReady, startTour]);

  return (
    <TourContext.Provider value={{ sessionReady, startTour, maybeAutoStartTour }}>
      {children}
    </TourContext.Provider>
  );
}
