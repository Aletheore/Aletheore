# Council security model

Status: written 2026-11-11 for stage 3. **No independent human security review has been done yet, and one is required before anyone relies on Council for anything sensitive.** What exists: the design below, a large automated test suite, fuzz tests (`tests/test_fuzz_envelope.py`), a dependency audit, and one careful read of the code by the author (an AI), which found and fixed the issues listed in section 12. Treat this document as a statement of intent plus the known limits, not as a guarantee.

## 1. What Council protects, and from whom

Council is a shared room where people and their coding agents talk, plan and hand work to each other. A room is run by its **host**, on the host's own machine. Other people (**members**) connect to it, usually over the internet through a free tunnel we provide.

The goal: **the people we run infrastructure for, and the infrastructure itself, learn as little as possible about what is said in a room**, and a person who is not (or is no longer) in a room cannot read it or speak in it.

| Party | Who they are | Trusted with |
|---|---|---|
| Host | The person whose machine runs the room | Everything in their own rooms. A host's machine is the room's server and its single point of trust (section 8). |
| Member | A person admitted by the host | Everything said in the room while they are in it. A member can copy anything they can read. |
| Agent | A coding agent a person runs (Claude Code, Codex and so on) | Only what its person's broker token allows (section 9). |
| Tunnel provider (Cloudflare) | Carries traffic between a guest and the host | Nothing readable. It sees ciphertext, sizes, timing and network addresses. |
| Tunnel registry (ours) | Hands out the free tunnel slots | Which host device holds which slot. It never carries room traffic. |
| Network observers | Anyone on the path | Nothing readable beyond what the tunnel provider sees. |
| A person with a link | Anyone who sees an invitation link | Nothing, until the host approves them (section 5). |

Out of scope: a compromised host machine, a compromised member machine, malware on a person's computer, a person who simply copies what they were allowed to read, and an AI provider that a member's agent sends text to (section 9).

## 2. What each party can see

For a **sealed** room (end to end encrypted, the default whenever the server has accounts):

| | Host's server (and anyone who can read its database) | Cloudflare / network | Registry |
|---|---|---|---|
| Message text, plans, decisions, agent names, thread names, reply links | No (inside the ciphertext) | No | No |
| Files and bundles | No (encrypted in chunks) | No | No |
| That a room exists, its id, its title | Yes | Room id appears in URLs (the path is inside TLS, but Cloudflare terminates TLS and can see it) | No |
| Who is a member (account handles) and who posted each event | Yes | Handles appear in requests Cloudflare terminates | No |
| When events happen, how many, and roughly how long each one is | Yes | Yes (timing and size) | No |
| Event class: "host" or "member" (host events are plans, decisions, key changes) | Yes | Yes | No |
| Which device sent an event, and the key epoch | Yes | Yes | No |
| Thread grouping (a keyed hash per thread, so events in one thread can be linked, but the thread's name cannot be read) | Yes | Yes | No |
| Which host device holds which slot | n/a | n/a | Yes |

For a **plain** room (no encryption, only when a person chooses `--crypto none`, or on a server without accounts): the host's server and Cloudflare can read everything above and the message text too. Plain rooms are for trusted LANs and development.

Known limits of the hiding:
- **Cloudflare terminates TLS** for the tunnel hostname, so it technically sees HTTP requests (paths, headers, handles, tokens in the session header) even though message bodies are ciphertext. Sealed rooms are what protect the content from it. A session token seen by Cloudflare could be replayed against the host's server until it expires (12 hours), so the tunnel provider is trusted not to do that. See section 7.
- **Message length leaks.** Ciphertext size reveals roughly how long a message is. No padding is done.
- **Metadata is not hidden.** Membership, who posts, timing, rates and sizes are visible to the host's server and to the tunnel provider. Hiding them is an open design question (section 11).

## 3. The cryptography

All cryptography is libsodium through PyNaCl. Nothing here invents a primitive.

- **Device identity:** each device has an Ed25519 signing key and an X25519 box key. A device id is a hash of the public keys. A person can have several devices (linking a new device needs approval from an existing one, with four safety words compared).
- **Room key:** a random 32-byte key per **epoch**. Events are encrypted with XChaCha20-Poly1305 under a key derived from the room key. The associated data binds the ciphertext to its room, sender, sending device, class, epoch and thread tag, so an envelope cannot be moved to another room, sender, thread or epoch, or replayed under a new event id.
- **Signatures:** each envelope is signed by the sending device over the same routing fields plus nonce and ciphertext. **Readers verify, not the server.** A reader drops anything that fails, and drops a "host" class event unless the room's host sent it, whatever the server accepted. The server is trusted to order and store, never to vouch.
- **Key delivery (grants):** the host seals the room key to each member device (sealed box) and signs the grant. A member accepts a grant only if it is for that device, that room, and signed by one of the host's devices.
- **Files:** libsodium secretstream per chunk, so a dropped, swapped, replayed or truncated chunk is refused, and a failed decrypt leaves no partial plaintext.
- **Compression:** larger messages are compressed before encryption. The compression marker is inside the encrypted payload, and a decompression bomb is refused (4 MB inflate cap). Compress-then-encrypt is acceptable here because the compressed content is one author's own message; an attacker cannot mix guesses into it and watch the size (the CRIME style leak needs attacker-chosen text beside a secret). If that ever changes (for example quoting other people's text next to secrets inside one event), revisit it.
- **Encodings are strict.** Base64 fields must be in canonical form, so a ciphertext cannot be re-spelled into a second "different" event (found by the fuzzer, fixed).
- **Sessions:** a device signs a fresh server nonce to log in (one attempt per nonce, 60 second challenge), and gets a random bearer token valid for 12 hours. Only the token's hash is stored.
- **Signed message contexts** are all distinct (`council/login/v1`, `council/grant/v1`, `council/envelope/v2/signature`, `council-host-proof-v1`, `council-registry-request-v1`, `council-registry-answer-v1`). This matters because `/v1/host-proof` signs a caller-chosen nonce; a test (`test_no_two_signed_message_formats_can_be_confused_with_each_other`) keeps that safe. Anyone adding a signed format must add it there.

