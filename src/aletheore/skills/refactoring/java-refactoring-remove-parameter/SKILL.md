---
name: java-refactoring-remove-parameter
description: Refactoring using Remove Parameter in Java Language
skill_id: refactoring/java-refactoring-remove-parameter
title: Java Refactoring Remove Parameter
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/java-refactoring-remove-parameter
license: MIT
shelf: quality
aletheore: {"id": "refactoring/java-refactoring-remove-parameter", "shelf": "quality", "category": "refactoring", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/java-refactoring-remove-parameter", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Java refactorings", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Refactoring Java Methods with Remove Parameter

## Role

You are an expert in refactoring Java methods.

Below are **2 examples** (with titles code before and code after refactoring) that represents **Remove Parameter**.

## Code Before Refactoring 1:
```java
public Backend selectBackendForGroupCommit(long tableId, ConnectContext context, boolean isCloud)
        throws LoadException, DdlException {
    if (!Env.getCurrentEnv().isMaster()) {
        try {
            long backendId = new MasterOpExecutor(context)
                    .getGroupCommitLoadBeId(tableId, context.getCloudCluster(), isCloud);
            return Env.getCurrentSystemInfo().getBackend(backendId);
        } catch (Exception e) {
            throw new LoadException(e.getMessage());
        }
    } else {
        return Env.getCurrentSystemInfo()
                .getBackend(selectBackendForGroupCommitInternal(tableId, context.getCloudCluster(), isCloud));
    }
}
```

## Code After Refactoring 1:
```java
public Backend selectBackendForGroupCommit(long tableId, ConnectContext context)
        throws LoadException, DdlException {
    if (!Env.getCurrentEnv().isMaster()) {
        try {
            long backendId = new MasterOpExecutor(context)
                    .getGroupCommitLoadBeId(tableId, context.getCloudCluster());
            return Env.getCurrentSystemInfo().getBackend(backendId);
        } catch (Exception e) {
            throw new LoadException(e.getMessage());
        }
    } else {
        return Env.getCurrentSystemInfo()
                .getBackend(selectBackendForGroupCommitInternal(tableId, context.getCloudCluster()));
    }
}
```

## Code Before Refactoring 2:
```java
NodeImpl( long id, long firstRel, long firstProp )
{
     this( id, false );
}
```

## Code After Refactoring 2:
```java
NodeImpl( long id)
{
     this( id, false );
}
```

## Task

Apply **Remove Parameter** to improve readability, testability, maintainability, reusability, modularity, cohesion, low coupling, and consistency.

Always return a complete and compilable method (Java 17).

Perform intermediate steps internally:
- First, analyze each method and identify parameters that are unused or redundant (i.e., values that can be obtained from class fields, constants, or other method calls).
- For each qualifying method, remove the unnecessary parameters from its definition and from all its internal calls.
- Ensure that the method continues to function correctly after parameter removal.
- Output only the refactored code inside a single ```java``` block.
- Do not remove any functionality from the original method.
- Include a one-line comment above each modified method indicating which parameter was removed and why.

## Code to be Refactored:

Now, assess all methods with unused parameters and refactor them using **Remove Parameter**
