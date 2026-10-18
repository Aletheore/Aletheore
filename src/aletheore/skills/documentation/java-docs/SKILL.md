---
name: java-docs
description: Ensure that Java types are documented with Javadoc comments and follow best practices for documentation.
skill_id: documentation/java-docs
title: Java Docs
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/java-docs
license: MIT
shelf: ship-operate
aletheore: {"id": "documentation/java-docs", "shelf": "ship-operate", "category": "documentation", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/java-docs", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Javadoc", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Java Documentation (Javadoc) Best Practices

- Public and protected members should be documented with Javadoc comments.
- It is encouraged to document package-private and private members as well, especially if they are complex or not self-explanatory.
- The first sentence of the Javadoc comment is the summary description. It should be a concise overview of what the method does and end with a period.
- Use `@param` for method parameters. The description starts with a lowercase letter and does not end with a period.
- Use `@return` for method return values.
- Use `@throws` or `@exception` to document exceptions thrown by methods.
- Use `@see` for references to other types or members.
- Use `{@inheritDoc}` to inherit documentation from base classes or interfaces.
  - Unless there is major behavior change, in which case you should document the differences.
- Use `@param <T>` for type parameters in generic types or methods.
- Use `{@code}` for inline code snippets.
- Use `<pre>{@code ... }</pre>` for code blocks.
- Use `@since` to indicate when the feature was introduced (e.g., version number).
- Use `@version` to specify the version of the member.
- Use `@author` to specify the author of the code.
- Use `@deprecated` to mark a member as deprecated and provide an alternative.
