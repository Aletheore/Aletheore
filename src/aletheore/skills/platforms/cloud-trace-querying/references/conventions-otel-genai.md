# OpenTelemetry GenAI Semantic Conventions

This document details semantic conventions for Generative AI (GenAI) models,
agentic workflows, and Model Context Protocol (MCP) interactions.

## GenAI Semantic Conventions

The table below lists canonical GenAI semantic attributes and maps them to
deprecated or legacy aliases (noting Vertex/Gemini values where applicable):

Attribute Key                    | Aliases / Deprecated Keys        | Description                                                                      | Example
:------------------------------- | :------------------------------- | :------------------------------------------------------------------------------- | :------
`gen_ai.provider.name`           | `gen_ai.system`                  | Generative AI system/provider name. For Google Cloud, canonical values are `gcp.vertex_ai` (for Vertex AI API), `gcp.gemini` (for Gemini / AI Studio API), or `gcp.gen_ai`. | `gcp.vertex_ai`
`gen_ai.request.model`           |                                  | The requested LLM model name.                                                    | `gemini-1.5-pro`
`gen_ai.response.model`          |                                  | The actual model name generating the response.                                   | `gemini-1.5-pro-001`
`gen_ai.request.temperature`     |                                  | Temperature configuration parameter.                                             | `0.7`
`gen_ai.request.max_tokens`      |                                  | Maximum number of tokens to generate.                                            | `2048`
`gen_ai.request.top_p`           |                                  | Top-p (nucleus) sampling configuration.                                          | `0.9`
`gen_ai.response.id`             |                                  | The unique identifier of the response.                                           | `chatcmpl-12345`
`gen_ai.response.finish_reasons` |                                  | Finish reasons for generation.                                                   | `["stop"]`, `["length"]`
`gen_ai.usage.input_tokens`      | `gen_ai.usage.prompt_tokens`     | Count of prompt (input) tokens.                                                  | `1024`
`gen_ai.usage.output_tokens`     | `gen_ai.usage.completion_tokens` | Count of completion (output) tokens.                                             | `256`

## GenAI Agent & Span Conventions

When querying trace data for agentic frameworks and multi-agent systems, look for these standard OTel GenAI attributes:

| Attribute Key                | Description          | Example               |
| :--------------------------- | :------------------- | :-------------------- |
| `gen_ai.agent.name`          | Human-readable name  | `Codebase Researcher` |
:                              : of the agent.        :                       :
| `gen_ai.system_instructions` | The system prompt or | `You are a helpful    |
:                              : instructions sent to : coding assistant...`  :
:                              : the model.           :                       :
| `gen_ai.input.messages`      | Serialized input     | `[{"role": "user",    |
:                              : messages list (JSON  : "content"\: "..."}]`  :
:                              : array).              :                       :
| `gen_ai.output.messages`     | Serialized output    | `[{"role":            |
:                              : messages list (JSON  : "assistant",          :
:                              : array).              : "content"\: "..."}]`  :
| `gen_ai.tool.name`           | The name of the tool | `read_file`           |
:                              : called by the agent. :                       :
| `gen_ai.tool.status`         | Completion status of | `success`             |
:                              : the tool execution.  :                       :
| `gen_ai.memory.id`           | The unique ID of the | `session_abc_123`     |
:                              : conversation memory  :                       :
:                              : scope.               :                       :

## Model Context Protocol (MCP) Semantic Conventions

Attributes used for Model Context Protocol (MCP) client-server interactions:

| Attribute Key          | Aliases /    | Description | Example                |
:                        : Deprecated   :             :                        :
:                        : Keys         :             :                        :
| :--------------------- | :----------- | :---------- | :--------------------- |
| `mcp.method.name`      | `mcp.method` | The MCP     | `tools/call`           |
:                        :              : method      :                        :
:                        :              : being       :                        :
:                        :              : called.     :                        :
| `mcp.protocol.version` |              | The version | `2024-11-05`           |
:                        :              : of the MCP  :                        :
:                        :              : protocol.   :                        :
| `mcp.session.id`       |              | Session     | `session_xyz_789`      |
:                        :              : identifier  :                        :
:                        :              : correlating :                        :
:                        :              : requests.   :                        :
| `mcp.resource.uri`     |              | Resource    | `file:///path/to/file` |
:                        :              : URI         :                        :
:                        :              : accessed.   :                        :

## Non-Standard Attributes for GenAI

Some older or alternative frameworks use different prefixes, but these are not
canonical OTel keys:

*   **`llm.` Prefixes**: Attributes like `llm.tool.name` or `llm.request.model`
    are injected by older versions of tools such as OpenInference.

## Correlated Log Conventions

For large prompt payloads, model responses, or instructions that do not fit
inside span attributes, applications write structured log events with reference
indicators:

*   **`gen_ai.client.inference.operation.details` Event**: Logged when executing
    inference operations.
*   **Key Fields**:
    *   `gen_ai.input.messages_ref`: Reference/URI pointing to the prompt input
        payload.
    *   `gen_ai.output.messages_ref`: Reference/URI pointing to the response
        output payload.
    *   `gen_ai.system_instructions_ref`: Reference/URI pointing to the system
        instructions payload.

## See Also

*   [OpenTelemetry GenAI Semantic Conventions - GitHub Specification](https://github.com/open-telemetry/semantic-conventions-genai)
*   [Collect and View Multimodal Prompts & Responses - Google Cloud docs](https://docs.cloud.google.com/trace/docs/collect-view-multimodal-prompts-responses)
