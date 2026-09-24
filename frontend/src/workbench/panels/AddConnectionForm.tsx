/* Add-connection form for the Connectors panel (BUG-157). Generated off the
   connector registry (GET /connectors/registry), the single source of truth
   for each connector's fields.

   Only connectors whose fields POST /connections can actually PERSIST are
   offered: that endpoint stores name/type/host/port/database/username/
   password/ssl and drops everything else (its `extra` dict is never written),
   so BigQuery (project_id/dataset/credentials_json) cannot be created through
   it today — offering it would accept input the backend silently discards. */
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui-kit/button';
import { Panel } from '@/components/ui-kit/panel';
import { cn } from '@/lib/cn';
import { connectorService, type ConnectorField, type ConnectorSpec } from '../../services/api';

const STORABLE_KEYS = new Set(['host', 'port', 'database', 'username', 'password', 'ssl']);

const INPUT_CLASS = cn(
  'h-8 w-full rounded-none border border-border-hairline bg-secondary px-2 font-mono text-xs text-card-foreground',
  'outline-none placeholder:text-text-tertiary',
  'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
  'disabled:pointer-events-none disabled:opacity-50',
);

type Values = Record<string, string | boolean>;

/** Connectors this form can create end-to-end, in registry order. */
function creatableConnectors(specs: ConnectorSpec[]): ConnectorSpec[] {
  return specs.filter(
    (s) => s.available && s.fields.length > 0 && s.fields.every((f) => STORABLE_KEYS.has(f.key)),
  );
}

/** The registry lists `port` twice for postgresql/mysql: the shared base field
    (no default) and the connector's own (default 5432/3306) appended after it.
    The later definition is the intended override, so it wins — at the position
    of the first — otherwise the port would render blank. */
function uniqueFields(spec: ConnectorSpec): ConnectorField[] {
  const byKey = new Map<string, ConnectorField>();
  for (const f of spec.fields) byKey.set(f.key, f);
  return [...byKey.values()];
}

function initialValues(spec: ConnectorSpec): Values {
  const values: Values = {};
  for (const f of uniqueFields(spec)) {
    if (f.type === 'boolean') values[f.key] = f.default === true;
    else values[f.key] = f.default != null ? String(f.default) : '';
  }
  return values;
}

