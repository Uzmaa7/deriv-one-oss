'use strict';

import rawConfig from '@/src/config/tourConfig.json';

export type TourStepPosition = 'top' | 'right' | 'bottom' | 'left';

export interface TourStep {
  stepId: string;
  targetSelector: string;
  title: string;
  content: string;
  position: TourStepPosition;
}

export interface TourConfig {
  tourVersion: string;
  steps: TourStep[];
}

export const tourConfig = rawConfig as TourConfig;

export const CURRENT_TOUR_VERSION = tourConfig.tourVersion;

export const INACTIVE_THRESHOLD_DAYS = 30;

export const TOUR_STEPS = tourConfig.steps;
