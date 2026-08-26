/* Metadata Store — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md):
   ui-kit primitives + token utilities, no inline styles. Real catalog entries
   from GET /semantic/models via metadataService — semantic models are
   auto-generated from an ingested dataset's profile (or hand-registered),
   each carrying its real column/measure schema, not a mock catalog. There is
   no bulk dataset-profile listing endpoint — only per-file lookup — so this
   panel surfaces the one real list the gateway exposes: the semantic layer. */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { Panel } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { EmptyState } from '@/components/ui-kit/empty-state';
import { cn } from '@/lib/cn';
import { metadataService, type SemanticModel } from '../../services/api';

const fieldTone = (t: string) => (t === 'measure' ? 'text-signal' : 'text-text-secondary');

export default function MetadataStorePanel() {
  const [models, setModels] = useState<SemanticModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await metadataService.listModels();
      setModels(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the metadata store to list the catalog.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const count = models?.length ?? 0;
  const totalFields = (models ?? []).reduce((n, m) => n + (m.fields?.length ?? 0), 0);

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-metadata-panel">
      <div className="flex items-center gap-3">
        <span className="font-mono text-2xs text-text-tertiary">
          {models === null && !error ? 'loading…' : `${count} catalog model${count === 1 ? '' : 's'} · ${totalFields} field${totalFields === 1 ? '' : 's'}`}
        </span>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw /> Refresh
        </Button>
      </div>

      {error && <div className="border border-border bg-secondary px-3 py-1.5 font-mono text-xs text-danger">{error}</div>}

      <Panel>
        {models === null && !error && <div className="px-4 py-3.5 text-xs text-text-tertiary">Loading catalog…</div>}
        {error && models === null && (
          <EmptyState intent="error" title="Unavailable" action={<Button variant="outline" size="sm" onClick={load}>Retry</Button>} />
        )}
        {models !== null && count === 0 && !error && (
          <EmptyState
            intent="empty"
            title="No catalog models yet"
            description="Semantic models are generated from a dataset's profile once it's ingested — upload a file or run a connector ingest to populate the catalog."
          />
        )}
        {(models ?? []).map((m, i) => (
          <div key={m.id} className={cn('flex flex-col gap-2 px-4 py-2.5', i > 0 && 'border-t border-border')}>
            <div className="flex items-center gap-2.5">
              <span className="truncate text-sm font-semibold text-card-foreground">{m.name}</span>
              <div className="flex-1" />
              <span className="font-mono text-2xs text-text-tertiary">
                {m.fields?.length ?? 0} field{(m.fields?.length ?? 0) === 1 ? '' : 's'}
              </span>
            </div>
            {m.description && <div className="text-xs leading-snug text-text-secondary">{m.description}</div>}
            {(m.tags ?? []).length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {m.tags.map((t) => (
                  <span key={t} className="border border-border bg-secondary px-1.5 py-0.5 font-mono text-2xs text-text-secondary">{t}</span>
                ))}
              </div>
            )}
            {(m.fields ?? []).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {m.fields.map((f) => (
                  <span key={f.id} className={cn('border border-border px-1.5 py-0.5 font-mono text-2xs', fieldTone(f.field_type))}>
                    {f.name}{f.data_type ? `:${f.data_type}` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </Panel>
    </div>
  );
}
