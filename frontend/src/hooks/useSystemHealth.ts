/**
 * useSystemHealth Hook
 * Monitors backend health status and provides connection state
 * Used by Dashboard and App components for graceful degradation
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

const INITIAL_STATE: SystemHealthState = {
  isOnline: true,
  status: 'healthy',
  lastCheck: null,
  isChecking: false,
  retries: 0,
};

/**
 * Hook for monitoring system health
 * Polls backend every 10 seconds to detect connection issues
 */
export const useSystemHealth = (onStatusChange?: (status: SystemHealthState) => void) => {
  const [health, setHealth] = useState<SystemHealthState>(INITIAL_STATE);
  const timerRef = useRef<number | null>(null);
  const isUnmountingRef = useRef(false);

  const checkHealth = useCallback(async () => {
    if (health.isChecking) return;

    setHealth((prev) => ({ ...prev, isChecking: true }));

    try {
      const result = await healthService.checkHealth();

      if (!isUnmountingRef.current) {
        const newStatus: SystemHealthState = {
          isOnline: true,
          status: result.status || 'healthy',
          lastCheck: new Date(),
          isChecking: false,
          retries: 0,
        };

        setHealth(newStatus);
        onStatusChange?.(newStatus);
      }
    } catch (error) {
      if (!isUnmountingRef.current) {
        const newStatus: SystemHealthState = {
          isOnline: false,
          status: 'down',
          lastCheck: new Date(),
          isChecking: false,
          retries: health.retries + 1,
        };

        setHealth(newStatus);
        onStatusChange?.(newStatus);
      }
    }
  }, [health.isChecking, health.retries, onStatusChange]);

  useEffect(() => {
    isUnmountingRef.current = false;

    // Initial check
    checkHealth();

    // Start periodic polling (10 seconds)
    timerRef.current = window.setInterval(() => {
      checkHealth();
    }, 10000);

    return () => {
      isUnmountingRef.current = true;
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, [checkHealth]);

  return health;
};

/**
 * Hook for managing reconnection attempts
 */
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
