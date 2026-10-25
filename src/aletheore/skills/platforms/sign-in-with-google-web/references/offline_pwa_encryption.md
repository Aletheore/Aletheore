# Offline PWA WebCrypto Encrypted IndexedDB Storage Architecture

This guide details Pattern D for offline-first Progressive Web Apps (PWAs) where
user authentication proof must survive page reloads when disconnected from the
network without exposing raw ID tokens to plaintext XSS exfiltration.

## 1. Non-Extractable WebCrypto Key Persistence

`window.crypto.subtle.generateKey` produces a `CryptoKey`. To survive browser
restarts without a backend, the `CryptoKey` must be stored in `IndexedDB`.

> [!IMPORTANT] `IndexedDB` supports native storage of `CryptoKey` objects via
> the structured clone algorithm while strictly preserving `extractable: false`.
> An attacker with XSS can ask the browser to decrypt existing data during an
> active session, but **cannot** exfiltrate or export the raw cryptographic key
> material.

## 2. Complete Implementation

```typescript
export class EncryptedIdTokenStore {
  private static DB_NAME = 'auth_offline_vault';
  private static STORE_NAME = 'keys_and_tokens';

  private static async getDb(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(this.DB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore(this.STORE_NAME);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  // Generate or retrieve existing non-extractable AES-GCM session key
  static async getOrCreateKey(): Promise<CryptoKey> {
    const db = await this.getDb();
    const tx = db.transaction(this.STORE_NAME, 'readonly');
    const store = tx.objectStore(this.STORE_NAME);

    const existingKey = await new Promise<CryptoKey | undefined>((resolve, reject) => {
      const getReq = store.get('session_aes_key');
      getReq.onsuccess = () => resolve(getReq.result);
      getReq.onerror = () => reject(getReq.error);
    });

    if (existingKey) {
      return existingKey;
    }

    // Generate non-extractable key
    const newKey = await window.crypto.subtle.generateKey(
      { name: 'AES-GCM', length: 256 },
      false, // non-extractable: cannot be exported via exportKey()
      ['encrypt', 'decrypt']
    );

    const writeTx = db.transaction(this.STORE_NAME, 'readwrite');
    writeTx.objectStore(this.STORE_NAME).put(newKey, 'session_aes_key');
    await new Promise((resolve, reject) => {
      writeTx.oncomplete = resolve;
      writeTx.onerror = () => reject(writeTx.error);
    });

    return newKey;
  }

  static async encryptAndStore(idToken: string): Promise<void> {
    const key = await this.getOrCreateKey();
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    const encoded = new TextEncoder().encode(idToken);

    const ciphertext = await window.crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      key,
      encoded
    );

    const db = await this.getDb();
    const tx = db.transaction(this.STORE_NAME, 'readwrite');
    tx.objectStore(this.STORE_NAME).put({ iv, ciphertext }, 'encrypted_token');
    await new Promise((resolve, reject) => {
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  }

  static async getDecryptedToken(): Promise<string | null> {
    const db = await this.getDb();
    const tx = db.transaction(this.STORE_NAME, 'readonly');
    const store = tx.objectStore(this.STORE_NAME);

    const record = await new Promise<{ iv: Uint8Array; ciphertext: ArrayBuffer } | undefined>(
      (resolve, reject) => {
        const getReq = store.get('encrypted_token');
        getReq.onsuccess = () => resolve(getReq.result);
        getReq.onerror = () => reject(getReq.error);
      }
    );

    if (!record) return null;

    const key = await this.getOrCreateKey();
    const decrypted = await window.crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: record.iv },
      key,
      record.ciphertext
    );

    return new TextDecoder().decode(decrypted);
  }
}
```
