import axios from 'axios';

function resolveApiBase() {
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    // When running locally in browser, always communicate with the local backend
    if (host === 'localhost' || host === '127.0.0.1' || host === '[::1]') {
      return '/api';
    }
  }
  return import.meta.env.VITE_API_URL || '/api';
}

const API_BASE = resolveApiBase();

/*
 * Single Axios instance for the whole workspace, so timeouts and error shaping
 * are defined once. Every call in this file goes through it.
 */
const client = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' }
});

/*
 * User Identity Context Abstraction.
 * Derives current operator identity from local storage or defaults to 'local_user'.
 * Injected automatically into all API requests via X-User-ID headers.
 */
let currentUserId = (typeof window !== 'undefined' && localStorage.getItem('resolve_user_id')) || 'local_user';
let currentUserEmail = (typeof window !== 'undefined' && localStorage.getItem('resolve_user_email')) || 'aman@ckript.com';
let currentUserName = (typeof window !== 'undefined' && localStorage.getItem('resolve_user_name')) || 'Aman Azad';

export function getCurrentUser() {
  return {
    user_id: currentUserId,
    email: currentUserEmail,
    name: currentUserName
  };
}

export function setCurrentUser({ user_id, email, name }) {
  if (user_id) {
    currentUserId = user_id;
    if (typeof window !== 'undefined') localStorage.setItem('resolve_user_id', user_id);
  }
  if (email !== undefined) {
    currentUserEmail = email;
    if (typeof window !== 'undefined') localStorage.setItem('resolve_user_email', email);
  }
  if (name !== undefined) {
    currentUserName = name;
    if (typeof window !== 'undefined') localStorage.setItem('resolve_user_name', name);
  }
}

export const CURRENT_USER = currentUserId;

client.interceptors.request.use((config) => {
  config.headers = config.headers || {};
  config.headers['X-User-ID'] = currentUserId;
  config.headers['X-User-Email'] = currentUserEmail;
  config.headers['X-User-Name'] = currentUserName;
  return config;
});


/**
 * Normalises an Axios failure into something a screen can render.
 * FastAPI puts structured errors in `detail`, which may be a string or an object
 * (the permission layer returns an object with reasons and a fix).
 */
export function toApiError(err) {
  const status = err?.response?.status ?? null;
  const detail = err?.response?.data?.detail;

  let message = err?.message || 'Something went wrong.';
  let reasons = [];
  let howToFix = null;
  let code = null;

  if (typeof detail === 'string') {
    message = detail;
  } else if (detail && typeof detail === 'object') {
    message = detail.message || message;
    reasons = detail.reasons || [];
    howToFix = detail.how_to_fix || null;
    code = detail.error || null;
  }

  if (!err?.response) {
    message = 'Cannot reach the Resolve AI backend. Check that the API is running.';
    code = 'network_error';
  }

  return { status, code, message, reasons, howToFix, raw: detail ?? null };
}

const unwrap = async (promise) => {
  try {
    const response = await promise;
    return response.data;
  } catch (err) {
    throw toApiError(err);
  }
};

/* ==========================================================================
 * Support chat (unchanged behaviour, kept for the assistant experience)
 * ========================================================================== */

export const sendChatMessage = async (sessionId, userId, message) =>
  unwrap(client.post('/chat', { session_id: sessionId, user_id: userId, message }));

export const fetchChatHistory = async (sessionId) =>
  unwrap(client.get(`/chat/history/${sessionId}`));

export const escalateSession = async (sessionId, userId, reason) =>
  unwrap(client.post('/escalate', { session_id: sessionId, user_id: userId, reason }));

export const checkHealth = async () => unwrap(client.get('/health'));

/* ==========================================================================
 * Datasets and contacts
 * ========================================================================== */

export const uploadContacts = async (file, onProgress) => {
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await client.post('/contacts/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
      onUploadProgress: (event) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded * 100) / event.total));
        }
      }
    });
    return response.data;
  } catch (err) {
    throw toApiError(err);
  }
};

export const listDatasets = async (page = 1, pageSize = 50) =>
  unwrap(client.get('/contacts/datasets/list', { params: { page, page_size: pageSize } }));

export const getDataset = async (datasetId) =>
  unwrap(client.get(`/contacts/datasets/${datasetId}`));

