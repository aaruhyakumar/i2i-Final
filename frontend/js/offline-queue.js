/**
 * INK2Insights Offline Queue — Phase 4
 * ======================================
 * Encrypted IndexedDB scan queue with WebCrypto AES-GCM.
 *
 * Architecture
 * ─────────────
 *  ┌────────────────────┐    offline     ┌──────────────────────┐
 *  │   api.js           │ ──────────────▶│  OfflineQueue.enqueue│
 *  │ extractPages()     │                │  (encrypt + IDB put) │
 *  └────────────────────┘                └──────────────────────┘
 *         │ online                                │
 *         │                         SW sync event │ or network restore
 *         ▼                                       ▼
 *  ┌────────────────────┐            ┌─────────────────────────┐
 *  │  Flask /api/extract│◀───────────│  OfflineQueue.drain()   │
 *  │  (normal path)     │            │  (decrypt + fetch + del)│
 *  └────────────────────┘            └─────────────────────────┘
 *
 * Security model
 * ───────────────
 * • Each session generates a fresh AES-GCM-256 key stored only in
 *   sessionStorage (cleared on tab close — deliberate ephemeral scope).
 * • Per-entry 96-bit IV is randomly generated and stored alongside
 *   the ciphertext in IDB.
 * • Images are encrypted as ArrayBuffers; metadata (campId, etc.) is
 *   encrypted as JSON.  No plaintext PHI ever touches IDB.
 * • On drain, each entry is decrypted, submitted to /api/extract,
 *   then deleted from IDB regardless of server response (success or
 *   permanent 4xx).  Transient errors (5xx / network) leave the entry
 *   for the next drain cycle.
 *
 * DPDP compliance
 * ────────────────
 * • Queue entries automatically expire after MAX_AGE_MS (default 48 h).
 * • Expired entries are purged without upload on every drain call.
 * • purgeAll() is available for DPDP right-to-erasure requests.
 */

const DB_NAME = 'ink2i_offline_queue';
const DB_VERSION = 2;
const STORE_NAME = 'scan_queue';
const KEY_STORE = 'crypto_keys';

/** Maximum age a queued scan is retained before being purged (48 hours). */
const MAX_AGE_MS = 48 * 60 * 60 * 1000;

/** sessionStorage key for the base64-exported AES-GCM key. */
const SESSION_KEY_NAME = 'ink2i_queue_aes_key';

