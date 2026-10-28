import { useEffect, useId, useState } from 'react';
import { COMPAS } from './compasResult';


async function sha256(text: string): Promise<string> {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
}

function shortHash(hex: string): string {
  return hex ? `${hex.slice(0, 12)}…${hex.slice(-8)}` : 'computing…';
}

/**
 * The hero's live control. The verdict is sealed by its SHA-256 digest; the
 * visitor can rewrite it, and the digest the page recomputes on every keystroke
 * stops matching the seal. Real certificates are ED25519-signed over the whole
 * record and checked on /verify; this shows the same idea on one line.
 */
export function SealFragment() {
  const fieldId = useId();
  const [text, setText] = useState<string>(COMPAS.verdict);
  const [sealed, setSealed] = useState('');
  const [current, setCurrent] = useState('');

  useEffect(() => {
    let live = true;
    sha256(COMPAS.verdict).then((h) => { if (live) setSealed(h); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    let live = true;
    sha256(text).then((h) => { if (live) setCurrent(h); });
    return () => { live = false; };
  }, [text]);

  const intact = sealed !== '' && current === sealed;
  const state = current === '' || sealed === '' ? 'pending' : intact ? 'intact' : 'broken';

  return (
    <figure data-ui className="seal" aria-labelledby={`${fieldId}-title`}>
      <figcaption className="seal__head">
        <span id={`${fieldId}-title`} className="seal__title">COMPAS recidivism audit</span>
        <span className="seal__meta">{COMPAS.records} ProPublica records · race → two-year reoffending</span>
      </figcaption>

      <table className="seal__table">
        <caption className="sr-only">Estimated effect of race, in percentage points</caption>
        <thead>
          <tr><th scope="col">Estimate</th><th scope="col">Effect</th><th scope="col">95% interval</th></tr>
        </thead>
        <tbody>
          <tr><td>Raw gap, unadjusted</td><td className="num">{COMPAS.rawGap}</td><td className="num">—</td></tr>
          {COMPAS.estimates.map((e) => (
            <tr key={e.method}><td>{e.method}</td><td className="num">{e.point}</td><td className="num">{e.ci}</td></tr>
          ))}
        </tbody>
      </table>

      <label htmlFor={fieldId} className="seal__label">Verdict — edit it</label>
      <textarea
        id={fieldId}
        className="seal__verdict"
        value={text}
        rows={2}
        spellCheck={false}
        onChange={(e) => setText(e.target.value)}
        aria-describedby={`${fieldId}-status`}
      />

      <div className="seal__digests">
        <span>Sealed</span><code>{shortHash(sealed)}</code>
        <span>Now</span><code>{shortHash(current)}</code>
      </div>
      <p id={`${fieldId}-status`} className={`seal__status seal__status--${state}`} role="status" aria-live="polite">
        {state === 'broken'
          ? 'The digest no longer matches the seal. Verification would fail.'
          : 'Matches the seal. Change one word and it will not.'}
      </p>
      {state === 'broken' && (
        <button type="button" className="seal__restore" onClick={() => setText(COMPAS.verdict)}>
          Restore the sealed text
        </button>
      )}
    </figure>
  );
}
