# Google Analytics Admin API PHP Client Library Installation

This guide provides specific instructions for installing and setting up the
Google Analytics Admin API client library for PHP.

## Prerequisites

*   **PHP:** Version 8.0 or higher.
*   **Package Manager:** Composer.
*   **Authentication:** Application Default Credentials (ADC) configured via
    `gcloud auth application-default login`.

## Installation

Install the library using Composer.

> [!NOTE] For full installation details, refer to the
> [PHP Analytics Admin Repository](https://github.com/googleapis/php-analytics-admin#installation).

Run the following command in your project directory:

```bash
composer require google/analytics-admin
```

If `composer` is not available, prompt the user to install `Composer` before
installing the package.

*Why: Composer installs the Google Analytics Admin client library and autoloader
along with its authentication dependencies.*

## Quickstart / Usage

Make sure to include Composer's autoloader before initializing the client:

```php
require_once __DIR__ . '/vendor/autoload.php';

use Google\Analytics\Admin\V1beta\Client\AnalyticsAdminServiceClient;
use Google\Analytics\Admin\V1beta\ListAccountSummariesRequest;

// Initialize the client. Uses ADC from environment.
$client = new AnalyticsAdminServiceClient();

$request = new ListAccountSummariesRequest();
$accountSummaries = $client->listAccountSummaries($request);

foreach ($accountSummaries as $summary) {
    printf("Account: %s (%s)\n", $summary->getDisplayName(), $summary->getName());
}
```
