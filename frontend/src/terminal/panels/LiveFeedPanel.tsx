import { useCallback, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { useSSE, type SSEEvent } from '../../hooks/useSSE';

const FEED_LIMIT = 200;

export default function LiveFeedPanel(_props: IDockviewPanelProps) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const onEvent = useCallback((e: SSEEvent) => {
    setEvents((prev) => [e, ...prev].slice(0, FEED_LIMIT));
  }, []);
  const { connected, error } = useSSE({ topic: 'system:health', onEvent });

  return (
    <div data-testid="livefeed-panel" className="aura-panel livefeed-panel">
      <div className={`feed-status ${connected ? 'is-on' : 'is-off'}`}>
        {connected ? '● live' : '○ offline'}{error ? ' · error' : ''}
      </div>
      <ul className="feed-list">
        {events.map((e) => (
          <li key={e.id} className={`feed-item type-${e.type}`}>
            <span className="feed-ts">{e.timestamp}</span>
            <span className="feed-topic">{e.topic}</span>
            <span className="feed-payload">{JSON.stringify(e.payload)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
