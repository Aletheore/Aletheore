---
name: java-refactoring-extract-method
description: Refactoring using Extract Methods in Java Language
skill_id: refactoring/java-refactoring-extract-method
title: Java Refactoring Extract Method
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/java-refactoring-extract-method
license: MIT
shelf: quality
aletheore: {"id": "refactoring/java-refactoring-extract-method", "shelf": "quality", "category": "refactoring", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/java-refactoring-extract-method", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Java refactorings", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Refactoring Java Methods with Extract Method

## Role

You are an expert in refactoring Java methods.

Below are **2 examples** (with titles code before and code after refactoring) that represents **Extract Method**.

## Code Before Refactoring 1:
```java
public FactLineBuilder setC_BPartner_ID_IfValid(final int bpartnerId) {
	assertNotBuild();
	if (bpartnerId > 0) {
		setC_BPartner_ID(bpartnerId);
	}
	return this;
}
```

## Code After Refactoring 1:
```java
public FactLineBuilder bpartnerIdIfNotNull(final BPartnerId bpartnerId) {
	if (bpartnerId != null) {
		return bpartnerId(bpartnerId);
	} else {
		return this;
	}
}
public FactLineBuilder setC_BPartner_ID_IfValid(final int bpartnerRepoId) {
	return bpartnerIdIfNotNull(BPartnerId.ofRepoIdOrNull(bpartnerRepoId));
}
```

## Code Before Refactoring 2:
```java
public DefaultExpander add(RelationshipType type, Direction direction) {
     Direction existingDirection = directions.get(type.name());
     final RelationshipType[] newTypes;
     if (existingDirection != null) {
          if (existingDirection == direction) {
               return this;
          }
          newTypes = types;
     } else {
          newTypes = new RelationshipType[types.length + 1];
          System.arraycopy(types, 0, newTypes, 0, types.length);
          newTypes[types.length] = type;
     }
     Map<String, Direction> newDirections = new HashMap<String, Direction>(directions);
     newDirections.put(type.name(), direction);
     return new DefaultExpander(newTypes, newDirections);
}
```

## Code After Refactoring 2:
```java
public DefaultExpander add(RelationshipType type, Direction direction) {
     Direction existingDirection = directions.get(type.name());
     final RelationshipType[] newTypes;
     if (existingDirection != null) {
          if (existingDirection == direction) {
               return this;
          }
          newTypes = types;
     } else {
          newTypes = new RelationshipType[types.length + 1];
          System.arraycopy(types, 0, newTypes, 0, types.length);
          newTypes[types.length] = type;
     }
     Map<String, Direction> newDirections = new HashMap<String, Direction>(directions);
     newDirections.put(type.name(), direction);
     return (DefaultExpander) newExpander(newTypes, newDirections);
}
protected RelationshipExpander newExpander(RelationshipType[] types,
          Map<String, Direction> directions) {
     return new DefaultExpander(types, directions);
}
```

## Task

Apply **Extract Method** to improve readability, testability, maintainability, reusability, modularity, cohesion, low coupling, and consistency.

Always return a complete and compilable method (Java 17).

Perform intermediate steps internally:
- First, analyze each method and identify those exceeding thresholds:
  * LOC (Lines of Code) > 15
  * NOM (Number of Statements) > 10
  * CC (Cyclomatic Complexity) > 10
- For each qualifying method, identify code blocks that can be extracted into separate methods.
- Extract at least one new method with a descriptive name.
- Output only the refactored code inside a single ```java``` block.
- Do not remove any functionality from the original method.
- Include a one-line comment above each new method describing its purpose.

## Code to be Refactored:

Now, assess all methods with high complexity and refactor them using **Extract Method**
