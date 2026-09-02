import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';

export function SortHeader({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: 'asc' | 'desc';
  onClick: () => void;
}) {
  const Icon = !active ? ChevronsUpDown : dir === 'asc' ? ChevronUp : ChevronDown;
  return (
    <button
      onClick={onClick}
      className={`table-head inline-flex items-center gap-1 hover:text-ink-700 ${
        active ? 'text-ink-800' : ''
      }`}
    >
      {label}
      <Icon className="h-3 w-3" aria-hidden />
    </button>
  );
}
