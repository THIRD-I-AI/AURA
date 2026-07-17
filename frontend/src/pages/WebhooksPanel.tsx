/**
 * WebhooksPanel
 * =============
 * Three-tab UI for the AURA webhook system:
 *   1. Outbound — register URL subscriptions to AURA events (pipeline.complete,
 *      agent.failed, uasr.drift, …); supports test-fire and recent deliveries.
 *   2. Inbound — register POST hooks at /hooks/fire/{slug} that trigger a
 *      saved pipeline or agent prompt.
 *   3. Deliveries — recent outbound webhook delivery log (status, attempts,
 *      HTTP status, error).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useSSE } from '../hooks/useSSE';
import {
  inboundHookService,
  pipelineService,
  webhookService,
  type InboundHook,
  type OutboundWebhook,
  type WebhookDelivery,
} from '../services/api';
import './WebhooksPanel.css';

type Tab = 'outbound' | 'inbound' | 'deliveries';

interface WebhooksPanelProps {
  setCurrentPage?: (page: any) => void;
}

const WebhooksPanel: React.FC<WebhooksPanelProps> = () => {
  const [tab, setTab] = useState<Tab>('outbound');
  const [outbound, setOutbound] = useState<OutboundWebhook[]>([]);
  const [inbound, setInbound] = useState<InboundHook[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [knownEvents, setKnownEvents] = useState<string[]>([]);
  const [pipelines, setPipelines] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; kind: 'ok' | 'err' } | null>(null);

  // Outbound form
  const [oUrl, setOUrl] = useState('');
  const [oEvents, setOEvents] = useState<string[]>(['*']);
  const [oSecret, setOSecret] = useState('');
  const [oDesc, setODesc] = useState('');
  const [oRetries, setORetries] = useState(3);

  // Inbound form
  const [iSlug, setISlug] = useState('');
  const [iKind, setIKind] = useState<'pipeline' | 'agent'>('pipeline');
  const [iTarget, setITarget] = useState('');
  const [iSecret, setISecret] = useState('');
  const [iDesc, setIDesc] = useState('');
  const [iPayloadKey, setIPayloadKey] = useState('');

  const showToast = (msg: string, kind: 'ok' | 'err' = 'ok') => {
    setToast({ msg, kind });
    setTimeout(() => setToast(null), 3500);
  };

  const refresh = useCallback(async () => {
    try {
      const [o, i, d, e] = await Promise.all([
        webhookService.list(),
        inboundHookService.list(),
        webhookService.deliveries(),
        webhookService.events(),
      ]);
      setOutbound(o.webhooks || []);
      setInbound(i.hooks || []);
      setDeliveries(d.deliveries || []);
      setKnownEvents(e.events || []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load webhook state');
    }
  }, []);

  useEffect(() => {
    refresh();
    pipelineService.list()
      .then(r => setPipelines((r.pipelines || []).map(p => ({ id: p.id, name: p.name }))))
      .catch(() => undefined);
  }, [refresh]);

  // Live-refresh deliveries via SSE — dispatcher publishes each DeliveryRecord
  // to the 'webhooks:deliveries' topic after attempting a POST.
  useSSE({
    topic: 'webhooks:deliveries',
    onEvent: (ev) => {
      const rec = ev.payload as WebhookDelivery | undefined;
      if (!rec || !rec.id) return;
      setDeliveries(prev => {
        if (prev.some(d => d.id === rec.id)) return prev;
        const next = [...prev, rec];
        return next.length > 200 ? next.slice(next.length - 200) : next;
      });
    },
  });

  // ── Outbound CRUD ──
  const handleCreateOutbound = async () => {
    if (!oUrl.trim()) return;
    try {
      await webhookService.create({
        url: oUrl.trim(),
        events: oEvents.length ? oEvents : ['*'],
        secret: oSecret.trim() || undefined,
        retries: oRetries,
        description: oDesc.trim(),
      });
      setOUrl(''); setOSecret(''); setODesc(''); setOEvents(['*']); setORetries(3);
      showToast('Webhook registered');
      refresh();
    } catch (err: any) {
      showToast(err?.message || 'Create failed', 'err');
    }
  };

  const toggleOutbound = async (id: string, active: boolean) => {
    try { await webhookService.update(id, { active: !active }); refresh(); }
    catch (e: any) { showToast(e?.message || 'Update failed', 'err'); }
  };

  const removeOutbound = async (id: string) => {
    if (!confirm('Delete this webhook?')) return;
    try { await webhookService.remove(id); showToast('Deleted'); refresh(); }
    catch (e: any) { showToast(e?.message || 'Delete failed', 'err'); }
  };

  const testOutbound = async (id: string) => {
    try {
      const r = await webhookService.test(id);
      showToast(`Test → ${r.delivery.status} (HTTP ${r.delivery.http_status ?? 'n/a'})`,
        r.delivery.status === 'success' ? 'ok' : 'err');
      refresh();
    } catch (e: any) { showToast(e?.message || 'Test failed', 'err'); }
  };

  // ── Inbound CRUD ──
  const handleCreateInbound = async () => {
    if (!iSlug.trim() || !iTarget.trim()) return;
    try {
      await inboundHookService.create({
        slug: iSlug.trim(),
        kind: iKind,
        target: iTarget.trim(),
        secret: iSecret.trim() || undefined,
        description: iDesc.trim(),
        pass_payload_as: iPayloadKey.trim() || undefined,
      });
      setISlug(''); setITarget(''); setISecret(''); setIDesc(''); setIPayloadKey('');
      showToast('Hook registered');
      refresh();
    } catch (err: any) {
      showToast(err?.message || 'Create failed', 'err');
    }
  };

  const toggleInbound = async (id: string, active: boolean) => {
    try { await inboundHookService.update(id, { active: !active }); refresh(); }
    catch (e: any) { showToast(e?.message || 'Update failed', 'err'); }
  };

  const removeInbound = async (id: string) => {
    if (!confirm('Delete this hook?')) return;
    try { await inboundHookService.remove(id); showToast('Deleted'); refresh(); }
    catch (e: any) { showToast(e?.message || 'Delete failed', 'err'); }
  };

  const copyFireUrl = (slug: string) => {
    const url = inboundHookService.fireUrl(slug);
    navigator.clipboard.writeText(url).then(
      () => showToast('URL copied'),
      () => showToast('Copy failed', 'err'),
    );
  };

  const toggleEvent = (ev: string) => {
    setOEvents(prev =>
      prev.includes(ev) ? prev.filter(e => e !== ev) : [...prev.filter(e => e !== '*'), ev],
    );
  };

  return (
    <div className="webhooks">
      <div className="webhooks__tabs">
        <button className={`webhooks__tab ${tab === 'outbound' ? 'is-active' : ''}`}
          onClick={() => setTab('outbound')}>Outbound ({outbound.length})</button>
        <button className={`webhooks__tab ${tab === 'inbound' ? 'is-active' : ''}`}
          onClick={() => setTab('inbound')}>Inbound ({inbound.length})</button>
        <button className={`webhooks__tab ${tab === 'deliveries' ? 'is-active' : ''}`}
          onClick={() => setTab('deliveries')}>Deliveries ({deliveries.length})</button>
        <button className="webhooks__refresh" onClick={refresh}>↻ Refresh</button>
      </div>

      {error && (
        <div className="webhooks__error">
          {error}
          <button onClick={() => setError(null)} aria-label="Dismiss error" title="Dismiss"><span aria-hidden="true">✕</span></button>
        </div>
      )}

      {/* ── OUTBOUND ── */}
      {tab === 'outbound' && (
        <>
          <section className="webhooks__card">
            <h3>Register outbound webhook</h3>
            <div className="webhooks__form">
              <label>
                Target URL
                <input value={oUrl} onChange={e => setOUrl(e.target.value)}
                  placeholder="https://example.com/aura-events" />
              </label>
              <label>
                Description
                <input value={oDesc} onChange={e => setODesc(e.target.value)}
                  placeholder="Slack relay, audit log, …" />
              </label>
              <label>
                Secret (optional, HMAC-SHA256 sent in X-AURA-Signature)
                <input value={oSecret} onChange={e => setOSecret(e.target.value)} type="password" />
              </label>
              <label>
                Retries
                <input type="number" min={0} max={10} value={oRetries}
                  onChange={e => setORetries(parseInt(e.target.value, 10) || 0)} />
              </label>
              <div className="webhooks__events-grid">
                <div className="webhooks__events-label">Subscribed events</div>
                <label className="webhooks__event-chip">
                  <input type="checkbox" checked={oEvents.includes('*')}
                    onChange={() => setOEvents(oEvents.includes('*') ? [] : ['*'])} />
                  All events (*)
                </label>
                {knownEvents.map(ev => (
                  <label key={ev} className="webhooks__event-chip">
                    <input type="checkbox"
                      checked={oEvents.includes(ev)}
                      disabled={oEvents.includes('*')}
                      onChange={() => toggleEvent(ev)} />
                    {ev}
                  </label>
                ))}
              </div>
              <button className="webhooks__btn webhooks__btn--primary"
                onClick={handleCreateOutbound} disabled={!oUrl.trim()}>
                Register
              </button>
            </div>
          </section>

          <section className="webhooks__card">
            <h3>Registered outbound ({outbound.length})</h3>
            {outbound.length === 0 && <div className="webhooks__empty">No outbound webhooks yet.</div>}
            <div className="webhooks__list">
              {outbound.map(w => (
                <div key={w.id} className={`webhooks__item ${w.active ? '' : 'is-disabled'}`}>
                  <div className="webhooks__item-main">
                    <div className="webhooks__item-url">{w.url}</div>
                    <div className="webhooks__item-meta">
                      {w.description || '—'} · retries {w.retries}
                      {w.has_secret && <span className="webhooks__pill webhooks__pill--ok">signed</span>}
                      {!w.active && <span className="webhooks__pill webhooks__pill--off">disabled</span>}
                    </div>
                    <div className="webhooks__item-events">
                      {w.events.map(e => <span key={e} className="webhooks__event-tag">{e}</span>)}
                    </div>
                  </div>
                  <div className="webhooks__item-actions">
                    <button onClick={() => testOutbound(w.id)}>Test</button>
                    <button onClick={() => toggleOutbound(w.id, w.active)}>
                      {w.active ? 'Disable' : 'Enable'}
                    </button>
                    <button className="webhooks__btn--danger"
                      onClick={() => removeOutbound(w.id)}>Delete</button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {/* ── INBOUND ── */}
      {tab === 'inbound' && (
        <>
          <section className="webhooks__card">
            <h3>Register inbound hook</h3>
            <div className="webhooks__form">
              <label>
                Slug (URL: <code>/hooks/fire/&lt;slug&gt;</code>)
                <input value={iSlug} onChange={e => setISlug(e.target.value)}
                  placeholder="github-push" />
              </label>
              <label>
                Kind
                <select value={iKind} onChange={e => setIKind(e.target.value as 'pipeline' | 'agent')}>
                  <option value="pipeline">Pipeline (run a saved pipeline)</option>
                  <option value="agent">Agent (run a prompt)</option>
                </select>
              </label>
              {iKind === 'pipeline' ? (
                <label>
                  Target pipeline
                  <select value={iTarget} onChange={e => setITarget(e.target.value)}>
                    <option value="">— select —</option>
                    {pipelines.map(p => (
                      <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  Agent prompt
                  <textarea value={iTarget} onChange={e => setITarget(e.target.value)} rows={3}
                    placeholder="Profile the latest dataset and surface anomalies" />
                </label>
              )}
              {iKind === 'agent' && (
                <label>
                  Expose request body in schema_context as (optional)
                  <input value={iPayloadKey} onChange={e => setIPayloadKey(e.target.value)}
                    placeholder="payload" />
                </label>
              )}
              <label>
                Secret (optional, expects HMAC-SHA256 in X-AURA-Signature)
                <input type="password" value={iSecret} onChange={e => setISecret(e.target.value)} />
              </label>
              <label>
                Description
                <input value={iDesc} onChange={e => setIDesc(e.target.value)} />
              </label>
              <button className="webhooks__btn webhooks__btn--primary"
                onClick={handleCreateInbound} disabled={!iSlug.trim() || !iTarget.trim()}>
                Register
              </button>
            </div>
          </section>

          <section className="webhooks__card">
            <h3>Registered inbound ({inbound.length})</h3>
            {inbound.length === 0 && <div className="webhooks__empty">No inbound hooks yet.</div>}
            <div className="webhooks__list">
              {inbound.map(h => (
                <div key={h.id} className={`webhooks__item ${h.active ? '' : 'is-disabled'}`}>
                  <div className="webhooks__item-main">
                    <div className="webhooks__item-url">
                      <span className="webhooks__pill webhooks__pill--kind">{h.kind}</span>
                      &nbsp;<code>/hooks/fire/{h.slug}</code>
                    </div>
                    <div className="webhooks__item-meta">
                      target: <code>{h.target.length > 60 ? h.target.slice(0, 60) + '…' : h.target}</code>
                    </div>
                    <div className="webhooks__item-meta">
                      fired {h.fire_count}× · last {h.last_fired_at || 'never'}
                      {h.has_secret && <span className="webhooks__pill webhooks__pill--ok">signed</span>}
                      {!h.active && <span className="webhooks__pill webhooks__pill--off">disabled</span>}
                    </div>
                  </div>
                  <div className="webhooks__item-actions">
                    <button onClick={() => copyFireUrl(h.slug)}>Copy URL</button>
                    <button onClick={() => toggleInbound(h.id, h.active)}>
                      {h.active ? 'Disable' : 'Enable'}
                    </button>
                    <button className="webhooks__btn--danger"
                      onClick={() => removeInbound(h.id)}>Delete</button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {/* ── DELIVERIES ── */}
      {tab === 'deliveries' && (
        <section className="webhooks__card">
          <h3>Recent outbound deliveries (last {deliveries.length})</h3>
          {deliveries.length === 0 && <div className="webhooks__empty">No deliveries yet.</div>}
          <table className="webhooks__table">
            <thead>
              <tr>
                <th>Time</th><th>Event</th><th>URL</th>
                <th>Status</th><th>HTTP</th><th>Attempts</th><th>Error</th>
              </tr>
            </thead>
            <tbody>
              {[...deliveries].reverse().map(d => (
                <tr key={d.id} className={d.status === 'success' ? '' : 'is-failed'}>
                  <td>{new Date(d.timestamp).toLocaleTimeString()}</td>
                  <td><code>{d.event_type}</code></td>
                  <td className="webhooks__td-url">{d.url}</td>
                  <td>
                    <span className={`webhooks__pill webhooks__pill--${d.status === 'success' ? 'ok' : 'fail'}`}>
                      {d.status}
                    </span>
                  </td>
                  <td>{d.http_status ?? '—'}</td>
                  <td>{d.attempts}</td>
                  <td>{d.error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {toast && (
        <div className={`webhooks__toast webhooks__toast--${toast.kind}`}>{toast.msg}</div>
      )}
    </div>
  );
};

export default WebhooksPanel;
