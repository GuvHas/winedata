'use client';

/**
 * A toggleable filter pill.
 *
 * Uses a real <button> with `aria-pressed` rather than a styled checkbox, so
 * it is keyboard-operable and announces its state. The count is rendered in a
 * fixed-width slot so toggling a neighbour cannot reflow the row.
 */
interface FilterPillProps {
  label: string;
  active: boolean;
  count?: number;
  onToggle: () => void;
  /** Optional leading swatch or icon. */
  adornment?: React.ReactNode;
}

export function FilterPill({ label, active, count, onToggle, adornment }: FilterPillProps) {
  const disabled = count === 0 && !active;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      disabled={disabled}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-medium whitespace-nowrap transition-colors ${
        active
          ? 'border-claret-500 bg-claret-500 text-white'
          : 'border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink'
      } ${disabled ? 'cursor-not-allowed opacity-40' : ''}`}
    >
      {adornment}
      {label}
      {count !== undefined && (
        <span className={`tabular text-[11px] ${active ? 'text-white/75' : 'text-ink-faint'}`}>
          {count}
        </span>
      )}
    </button>
  );
}
