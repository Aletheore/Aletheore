-- One-time codes required, alongside the existing typed account-login
-- confirmation, to actually run a full-installation delete.
--
-- The typed confirmation defends against an accidental click - the account
-- login is right there on the page, so typing it back proves intent, not
-- identity. It does nothing against a stolen session cookie: an attacker
-- holding one can read the org name off the same page and type it back.
-- This table closes that gap by requiring proof the caller still controls
-- the account's own email right now, independent of the session itself.
--
-- Cascades with the installation - if the installation is gone there's
-- nothing left to guard a delete against, and an outstanding code for a
-- dead installation isn't of interest to anyone.
CREATE TABLE IF NOT EXISTS deletion_otp_codes (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    code_hash        TEXT NOT NULL,
    requested_by     TEXT NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    used_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Supports the verification lookup: unused, unexpired codes for this
-- installation, newest first.
CREATE INDEX IF NOT EXISTS deletion_otp_codes_installation_idx
    ON deletion_otp_codes (installation_id, created_at DESC);
