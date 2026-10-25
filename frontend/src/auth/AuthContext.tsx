/**
 * Authentication context.
 *
 * Holds the one piece of state the whole application branches on: who is signed
 * in. It never *decides* whether a request is allowed — the backend does that on
 * every call — it only decides what to render and where to send the user. A
 * reload re-reads the user from `GET /api/auth/session`, so the session survives
 * a refresh but is still revoked the moment the server says it is.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { api } from '../api/client';
import {
  clearToken,
  getDeviceId,
  getToken,
  setToken,
  setUnauthorizedHandler,
} from '../api/session';
import type { SessionUser, UserRole } from '../api/types';

export type AuthStatus = 'checking' | 'authenticated' | 'anonymous';

interface AuthState {
  status: AuthStatus;
  user: SessionUser | null;
  expiresAt: string | null;
  signIn: (email: string, password: string) => Promise<SessionUser>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() => (getToken() ? 'checking' : 'anonymous'));
  const [user, setUser] = useState<SessionUser | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);

  const forget = useCallback(() => {
    clearToken();
    setUser(null);
    setExpiresAt(null);
    setStatus('anonymous');
  }, []);

  // An expired or revoked token arrives as a 401 on any request. Dropping the
  // client state here means the app returns to the sign-in page once, centrally.
  useEffect(() => {
    setUnauthorizedHandler(forget);
    return () => setUnauthorizedHandler(null);
  }, [forget]);

  // Restore a session on first mount when a token is already present.
  useEffect(() => {
    if (!getToken()) {
      setStatus('anonymous');
      return;
    }
    let cancelled = false;
    api
      .session()
      .then((session) => {
        if (cancelled) return;
        setUser(session.user);
        setExpiresAt(session.expires_at);
        setStatus('authenticated');
      })
      .catch(() => {
        if (cancelled) return;
        forget();
      });
    return () => {
      cancelled = true;
    };
  }, [forget]);

  const signIn = useCallback(async (email: string, password: string) => {
    const session = await api.login({
      email,
      password,
      // The device id is part of the sign-in, so a first sign-in from this
      // browser is recorded as a new device rather than an anonymous one.
      device_id: getDeviceId(),
    });
    setToken(session.token);
    setUser(session.user);
    setExpiresAt(session.expires_at);
    setStatus('authenticated');
    return session.user;
  }, []);

  const signOut = useCallback(async () => {
    // Best effort: revoking server-side is the point, but a network failure must
    // still leave the browser signed out locally.
    try {
      await api.logout();
    } catch {
      /* the local session is cleared regardless */
    }
    forget();
  }, [forget]);

  const value = useMemo<AuthState>(
    () => ({ status, user, expiresAt, signIn, signOut }),
    [status, user, expiresAt, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside an <AuthProvider>');
  }
  return context;
}

/** True when the signed-in user holds `role`. */
export function useHasRole(role: UserRole): boolean {
  const { user } = useAuth();
  return user?.role === role;
}
