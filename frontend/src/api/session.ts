/**
 * Client-side session storage.
 *
 * The token is opaque and is the only thing the browser holds: everything about
 * *who* the user is comes from `GET /api/auth/session`, which re-validates the
 * token on the server on every call. Storing it in `localStorage` keeps the user
 * signed in across a reload; the server decides when that session ends.
 *
 * This module is deliberately dependency-free so both the API client and the
 * auth context can import it without a cycle.
 */

const TOKEN_KEY = 'rupeeshield.session.token';
const DEVICE_KEY = 'rupeeshield.device.id';

function safeStorage(): Storage | null {
  try {
    // Access can throw in private-mode or sandboxed iframes. Falling back to an
    // in-memory session is better than crashing the application.
    const probe = '__rupeeshield_probe__';
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    return null;
  }
}

let memoryToken: string | null = null;

export function getToken(): string | null {
  const storage = safeStorage();
  if (!storage) return memoryToken;
  return storage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  memoryToken = token;
  safeStorage()?.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  memoryToken = null;
  safeStorage()?.removeItem(TOKEN_KEY);
}

/**
 * A stable per-browser device id.
 *
 * The backend records it as the "new device" signal and it is the same value a
 * real client would send, so a sign-in from a browser that has never been seen is
 * genuinely a first sighting rather than a random string each request.
 */
export function getDeviceId(): string {
  const storage = safeStorage();
  const existing = storage?.getItem(DEVICE_KEY);
  if (existing) return existing;
  const suffix = Math.random().toString(36).slice(2, 8).toUpperCase();
  const created = `D-WEB-${suffix}`;
  storage?.setItem(DEVICE_KEY, created);
  return created;
}

type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler | null = null;

/** Registered by the auth context so an expired session drops the user out once. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

export function notifyUnauthorized(): void {
  onUnauthorized?.();
}
