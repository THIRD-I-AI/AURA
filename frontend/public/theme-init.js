// Sets data-theme before first paint so neither theme flashes. A file, not an
// inline script, because the CSP is script-src 'self'. A saved choice wins;
// otherwise the visitor's system setting decides. Mirrors ThemeContext.
(function () {
  var theme;
  try {
    var saved = window.localStorage.getItem('aura-theme');
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch (e) { /* storage blocked: fall through to the system setting */ }
  if (!theme) {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.documentElement.setAttribute('data-theme', theme);
  // Motion recipes hide their start state only under html.js, so content stays
  // visible when scripts fail.
  document.documentElement.classList.add('js');
})();
