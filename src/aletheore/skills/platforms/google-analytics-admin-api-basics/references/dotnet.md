# Google Analytics Admin API .NET Client Library Installation

This guide provides specific instructions for installing and setting up the
Google Analytics Admin API client library for .NET / C#.

## Prerequisites

*   **.NET SDK:** .NET Core 3.1, .NET Standard 2.0, or .NET 6.0+.
*   **Package Manager:** NuGet / `dotnet` CLI.
*   **Authentication:** Application Default Credentials (ADC) configured via
    `gcloud auth application-default login`.

## Installation

Install the official NuGet package.

> [!NOTE] For package options and versions, see the
> [NuGet Google.Analytics.Admin.V1Beta Page](https://www.nuget.org/packages/Google.Analytics.Admin.V1Beta#package-manager).

Using the .NET CLI:

```bash
dotnet add package Google.Analytics.Admin.V1Beta
```

If `dotnet` is not available, prompt the user to install the `.NET CLI` before
installing the package.

Or via Package Manager Console in Visual Studio:

```powershell
Install-Package Google.Analytics.Admin.V1Beta
```

*Why: The NuGet package contains strongly typed protobuf models and gRPC service
clients for .NET applications.*

## Quickstart / Usage

```csharp
using System;
using System.Threading.Tasks;
using Google.Analytics.Admin.V1Beta;

class Program
{
    static async Task Main()
    {
        // Initialize client. Uses Application Default Credentials.
        AnalyticsAdminServiceClient client = await AnalyticsAdminServiceClient.CreateAsync();

        // List account summaries
        var response = client.ListAccountSummariesAsync(new ListAccountSummariesRequest());
        await foreach (AccountSummary summary in response)
        {
            Console.WriteLine($"Account: {summary.DisplayName} ({summary.Name})");
        }
    }
}
```
