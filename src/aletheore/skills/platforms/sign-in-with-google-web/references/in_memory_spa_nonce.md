# Pattern C: Browser-Based Ephemeral In-Memory SPA with Native GIS Nonce

This reference provides the fallback implementation recipe for **Pattern C
(Browser-Based OAuth Client — IETF `draft-ietf-oauth-browser-based-apps` §
6.3)**, intended strictly for serverless static Single Page Applications (SPAs)
that have no backend server.

> [!WARNING] **Client-Side Verification Limitation:** Decoding a JWT payload in
> the browser via `atob` / `TextDecoder` only inspects claims for UI display and
> client-side `nonce` correlation. It does **not** replace backend cryptographic
> signature verification against Google's public JWKs. Whenever a backend
> exists, always use Pattern A (BFF) or Pattern B (TMB).

--------------------------------------------------------------------------------

## Complete React + TypeScript Implementation

```tsx
import React, { useEffect, useRef } from 'react';
import { useGoogleIdentityScript } from './useGoogleIdentityScript';

// In-Memory Token Manager with Safe UTF-8 Base64URL JWT Decoding
export const AuthManager = (() => {
  let _idToken: string | null = null;
  let _user: Record<string, any> | null = null;

  return {
    setCredential: (jwt: string, expectedNonce: string) => {
      const base64Url = jwt.split('.')[1];
      const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
      const binaryStr = atob(base64);
      const bytes = Uint8Array.from(binaryStr, (c) => c.charCodeAt(0));
      const payload = JSON.parse(new TextDecoder().decode(bytes));

      if (payload.nonce !== expectedNonce) {
        throw new Error('Security violation: ID token nonce mismatch!');
      }
      _idToken = jwt;
      _user = payload;
    },
    getIdToken: () => _idToken,
    getUser: () => (_user ? { ..._user } : null),
    clear: () => {
      _idToken = null;
      _user = null;
    },
  };
})();

export const GoogleSignInButton: React.FC<{
  clientId: string;
  onSuccess: (user: Record<string, any>) => void;
}> = ({ clientId, onSuccess }) => {
  const buttonRef = useRef<HTMLDivElement>(null);
  const isGisLoaded = useGoogleIdentityScript();

  useEffect(() => {
    if (!isGisLoaded || !window.google?.accounts?.id) return;

    // 1. Generate WebCrypto random nonce
    const nonceBytes = window.crypto.getRandomValues(new Uint8Array(16));
    const clientNonce = Array.from(nonceBytes, (b) =>
      b.toString(16).padStart(2, '0')
    ).join('');

    window.google.accounts.id.initialize({
      client_id: clientId,
      nonce: clientNonce, // Direct native nonce binding
      auto_select: true,
      callback: (response: { credential: string }) => {
        if (response.credential) {
          AuthManager.setCredential(response.credential, clientNonce);
          onSuccess(AuthManager.getUser()!);
        }
      },
    });

    if (buttonRef.current) {
      window.google.accounts.id.renderButton(buttonRef.current, {
        type: 'standard',
        theme: 'outline',
        size: 'large',
        text: 'signin_with',
        shape: 'rectangular',
      });
    }
  }, [isGisLoaded, clientId, onSuccess]);

  return <div ref={buttonRef} />;
};
```
