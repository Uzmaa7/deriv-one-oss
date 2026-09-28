'use strict';

import { driver, type DriveStep } from 'driver.js';
import 'driver.js/dist/driver.css';

import { apiFetch } from '@/lib/api';
import { CURRENT_TOUR_VERSION, TOUR_STEPS, type TourStep } from './tourConfig';

async function persistTourSeen(): Promise<void> {
  await apiFetch('/api/me/tour', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ last_seen_tour_version: CURRENT_TOUR_VERSION }),
  });
}

function visibleSteps(steps: TourStep[]): TourStep[] {
  return steps.filter((step) => Boolean(document.querySelector(step.targetSelector)));
}

function cleanupStaleDriverDom(): void {
  document.querySelectorAll('.driver-popover').forEach((el) => el.remove());
  document.body.classList.remove(
    'driver-active',
    'driver-fade',
    'driver-simple',
    'driver-no-scroll',
  );
}

export function runTour(onEnd?: () => void): () => void {
  cleanupStaleDriverDom();

  const steps = visibleSteps(TOUR_STEPS);
  if (steps.length === 0) {
    void persistTourSeen().finally(() => onEnd?.());
    return () => {};
  }

  let ended = false;
  // Also called when the caller tears the tour down early (e.g. the user
  // navigates away mid-tour by clicking a highlighted sidebar link) — must
  // still persist + destroy so the tour doesn't get stuck forever.
  const endTour = () => {
    if (ended) return;
    ended = true;
    void persistTourSeen().finally(() => onEnd?.());
    try {
      driverObj.destroy();
    } catch {
      /* already destroyed */
    }
  };

  const driverSteps: DriveStep[] = steps.map((step) => ({
    element: step.targetSelector,
    popover: {
      title: step.title,
      description: step.content,
      side: step.position,
    },
  }));

  const driverObj = driver({
    animate: true,
    overlayOpacity: 0.55,
    popoverClass: 'deriv-tour-popover',
    showProgress: true,
    progressText: 'Step {{current}} of {{total}}',
    showButtons: ['previous', 'next', 'close'],
    nextBtnText: 'Next',
    prevBtnText: 'Back',
    doneBtnText: 'Finish',
    skipMissingElement: true,
    onPopoverRender: (popover) => {
      popover.closeButton.textContent = 'Skip';
      popover.closeButton.setAttribute('aria-label', 'Skip tour');
    },
    onCloseClick: () => {
      endTour();
    },
    onDoneClick: () => {
      endTour();
    },
    steps: driverSteps,
  });

  driverObj.drive();

  return endTour;
}
