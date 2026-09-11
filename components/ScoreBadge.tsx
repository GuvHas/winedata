import type { QualityBand } from '@/types/wine';
import { BAND_LABELS, formatScore } from '@/lib/display';

/**
 * Munskänkarna's 20-point score.
 *
 * The band drives the visual weight so a strong score reads at a glance in a
 * dense table, while the exact number stays legible.
 */

const BAND_STYLES: Record<QualityBand, string> = {
  exceptionellt: 'bg-claret-600 text-white border-claret-700',
  hogklassigt: 'bg-claret-100 text-claret-700 border-claret-300',
  bra: 'bg-surface text-ink border-line-strong',
  medelbra: 'bg-surface-sunken text-ink-muted border-line',
  enkelt: 'bg-surface-sunken text-ink-faint border-line',
};

interface ScoreBadgeProps {
  score: number | null;
  band: QualityBand | null;
  /** `lg` for cards, `sm` for table rows. */
  size?: 'sm' | 'lg';
}

export function ScoreBadge({ score, band, size = 'sm' }: ScoreBadgeProps) {
  const style = band ? BAND_STYLES[band] : BAND_STYLES.medelbra;
  const dimensions =
    size === 'lg' ? 'h-14 w-14 text-xl' : 'h-10 w-11 text-sm';

  return (
    <span
      className={`tabular inline-flex shrink-0 items-center justify-center rounded-lg border font-semibold ${dimensions} ${style}`}
      title={band ? `${formatScore(score)} av 20 — ${BAND_LABELS[band]}` : 'Inget betyg'}
    >
      <span aria-hidden="true">{formatScore(score)}</span>
      <span className="sr-only">
        {score === null
          ? 'Inget betyg'
          : `Betyg ${formatScore(score)} av 20${band ? `, ${BAND_LABELS[band]}` : ''}`}
      </span>
    </span>
  );
}
