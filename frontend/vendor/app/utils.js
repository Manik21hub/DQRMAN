window.DQRMAN = window.DQRMAN || {};

window.DQRMAN.utils = {
  toUTCClock(offsetMs) {
    const drift = Number(offsetMs || 0);
    const now = new Date(Date.now() + (Number.isFinite(drift) ? drift : 0));
    return now.toISOString().slice(11, 19) + ' UTC';
  },

  formatTimestamp(ts) {
    if (!ts) return '--';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '--';
    return d.toISOString().replace('T', ' ').slice(0, 23) + 'Z';
  },

  formatLatencyMs(value) {
    if (!Number.isFinite(Number(value))) return '--';
    return `${Math.round(Number(value))}ms`;
  },

  formatTrust(value) {
    if (!Number.isFinite(Number(value))) return '0';
    return `${Math.round(Number(value) * 100)}%`;
  },

  statusTone(status) {
    const s = String(status || '').toUpperCase();
    if (s === 'ACTIVE') return 'ok';
    if (s === 'DESTROYED' || s === 'ISOLATED') return 'err';
    return 'warn';
  },

  hashText(text) {
    const src = String(text || '');
    let h1 = 0x811c9dc5;
    for (let i = 0; i < src.length; i += 1) {
      h1 ^= src.charCodeAt(i);
      h1 = Math.imul(h1, 0x01000193);
    }
    const hex = (h1 >>> 0).toString(16).toUpperCase().padStart(8, '0');
    return `${hex.slice(0, 4)}:${hex.slice(4, 8)}`;
  },

  severityFromEvent(eventType) {
    const e = String(eventType || '').toUpperCase();
    if (e.includes('REPLAY') || e.includes('SPOOF') || e.includes('JAMMING') || e.includes('DESTROYED') || e.includes('FAILED')) return 'Alert';
    if (e.includes('QUARANTINED') || e.includes('DEGRADED') || e.includes('ANOMALY') || e.includes('RATE_LIMITED') || e.includes('STOPPED')) return 'Warning';
    return 'Info';
  },

  eventColorClass(eventType) {
    const e = String(eventType || '').toUpperCase();
    if (e.includes('AUTH')) return 'badge-green';
    if (e.includes('REPLAY')) return 'badge-amber';
    if (e.includes('SPOOF') || e.includes('JAMMING')) return 'badge-blue';
    if (e.includes('DESTROYED') || e.includes('FAILED')) return 'badge-red';
    if (e.includes('ROUTE') || e.includes('LOCATION')) return 'badge-blue';
    return 'badge-blue';
  },

  esc(value) {
    return String(value == null ? '' : value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  },
};
