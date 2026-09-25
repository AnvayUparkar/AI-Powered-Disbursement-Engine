import { useEffect, useState } from 'react';
import { settingsService } from '@/services/settings';

/** Whether the DGCL verification pipeline is enabled. Defaults to false (disabled) until the
 * flag loads or if it fails to load, matching the backend's fail-safe default. */
export function useDgclPipelineFlag(): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    settingsService
      .getDgclPipelineFlag()
      .then((flag) => setEnabled(flag.enabled))
      .catch(() => setEnabled(false));
  }, []);
  return enabled;
}
