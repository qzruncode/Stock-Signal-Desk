import { useEffect, useMemo, useState } from 'react';
import { systemConfigApi } from '../api/systemConfig';
import type { SetupStatusResponse } from '../types/systemConfig';

export interface UseSetupStatusResult {
  setupStatus: SetupStatusResponse | null;
  setupNeedsAction: boolean;
  setupMissingLabels: string;
}

export function useSetupStatus(): UseSetupStatusResult {
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);

  useEffect(() => {
    let active = true;
    systemConfigApi.getSetupStatus()
      .then((status) => {
        if (active) {
          setSetupStatus(status);
        }
      })
      .catch(() => {
        if (active) {
          setSetupStatus(null);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const setupNeedsAction = setupStatus ? !setupStatus.isComplete : false;

  const setupMissingLabels = useMemo(() => {
    if (!setupStatus) {
      return '';
    }
    const requiredNeedsAction = setupStatus.checks
      .filter((check) => check.required && check.status === 'needs_action')
      .map((check) => check.title);
    return requiredNeedsAction.slice(0, 3).join('、');
  }, [setupStatus]);

  return {
    setupStatus,
    setupNeedsAction,
    setupMissingLabels,
  };
}

export default useSetupStatus;