// ─────────────────────────────────────────────────────────────────────────────
// IndexedDB helpers
// ─────────────────────────────────────────────────────────────────────────────

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: 'id',
          autoIncrement: true,
        });
        store.createIndex('created_at', 'created_at', { unique: false });
        store.createIndex('camp_id', 'camp_id', { unique: false });
      }
      if (!db.objectStoreNames.contains(KEY_STORE)) {
        db.createObjectStore(KEY_STORE, { keyPath: 'name' });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbGetKey(db, name) {
  return new Promise((resolve) => {
    try {
      if (!db || !db.objectStoreNames.contains(KEY_STORE)) return resolve(null);
      const tx = db.transaction(KEY_STORE, 'readonly');
      const req = tx.objectStore(KEY_STORE).get(name);
      req.onsuccess = (e) => resolve(e.target.result ? e.target.result.key : null);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function idbPutKey(db, name, key) {
  return new Promise((resolve) => {
    try {
      if (!db || !db.objectStoreNames.contains(KEY_STORE)) return resolve();
      const tx = db.transaction(KEY_STORE, 'readwrite');
      tx.objectStore(KEY_STORE).put({ name, key });
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    } catch {
      resolve();
    }
  });
}

function idbPut(db, record) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).put(record);
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbGetAll(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbDelete(db, id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).delete(id);
    req.onsuccess = () => resolve();
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbCount(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).count();
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbClear(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).clear();
    req.onsuccess = () => resolve();
    req.onerror = (e) => reject(e.target.error);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// WebCrypto AES-GCM helpers
// ─────────────────────────────────────────────────────────────────────────────

async function getOrCreateKey(db = null) {
  // 1. Check persistent IndexedDB key-store
  if (db) {
    const idbStoredKey = await idbGetKey(db, 'main_queue_key');
    if (idbStoredKey) {
      try {
        const raw = Uint8Array.from(atob(idbStoredKey), (c) => c.charCodeAt(0));
        const imported = await crypto.subtle.importKey(
          'raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']
        );
        try { sessionStorage.setItem(SESSION_KEY_NAME, idbStoredKey); } catch {}
        return imported;
      } catch {
        // Fall through
      }
    }
  }

  // 2. Reuse within the same session tab if available
  const stored = sessionStorage.getItem(SESSION_KEY_NAME);
  if (stored) {
    try {
      const raw = Uint8Array.from(atob(stored), (c) => c.charCodeAt(0));
      const imported = await crypto.subtle.importKey(
        'raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']
      );
      if (db) await idbPutKey(db, 'main_queue_key', stored);
      return imported;
    } catch {
      // Fall through to generate new
    }
  }

  // 3. Generate new AES-GCM-256 key and persist
  const key = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 },
    true,
    ['encrypt', 'decrypt']
  );

  const exported = await crypto.subtle.exportKey('raw', key);
  const b64 = btoa(String.fromCharCode(...new Uint8Array(exported)));
  try { sessionStorage.setItem(SESSION_KEY_NAME, b64); } catch {}
  if (db) await idbPutKey(db, 'main_queue_key', b64);

  return key;
}

async function encrypt(key, plaintextBuffer) {
  const iv = crypto.getRandomValues(new Uint8Array(12)); // 96-bit IV
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    plaintextBuffer
  );
  return { iv: Array.from(iv), ciphertext: Array.from(new Uint8Array(ciphertext)) };
}

async function decrypt(key, iv, ciphertext) {
  const ivBuf = new Uint8Array(iv);
  const ctBuf = new Uint8Array(ciphertext);
  return crypto.subtle.decrypt({ name: 'AES-GCM', iv: ivBuf }, key, ctBuf);
}

async function encryptBlob(key, blob) {
  const buf = await blob.arrayBuffer();
  return encrypt(key, buf);
}

async function decryptToBlob(key, encryptedBlob, mimeType = 'image/jpeg') {
  const plainBuf = await decrypt(key, encryptedBlob.iv, encryptedBlob.ciphertext);
  return new Blob([plainBuf], { type: mimeType });
}

async function encryptJson(key, obj) {
  const str = JSON.stringify(obj);
  const buf = new TextEncoder().encode(str);
  return encrypt(key, buf);
}

async function decryptJson(key, encryptedObj) {
  const plainBuf = await decrypt(key, encryptedObj.iv, encryptedObj.ciphertext);
  const str = new TextDecoder().decode(plainBuf);
  return JSON.parse(str);
}

// ─────────────────────────────────────────────────────────────────────────────
// Public OfflineQueue class
// ─────────────────────────────────────────────────────────────────────────────

function getFreshAuthToken() {
  try {
    if (typeof localStorage !== 'undefined') {
      const token = localStorage.getItem('auth_token') || localStorage.getItem('supabase_access_token');
      return token ? token.trim() : '';
    }
  } catch {}
  return '';
}

export class OfflineQueue {
  constructor() {
    this._db = null;
    this._key = null;
    this._draining = false;
    this._onProgress = null; // callback(event) for UI updates
  }

  /**
   * Initialize DB and crypto key. Call once at app startup.
   * Returns { supported: bool, queueLength: number }
   */
  async init(onProgress = null) {
    this._onProgress = onProgress;
    if (!window.indexedDB || !window.crypto?.subtle) {
      console.warn('[OfflineQueue] IndexedDB or WebCrypto not available.');
      return { supported: false, queueLength: 0 };
    }
    try {
      this._db = await openDB();
      this._key = await getOrCreateKey(this._db);
      const queueLength = await idbCount(this._db);
      this._emit('init', { queueLength });
      return { supported: true, queueLength };
    } catch (err) {
      console.error('[OfflineQueue] init failed:', err);
      return { supported: false, queueLength: 0 };
    }
  }

