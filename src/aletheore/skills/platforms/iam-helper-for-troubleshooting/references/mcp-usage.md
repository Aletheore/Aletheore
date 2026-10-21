# Policy Troubleshooter MCP Usage

Policy Troubleshooter is supported by a remote Model Context Protocol (MCP)
server that provides a set of tools for automated Identity and Access Management
(IAM) access analysis and troubleshooting.

## MCP Tools for Policy Troubleshooter

-   **`troubleshoot_access`**: Troubleshoots an IAM access check for a specific
    principal, resource, and permission. It analyzes Allow, Deny, and Principal
    Access Boundary (PAB) policies to explain why access was granted or denied.
-   **`troubleshoot_iam_error_id`**: Troubleshoots an IAM access denied error
    using the unique error ID (`error_info_id`) reported in Google Cloud Console
    error messages or API error details.

## Setup Instructions

The Policy Troubleshooter remote MCP server is available at the following URL:

```
https://policytroubleshooter.googleapis.com/mcp
```

To connect an MCP client (such as Claude Desktop, Gemini CLI, or custom agents)
to the Policy Troubleshooter remote MCP server, configure your client to connect
via Server-Sent Events (SSE) or HTTP to the server URL, passing an OAuth 2.0
Bearer token in the `Authorization` header.

To connect to the Policy Troubleshooter MCP server, see
[Configure a client connection](https://cloud.google.com/policy-intelligence/docs/use-policy-troubleshooter-mcp).

### Example: Client Configuration

Add the following configuration to your client configuration (for example,
`claude_desktop_config.json`). This configuration uses the `gcloud` CLI to
dynamically fetch a valid access token:

```json
{
  "mcpServers": {
    "policy_troubleshooter": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-sse-client",
        "https://policytroubleshooter.googleapis.com/mcp"
      ],
      "env": {
        "AUTHORIZATION": "Bearer $(gcloud auth print-access-token)"
      }
    }
  }
}
```

## Supported Operations

Agents using the Policy Troubleshooter remote MCP server can perform tasks such
as:

-   **Diagnose permission denied errors**: Paste an error ID (`error_info_id`)
    or specify a user, resource, and permission to immediately understand which
    policy blocked access.
-   **Analyze IAM policy hierarchies**: Explain how allow policies, deny rules,
    and Principal Access Boundary (PAB) policies interact across organizations,
    folders, and projects.
-   **Automate remediation suggestions**: Determine whether a missing role
    binding, an explicit deny policy, or a PAB boundary is causing an access
    failure.

For more information about the Policy Troubleshooter MCP server, visit:
[Use Policy Troubleshooter with MCP](https://cloud.google.com/policy-intelligence/docs/use-policy-troubleshooter-mcp).
For detailed tool schemas and parameter descriptions, see the
[Policy Troubleshooter MCP tools reference](https://cloud.google.com/policy-intelligence/docs/reference/policytroubleshooter/mcp).
For general IAM access troubleshooting concepts, see
[Troubleshooting access with Policy Troubleshooter](https://cloud.google.com/policy-intelligence/docs/troubleshoot-access).
