# Google Analytics Admin API Node.js Client Library Installation

This guide provides specific instructions for installing and setting up the
Google Analytics Admin API client library for Node.js.

## Prerequisites

*   **Node.js:** v14.x or higher (v16+ recommended).
*   **Package Manager:** `npm` or `yarn`.
*   **Authentication:** Application Default Credentials (ADC) configured via
    `gcloud auth application-default login`.

## Installation

Install the official Google Analytics Admin npm package.

> [!NOTE] For complete reference documentation, see the
> [Node.js Analytics Admin Reference](https://googleapis.dev/nodejs/analytics-admin/latest/index.html).

Run the following command in your project directory:

```bash
npm install @google-analytics/admin
```

If `npm` is not available, prompt the user to install `Node.js` (and `npm`)
before installing the package.

*Why: Installing `@google-analytics/admin` pulls in the official gRPC and REST
client bindings for Node.js.*

## Quickstart / Usage

```javascript
const {AnalyticsAdminServiceClient} = require('@google-analytics/admin');

async function listAccounts() {
  // Initialize the client. Uses ADC from the execution environment.
  const analyticsAdminClient = new AnalyticsAdminServiceClient();

  const [accountSummaries] = await analyticsAdminClient.listAccountSummaries();

  console.log('Available Accounts:');
  for (const account of accountSummaries) {
    console.log(`Account: ${account.displayName} (${account.name})`);
  }
}

listAccounts().catch(console.error);
```
