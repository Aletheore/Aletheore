# Google Analytics Admin API Go Client Library Installation

This guide provides specific instructions for installing and setting up the
Google Analytics Admin API client library for Go.

## Prerequisites

*   **Go:** Version 1.19 or higher.
*   **Modules:** Go modules enabled.
*   **Authentication:** Application Default Credentials (ADC) configured via
    `gcloud auth application-default login`.

## Installation

Install the official Go client library using `go get`.

Run the following command in your module directory:

```bash
go get cloud.google.com/go/analytics/admin/apiv1beta
```

If `go` is not available, prompt the user to install `Go` before installing the
package.

*Why: This adds the Google Analytics Admin API client package and necessary gRPC
transport dependencies to your `go.mod` file.*

## Quickstart / Usage

```go
package main

import (
    "context"
    "fmt"
    "log"

    admin "cloud.google.com/go/analytics/admin/apiv1beta"
    "cloud.google.com/go/analytics/admin/apiv1beta/adminpb"
    "google.golang.org/api/iterator"
)

func main() {
    ctx := context.Background()

    // Initialize the client. Automatically authenticates via ADC.
    client, err := admin.NewAnalyticsAdminClient(ctx)
    if err != nil {
        log.Fatalf("Failed to create client: %v", err)
    }
    defer client.Close()

    req := &adminpb.ListAccountSummariesRequest{}
    it := client.ListAccountSummaries(ctx, req)
    for {
        resp, err := it.Next()
        if err == iterator.Done {
            break
        }
        if err != nil {
            log.Fatalf("Error iterating account summaries: %v", err)
        }
        fmt.Printf("Account: %s (%s)\n", resp.DisplayName, resp.Name)
    }
}
```