export function AddConnectionForm({ onCreated, onClose }: { onCreated: () => void; onClose: () => void }) {
  const [specs, setSpecs] = useState<ConnectorSpec[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [type, setType] = useState('');
  const [name, setName] = useState('');
  const [values, setValues] = useState<Values>({});
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let live = true;
    connectorService
      .registry(false)
      .then((all) => {
        if (!live) return;
        const usable = creatableConnectors(all);
        setSpecs(usable);
        if (usable.length > 0) {
          setType(usable[0].id);
          setValues(initialValues(usable[0]));
        }
      })
      .catch(() => { if (live) setLoadError('Could not load the list of connector types.'); });
    return () => { live = false; };
  }, []);

  const spec = useMemo(() => specs?.find((s) => s.id === type) ?? null, [specs, type]);
  const fields = useMemo(() => (spec ? uniqueFields(spec) : []), [spec]);

  const valid =
    name.trim() !== '' &&
    fields.every((f) => !f.required || f.type === 'boolean' || String(values[f.key] ?? '').trim() !== '');

  const changeType = (id: string) => {
    setType(id);
    const next = specs?.find((s) => s.id === id);
    if (next) setValues(initialValues(next));
    setSaveError(null);
    setOutcome(null);
  };

  const save = async () => {
    if (!spec || !valid || busy) return;
    setBusy(true);
    setSaveError(null);
    setOutcome(null);
    const str = (k: string) => (typeof values[k] === 'string' && values[k] !== '' ? (values[k] as string) : undefined);
    try {
      const saved = await connectorService.registerSource({
        name: name.trim(),
        type: spec.id,
        host: str('host'),
        port: str('port') ? Number(str('port')) : undefined,
        database: str('database'),
        username: str('username'),
        password: str('password'),
        ssl: values.ssl === true,
      });
      // Saving proves nothing about reachability, so test it and say what
      // happened rather than leaving a green-looking row that may not connect.
      let text = `Saved "${name.trim()}".`;
      let ok = true;
      try {
        const test = await connectorService.testConnection(saved.id);
        text += test.success ? ` Connected — ${test.message}` : ` The connection test failed: ${test.message}`;
        ok = test.success;
      } catch (e) {
        ok = false;
        text += ` The connection test failed: ${e instanceof Error ? e.message : 'could not reach the gateway'}`;
      }
      setOutcome({ ok, text });
      // Never keep a credential in component state after it has been sent.
      setName('');
      setValues(initialValues(spec));
      onCreated();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Could not save the connection.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel>
      <form
        data-testid="wb-add-connection-form"
        className="flex flex-col gap-3 px-4 py-3.5"
        onSubmit={(e) => { e.preventDefault(); void save(); }}
      >
        <div className="flex items-center gap-2">
          <div className="font-mono text-2xs font-semibold uppercase tracking-widest text-text-tertiary">Add connection</div>
          <div className="flex-1" />
          <Button type="button" variant="ghost" size="xs" onClick={onClose}>Close</Button>
        </div>

        {loadError && <p role="alert" className="font-mono text-xs text-danger">{loadError}</p>}
        {specs === null && !loadError && <p className="font-mono text-2xs text-text-tertiary">Loading connector types…</p>}
        {specs !== null && specs.length === 0 && !loadError && (
          <p className="font-mono text-2xs text-text-tertiary">No connector types are available to add from here.</p>
        )}

        {spec && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label htmlFor="wb-conn-type" className="font-mono text-2xs text-text-secondary">Type</label>
              <select
                id="wb-conn-type"
                value={type}
                onChange={(e) => changeType(e.target.value)}
                disabled={busy}
                className={INPUT_CLASS}
              >
                {specs?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="wb-conn-name" className="font-mono text-2xs text-text-secondary">Name</label>
              <input
                id="wb-conn-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
                placeholder="e.g. Production warehouse"
                autoComplete="off"
                className={INPUT_CLASS}
              />
            </div>
            {fields.map((f) =>
              f.type === 'boolean' ? (
                <label key={f.key} className="flex items-center gap-2 font-mono text-2xs text-text-secondary sm:col-span-2">
                  <input
                    type="checkbox"
                    data-testid={`wb-conn-field-${f.key}`}
                    checked={values[f.key] === true}
                    onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.checked }))}
                    disabled={busy}
                  />
                  {f.label}
                </label>
              ) : (
                <div key={f.key} className="flex flex-col gap-1">
                  <label htmlFor={`wb-conn-field-${f.key}`} className="font-mono text-2xs text-text-secondary">
                    {f.label}{f.required ? ' *' : ''}
                  </label>
                  <input
                    id={`wb-conn-field-${f.key}`}
                    data-testid={`wb-conn-field-${f.key}`}
                    type={f.type === 'secret' ? 'password' : f.type === 'number' ? 'number' : 'text'}
                    value={String(values[f.key] ?? '')}
                    onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                    disabled={busy}
                    placeholder={f.placeholder ?? undefined}
                    autoComplete={f.type === 'secret' ? 'new-password' : 'off'}
                    className={INPUT_CLASS}
                  />
                  {f.help && <span className="text-2xs text-text-tertiary">{f.help}</span>}
                </div>
              ),
            )}
          </div>
        )}

        {saveError && <p role="alert" data-testid="wb-add-connection-error" className="font-mono text-xs text-danger">{saveError}</p>}
        {outcome && (
          <p
            role={outcome.ok ? 'status' : 'alert'}
            data-testid="wb-add-connection-outcome"
            className={cn('font-mono text-xs', outcome.ok ? 'text-signal' : 'text-warn')}
          >
            {outcome.text}
          </p>
        )}

        {spec && (
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={!valid || busy} data-testid="wb-add-connection-save">
              {busy ? 'Saving…' : 'Save connection'}
            </Button>
          </div>
        )}
      </form>
    </Panel>
  );
}