  /**
   * Add a scan to the encrypted offline queue.
   * @param {Blob} page1Blob
   * @param {Blob} page2Blob
   * @param {object} meta  – { campId, apiHeaders }
   * @returns {number} IDB record id
   */
  async enqueue(page1Blob, page2Blob, meta = {}) {
    if (!this._ready()) throw new Error('OfflineQueue not initialized.');

    const key = this._key;
    const [encPage1, encPage2, encMeta] = await Promise.all([
      encryptBlob(key, page1Blob),
      encryptBlob(key, page2Blob),
      encryptJson(key, {
        campId: meta.campId || null,
        apiHeaders: meta.apiHeaders || {},
        page1Mime: page1Blob.type || 'image/jpeg',
        page2Mime: page2Blob.type || 'image/jpeg',
      }),
    ]);

    const record = {
      created_at: Date.now(),
      camp_id: meta.campId || null,  // unencrypted for index only — no PHI
      enc_page1: encPage1,
      enc_page2: encPage2,
      enc_meta: encMeta,
      attempts: 0,
      last_error: null,
      status: 'pending',
      requires_attention: false,
    };

    const id = await idbPut(this._db, record);
    const total = await idbCount(this._db);
    this._emit('enqueue', { id, queueLength: total });

    // Request background sync if available
    this._requestBackgroundSync();

    return id;
  }

