'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, ExternalLink, Search } from 'lucide-react';
import type { Wine } from '@/types/wine';
import { buildSearchUrl } from '@/lib/ingest/systembolaget';

/**
 * Opens the wine on Systembolaget, plus a copy button for the article number
 * (the number is what you type into the in-store terminals and the app).
 *
 * Wines reviewed from web merchants have no article number at all; rather than
 * render a dead control, those fall back to a catalog search for the name.
 */

interface SystembolagetLinkProps {
  wine: Wine;
  /** `full` for cards, `compact` for table rows. */
  variant?: 'full' | 'compact';
}

export function SystembolagetLink({ wine, variant = 'full' }: SystembolagetLinkProps) {
  const { articleNumber, productUrl } = wine.systembolaget;

  if (!productUrl || !articleNumber) {
    const query = [wine.name, wine.producer].filter(Boolean).join(' ');
    return (
      <a
        href={buildSearchUrl(query)}
        target="_blank"
        rel="noreferrer noopener"
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-ink-muted underline decoration-dotted underline-offset-2 transition-colors hover:bg-surface-sunken hover:text-ink"
        title="Vinet saknar artikelnummer — sök i Systembolagets sortiment"
      >
        <Search className="h-3.5 w-3.5" aria-hidden="true" />
        <span>{variant === 'full' ? 'Sök på Systembolaget' : 'Sök'}</span>
        <span className="sr-only">— {wine.fullName} saknar artikelnummer</span>
      </a>
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      <a
        href={productUrl}
        target="_blank"
        rel="noreferrer noopener"
        className={`tabular inline-flex items-center gap-1.5 rounded-md border border-claret-300/50 bg-claret-50 font-medium text-claret-700 transition-colors hover:border-claret-500 hover:bg-claret-100 ${
          variant === 'full' ? 'px-2.5 py-1.5 text-sm' : 'px-2 py-1 text-xs'
        }`}
      >
        <span>{articleNumber}</span>
        <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="sr-only">
          — öppna {wine.fullName} på Systembolaget (nytt fönster)
        </span>
      </a>
      <CopyButton value={articleNumber} label={wine.fullName} />
    </span>
  );
}

/** Copies the article number, with a short confirmation state. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear the pending reset if the row unmounts while confirming.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Clipboard access can be denied (insecure origin, permissions policy).
      // Fall back to a selection-based copy so the button still does something.
      const field = document.createElement('textarea');
      field.value = value;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.opacity = '0';
      document.body.appendChild(field);
      field.select();
      try {
        document.execCommand('copy');
      } catch {
        // Nothing else to try; leave the number visible for manual copying.
      }
      document.body.removeChild(field);
    }

    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1600);
  }, [value]);

  return (
    <button
      type="button"
      onClick={copy}
      // Fixed size so swapping the icon cannot nudge the layout.
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line text-ink-muted transition-colors hover:border-line-strong hover:bg-surface-sunken hover:text-ink"
      title={copied ? 'Kopierat' : `Kopiera artikelnummer ${value}`}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-value-fynd" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      <span className="sr-only">
        {copied ? `Artikelnummer ${value} kopierat` : `Kopiera artikelnummer för ${label}`}
      </span>
    </button>
  );
}