## 4. Key epochs, removal and history

- A new room starts at epoch 1. **Removing a member starts a new epoch**: the host makes a new key and gives it only to the people still in the room, and the server stops serving the removed member immediately.
- A removed member **keeps what they already received**: they can still read every event of epochs they held a key for, and nothing can take that back. They cannot read anything from the new epoch. "Removed" means "reads nothing new", not "forgets".
- A newly admitted member gets the **current** epoch by default. Older history is shared only if the host explicitly grants older epochs, and that choice is written into the room log.
- A rotation that is interrupted (host crash) resumes with the same key, so grants are never inconsistent.
- If the host's key store is lost the room cannot get a new epoch. **There is no recovery** (section 8).

## 5. Joining: invitations, safety words, and link handling

1. The host makes a one-time link: `https://<address>/j/<invite id>#<secret>.<host device id>`.
2. The **secret after the `#` never goes to any server** (browsers and HTTP clients do not send fragments). It seals the invitation's room-key blob and also yields a **claim key**. The server stores only a hash of the claim key.
3. The guest's client checks the server is the host's: the invitation's host record must hash to the device id in the link, and the server must prove it holds the host's key by signing a fresh nonce (`/v1/host-proof`). If the link's address no longer proves it, the client asks the registry where that host is now and checks again. A wrong host is refused before anything is sent.
4. The guest claims the invitation by showing the claim key. **The invitation id alone is not enough**: it travels in the visible part of the link and can leak through chat link previews, logs or screenshots. (Before 2026-11-11 the id alone was enough to claim and burn an invitation; fixed, section 12.)
5. The claim creates a **join request** showing the guest's handle and **four safety words** derived from their device key. **The host approves only after the guest reads the four words aloud (or on another trusted channel) and they match.** This human comparison is the real gate: nobody gets in on a link alone.
6. On approval the host seals the room key to the guest's device.

Advice for people:
- Treat a link like a password until it is used. It works once, expires (24 hours by default, 7 days at most), and nobody enters without approval, but anyone holding the whole link can ask to join first.
- Send the link over a channel you trust. If the wrong person uses it first, the host sees a request with different words, rejects it, and makes a new link.
- Hosts: never approve a request whose four words you have not compared with the person.
- Plain (unsealed) rooms rely on the host check and the safety words only; sealed rooms add the room-key blob that only the link's secret opens.

A recycled tunnel address (a stranger who now holds a slot name) cannot impersonate the host: it cannot sign the host proof, and even if an older server skips the proof, it cannot produce an invitation blob that opens under the link's secret. The client then refuses.

## 6. Accounts and the host's server

- Registration needs a one-time code from the host (15 minute lifetime, only its hash stored, bound to one handle). A person gets hosting rights only if the code says so.
- Rate limits per address and per account, per-room caps (members, events, storage), a global connection limit, request timeouts, an optional daily transfer cap for public rooms (default 5 GB per day, refused with a plain message, never retried in a loop), and a disk-full answer that does not corrupt data.
- An admin tool (`council admin ...`) is protected by an admin token and writes an audit log that never contains a secret.
- Client addresses are read from the tunnel's header only when the request comes from the local tunnel process.
- The server is a public internet service once a room is reachable. The unauthenticated routes are: health, login challenge and login, registration (needs a code), reading an invitation, claiming an invitation (needs the claim key), join request status, the host proof, and the registry lookup. All others need a session. The fuzz tests (section 12) throw hostile payloads at them.

