import { useEffect, useState } from 'react';
import { settingsService } from '@/services/settings';

export interface DgclPipelineStatus {
  enabled: boolean;
  loading: boolean;
}

/**
 * Returns both the enabled state and the loading state of the DGCL verification pipeline flag.
 */
export function useDgclPipelineStatus(): DgclPipelineStatus {
  const [state, setState] = useState<DgclPipelineStatus>({
    enabled: false,
    loading: true,
  });

  useEffect(() => {
    let mounted = true;
    settingsService
      .getDgclPipelineFlag()
      .then((flag) => {
        if (mounted) setState({ enabled: flag.enabled, loading: false });
      })
      .catch(() => {
        if (mounted) setState({ enabled: false, loading: false });
      });

    const handler = (e: Event) => {
      const customEvent = e as CustomEvent<boolean>;
      if (mounted && typeof customEvent.detail === 'boolean') {
        setState({ enabled: customEvent.detail, loading: false });
      }
    };

    window.addEventListener('dgcl-flag-changed', handler);
    return () => {
      mounted = false;
      window.removeEventListener('dgcl-flag-changed', handler);
    };
  }, []);

  return state;
}

/** Whether the DGCL verification pipeline is enabled. Defaults to false (disabled) until the
 * flag loads or if it fails to load, matching the backend's fail-safe default. */
export function useDgclPipelineFlag(): boolean {
  return useDgclPipelineStatus().enabled;
}
