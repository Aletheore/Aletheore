---
name: rust-async-patterns
description: Master Rust async programming with Tokio, async traits, error handling, and concurrent patterns. Use when building async Rust applications, implementing concurrent systems, or debugging async code.
skill_id: backend/rust-async-patterns
title: Rust Async Patterns
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/systems-programming/skills/rust-async-patterns
license: MIT
shelf: build
aletheore: {"id": "backend/rust-async-patterns", "shelf": "build", "category": "backend", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/systems-programming/skills/rust-async-patterns", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 4, "curation_note": "Rust async/tokio patterns; tag rust", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Rust Async Patterns

Production patterns for async Rust programming with Tokio runtime, including tasks, channels, streams, and error handling.

## When to Use This Skill

- Building async Rust applications
- Implementing concurrent network services
- Using Tokio for async I/O
- Handling async errors properly
- Debugging async code issues
- Optimizing async performance

## Core Concepts

### 1. Async Execution Model

```
Future (lazy) → poll() → Ready(value) | Pending
                ↑           ↓
              Waker ← Runtime schedules
```

### 2. Key Abstractions

| Concept    | Purpose                                  |
| ---------- | ---------------------------------------- |
| `Future`   | Lazy computation that may complete later |
| `async fn` | Function returning impl Future           |
| `await`    | Suspend until future completes           |
| `Task`     | Spawned future running concurrently      |
| `Runtime`  | Executor that polls futures              |

## Quick Start

```toml
# Cargo.toml
[dependencies]
tokio = { version = "1", features = ["full"] }
futures = "0.3"
async-trait = "0.1"
anyhow = "1.0"
tracing = "0.1"
tracing-subscriber = "0.3"
```

```rust
use tokio::time::{sleep, Duration};
use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt::init();

    // Async operations
    let result = fetch_data("https://api.example.com").await?;
    println!("Got: {}", result);

    Ok(())
}

async fn fetch_data(url: &str) -> Result<String> {
    // Simulated async operation
    sleep(Duration::from_millis(100)).await;
    Ok(format!("Data from {}", url))
}
```

## Detailed patterns and worked examples

Detailed pattern documentation lives in `references/details.md`. Read that file when the navigation tier above is insufficient.

## Best Practices

### Do's

- **Use `tokio::select!`** - For racing futures
- **Prefer channels** - Over shared state when possible
- **Use `JoinSet`** - For managing multiple tasks
- **Instrument with tracing** - For debugging async code
- **Handle cancellation** - Check `CancellationToken`

### Don'ts

- **Don't block** - Never use `std::thread::sleep` in async
- **Don't hold locks across awaits** - Causes deadlocks
- **Don't spawn unboundedly** - Use semaphores for limits
- **Don't ignore errors** - Propagate with `?` or log
- **Don't forget Send bounds** - For spawned futures
