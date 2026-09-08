import { useEffect, useState, useRef, useCallback } from 'react';
import { getCampaignEventsUrl } from '../services/api';

/**
 * React hook that streams real-time campaign progress, worker status,
 * and activity events via Server-Sent Events (SSE).
 *
 * Automatically connects, decodes events, updates progress states,
 * and handles reconnection seamlessly.
 */
export function useCampaignEvents(campaignId, { onEvent, onProgress } = {}) {
  const [isConnected, setIsConnected] = useState(false);
  const [liveProgress, setLiveProgress] = useState(null);
  const [workerStatus, setWorkerStatus] = useState('idle');
  const [lastEvent, setLastEvent] = useState(null);
  const eventSourceRef = useRef(null);

  const handleMessage = useCallback((event) => {
    try {
      const data = JSON.parse(event.data);
      setLastEvent(data);
      onEvent?.(data);

      if (data.progress) {
        setLiveProgress((prev) => ({
          ...(prev || {}),
          ...data.progress,
        }));
        if (data.progress.worker_status) {
          setWorkerStatus(data.progress.worker_status);
        }
        onProgress?.(data.progress);
      }
    } catch (err) {
      // Ignore keepalive comments or non-JSON payloads
    }
  }, [onEvent, onProgress]);

  useEffect(() => {
    if (!campaignId) return;

    const url = getCampaignEventsUrl(campaignId);
    let es;
    try {
      es = new EventSource(url);
      eventSourceRef.current = es;

      es.onopen = () => {
        setIsConnected(true);
      };

      es.onmessage = handleMessage;

      // Also listen to specific event type names if fired by server
      const eventTypes = [
        'CAMPAIGN_CREATED',
        'PLAN_GENERATED',
        'DRY_RUN_COMPLETED',
        'CAMPAIGN_STARTED',
        'MESSAGE_GENERATED',
        'MESSAGE_SENT',
        'MESSAGE_FAILED',
        'MESSAGE_RETRIED',
        'CAMPAIGN_PAUSED',
        'CAMPAIGN_RESUMED',
        'CAMPAIGN_CANCELLED',
        'CAMPAIGN_COMPLETED',
        'PROGRESS_UPDATED',
        'WORKER_STATUS',
      ];

      eventTypes.forEach((type) => {
        es.addEventListener(type, handleMessage);
      });

      es.onerror = () => {
        setIsConnected(false);
      };
    } catch (e) {
      console.warn('Failed to initialize EventSource for campaign', campaignId, e);
    }

    return () => {
      if (es) {
        es.close();
      }
      setIsConnected(false);
    };
  }, [campaignId, handleMessage]);

  return {
    isConnected,
    liveProgress,
    workerStatus,
    lastEvent,
  };
}
