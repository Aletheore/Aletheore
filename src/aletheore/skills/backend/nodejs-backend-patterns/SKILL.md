---
name: nodejs-backend-patterns
description: Build production-ready Node.js backend services with Express/Fastify, implementing middleware patterns, error handling, authentication, database integration, and API design best practices. Use when creating Node.js servers, REST APIs, GraphQL backends, or microservices architectures.
skill_id: backend/nodejs-backend-patterns
title: Nodejs Backend Patterns
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/javascript-typescript/skills/nodejs-backend-patterns
license: MIT
shelf: build
aletheore: {"id": "backend/nodejs-backend-patterns", "shelf": "build", "category": "backend", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/javascript-typescript/skills/nodejs-backend-patterns", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "JS/node patterns", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 2, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Node.js Backend Patterns

Comprehensive guidance for building scalable, maintainable, and production-ready Node.js backend applications with modern frameworks, architectural patterns, and best practices.

## When to Use This Skill

- Building REST APIs or GraphQL servers
- Creating microservices with Node.js
- Implementing authentication and authorization
- Designing scalable backend architectures
- Setting up middleware and error handling
- Integrating databases (SQL and NoSQL)
- Building real-time applications with WebSockets
- Implementing background job processing

## Detailed patterns and worked examples

Detailed pattern documentation lives in `references/details.md`. Read that file when the navigation tier above is insufficient.

## Best Practices

1. **Use TypeScript**: Type safety prevents runtime errors
2. **Implement proper error handling**: Use custom error classes
3. **Validate input**: Use libraries like Zod or Joi
4. **Use environment variables**: Never hardcode secrets
5. **Implement logging**: Use structured logging (Pino, Winston)
6. **Add rate limiting**: Prevent abuse
7. **Use HTTPS**: Always in production
8. **Implement CORS properly**: Don't use `*` in production
9. **Use dependency injection**: Easier testing and maintenance
10. **Write tests**: Unit, integration, and E2E tests
11. **Handle graceful shutdown**: Clean up resources
12. **Use connection pooling**: For databases
13. **Implement health checks**: For monitoring
14. **Use compression**: Reduce response size
15. **Monitor performance**: Use APM tools

## Testing Patterns

See `javascript-testing-patterns` skill for comprehensive testing guidance.
