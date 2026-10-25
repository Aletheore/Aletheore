# Google Analytics Admin API Java Client Library Installation

This guide provides specific instructions for installing and setting up the
Google Analytics Admin API client library for Java.

## Prerequisites

*   **Java Development Kit (JDK):** Java 8 or higher (Java 11+ recommended).
*   **Build Tool:** Maven or Gradle.
*   **Authentication:** Application Default Credentials (ADC) configured via
    `gcloud auth application-default login`.

## Installation

Add the official Google Analytics Admin client library dependency to your
project.

> [!NOTE] For more information and full documentation, refer to the
> [Google Cloud Java Analytics Admin Repository](https://github.com/googleapis/google-cloud-java/blob/main/java-analytics-admin/README.md).

> [!TIP]
> Always replace `LATEST_LIBRARY_VERSION` with the latest stable version of the
> dependency available on Maven Central.

### Option A: Maven (`pom.xml`)

Add the following dependency to your `pom.xml` inside `<dependencies>`:

```xml
<dependency>
  <groupId>com.google.cloud</groupId>
  <artifactId>google-cloud-analytics-admin</artifactId>
  <version>LATEST_LIBRARY_VERSION</version>
</dependency>
```

*Why: Using Maven ensures transitive dependencies (like Netty, gRPC, and Google
Auth Library) are automatically resolved.*

### Option B: Gradle (`build.gradle`)

Add the following to your `dependencies` block:

```groovy
implementation 'com.google.cloud:google-cloud-analytics-admin:LATEST_LIBRARY_VERSION'
```

## Quickstart / Usage

Initialize the client using try-with-resources to automatically close the gRPC
channel:

```java
import com.google.analytics.admin.v1beta.AnalyticsAdminServiceClient;
import com.google.analytics.admin.v1beta.AccountSummary;

public class AdminApiDemo {
  public static void main(String[] args) throws Exception {
    // Initialize the client. Automatically authenticates using ADC.
    try (AnalyticsAdminServiceClient client = AnalyticsAdminServiceClient.create()) {
      for (AccountSummary summary : client.listAccountSummaries().iterateAll()) {
        System.out.printf("Account: %s (%s)%n", summary.getDisplayName(), summary.getName());
      }
    }
  }
}
```
