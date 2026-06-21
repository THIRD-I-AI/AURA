import { describe, expect, it } from 'vitest';
import { PANEL_REGISTRY, PANEL_IDS } from '../panels/registry';

describe('panel registry', () => {
  it('exposes the four v1 panels, each with a title, icon, and lazy component', () => {
    expect(PANEL_IDS).toEqual(['query', 'datasets', 'findings', 'livefeed']);
    for (const id of PANEL_IDS) {
      const def = PANEL_REGISTRY[id];
      expect(def.title).toBeTruthy();
      expect(def.icon).toBeTruthy();
      expect(def.component).toBeTruthy();
    }
  });
});
