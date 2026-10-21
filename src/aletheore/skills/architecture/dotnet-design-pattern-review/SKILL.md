---
name: dotnet-design-pattern-review
description: Review the C#/.NET code for design pattern implementation and suggest improvements.
skill_id: architecture/dotnet-design-pattern-review
title: Dotnet Design Pattern Review
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/dotnet-design-pattern-review
license: MIT
shelf: build
aletheore: {"id": "architecture/dotnet-design-pattern-review", "shelf": "build", "category": "architecture", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/dotnet-design-pattern-review", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": ".NET design pattern review", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# .NET/C# Design Pattern Review

Review the C#/.NET code in ${selection} for design pattern implementation and suggest improvements for the solution/project. Do not make any changes to the code, just provide a review.

## Required Design Patterns

- **Command Pattern**: Generic base classes (`CommandHandler<TOptions>`), `ICommandHandler<TOptions>` interface, `CommandHandlerOptions` inheritance, static `SetupCommand(IHost host)` methods
- **Factory Pattern**: Complex object creation service provider integration
- **Dependency Injection**: Primary constructor syntax, `ArgumentNullException` null checks, interface abstractions, proper service lifetimes
- **Repository Pattern**: Async data access interfaces provider abstractions for connections
- **Provider Pattern**: External service abstractions (database, AI), clear contracts, configuration handling
- **Resource Pattern**: ResourceManager for localized messages, separate .resx files (LogMessages, ErrorMessages)

## Review Checklist

- **Design Patterns**: Identify patterns used. Are Command Handler, Factory, Provider, and Repository patterns correctly implemented? Missing beneficial patterns?
- **Architecture**: Follow namespace conventions (`{Core|Console|App|Service}.{Feature}`)? Proper separation between Core/Console projects? Modular and readable?
- **.NET Best Practices**: Primary constructors, async/await with Task returns, ResourceManager usage, structured logging, strongly-typed configuration?
- **GoF Patterns**: Command, Factory, Template Method, Strategy patterns correctly implemented?
- **SOLID Principles**: Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion violations?
- **Performance**: Proper async/await, resource disposal, ConfigureAwait(false), parallel processing opportunities?
- **Maintainability**: Clear separation of concerns, consistent error handling, proper configuration usage?
- **Testability**: Dependencies abstracted via interfaces, mockable components, async testability, AAA pattern compatibility?
- **Security**: Input validation, secure credential handling, parameterized queries, safe exception handling?
- **Documentation**: XML docs for public APIs, parameter/return descriptions, resource file organization?
- **Code Clarity**: Meaningful names reflecting domain concepts, clear intent through patterns, self-explanatory structure?
- **Clean Code**: Consistent style, appropriate method/class size, minimal complexity, eliminated duplication?

## Improvement Focus Areas

- **Command Handlers**: Validation in base class, consistent error handling, proper resource management
- **Factories**: Dependency configuration, service provider integration, disposal patterns
- **Providers**: Connection management, async patterns, exception handling and logging
- **Configuration**: Data annotations, validation attributes, secure sensitive value handling
- **AI/ML Integration**: Semantic Kernel patterns, structured output handling, model configuration

Provide specific, actionable recommendations for improvements aligned with the project's architecture and .NET best practices.