  /**
   * Drain the queue: decrypt each entry, POST to /api/extract, delete ONLY on confirmed success.
   * Transient errors (network, 5xx) leave the entry for the next cycle.
   * Auth (401), Permission (403), Validation (422), and Client (4xx) errors RETAIN the item.
   *
   * @param {Function} extractFn  – async (page1Blob, page2Blob, apiHeaders) => response
   * @param {object} options      – { retryBlocked: bool, targetId: number }
   */
  async drain(extractFn, options = {}) {
    if (!this._ready() || this._draining) return { synced: 0, failed: 0, expired: 0, blocked: 0, paused: false };
    this._draining = true;

    const stats = { synced: 0, failed: 0, expired: 0, blocked: 0, paused: false };
    const retryBlocked = Boolean(options.retryBlocked);
    const targetId = options.targetId != null ? options.targetId : null;

    try {
      const entries = await idbGetAll(this._db);
      const now = Date.now();

      for (const entry of entries) {
        if (targetId != null && entry.id !== targetId) {
          continue;
        }

        // 1. Purge expired entries (DPDP / 48-hour TTL) ONLY if not requiring attention/failed.
        // Failed / blocked items may be the only copy of a clinical scan and must NEVER be auto-deleted.
        if (now - entry.created_at > MAX_AGE_MS) {
          if (!entry.requires_attention && entry.status !== 'auth_required' && entry.status !== 'validation_error') {
            await idbDelete(this._db, entry.id);
            stats.expired++;
            this._emit('expired', { id: entry.id });
            continue;
          } else {
            entry.is_expired = true;
          }
        }

        // 2. Skip items requiring manual attention on automatic background drains (prevents tight retry loops)
        if (entry.requires_attention && !retryBlocked) {
          stats.blocked++;
          continue;
        }

        // 3. Decrypt
        let page1Blob, page2Blob, meta;
        try {
          const key = this._key;
          [page1Blob, page2Blob, meta] = await Promise.all([
            decryptToBlob(key, entry.enc_page1, (meta?.page1Mime) || 'image/jpeg'),
            decryptToBlob(key, entry.enc_page2, (meta?.page2Mime) || 'image/jpeg'),
            decryptJson(key, entry.enc_meta),
          ]);
        } catch (decErr) {
          console.warn('[OfflineQueue] Decryption failed for entry', entry.id, decErr);
          entry.decrypt_attempts = (entry.decrypt_attempts || 0) + 1;
          // Never silently delete corrupted items; preserve and flag for attention
          entry.status = 'decryption_error';
          entry.requires_attention = true;
          entry.last_error = decErr.message || 'Decryption failed';
          await idbPut(this._db, entry);
          stats.failed++;
          this._emit('decrypt_error', { id: entry.id, error: decErr.message });
          continue;
        }

        // 4. Resolve fresh authentication token
        const freshToken = getFreshAuthToken();
        const headersToSend = { ...(meta?.apiHeaders || {}) };
        if (freshToken) {
          headersToSend['Authorization'] = `Bearer ${freshToken}`;
        } else {
          // If no token exists at all (user logged out / unauthenticated), pause queue
          delete headersToSend['Authorization'];
          entry.status = 'auth_required';
          entry.last_status_code = 401;
          entry.last_error = 'Sign-in required to sync scans';
          await idbPut(this._db, entry);
          stats.paused = true;
          this._emit('auth_required', {
            id: entry.id,
            status: 401,
            message: 'Authentication required. Please sign in to sync offline scans.'
          });
          break; // Pause queue processing immediately
        }

        // 5. Submit to backend
        this._emit('syncing', { id: entry.id, attempt: (entry.attempts || 0) + 1 });
        try {
          const result = await extractFn(page1Blob, page2Blob, headersToSend);
          // Confirm success before deletion: only delete when extractFn resolves without throwing
          await idbDelete(this._db, entry.id);
          stats.synced++;
          this._emit('synced', { id: entry.id, queueLength: await idbCount(this._db), result });
        } catch (fetchErr) {
          const status = fetchErr.status || 0;

          if (status === 401) {
            // A. HTTP 401: Authentication expired
            // NEVER delete the scan. Stop/pause queue processing. Inform user.
            await idbPut(this._db, {
              ...entry,
              status: 'auth_required',
              last_status_code: 401,
              last_error: fetchErr.message || 'Authentication token expired. Please sign in again.',
              updated_at: Date.now()
            });
            stats.paused = true;
            this._emit('auth_required', {
              id: entry.id,
              status: 401,
              message: 'Authentication expired. Please sign in again to sync scans.'
            });
            break; // Pause queue processing immediately
          }

          if (status === 403) {
            // B. HTTP 403: Authorization failure
            // Retain scan. Do not repeatedly retry in a tight loop. Surface permission error.
            await idbPut(this._db, {
              ...entry,
              status: 'permission_denied',
              requires_attention: true,
              last_status_code: 403,
              last_error: fetchErr.message || 'Access denied / unauthorized community.',
              updated_at: Date.now()
            });
            stats.failed++;
            this._emit('permission_error', {
              id: entry.id,
              status: 403,
              error: fetchErr.message || 'Access denied'
            });
            continue;
          }

          if (status === 422) {
            // C. HTTP 422: Validation or image-quality failure
            // Retain original images. Mark as requiring attention. Do not auto-retry in tight loop.
            await idbPut(this._db, {
              ...entry,
              status: 'validation_error',
              requires_attention: true,
              last_status_code: 422,
              last_error: fetchErr.message || 'Validation or image quality check failed.',
              updated_at: Date.now()
            });
            stats.failed++;
            this._emit('validation_error', {
              id: entry.id,
              status: 422,
              error: fetchErr.message || 'Validation or image quality check failed'
            });
            continue;
          }

          if (status >= 400 && status < 500) {
            // D. Other HTTP 4xx (400, 413, etc.)
            // NEVER automatically delete queued clinical images. Retain item, report error.
            await idbPut(this._db, {
              ...entry,
              status: 'client_error',
              requires_attention: true,
              last_status_code: status,
              last_error: fetchErr.message || `Client request error (${status})`,
              updated_at: Date.now()
            });
            stats.failed++;
            this._emit('client_error', {
              id: entry.id,
              status,
              error: fetchErr.message || `Client request error (${status})`
            });
            continue;
          }

          // E. HTTP 5xx or network failures (status >= 500 or status === 0)
          // Retain queued scan. Preserve retry/backoff behaviour.
          await idbPut(this._db, {
            ...entry,
            status: 'transient_error',
            attempts: (entry.attempts || 0) + 1,
            last_status_code: status || 0,
            last_error: fetchErr.message || String(fetchErr),
            updated_at: Date.now()
          });
          stats.failed++;
          this._emit('retry_later', {
            id: entry.id,
            attempts: (entry.attempts || 0) + 1,
            status: status || 0,
            error: fetchErr.message || String(fetchErr)
          });
        }
      }
    } finally {
      this._draining = false;
    }

    const remaining = await idbCount(this._db);
    this._emit('drain_complete', { ...stats, queueLength: remaining });
    return stats;
  }

