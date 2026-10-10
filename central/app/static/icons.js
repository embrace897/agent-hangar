/* Ícones SVG (traço 1.75, grade 24×24) — um conjunto só, no lugar de emojis. icon('bot') devolve o <svg>. */
const ICONS = {
  logo: '<path d="M2.5 20.5V11L12 3.5 21.5 11v9.5" /><path d="M6.5 20.5v-6h11v6" /><path d="M9.5 14.5v6M14.5 14.5v6" />',
  gauge: '<path d="M12 14l4.5-4.5" /><path d="M3.4 18.5a10 10 0 1 1 17.2 0" /><circle cx="12" cy="14" r="1.2" />',
  bot: '<rect x="4" y="8" width="16" height="12" rx="2.5" /><path d="M12 8V4.5" /><circle cx="12" cy="3.5" r="1" /><path d="M9 14h.01M15 14h.01" /><path d="M2 13v3M22 13v3" />',
  approve: '<path d="M9 11.5l3 3 8.5-8.5" /><path d="M20 12.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h10" />',
  team: '<circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8" /><path d="M18.2 14a6.5 6.5 0 0 1 3.3 6" />',
  template: '<rect x="3" y="3" width="18" height="7" rx="1.5" /><rect x="3" y="14" width="8" height="7" rx="1.5" /><rect x="15" y="14" width="6" height="7" rx="1.5" />',
  server: '<rect x="3" y="4" width="18" height="7" rx="1.5" /><rect x="3" y="13" width="18" height="7" rx="1.5" /><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6" />',
  flask: '<path d="M9 3h6M10 3v6L4.6 18.9A1.5 1.5 0 0 0 5.9 21h12.2a1.5 1.5 0 0 0 1.3-2.1L14 9V3" /><path d="M7.5 15h9" />',
  chart: '<path d="M3 3v18h18" /><path d="M7 15l4-4 3 3 6-6" />',
  book: '<path d="M4 19.5V5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2z" /><path d="M8.5 7.5h7M8.5 11h5" />',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9.5 2.5v3.5M14.5 2.5v3.5M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5" />',
  key: '<circle cx="7.5" cy="15.5" r="4.5" /><path d="M10.7 12.3L21 2M16 7l3 3M18.5 4.5l2 2" />',
  puzzle: '<path d="M10 3.5a2 2 0 0 1 4 0V6h4a1 1 0 0 1 1 1v4h-2.5a2 2 0 0 0 0 4H19v4a1 1 0 0 1-1 1h-4v-2.5a2 2 0 0 0-4 0V20H6a1 1 0 0 1-1-1v-4h2.5a2 2 0 0 0 0-4H5V7a1 1 0 0 1 1-1h4z" />',
  plug: '<path d="M9 2.5v5M15 2.5v5" /><path d="M5.5 7.5h13v3a6.5 6.5 0 0 1-13 0z" /><path d="M12 17v4.5" />',
  userCog: '<circle cx="10" cy="7.5" r="4" /><path d="M3 21a7 7 0 0 1 10.5-6" /><circle cx="18" cy="17.5" r="2.5" /><path d="M18 13.5v1.5M18 20v1.5M14 17.5h1.5M20.5 17.5H22" />',
  shield: '<path d="M12 3l8 3v6c0 5-3.4 8.2-8 9-4.6-.8-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" />',
  scroll: '<path d="M8 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6.5" /><path d="M6.5 21A2.5 2.5 0 0 1 4 18.5V5.5A2.5 2.5 0 0 1 6.5 3" /><path d="M9 8h8M9 12h8M9 16h5" />',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7.5a4 4 0 0 1 8 0V11" />',
  building: '<rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M9 7.5h1M14 7.5h1M9 11.5h1M14 11.5h1M10 21v-4h4v4" />',
  globe: '<circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />',
  chat: '<path d="M21 11.5a8.5 8.5 0 0 1-12.4 7.6L3 21l1.9-5.6A8.5 8.5 0 1 1 21 11.5z" />',
  check: '<path d="M5 12.5l4.5 4.5L19.5 7" />',
  x: '<path d="M6 6l12 12M18 6L6 18" />',
  plus: '<path d="M12 5v14M5 12h14" />',
  logout: '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><path d="M10 17l5-5-5-5M15 12H3" />',
  login: '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><path d="M10 17l5-5-5-5M15 12H3" />',
  briefcase: '<rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8.5 7V5a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2" /><path d="M3 12.5h18" /><path d="M11 12.5v1.5h2v-1.5" />',
  inbox: '<path d="M3 13l3-8h12l3 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M3 13h5l1.5 2.5h5L16 13h5" />',
  user: '<circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" />',
  target: '<circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.5" />',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />',
  wallet: '<rect x="3" y="6" width="18" height="14" rx="2" /><path d="M3 10h18" /><path d="M16 15h2" /><path d="M7 6V4.5A1.5 1.5 0 0 1 8.5 3H17" />',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />',
};
const BRAND = {  // marca da Microsoft (quatro quadrados) é simples e exata; as outras usam o ícone de login
  microsoft: '<svg class="ic ic-brand" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="2" width="9.5" height="9.5" fill="#F25022"/><rect x="12.5" y="2" width="9.5" height="9.5" fill="#7FBA00"/><rect x="2" y="12.5" width="9.5" height="9.5" fill="#00A4EF"/><rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#FFB900"/></svg>',
};
function icon(name, cls = '') {
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}
