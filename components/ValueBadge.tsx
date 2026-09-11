import type { ValueRating } from '@/types/wine';
import { VALUE_LABELS, VALUE_SHORT } from '@/lib/display';

/**
 * Munskänkarna's price/quality verdict.
 *
 * Colour alone never carries the meaning — the wording is always present, so
 * the badge survives greyscale and colour-vision differences.
 */

const VALUE_STYLES: Record<ValueRating, string> = {
  fynd: 'bg-value-fynd-bg text-value-fynd border-value-fynd/25',
  'mer-an-prisvart': 'bg-value-more-bg text-value-more border-value-more/25',
  prisvart: 'bg-value-worth-bg text-value-worth border-value-worth/25',
  'ej-prisvart': 'bg-value-not-bg text-value-not border-value-not/25',
};

interface ValueBadgeProps {
  rating: ValueRating | null;
  /** Use the abbreviated wording, for narrow table columns. */
  short?: boolean;
}

export function ValueBadge({ rating, short = false }: ValueBadgeProps) {
  if (rating === null) {
    return <span className="text-xs text-ink-faint">–</span>;
  }

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${VALUE_STYLES[rating]}`}
      title={VALUE_LABELS[rating]}
    >
      {rating === 'fynd' && <span aria-hidden="true">★</span>}
      {short ? VALUE_SHORT[rating] : VALUE_LABELS[rating]}
    </span>
  );
}
