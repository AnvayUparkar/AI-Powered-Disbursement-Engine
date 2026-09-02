import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';

export function EmptyState({
  title,
  description,
  action,
  icon: Icon = Inbox,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: typeof Inbox;
}) {
  return (
    <div className="card p-10 flex flex-col items-center justify-center text-center">
      <div className="rounded-full bg-ink-100 p-3 mb-3">
        <Icon className="h-6 w-6 text-ink-400" aria-hidden />
      </div>
      <p className="text-sm font-medium text-ink-700">{title}</p>
      {description && <p className="mt-1 text-sm text-ink-500 max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