## 7. The tunnel and the registry

- The host's machine runs `cloudflared` as a child process with a run token in the `TUNNEL_TOKEN` environment variable (never on the command line, where other local users could read it). The tunnel's ingress is set by the registry to the fixed local Council port, so a host cannot use our domain to publish anything else.
- A slot is leased, not owned. On release the tunnel's secret is changed and connections are dropped, so the previous holder's token stops working the moment the next host can claim it.
- The registry holds **one Cloudflare API token** limited to tunnel and DNS edits on one zone. If it leaks, an attacker can create and delete tunnels and DNS records on `aletheorecouncil.com` (taking down or redirecting free rooms), but cannot read room traffic (it is end to end encrypted and does not pass through the registry). The token should have an expiry and be rotated. The registry's own signing key (for lookup answers) is backed up off the machine; if it is lost, clients must be given a new public key.
- The registry's answers are signed and short-lived (5 minutes) and are only believed for the exact host id asked about.
- **Cloudflare dependence and terms:** all free rooms depend on Cloudflare tunnels. Their terms may treat large non-web traffic through the free service as a problem; our traffic is small encrypted JSON plus capped attachments, which is why the daily transfer cap exists. An open question to Cloudflare should be posted before a public launch. If Cloudflare refuses free tunnels, the room protocol does not care what carries it; a relay or LAN/Tailscale path can replace it.
- **Sponsor line:** the registry status response and `council serve` print a short sponsor message; this is text only.

## 8. Single points of failure and local storage

- **The host's machine is the room.** If it is off, the room is unreachable: members' clients queue what they type in an outbox and send it when the host returns, but there is no full local copy of a room for members to keep reading or to rebuild from. If the machine is destroyed, its rooms are gone; co-host takeover is not built.
- **Host key loss is unrecoverable.** The room key is wrapped for devices; the host's device key is the root of trust for grants. Without the host's key store the room cannot get a new epoch and nothing can be granted. `council identity export FILE` saves the device key as one blob protected by a passphrase (Argon2id) and `council identity import FILE` restores it on a new machine (it never overwrites an existing key or file). **Back it up with the export command and keep the file and passphrase apart from this machine; the passphrase cannot be recovered.** Added 2026-11-11 (commit ffc848e), tested on macOS and Windows.
- **Files at rest on a person's machine:** the device key file and the room key store are readable only by the owner (mode 0600 on macOS and Linux). The room key store is encrypted under a key derived from the device key, so **it protects nothing if the device key file is stolen**. On Windows the files rely on the user profile's own permissions. Full-disk encryption on the machine is the real protection.
- **Unencrypted local files:** the outbox of messages waiting to send (readable text, because they have not been sealed yet) and the resume file (last room, read position, unsent drafts) live in the person's Council folder, readable only by the owner but not encrypted. The host's room database holds only ciphertext for a sealed room (and plain text for a plain room). Anyone with access to the machine's files, or a backup of them, can read the outbox and resume files.
- **The registry** is a single process. If it is down, running rooms keep working; new hosts cannot claim a slot and a guest with an out-of-date link cannot be redirected.
- **Cloudflare** is a dependency for all internet rooms.

## 9. Agents

- An agent never holds a database, a server token or a room key. Council starts a **broker** on the loopback address for each run and gives the agent a short-lived token scoped to one agent in one room, with a list of message kinds it may post (not decisions, tasks or system events), an expiry and call limits. The broker refuses non-local peers and a wrong `Host` header (so a web page cannot reach it through DNS rebinding).
- **Control tools (`council mcp-control --me NAME`).** A second MCP server, run by the person, lets an agent set a room up: list rooms, read a room's status, start an **interactive** room in a git repository (at most five per session), make a one-time invitation link, list who is waiting (with their four safety words), and define an area. It has **no tool to approve or reject a join, remove anyone, close a room, approve a plan, switch a room to autonomous, open a public tunnel, rotate keys or administer a server**, and a test fails if one is added. The person's identity is fixed at launch, never an argument. Every use is written to `control-audit.log` on that machine (never with the link). Two honest limits: an invitation link returned to an agent is in that agent's context, so it reaches that person's AI provider (it still admits nobody; the host must compare four words and approve); and a room title written by someone else is framed so it cannot pass for instructions, but an agent can still be misled by text it reads.
- **What encryption does not protect:** a person's agent reads room text and sends it to that person's AI provider (Anthropic, OpenAI, Google and so on) under that provider's terms. Anything in a room that an agent reads may reach that provider. Do not discuss secrets in a room if any member's agent will read them and you have not accepted that.
- **Members can steer each other's agents.** Any member can write text an agent will read. That text can contain instructions (prompt injection). Mitigations: agents' posting kinds are limited, a person's own plans and applies go through approval by that person, and workers run in their own git worktrees. The remaining risk is real: **treat every member of a room as someone who can try to influence your agent**, and admit only people you would let type into your agent's prompt.
- Workers run commands on the person's own machine with that person's permissions inside a worktree. That is the same trust as running the agent yourself.

