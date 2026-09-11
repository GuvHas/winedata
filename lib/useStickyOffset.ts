'use client';

import { useEffect, useRef } from 'react';

/**
 * Publishes the measured height of the sticky filter block as a CSS variable
 * on <html>, so the table's sticky column headers can park directly beneath it.
 *
 * The block's height is genuinely variable — pill rows wrap differently per
 * breakpoint, and the mobile panel collapses — so a hard-coded offset would
 * either overlap the first row or leave a gap. A ResizeObserver keeps the
 * variable correct across resizes, font loading and filter toggles.
 */
export function useStickyOffset<T extends HTMLElement>() {
  const ref = useRef<T>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const root = document.documentElement;
    const publish = () => {
      root.style.setProperty('--filters-height', `${Math.round(element.offsetHeight)}px`);
    };

    publish();

    const observer = new ResizeObserver(publish);
    observer.observe(element);

    return () => {
      observer.disconnect();
      root.style.removeProperty('--filters-height');
    };
  }, []);

  return ref;
}
