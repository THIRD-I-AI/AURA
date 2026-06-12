/**
 * useSystemHealth Hook
 * Monitors backend health and fires the callback ONLY when the
 * online/offline status actually changes — never on every poll tick.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { healthService } from '../services/api';

export interface SystemHealthState {
  isOnline: boolean;
  status: 'healthy' | 'degraded' | 'down';
  lastCheck: Date | null;
  isChecking: boolean;
  retries: number;
}

/**
 * Hint copy for the dashboard health card. The /health probe only proves
 * the GATEWAY answered — it says nothing about the other services (the
 * services card right below may show 1/8), so the copy must not claim
 * "all services operational" (S35b).
 */
export function healthHint(status: SystemHealthState['status']): string {
  switch (status) {
    case 'healthy': return 'Gateway healthy';
    case 'degraded': return 'Some services degraded';
    default: return 'Gateway unreachable';
  }
}

const INITIAL_STATE: SystemHealthState = {
  isOnline: true,
  status: 'healthy',
  lastCheck: null,
  isChecking: false,
  retries: 0,
};

export const useSystemHealth = (onStatusChange?: (status: SystemHealthState) => void) => {
  const [health, setHealth] = useState<SystemHealthState>(INITIAL_STATE);

  const timerRef          = useRef<number | null>(null);
  const isUnmountingRef   = useRef(false);
  const isCheckingRef     = useRef(false);
  const onStatusChangeRef = useRef(onStatusChange);
  /** null = first check not done yet; true/false = last known state */
  const prevOnlineRef     = useRef<boolean | null>(null);

  useEffect(() => { onStatusChangeRef.current = onStatusChange; }, [onStatusChange]);

  const checkHealth = useCallback(async () => {
    if (isCheckingRef.current) return;
    isCheckingRef.current = true;

    setHealth((prev) => ({ ...prev, isChecking: true }));

    try {
      const result = await healthService.checkHealth();

      if (!isUnmountingRef.current) {
        setHealth((_prev) => {
          const next: SystemHealthState = {
            isOnline: true,
            status: result.status || 'healthy',
            lastCheck: new Date(),
            isChecking: false,
            retries: 0,
          };
          // Only fire callback when transitioning from offline → online
          if (prevOnlineRef.current === false) {
            onStatusChangeRef.current?.(next);
          }
          prevOnlineRef.current = true;
          return next;
        });
      }
    } catch {
      if (!isUnmountingRef.current) {
        setHealth((prev) => {
          const next: SystemHealthState = {
            isOnline: false,
            status: 'down',
            lastCheck: new Date(),
            isChecking: false,
            retries: prev.retries + 1,
          };
          // Only fire callback on first failure (online → offline), not on every retry
          if (prevOnlineRef.current === true) {
            onStatusChangeRef.current?.(next);
          }
          prevOnlineRef.current = false;
          return next;
        });
      }
    } finally {
      isCheckingRef.current = false;
    }
  }, []);

  useEffect(() => {
    isUnmountingRef.current = false;
    checkHealth();
    timerRef.current = window.setInterval(checkHealth, 30_000); // 30 s — less noisy than 10 s
    return () => {
      isUnmountingRef.current = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [checkHealth]);

  return health;
};

export const useReconnect = () => {
  const [isReconnecting, setIsReconnecting] = useState(false);

  const attemptReconnect = useCallback(async () => {
    setIsReconnecting(true);
    try {
      await healthService.checkHealth();
      setIsReconnecting(false);
      return true;
    } catch {
      setIsReconnecting(false);
      return false;
    }
  }, []);

  return { isReconnecting, attemptReconnect };
};
