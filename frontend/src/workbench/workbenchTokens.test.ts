/* BUG-151 guard: the workbench's short color names must alias tokens.css, not
   restate its hex — otherwise a tokens.css rebrand silently skips the cockpit. */
import { describe, expect, it } from 'vitest';

import workbenchCss from './workbench.css?raw';
import tokensCss from '../styles/tokens.css?raw';

const awBlock = workbenchCss.slice(workbenchCss.indexOf('\n.aw {'), workbenchCss.indexOf('\n}', workbenchCss.indexOf('\n.aw {')));

const ALIASES: Record<string, string> = {
  '--bg': '--bg-base',
  '--surface': '--bg-surface',
  '--raised': '--bg-raised',
  '--sunken': '--bg-sunken',
  '--border': '--border-default',
  '--hair': '--border-hairline',
  '--text': '--text-primary',
  '--text2': '--text-secondary',
  '--accent-bd': '--accent-border',
};

describe('workbench.css color vocabulary (BUG-151)', () => {
  it.each(Object.entries(ALIASES))('%s aliases %s instead of restating hex', (short, canonical) => {
    expect(awBlock).toMatch(new RegExp(`${short}:\\s*var\\(${canonical}\\)`));
  });

  it('every alias target is a real tokens.css custom property', () => {
    for (const canonical of Object.values(ALIASES)) {
      expect(tokensCss).toMatch(new RegExp(`${canonical}:`));
    }
  });

  it('does not redeclare the same-named tokens (they inherit identical values from :root)', () => {
    for (const name of ['--accent', '--warn', '--danger', '--cyan']) {
      expect(awBlock).not.toMatch(new RegExp(`(^|[;\\s{])${name}:`));
    }
  });
});
