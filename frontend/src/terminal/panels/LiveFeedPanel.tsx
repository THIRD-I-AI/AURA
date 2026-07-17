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
      <div className="panel-head">
        <span className="panel-head-glyph" aria-hidden>⌁</span>
        <span className="panel-head-title">Live Feed</span>
        <span className="panel-head-metric">
          {events.length ? `${events.length} event${events.length === 1 ? '' : 's'}` : 'system:health'}
        </span>
      </div>
      <div className={`feed-status ${connected ? 'is-on' : 'is-off'}`}>
        {connected ? 'live' : 'offline'}{error ? ' · error' : ''}
      </div>
      {events.length === 0 ? (
        <div className="panel-empty" role="status">
          <span className="panel-empty-glyph" aria-hidden>◌</span>
          <span className="panel-empty-title">
            {connected ? 'Awaiting events' : 'Feed offline'}
          </span>
          <span className="panel-empty-hint">
            {connected
              ? 'Connected to system:health — events will stream in as they occur.'
              : 'Not connected to the event stream.'}
          </span>
        </div>
      ) : (
        <ul className="feed-list">
          {events.map((e, i) => (
            /* SSE streams without id: lines yield id="" — index keeps keys unique */
            <li key={`${e.id}-${i}`} className={`feed-item type-${e.type}`}>
              <span className="feed-ts">{e.timestamp}</span>
              <span className="feed-topic">{e.topic}</span>
              <span className="feed-payload">{JSON.stringify(e.payload)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