## 10. Supply chain

- `cloudflared` is found on the PATH first. If it is missing, Council asks, and with a yes downloads the official release from GitHub and checks its SHA-256 against the digest GitHub publishes for that asset; a mismatch means it is thrown away. (The digest comes from the same publisher as the file, so this protects against a corrupted or swapped download, not against a compromise of the release itself.)
- Dependencies are version-bounded in `pyproject.toml`. A `pip-audit` run on the frozen set on 2026-11-11 found **no known vulnerabilities** (63 packages). Re-run it before each release.

## 11. Open questions and things not done

- **Metadata hiding.** Who posts, when, how often and how big is visible to the host's server and the tunnel provider. Padding, batching or cover traffic are not done. Sealed senders (hiding the poster from the server) would need a redesign of how events are authorised.
- **Forward secrecy per message.** Room keys change on removal or manual rotation, not per message. A stolen current room key reads that epoch's past and future messages.
- **Post-compromise recovery** for a single device: revoke the device (the terminal's devices screen, ctrl+d) and remove the person from rooms, which starts a new epoch. There is no command to rotate a room's key without removing someone, and no automatic ratcheting.
- **Co-host takeover** is not built, so a lost host is a lost room.
- **Rate limits are in-process**, and per-address limits trust the tunnel's header. A determined flood through Cloudflare is handled by Cloudflare, not by us.
- **Denial of service** against a host's machine through the tunnel is limited by the caps above but not eliminated.
- **Human review is required.** No person outside this project has examined the cryptographic protocol, the server, the registry or the clients. Do not use Council for anything where a mistake would be serious until that has happened.

## 12. What this review found

Found and fixed (each has a test):

1. **Invitation id alone could claim an invitation** (important). The claim did not require proof of the link's secret, so anyone who learned only the id (chat link preview, a log line, a screenshot of the visible part) could claim and burn it, and if the host approved without comparing safety words, be admitted. Now the claim requires a key derived from the secret, checked against a stored hash in the same transaction. Older invitations without a verifier still work; new ones always carry one. Tests: `test_claiming_an_invite_needs_the_links_secret_not_just_its_id`, `test_the_claim_key_cannot_be_turned_back_into_the_link_secret_or_the_room_key`, `test_a_database_made_before_claim_keys_is_upgraded_in_place`.
2. **Non-canonical base64** (minor). Changing the last character of a ciphertext (spare bits) kept the same bytes and the signature valid, so one event could exist as two different stored spellings. Decoding is now strict for envelopes and link secrets. Found by the fuzzer. Test: `test_two_spellings_of_the_same_ciphertext_are_not_two_events`.
3. **Deeply nested JSON escaped as a crash** (important). Python's JSON parser raises `RecursionError`, not `ValueError`, past a platform-dependent depth (about 5,000 levels on Windows, far more on a Mac, where the request size limits happened to hide it). The server returned a 500 for such a body, the event-body check itself blew up, and, worst, a **member could seal an event whose decrypted payload is nested too deep, which made every reader's `open_event` raise** (the server cannot see inside a sealed event, so only readers can refuse it). All of these are now refusals, and a client talking to a hostile server gets a connection error instead of an exception it does not expect. Found by the fuzz tests on the Windows run. Tests: the four `deeply nested` tests in `tests/test_fuzz_envelope.py`.
4. **Invitations from a registered host dropped the host pin** (found earlier by the real-network acceptance run): without the pin a guest could not use the registry to follow a host whose address had been recycled. Fixed.

Checked and found sound (no change): envelope parsing never raises on hostile input (mutated fields, bit flips, wrong types, huge values, decompression bombs); the link parser only raises `ValueError`; grant parsing only raises `GrantError`; the server answers hostile payloads to every route without a server error; the host proof signs a prefixed message and no other signed format can start with that prefix; the tunnel run token is not on the command line; error messages never repeat link content or keys.

Not examined in depth (needs the human review): the Postgres store's concurrency under attack, the TUI's handling of hostile text (terminal escape sequences inside messages), the registry's Cloudflare provider calls against a hostile API, side channels, and the whole Windows-specific file permission story.

How this was tested: seeded fuzz tests (`tests/test_fuzz_envelope.py`, 40 seeds per parser) for envelopes, links, grants and 300 hostile requests across the routes; chaos tests for network faults and restarts; a five-configuration real-network acceptance run across Mac and Windows with the real tunnel and registry. None of that replaces review by a security professional.
