---
name: csharp-nunit
description: Get best practices for NUnit unit testing, including data-driven tests
skill_id: testing/csharp-nunit
title: Csharp NUnit
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/csharp-nunit
license: MIT
shelf: quality
aletheore: {"id": "testing/csharp-nunit", "shelf": "quality", "category": "testing", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/csharp-nunit", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "C# test frameworks", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# NUnit Best Practices

Your goal is to help me write effective unit tests with NUnit, covering both standard and data-driven testing approaches.

## Project Setup

- Use a separate test project with naming convention `[ProjectName].Tests`
- Reference Microsoft.NET.Test.Sdk, NUnit, and NUnit3TestAdapter packages
- Create test classes that match the classes being tested (e.g., `CalculatorTests` for `Calculator`)
- Use .NET SDK test commands: `dotnet test` for running tests

## Test Structure

- Apply `[TestFixture]` attribute to test classes
- Use `[Test]` attribute for test methods
- Follow the Arrange-Act-Assert (AAA) pattern
- Name tests using the pattern `MethodName_Scenario_ExpectedBehavior`
- Use `[SetUp]` and `[TearDown]` for per-test setup and teardown
- Use `[OneTimeSetUp]` and `[OneTimeTearDown]` for per-class setup and teardown
- Use `[SetUpFixture]` for assembly-level setup and teardown

## Standard Tests

- Keep tests focused on a single behavior
- Avoid testing multiple behaviors in one test method
- Use clear assertions that express intent
- Include only the assertions needed to verify the test case
- Make tests independent and idempotent (can run in any order)
- Avoid test interdependencies

## Data-Driven Tests

- Use `[TestCase]` for inline test data
- Use `[TestCaseSource]` for programmatically generated test data
- Use `[Values]` for simple parameter combinations
- Use `[ValueSource]` for property or method-based data sources
- Use `[Random]` for random numeric test values
- Use `[Range]` for sequential numeric test values
- Use `[Combinatorial]` or `[Pairwise]` for combining multiple parameters

## Assertions

- Use `Assert.That` with constraint model (preferred NUnit style)
- Use constraints like `Is.EqualTo`, `Is.SameAs`, `Contains.Item`
- Use `Assert.AreEqual` for simple value equality (classic style)
- Use `CollectionAssert` for collection comparisons
- Use `StringAssert` for string-specific assertions
- Use `Assert.Throws<T>` or `Assert.ThrowsAsync<T>` to test exceptions
- Use descriptive messages in assertions for clarity on failure

## Mocking and Isolation

- Consider using Moq or NSubstitute alongside NUnit
- Mock dependencies to isolate units under test
- Use interfaces to facilitate mocking
- Consider using a DI container for complex test setups

## Test Organization

- Group tests by feature or component
- Use categories with `[Category("CategoryName")]`
- Use `[Order]` to control test execution order when necessary
- Use `[Author("DeveloperName")]` to indicate ownership
- Use `[Description]` to provide additional test information
- Consider `[Explicit]` for tests that shouldn't run automatically
- Use `[Ignore("Reason")]` to temporarily skip tests
