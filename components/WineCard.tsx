import { Grape, MapPin } from 'lucide-react';
import type { Wine } from '@/types/wine';
import {
  COLOR_LABELS,
  formatAlcohol,
  formatOrigin,
  formatPrice,
  formatPricePerLitre,
  formatVintage,
  formatVolume,
} from '@/lib/display';
import { ColorDot } from './ColorDot';
import { ScoreBadge } from './ScoreBadge';
import { ValueBadge } from './ValueBadge';
import { SystembolagetLink } from './SystembolagetLink';

/**
 * Mobile card.
 *
 * Laid out for one-handed browsing: the score anchors the left edge, the name
 * and origin carry the scan, and the Systembolaget control sits along the
 * bottom edge within thumb reach.
 */
export function WineCard({ wine }: { wine: Wine }) {
  const origin = formatOrigin(wine);

  return (
    <article className="rounded-xl border border-line bg-surface p-3.5 shadow-sm">
      <div className="flex gap-3">
        <div className="flex flex-col items-center gap-1.5">
          <ScoreBadge score={wine.review.score} band={wine.review.band} size="lg" />
          {wine.review.typical && (
            <span
              className="text-[10px] leading-tight font-medium text-ink-faint"
              title="Druv- eller distrikttypisk"
            >
              typisk
            </span>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] leading-snug font-semibold text-ink">
            {wine.name}{' '}
            <span className="tabular font-normal text-ink-muted">
              {formatVintage(wine.vintage)}
            </span>
          </h3>

          {wine.producer && (
            <p className="mt-0.5 truncate text-[13px] text-ink-muted">{wine.producer}</p>
          )}

          <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-ink-muted">
            <span className="inline-flex items-center gap-1.5">
              <ColorDot color={wine.color} />
              {COLOR_LABELS[wine.color]}
            </span>
            {origin && (
              <span className="inline-flex min-w-0 items-center gap-1">
                <MapPin className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="truncate">{origin}</span>
              </span>
            )}
          </div>

          {wine.grapes.length > 0 && (
            <p className="mt-1 flex items-start gap-1 text-[12px] text-ink-faint">
              <Grape className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
              <span className="line-clamp-1">{wine.grapes.join(', ')}</span>
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-0.5 text-right">
          <span className="tabular text-base leading-tight font-semibold text-ink">
            {formatPrice(wine.priceSek)}
          </span>
          <span className="tabular text-[11px] text-ink-faint">
            {formatVolume(wine.volumeMl)}
            {wine.alcoholPercent !== null && ` · ${formatAlcohol(wine.alcoholPercent)}`}
          </span>
          <span className="tabular text-[11px] text-ink-faint">
            {formatPricePerLitre(wine.pricePerLitre)}
          </span>
        </div>
      </div>

      {wine.review.tastingNote && (
        <p className="mt-2.5 line-clamp-3 text-[13px] leading-relaxed text-ink-muted">
          {wine.review.tastingNote}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2.5">
        <ValueBadge rating={wine.review.valueRating} />
        <SystembolagetLink wine={wine} />
      </div>
    </article>
  );
}
