---
name: javascript-typescript-jest
description: Best practices for writing JavaScript/TypeScript tests using Jest, including mocking strategies, test structure, and common patterns.
skill_id: testing/javascript-typescript-jest
title: Javascript Typescript Jest
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/javascript-typescript-jest
license: MIT
shelf: quality
aletheore: {"id": "testing/javascript-typescript-jest", "shelf": "quality", "category": "testing", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/javascript-typescript-jest", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Jest", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

### Test Structure
- Name test files with `.test.ts` or `.test.js` suffix
- Place test files next to the code they test or in a dedicated `__tests__` directory
- Use descriptive test names that explain the expected behavior
- Use nested describe blocks to organize related tests
- Follow the pattern: `describe('Component/Function/Class', () => { it('should do something', () => {}) })`

### Effective Mocking
- Mock external dependencies (APIs, databases, etc.) to isolate your tests
- Use `jest.mock()` for module-level mocks
- Use `jest.spyOn()` for specific function mocks
- Use `mockImplementation()` or `mockReturnValue()` to define mock behavior
- Reset mocks between tests with `jest.resetAllMocks()` in `afterEach`

### Testing Async Code
- Always return promises or use async/await syntax in tests
- Use `resolves`/`rejects` matchers for promises
- Set appropriate timeouts for slow tests with `jest.setTimeout()`

### Snapshot Testing
- Use snapshot tests for UI components or complex objects that change infrequently
- Keep snapshots small and focused
- Review snapshot changes carefully before committing

### Testing React Components
- Use React Testing Library over Enzyme for testing components
- Test user behavior and component accessibility
- Query elements by accessibility roles, labels, or text content
- Use `userEvent` over `fireEvent` for more realistic user interactions

## Common Jest Matchers
- Basic: `expect(value).toBe(expected)`, `expect(value).toEqual(expected)`
- Truthiness: `expect(value).toBeTruthy()`, `expect(value).toBeFalsy()`
- Numbers: `expect(value).toBeGreaterThan(3)`, `expect(value).toBeLessThanOrEqual(3)`
- Strings: `expect(value).toMatch(/pattern/)`, `expect(value).toContain('substring')`
- Arrays: `expect(array).toContain(item)`, `expect(array).toHaveLength(3)`
- Objects: `expect(object).toHaveProperty('key', value)`
- Exceptions: `expect(fn).toThrow()`, `expect(fn).toThrow(Error)`
- Mock functions: `expect(mockFn).toHaveBeenCalled()`, `expect(mockFn).toHaveBeenCalledWith(arg1, arg2)`
