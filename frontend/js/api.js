/* =========================================================
   API wrapper — talks to the FastAPI backend.
   All requests use credentials:'include' so the httpOnly JWT
   session cookie set by /api/auth/login is sent automatically.
   ========================================================= */

const API_BASE = '/api';

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function apiRequest(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    credentials: 'include',
    headers: options.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...options,
  });

  if (res.status === 401) {
    // Session expired or not authenticated — bounce back to login screen.
    if (window.onApiUnauthorized) window.onApiUnauthorized();
    throw new ApiError(401, 'Phiên đăng nhập đã hết hạn.');
  }

  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }

  if (!res.ok) {
    const message = (data && data.detail) ? data.detail : `Lỗi API (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return data;
}

const api = {
  login: (username, password) => apiRequest('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => apiRequest('/auth/logout', { method: 'POST' }),
  me: () => apiRequest('/auth/me'),

  listVcenters: () => apiRequest('/vcenters'),
  syncVcenter: (key) => apiRequest(`/vcenters/${encodeURIComponent(key)}/sync`, { method: 'POST' }),
  syncAll: () => apiRequest('/vcenters/sync-all', { method: 'POST' }),

  getLatestData: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return apiRequest(`/data/latest${qs ? '?' + qs : ''}`);
  },

  getTrend: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return apiRequest(`/dashboard/trend${qs ? '?' + qs : ''}`);
  },

  getComplianceFindings: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return apiRequest(`/compliance/findings${qs ? '?' + qs : ''}`);
  },

  importData: (payload) => apiRequest('/import', { method: 'POST', body: JSON.stringify(payload) }),

  getSettings: () => apiRequest('/settings'),
  updateSettings: (payload) => apiRequest('/settings', { method: 'PUT', body: JSON.stringify(payload) }),

  /* PDF report is a binary download, not JSON — bypasses apiRequest and
     streams the response straight into a file-save via an object URL. */
  downloadPdfReport: async (view, vcenter) => {
    const qs = new URLSearchParams({ view, vcenter: vcenter || 'all' }).toString();
    const res = await fetch(`${API_BASE}/reports/pdf?${qs}`, { credentials: 'include' });
    if (res.status === 401) {
      if (window.onApiUnauthorized) window.onApiUnauthorized();
      throw new ApiError(401, 'Phiên đăng nhập đã hết hạn.');
    }
    if (!res.ok) {
      let detail = `Lỗi API (${res.status})`;
      try { const data = await res.json(); if (data && data.detail) detail = data.detail; } catch { /* body wasn't JSON */ }
      throw new ApiError(res.status, detail);
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const match = /filename="?([^";]+)"?/.exec(cd);
    const filename = match ? match[1] : `vco_report_${view}_${Date.now()}.pdf`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
