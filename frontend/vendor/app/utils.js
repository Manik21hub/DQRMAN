window.DQRMAN = window.DQRMAN || {};

function parseTimestampValue(ts) {
  if (ts == null || ts === '') return null;

  if (typeof ts === 'number') {
    const millis = ts > 1e12 ? ts : ts * 1000;
    const d = new Date(millis);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  if (typeof ts === 'string') {
    const trimmed = ts.trim();
    if (!trimmed) return null;

    if (/^\d+(\.\d+)?$/.test(trimmed)) {
      const numeric = Number(trimmed);
      const millis = numeric > 1e12 ? numeric : numeric * 1000;
      const d = new Date(millis);
      return Number.isNaN(d.getTime()) ? null : d;
    }

    const d = new Date(trimmed);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatIndiaDateTime(date) {
  const parts = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date);

  const values = {};
  parts.forEach((part) => {
    if (part.type !== 'literal') values[part.type] = part.value;
  });

  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second} IST`;
}

window.DQRMAN.utils = {
  toUTCClock(offsetMs) {
    const drift = Number(offsetMs || 0);
    const now = new Date(Date.now() + (Number.isFinite(drift) ? drift : 0));
    const time = new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(now);
    return `${time} IST`;
  },

  formatTimestamp(ts) {
    if (!ts) return '--';
    const d = parseTimestampValue(ts);
    if (!d) return '--';
    return formatIndiaDateTime(d);
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