export const listContacts = async (params = {}) =>
  unwrap(client.get('/contacts', { params }));

/* ==========================================================================
 * Campaigns
 * ========================================================================== */

export const listCampaigns = async (ownerId) =>
  unwrap(client.get('/campaigns', { params: ownerId ? { owner_id: ownerId } : {} }));

export const getCampaign = async (campaignId) =>
  unwrap(client.get(`/campaigns/${campaignId}`));

export const planCampaign = async ({ goal, datasetId, name, channel }) =>
  unwrap(
    client.post('/campaigns/plan', {
      goal,
      dataset_id: datasetId || null,
      name: name || null,
      owner_id: CURRENT_USER,
      channel: channel || 'EMAIL'
    })
  );

export const startCampaign = async (campaignId, options = {}) =>
  unwrap(
    client.post(`/campaigns/${campaignId}/start`, {
      user_id: CURRENT_USER,
      dry_run: options.dryRun ?? false,
      rate_per_minute: options.ratePerMinute ?? 60,
      concurrency: options.concurrency ?? 4,
      max_attempts: options.maxAttempts ?? 3,
      sender_profile: options.senderProfile ?? null,
      startup_info: options.startupInfo ?? null
    })
  );

export const pauseCampaign = async (campaignId) =>
  unwrap(client.post(`/campaigns/${campaignId}/pause`));

export const resumeCampaign = async (campaignId, options = {}) =>
  unwrap(
    client.post(`/campaigns/${campaignId}/resume`, {
      user_id: CURRENT_USER,
      rate_per_minute: options.ratePerMinute ?? null,
      concurrency: options.concurrency ?? null
    })
  );

export const cancelCampaign = async (campaignId) =>
  unwrap(client.post(`/campaigns/${campaignId}/cancel`));

export const getCampaignProgress = async (campaignId) =>
  unwrap(client.get(`/campaigns/${campaignId}/progress`));

export const listCampaignJobs = async (campaignId, { status, page = 1, pageSize = 50 } = {}) =>
  unwrap(
    client.get(`/campaigns/${campaignId}/jobs`, {
      params: { status: status || undefined, page, page_size: pageSize }
    })
  );

export const listCampaignActivity = async (campaignId, { eventType, page = 1, pageSize = 50 } = {}) =>
  unwrap(
    client.get(`/campaigns/${campaignId}/activity`, {
      params: { event_type: eventType || undefined, page, page_size: pageSize }
    })
  );

export const listGlobalCampaignActivity = async ({ eventType, page = 1, pageSize = 50 } = {}) =>
  unwrap(
    client.get('/campaigns/activity/log', {
      params: { event_type: eventType || undefined, page, page_size: pageSize }
    })
  );

export const getCampaignEventsUrl = (campaignId) =>
  `${API_BASE}/campaigns/${campaignId}/events`;

export const getCampaignWebSocketUrl = (campaignId) => {
  const wsBase = API_BASE.replace(/^http/, 'ws');
  return `${wsBase}/campaigns/${campaignId}/ws`;
};

/* ==========================================================================
 * Automation agent
 *
 * The plan/approve flow maps onto the backend's authorization scope:
 *   plan only  -> allow_send false  (stops at CHECK_AUTHORIZATION, creates nothing)
 *   dry run    -> allow_dry_run     (generates messages, sends nothing)
 *   start      -> allow_send + authorized_by (an explicit human approval)
 * ========================================================================== */

const runAgent = async ({ message, datasetId, scope }) =>
  unwrap(
    client.post(
      '/automation/agent/run',
      {
        message,
        user_id: CURRENT_USER,
        dataset_id: datasetId || null,
        authorization_scope: scope
      },
      { timeout: 120000 }
    )
  );

export const planAutomation = async ({ message, datasetId }) =>
  runAgent({ message, datasetId, scope: { allow_send: false, allow_dry_run: false } });

export const dryRunAutomation = async ({ message, datasetId }) =>
  runAgent({ message, datasetId, scope: { allow_dry_run: true, dry_run: true } });

export const approveAndRunAutomation = async ({ message, datasetId, maxRecipients }) =>
  runAgent({
    message,
    datasetId,
    scope: {
      allow_send: true,
      authorized_by: CURRENT_USER,
      ...(maxRecipients ? { max_recipients: maxRecipients } : {})
    }
  });

