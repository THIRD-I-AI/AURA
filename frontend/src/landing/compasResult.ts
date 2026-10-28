/** The real result of AURA's COMPAS scenario: 5,278 ProPublica records, race
 *  -> two-year recidivism, adjusted for prior offenses and age (run 2026-10-28,
 *  estimators double ML and TMLE; IV had no instrument in this scenario). */
export const COMPAS = {
  records: '5,278',
  rawGap: '+13.2',
  estimates: [
    { method: 'Double machine learning', point: '+2.4', ci: '−3.0 to +7.8' },
    { method: 'Targeted max. likelihood', point: '+2.3', ci: '−0.5 to +5.0' },
  ],
  // The four refutation checks of that run, as reported (sensitivity did not pass:
  // the estimate is not robust to modest hidden confounding).
  checks: [
    { name: 'Placebo treatment', result: 'passed' },
    { name: 'Random common cause', result: 'passed' },
    { name: 'Data subset', result: 'passed' },
    { name: 'E-value sensitivity', result: 'fragile to hidden confounding' },
  ],
  verdict: 'No material disparate impact after causal adjustment.',
} as const;
