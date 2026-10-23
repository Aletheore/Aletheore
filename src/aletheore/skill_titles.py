"""Display titles for bundled Hallbook skills.

A bundled skill's `title:` frontmatter line is generated from its `name`, not typed by hand, so the
rules live here where a test can hold the catalogue to them. Nothing at runtime imports this module;
it is used by the catalogue tooling and by `tests/test_skill_catalogue_quality.py`.

`humanize_title("gha-security-review")` -> "GitHub Actions Security Review". The result is only a
display string. Skill ids, names and the tokens search ranks on are untouched (search tokenises
`name` separately from `title`, and lowercases both).
"""
from __future__ import annotations

import re

# Slug word -> how it is written in a title. Anything not listed is capitalised ("debugging" -> "Debugging").
WORDS: dict[str, str] = {
    # Acronyms and initialisms
    "ab": "A/B",
    "adrs": "ADRs",
    "ai": "AI",
    "aks": "AKS",
    "api": "API",
    "apis": "APIs",
    "aws": "AWS",
    "aso": "ASO",
    "cdk": "CDK",
    "cro": "CRO",
    "dom": "DOM",
    "e2e": "E2E",
    "grpo": "GRPO",
    "hf": "HF",
    "ima": "IMA",
    "md": "MD",
    "mcpb": "MCPB",
    "okrs": "OKRs",
    "pm": "PM",
    "rlvr": "RLVR",
    "rtl": "RTL",
    "sft": "SFT",
    "sms": "SMS",
    "sre": "SRE",
    "swot": "SWOT",
    "trl": "TRL",
    "wcag": "WCAG",
    "cd": "CD",
    "ci": "CI",
    "cli": "CLI",
    "cqrs": "CQRS",
    "css": "CSS",
    "dag": "DAG",
    "db": "DB",
    "dpop": "DPoP",
    "eas": "EAS",
    "gcp": "GCP",
    "gdpr": "GDPR",
    "gke": "GKE",
    "gpu": "GPU",
    "gsap": "GSAP",
    "gtm": "GTM",
    "hpc": "HPC",
    "html": "HTML",
    "iam": "IAM",
    "ide": "IDE",
    "js": "JS",
    "json": "JSON",
    "jwt": "JWT",
    "kpi": "KPI",
    "llm": "LLM",
    "llms": "LLMs",
    "lsp": "LSP",
    "mcp": "MCP",
    "ml": "ML",
    "mvvm": "MVVM",
    "nfs": "NFS",
    "oom": "OOM",
    "orm": "ORM",
    "owasp": "OWASP",
    "pci": "PCI",
    "pdf": "PDF",
    "pr": "PR",
    "prd": "PRD",
    "qa": "QA",
    "qps": "QPS",
    "rag": "RAG",
    "sast": "SAST",
    "sdk": "SDK",
    "seo": "SEO",
    "slo": "SLO",
    "sql": "SQL",
    "tdd": "TDD",
    "tpu": "TPU",
    "ui": "UI",
    "uiux": "UI/UX",
    "ux": "UX",
    "vm": "VM",
    "waf": "WAF",
    "yaml": "YAML",
    # Proper nouns and product spellings
    "gh": "GitHub",
    "junit": "JUnit",
    "mtls": "mTLS",
    "npm": "npm",
    "nuget": "NuGet",
    "nunit": "NUnit",
    "pypi": "PyPI",
    "qlora": "QLoRA",
    "sagemaker": "SageMaker",
    "tunit": "TUnit",
    "winui": "WinUI",
    "xunit": "xUnit",
    "alloydb": "AlloyDB",
    "aspnet": "ASP.NET",
    "bigquery": "BigQuery",
    "cosmosdb": "Cosmos DB",
    "devops": "DevOps",
    "dotnet": ".NET",
    "dynamodb": "DynamoDB",
    "fastapi": "FastAPI",
    "gha": "GitHub Actions",
    "github": "GitHub",
    "gitlab": "GitLab",
    "graphql": "GraphQL",
    "grpc": "gRPC",
    "ios": "iOS",
    "javascript": "JavaScript",
    "k8s": "K8s",
    "linkedin": "LinkedIn",
    "lora": "LoRA",
    "macos": "macOS",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "nestjs": "NestJS",
    "nextjs": "Next.js",
    "nodejs": "Node.js",
    "nosql": "NoSQL",
    "oauth": "OAuth",
    "openai": "OpenAI",
    "php": "PHP",
    "pnpm": "pnpm",
    "postgresql": "PostgreSQL",
    "powershell": "PowerShell",
    "pptx": "PPTX",
    "docx": "DOCX",
    "typescript": "TypeScript",
    "wordpress": "WordPress",
}

# Joining words stay lower case inside a title ("Oracle to Postgres"), but never open one.
SMALL_WORDS = frozenset({"a", "an", "and", "as", "for", "from", "in", "of", "on", "or", "the", "to", "vs", "with"})

# Titles that cannot be derived from the name: two skills share a name (an author-suffixed folder next to
# the plain one), so the derived title would be the same for both.
TITLE_OVERRIDES: dict[str, str] = {
    "marketing/ads-agricidaniel": "Paid Ads Suite",
    "marketing/ads": "Paid Ads Campaigns",
}

# Hand-written seed skills: their titles are authored, not derived.
AUTHORED_TITLES = frozenset({"brainstorming/open-ended", "code-review/security-focused"})


def humanize_title(name: str) -> str:
    """The display title for a skill name (the kebab or snake case slug)."""
    words = [word for word in re.split(r"[-_\s]+", name.strip()) if word]
    out: list[str] = []
    for index, word in enumerate(words):
        key = word.lower()
        if key in WORDS:
            out.append(WORDS[key])
        elif key in SMALL_WORDS and index > 0:
            out.append(key)
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def expected_title(skill_id: str, name: str) -> str:
    """The title a bundled skill should carry: an override, else derived from its frontmatter name."""
    return TITLE_OVERRIDES.get(skill_id) or humanize_title(name)