export const getAutomationRun = async (runId) =>
  unwrap(client.get(`/automation/agent/${runId}`));

export const listAutomationRuns = async (limit = 25) =>
  unwrap(client.get('/automation/agent', { params: { limit } }));

/* ==========================================================================
 * Integrations
 *
 * These endpoints return connection state only. No access token, refresh token
 * or client secret is ever part of a response, and none is stored client-side.
 * ========================================================================== */

export const listIntegrations = async () =>
  unwrap(client.get('/integrations'));

export const getGmailStatus = async (accountId) =>
  unwrap(client.get('/integrations/gmail/status', { params: accountId ? { account_id: accountId } : {} }));


export const connectGmail = async ({ accountId, loginHint, redirectAfter } = {}) =>
  unwrap(
    client.post('/integrations/gmail/connect', {
      account_id: accountId || null,
      login_hint: loginHint || null,
      redirect_after: redirectAfter || null
    })
  );

export const disconnectGmail = async (accountId) =>
  unwrap(client.post('/integrations/gmail/disconnect', null, { params: accountId ? { account_id: accountId } : {} }));

/* ==========================================================================
 * Permissions and audit
 * ========================================================================== */

export const listPermissions = async ({ userId = CURRENT_USER, includeRevoked = false } = {}) =>
  unwrap(client.get('/permissions', { params: { user_id: userId, include_revoked: includeRevoked } }));

export const revokePermission = async (grantId, reason) =>
  unwrap(client.delete(`/permissions/${grantId}`, { params: { revoked_by: CURRENT_USER, reason } }));

export const grantPermission = async (payload) =>
  unwrap(client.post('/permissions', { granted_by: CURRENT_USER, user_id: CURRENT_USER, ...payload }));

export const listAuditLog = async ({ event, campaignId, limit = 100 } = {}) =>
  unwrap(
    client.get('/permissions/audit/log', {
      params: { event: event || undefined, campaign_id: campaignId || undefined, limit }
    })
  );

/* ==========================================================================
 * Autonomous Execution Tasks
 * ========================================================================== */

export const createTask = async ({ objective, attachments = [], authorizationScope = {}, dryRun = false }) =>
  unwrap(
    client.post(
      '/tasks',
      {
        objective,
        user_id: CURRENT_USER,
        attachments,
        authorization_scope: authorizationScope,
        dry_run: dryRun
      },
      { timeout: 120000 }
    )
  );

export const listTasks = async ({ status, limit = 50, user_id } = {}) =>
  unwrap(client.get('/tasks', { params: { user_id, status, limit } }));

export const getTask = async (taskId) =>
  unwrap(client.get(`/tasks/${taskId}`));

export const sendTaskMessage = async (taskId, message) =>
  unwrap(client.post(`/tasks/${taskId}/message`, { message }));

export const approveTask = async (taskId) =>
  unwrap(client.post(`/tasks/${taskId}/approve`));

export const resumeTask = async (taskId) =>
  unwrap(client.post(`/tasks/${taskId}/resume`));

export const pauseTask = async (taskId) =>
  unwrap(client.post(`/tasks/${taskId}/pause`));

export const cancelTask = async (taskId) =>
  unwrap(client.post(`/tasks/${taskId}/cancel`));

export const getTaskJobs = async (taskId) =>
  unwrap(client.get(`/tasks/${taskId}/jobs`));

export const getTaskActivity = async (taskId) =>
  unwrap(client.get(`/tasks/${taskId}/activity`));

export const getDiagnostics = async () =>
  unwrap(client.get('/integrations/diagnostics'));

export const getTaskEventsUrl = (taskId) => {
  if (API_BASE.startsWith('http')) {
    return `${API_BASE}/tasks/${taskId}/events`;
  }
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const prefix = API_BASE.startsWith('/') ? API_BASE : `/${API_BASE}`;
  return `${origin}${prefix}/tasks/${taskId}/events`;
};

export const uploadTaskAttachment = async (file) => {
  const form = new FormData();
  form.append('file', file);
  try {
    const response = await client.post('/tasks/attachments', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000
    });
    return response.data;
  } catch (err) {
    throw toApiError(err);
  }
};

export default client;