  /** Return the current number of queued (pending) scans. */
  async length() {
    if (!this._ready()) return 0;
    return idbCount(this._db);
  }

  /**
   * Retrieve all queue entries from IndexedDB for inspection/UI/tests.
   * @returns {Promise<Array>}
   */
  async getEntries() {
    if (!this._ready()) return [];
    return idbGetAll(this._db);
  }

  /**
   * Retrieve a single queue entry by ID.
   * @param {number} id
   * @returns {Promise<object|null>}
   */
  async getEntry(id) {
    if (!this._ready()) return null;
    const entries = await idbGetAll(this._db);
    return entries.find((e) => e.id === id) || null;
  }

  /**
   * Explicitly retry a specific queued item (clearing requires_attention to allow immediate retry).
   * @param {number} id
   * @param {Function} extractFn
   * @returns {Promise<object>}
   */
  async retryEntry(id, extractFn) {
    if (!this._ready()) return null;
    const entry = await this.getEntry(id);
    if (!entry) return null;
    entry.requires_attention = false;
    await idbPut(this._db, entry);
    return this.drain(extractFn, { retryBlocked: true, targetId: id });
  }

  /**
   * DPDP right-to-erasure: wipe all queued encrypted entries immediately.
   * Called from the DPDP erasure flow in app.js.
   */
  async purgeAll() {
    if (!this._ready()) return;
    await idbClear(this._db);
    this._emit('purge_all', { queueLength: 0 });
  }

  // ── Internals ──────────────────────────────────────────────────────────────

  _ready() {
    return Boolean(this._db && this._key);
  }

  _emit(type, detail = {}) {
    if (this._onProgress) {
      try { this._onProgress({ type, ...detail }); } catch { /* ignore */ }
    }
    // Also dispatch a custom DOM event for UI components to listen to
    try {
      window.dispatchEvent(new CustomEvent('ink2i:queue', { detail: { type, ...detail } }));
    } catch { /* ignore in non-window contexts */ }
  }

  _requestBackgroundSync() {
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      navigator.serviceWorker.ready
        .then((reg) => reg.sync.register('ink2i-offline-sync'))
        .catch(() => {}); // Not fatal
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Singleton export + SW message listener
// ─────────────────────────────────────────────────────────────────────────────

/** Singleton accessible from app.js and api.js */
export const offlineQueue = new OfflineQueue();

/**
 * Register the ServiceWorker and wire the SW→window drain message.
 * Call once at app startup (from app.js).
 *
 * @param {Function} extractFn  – api.extractPages function reference
 */
export async function registerOfflineSupport(extractFn) {
  // Register SW
  if ('serviceWorker' in navigator) {
    try {
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      console.log('[OfflineQueue] ServiceWorker registered:', reg.scope);
      // Immediately check for SW script updates on the server
      reg.update().catch(() => {});
    } catch (err) {
      console.warn('[OfflineQueue] ServiceWorker registration failed:', err.message);
    }

    // Listen for SW-triggered drain messages
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data && event.data.type === 'INK2I_DRAIN_QUEUE') {
        offlineQueue.drain(extractFn);
      }
    });
  }

  // Also drain immediately on network restoration
  window.addEventListener('online', () => {
    console.log('[OfflineQueue] Network restored — draining queue.');
    offlineQueue.drain(extractFn);
  });

  // Drain on visibility change (tab regains focus while online)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && navigator.onLine) {
      offlineQueue.drain(extractFn);
    }
  });
}
