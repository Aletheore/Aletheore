# Google Analytics Admin API Ruby Client Library Installation

This guide provides specific instructions for installing and setting up the
Google Analytics Admin API client library for Ruby.

## Prerequisites

*   **Ruby:** Version 3.0 or higher.
*   **Package Manager:** RubyGems / Bundler.
*   **Authentication:** Application Default Credentials (ADC) configured via
    `gcloud auth application-default login`.

## Installation

Install the official Google Cloud Ruby client gem.

> [!NOTE] For complete installation details, refer to the
> [Ruby Analytics Admin Repository](https://github.com/googleapis/google-cloud-ruby/tree/main/google-analytics-admin-v1alpha#installation).

Using RubyGems:

```bash
gem install google-analytics-admin-v1alpha
```

If `gem` is not available, prompt the user to install `Ruby` (and `RubyGems`)
before installing the gem.

Or add to your `Gemfile`:

```ruby
gem "google-analytics-admin-v1alpha"
```

And run `bundle install`.

## Quickstart / Usage

```ruby
require "google/analytics/admin/v1alpha"

# Initialize client. Automatically authenticates using ADC.
client = Google::Analytics::Admin::V1alpha::AnalyticsAdminService::Client.new

account_summaries = client.list_account_summaries

account_summaries.each do |summary|
  puts "Account: #{summary.display_name} (#{summary.name})"
end
```
