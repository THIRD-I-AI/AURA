import type { ReactNode } from 'react';

/** Inline failure for a wizard step. role="alert" so assistive tech announces
    it — a bare <p> is silent, so a failed upload/run looked like nothing
    happened. Retry is the step's own control (re-pick the file / Run again). */
export function WizardError({ testId, children }: { testId: string; children: ReactNode }) {
  return (
    <p
      role="alert"
      data-testid={testId}
      className="bg-danger/10 px-3 py-2 text-sm text-danger"
    >
      {children}
    </p>
  );
}
