import type { WineColor } from '@/types/wine';
import { COLOR_LABELS } from '@/lib/display';

/** A small colour swatch for the wine style, paired with a text label. */

const DOT_STYLES: Record<WineColor, string> = {
  red: 'bg-wine-red',
  white: 'bg-wine-white',
  rose: 'bg-wine-rose',
  sparkling: 'bg-wine-sparkling',
  fortified: 'bg-wine-fortified',
  dessert: 'bg-wine-fortified',
  other: 'bg-wine-other',
};

export function ColorDot({ color, className = '' }: { color: WineColor; className?: string }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-inset ring-black/15 ${DOT_STYLES[color]} ${className}`}
      aria-hidden="true"
      title={COLOR_LABELS[color]}
    />
  );
}
