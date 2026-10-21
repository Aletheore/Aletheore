---
name: mobile-android-design
description: Master Material Design 3 and Jetpack Compose patterns for building native Android apps. Use when designing Android interfaces, implementing Compose UI, or following Google's Material Design guidelines.
skill_id: mobile/mobile-android-design
title: Mobile Android Design
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/ui-design/skills/mobile-android-design
license: MIT
shelf: build
aletheore: {"id": "mobile/mobile-android-design", "shelf": "build", "category": "mobile", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/ui-design/skills/mobile-android-design", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 4, "curation_note": "android/ios design", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 4, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": ["Codex"], "mentions": ["claude", "agents"], "adapted": {}, "status": "ready"}
---

# Android Mobile Design

Master Material Design 3 (Material You) and Jetpack Compose to build modern, adaptive Android applications that integrate seamlessly with the Android ecosystem.

## When to Use This Skill

- Designing Android app interfaces following Material Design 3
- Building Jetpack Compose UI and layouts
- Implementing Android navigation patterns (Navigation Compose)
- Creating adaptive layouts for phones, tablets, and foldables
- Using Material 3 theming with dynamic colors
- Building accessible Android interfaces
- Implementing Android-specific gestures and interactions
- Designing for different screen configurations

## Detailed section: Core Concepts

Originally a 9201-byte section in this SKILL.md. Moved to `references/details.md` to fit Codex's 8 KB skill body cap.

## Quick Start Component

```kotlin
@Composable
fun ItemListCard(
    item: Item,
    onItemClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Card(
        onClick = onItemClick,
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp)
    ) {
        Row(
            modifier = Modifier
                .padding(16.dp)
                .fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(48.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.Default.Star,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onPrimaryContainer
                )
            }

            Spacer(modifier = Modifier.width(16.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.title,
                    style = MaterialTheme.typography.titleMedium
                )
                Text(
                    text = item.subtitle,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            Icon(
                imageVector = Icons.Default.ChevronRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
```

## Best Practices

1. **Use Material Theme**: Access colors via `MaterialTheme.colorScheme` for automatic dark mode support
2. **Support Dynamic Color**: Enable dynamic color on Android 12+ for personalization
3. **Adaptive Layouts**: Use `WindowSizeClass` for responsive designs
4. **Content Descriptions**: Add `contentDescription` to all interactive elements
5. **Touch Targets**: Minimum 48dp touch targets for accessibility
6. **State Hoisting**: Hoist state to make components reusable and testable
7. **Remember Properly**: Use `remember` and `rememberSaveable` appropriately
8. **Preview Annotations**: Add `@Preview` with different configurations

## Common Issues

- **Recomposition Issues**: Avoid passing unstable lambdas; use `remember`
- **State Loss**: Use `rememberSaveable` for configuration changes
- **Performance**: Use `LazyColumn` instead of `Column` for long lists
- **Theme Leaks**: Ensure `MaterialTheme` wraps all composables
- **Navigation Crashes**: Handle back press and deep links properly
- **Memory Leaks**: Cancel coroutines in `DisposableEffect`
