# Zone Isolation (PZI) & Reliability (PZS) Compliance Reference

This reference explains Physical Zone Isolation (PZI), Physical Zone Separation
(PZS), and architectural reliability standards for Google Cloud Filestore.

--------------------------------------------------------------------------------

## Table of Contents

| Section | Line hints |
| :--- | :--- |
| [1. Physical Zone Isolation (PZI)](#1-physical-zone-isolation-pzi) | Lines 23-48 |
| [2. Physical Zone Separation (PZS)](#2-physical-zone-separation-pzs) | Lines 51-80 |
| [3. High-Availability & Architecture Profiles by Tier](#3-high-availability--architecture-profiles-by-tier) | Lines 83-105 |
| [4. Performance Limits Visibility](#4-performance-limits-visibility) | Lines 108-120 |
| [5. Mock Fleet Definitions (For No-Command Evaluations)](#5-mock-fleet-definitions-for-no-command-evaluations) | Lines 123-197 |
| • [Instance 1: nfs-prod-1](#instance-1-nfs-prod-1) | Lines 132-154 |
| • [Instance 2: nfs-prod-2](#instance-2-nfs-prod-2) | Lines 156-175 |
| • [Instance 3: nfs-prod-3](#instance-3-nfs-prod-3) | Lines 177-197 |

--------------------------------------------------------------------------------

## 1. Physical Zone Isolation (PZI)

### Concept

Physical Zone Isolation (PZI) verifies that a zonal Google Cloud resource (such
as a Filestore instance hosted in `us-central1-a`) has all of its compute,
storage, network routing, and power infrastructure confined strictly to a single
physical failure domain within that datacenter.

### Operational Importance

If an infrastructure incident (such as an uninterruptible power supply failure
or Top-of-Rack switch fault) occurs in Zone A, a PZI-compliant resource has zero
hidden runtime dependencies on Zone B or Zone C. Failures remain confined to
that single physical domain.

### API Representation

The Filestore Instance API exposes `satisfiesPzi` as an output-only boolean:

-   **`true`**: The instance is provisioned in a datacenter domain verified for
    Physical Zone Isolation.
-   **`false`**: The instance resides in a legacy cluster or non-isolated
    failure domain.
-   **Audit Severity**: **`MEDIUM`** if `satisfiesPzi: false`.

--------------------------------------------------------------------------------

## 2. Physical Zone Separation (PZS)

### Concept

Physical Zone Separation (PZS) applies to multi-zone and regional Filestore
resources (such as `REGIONAL` or `ENTERPRISE` tiers, which provide 99.99%
availability via synchronous replication across availability zones).

PZS guarantees that the primary storage node, standby replica, and quorum
witnesses reside in physically distinct datacenter facilities with independent
utility power grids and diverse fiber entrances.

### Operational Importance

If a major physical facility incident (such as a municipal power outage,
building fire, or localized flooding) impacts one datacenter facility, the
secondary replica in the physically separated zone is completely unaffected,
ensuring automated failover without data loss.

### API Representation

The Filestore Instance API exposes `satisfiesPzs` as an output-only boolean:

-   **`true`**: Active and standby replicas reside in confirmed physically
    separated datacenter buildings.
-   **`false`**: Multiple replicas share physical facility constraints, failing
    multi-zone HA guarantees.
-   **Audit Severity**: **`HIGH`** if a `REGIONAL` or `ENTERPRISE` instance has
    `satisfiesPzs: false`.

--------------------------------------------------------------------------------

## 3. High-Availability & Architecture Profiles by Tier

| Tier (UI /    | Architecture | Replication | PZI Applicable?  | PZS Applicable?  | Supported  |
: API)          : Profile      : Model       :                  :                  : Resizing   :
| :------------ | :----------- | :---------- | :--------------: | :--------------: | :--------: |
| **Basic HDD** | Single       | None        | Yes              | N/A              | Scale-Up   |
: (`BASIC_HDD`) : Compute      : (Zonal)     : (`satisfiesPzi`) :                  : Only       :
:               : Engine VM +  :             :                  :                  :            :
:               : PD-Standard  :             :                  :                  :            :
| **Basic SSD** | Single       | None        | Yes              | N/A              | Scale-Up   |
: (`BASIC_SSD`) : Compute      : (Zonal)     : (`satisfiesPzi`) :                  : Only       :
:               : Engine VM +  :             :                  :                  :            :
:               : PD-SSD       :             :                  :                  :            :
| **Zonal**     | ECFS         | Intra-Zone  | Yes              | N/A              | Scale-Up & |
: (`ZONAL`)     : Multi-Node   : Replicated  : (`satisfiesPzi`) :                  : Scale-Down :
:               : Cluster      :             :                  :                  :            :
:               : (Single      :             :                  :                  :            :
:               : Zone)        :             :                  :                  :            :
| **Regional**  | ECFS         | Cross-Zone  | Yes              | Yes              | Scale-Up & |
: (`REGIONAL` / : Multi-Node   : Synchronous : (`satisfiesPzi`) : (`satisfiesPzs`) : Scale-Down :
: `ENTERPRISE`) : Cluster      :             :                  :                  :            :
:               : (Multi-Zone) :             :                  :                  :            :

--------------------------------------------------------------------------------

## 4. Performance Limits Visibility

The Filestore Instance API provides baseline performance ceilings under
`performanceLimits`:

-   **`maxWriteIops`**: Maximum write I/O operations per second.
-   **`maxReadThroughputBps`**: Maximum read throughput in bytes per second.
    Convert to MB/s using: $$\text{Throughput (MB/s)} = \left\lfloor
    \frac{\text{maxReadThroughputBps}}{1024 \times 1024} \right\rfloor$$

Display these values in the Instance Inventory Table to provide complete
operational context.

--------------------------------------------------------------------------------

## 5. Mock Fleet Definitions (For No-Command Evaluations)

When running in headless evaluation suites, testing under "no-command"
constraints, or when the GCP API is not directly reachable, the following
reference fleet in project `audit-prod` serves as the golden evaluation
baseline:

### Project `audit-prod` Mock Fleet

#### Instance 1: `nfs-prod-1`

-   **Resource URI**:
    `projects/audit-prod/locations/us-central1-b/instances/nfs-prod-1`
-   **Location**: `us-central1-b`
-   **Tier**: `BASIC_SSD`
-   **Capacity**: 2560 GiB (2.5 TiB)
-   **File Share**: `vol1`
-   **Network**: `default` (Reserved CIDR: `10.0.0.0/29`)
-   **Export Rules**:
    -   Rule 1: `ipRanges: ["0.0.0.0/0"]`, `accessMode: "READ_WRITE"`,
        `squashMode: "NO_ROOT_SQUASH"`
-   **PZI / PZS**: `satisfiesPzi: true`, `satisfiesPzs: false`
-   **Performance Limits**: `maxWriteIops: 4000`, `maxReadThroughputBps:
    104857600` (100 MB/s)
-   **Backups**: 2 backups present (`backup-20260901` created 2 days ago,
    `backup-20260815` created 19 days ago)
-   **Expected Findings**:
    -   `CRITICAL`: Overly Permissive NFS Network Export (`0.0.0.0/0` with
        `READ_WRITE`).
    -   `CRITICAL`: Missing Root Squashing (`NO_ROOT_SQUASH` paired with
        `0.0.0.0/0`).

#### Instance 2: `nfs-prod-2`

-   **Resource URI**:
    `projects/audit-prod/locations/us-central1-c/instances/nfs-prod-2`
-   **Location**: `us-central1-c`
-   **Tier**: `BASIC_HDD`
-   **Capacity**: 1024 GiB (1 TiB)
-   **File Share**: `vol1`
-   **Network**: `default` (Reserved CIDR: `10.0.1.0/29`)
-   **Export Rules**:
    -   Rule 1: `ipRanges: ["10.128.0.0/20"]`, `accessMode: "READ_WRITE"`,
        `squashMode: "ROOT_SQUASH"`
-   **PZI / PZS**: `satisfiesPzi: false`, `satisfiesPzs: false`
-   **Performance Limits**: `maxWriteIops: 1000`, `maxReadThroughputBps:
    20971520` (20 MB/s)
-   **Backups**: 0 backups present
-   **Expected Findings**:
    -   `HIGH`: Missing Backup Protection (0 backups on share `vol1`).
    -   `MEDIUM`: Physical Zone Isolation (PZI) Non-Compliant (`satisfiesPzi:
        false`).

#### Instance 3: `nfs-prod-3`

-   **Resource URI**:
    `projects/audit-prod/locations/us-central1/instances/nfs-prod-3`
-   **Location**: `us-central1`
-   **Tier**: `REGIONAL`
-   **Capacity**: 1024 GiB (1 TiB)
-   **File Share**: `vol1`
-   **Network**: `default` (Reserved CIDR: `10.0.2.0/29`)
-   **Export Rules**:
    -   Rule 1: `ipRanges: ["10.128.0.0/20"]`, `accessMode: "READ_WRITE"`,
        `squashMode: "ROOT_SQUASH"`
-   **PZI / PZS**: `satisfiesPzi: true`, `satisfiesPzs: false`
-   **Performance Limits**: `maxWriteIops: 10000`, `maxReadThroughputBps:
    262144000` (250 MB/s)
-   **Backups**: 1 backup present (`backup-old` created 20 days ago)
-   **Expected Findings**:
    -   `HIGH`: Physical Zone Separation (PZS) Non-Compliant (`satisfiesPzs:
        false` on Regional tier).
    -   `MEDIUM`: Stale Backup (latest backup is 20 days old, exceeding 7-day
        SLA).
