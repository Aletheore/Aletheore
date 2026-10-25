---
name: google-cloud-waf-sustainability
metadata:
  version: 1.0.1
  category: WellArchitectedFramework
description: Provides recommendations for environmental sustainability, carbon footprint reduction, and energy efficiency based on the Sustainability pillar of the Google Cloud Well-Architected Framework (WAF). Use when the user asks to assess, design, or optimize Google Cloud workloads for sustainability—including the shared responsibility model, selecting low-carbon regions (CFE%), reducing resource and AI/ML energy waste, designing efficient software and storage lifecycles, or measuring and tracking emissions using Google Cloud Carbon Footprint. Don't use for financial cost reduction (use google-cloud-waf-cost-optimization), latency and throughput tuning (use google-cloud-waf-performance-optimization), or high availability and disaster recovery (use google-cloud-waf-reliability).
skill_id: platforms/google-cloud-waf-sustainability
title: Google Cloud WAF Sustainability
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-waf-sustainability
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-cloud-waf-sustainability", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-waf-sustainability", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Google Cloud Well-Architected Framework skill for the Sustainability pillar

## Overview

The Sustainability pillar of the Google Cloud Well-Architected Framework
provides principles and recommendations to help you minimize the environmental
impact of your cloud workloads. It focuses on a shared responsibility
model—Google optimizes the sustainability *of* the cloud, while customers
optimize sustainability *in* the cloud. By making informed decisions about
architecture, resource allocation, and region selection, you can significantly
reduce your carbon footprint and improve overall energy efficiency.

## Core principles

The recommendations in the sustainability pillar of the Well-Architected
Framework are aligned with the following core principles:

-   **Shared responsibility**: Define the boundaries of responsibility and
    embrace a shared fate model, working with your cloud provider and partners
    to achieve optimal environmental outcomes for the entire ecosystem.
    Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability.md.txt

-   **Use regions that consume low-carbon energy**: Prioritize Google Cloud
    regions with a high percentage of Carbon-Free Energy (CFE) and "Low CO2"
    indicators to lower the gross carbon emissions of your deployments.
    Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability/low-carbon-regions.md.txt

-   **Optimize AI and ML workloads**: Maximize computations per watt by matching
    algorithmic needs to specialized hardware (like TPUs) and applying
    mathematical techniques to reduce computational complexity. Grounding
    document:
    https://docs.cloud.google.com/architecture/framework/sustainability/ai-ml-energy-efficiency.md.txt

-   **Optimize resource usage**: Eliminate energy waste by scaling resources to
    zero when idle, rightsizing virtual machines, and prioritizing managed
    services that dynamically match actual demand. Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability/optimize-resource-usage.md.txt

-   **Develop energy-efficient software**: Design your applications to minimize
    unnecessary CPU, memory, and network activity on both backend servers and
    end-user devices by using event-driven logic and optimized assets. Grounding
    document:
    https://docs.cloud.google.com/architecture/framework/sustainability/energy-efficient-software.md.txt

-   **Optimize data and storage**: Reduce the environmental footprint of your
    storage by implementing lifecycle management to archive cold data and
    eliminating "dark data" that provides no business value. Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability/optimize-storage.md.txt

-   **Continuously measure and improve**: Gain visibility into your carbon
    emissions by analyzing granular data, identifying hotspots, and taking
    proactive steps to remediate inefficiencies. Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability/continuously-measure-improve.md.txt

-   **Promote a culture of sustainability**: Embed sustainability into your
    organizational governance, connect technical decisions to environmental
    goals, and ensure staff have the skills to implement green practices.
    Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability/culture.md.txt

-   **Align sustainability practices with industry guidelines**: Ensure that
    your sustainability initiatives are aligned with industry guidelines for
    measurement, reporting, and verification, such as W3C Web Sustainability
    Guidelines, Green Software Foundation, and Greenhouse Gas Protocol.
    Grounding document:
    https://docs.cloud.google.com/architecture/framework/sustainability/industry-guidelines.md.txt

## Relevant Google Cloud products

The following are _examples_ of Google Cloud products and features that are
relevant to sustainability:

-   **Visibility and measurement**:
    -   **Carbon Footprint**: Provides dashboard visibility into greenhouse gas
        emissions associated with Google Cloud usage.
    -   **BigQuery**: Analyzes exported Carbon Footprint data alongside billing
        data to identify emission hotspots.

-   **Infrastructure and operations**:
    -   **Google Cloud Region Picker**: Helps weigh carbon footprint, cost, and
        latency when selecting deployment locations.
    -   **Active Assist / Recommender**: Automatically identifies idle resources
        and provides VM rightsizing recommendations to reduce waste.
    -   **Cloud Run / GKE Autopilot**: Fully managed compute environments that
        optimize cluster usage and can scale to zero when idle.
    -   **Cloud Batch**: Optimizes the scheduling of batch jobs, allowing
        execution during periods of high Carbon-Free Energy.
    -   **Spot VMs**: Utilizes unused data center capacity for fault-tolerant
        workloads, improving overall hardware efficiency.

-   **Data and AI**:
    -   **Cloud Storage Lifecycle Management**: Automatically transitions older
        data to lower-energy storage classes (Nearline, Coldline, Archive).
    -   **Cloud TPUs**: Specialized hardware optimized for the energy efficiency
        of large-scale AI/ML matrix multiplications.

## Workload assessment questions

Ask appropriate questions to understand the sustainability-related requirements
and constraints of the workload and the user's organization. Choose questions
from the following list:

-   **Cloud sustainability**:
    -   How do you define the boundaries of sustainability responsibility
        between your organization and your cloud provider?
    -   How do you leverage cloud capabilities and AI to drive sustainability
        outcomes for your broader business operations?
    -   How does your cloud strategy account for the sustainability impact of
        your partner ecosystem and multi-cloud environments?

-   **Use regions that consume low-carbon energy**:
    -   How do you incorporate carbon intensity into your Google Cloud region
        selection strategy?

-   **Optimize AI and ML workloads**:
    -   How do you optimize the energy efficiency of your AI and machine
        learning lifecycles?

-   **Optimize resource usage**:
    -   How do you ensure your infrastructure footprint dynamically matches
        actual workload demand?
    -   How do you select and maintain the hardware types used for your cloud
        workloads?
    -   What is your strategy for handling non-urgent or compute-intensive
        background tasks?
    -   How do you balance the need for high availability and disaster recovery
        with sustainability?

-   **Develop energy-efficient software**:
    -   How do you ensure your backend logic minimizes unnecessary CPU, memory,
        and network activity?
    -   How do you manage the overall efficiency and maintenance of your
        codebase for sustainability?
    -   How do you minimize the data volume and processing load that your
        application places on end-user devices?
    -   How does your user experience (UX) design contribute to energy
        efficiency for the end user?

-   **Optimize data and storage**:
    -   What process do you have for managing the environmental footprint of
        your data and storage?

-   **Continuously measure and improve**:
    -   How do you analyze your carbon data to prioritize optimization efforts?
    -   How is sustainability measurement embedded into your organization’s
        governance and culture?
    -   What is your current process for gaining visibility into your
        cloud-related carbon emissions?
    -   What proactive steps do you take to remediate identified carbon
        hotspots?

-   **Promote a culture of sustainability**:
    -   How do you connect individual technical decisions to the organization's
        mission and hold teams accountable for results?
    -   How do you ensure your technical and business staff have the specific
        skills required to implement sustainability practices?

## Validation checklist

Use the following checklist to evaluate the architecture's alignment with
sustainability recommendations:

-   **Cloud sustainability**:
    -   [ ] The organization embraces a shared responsibility and shared fate
        model for sustainability.
    -   [ ] AI is used as a catalyst for profitability and resilience to
        streamline operations, or sustainability is integrated into the design
        process to create positive feedback loops.
    -   [ ] Collaborations with sustainable partners are prioritized and
        multi-cloud data portability is leveraged, or internal practices align
        with recognized global standards like the Green Software Foundation.

-   **Use regions that consume low-carbon energy**:
    -   [ ] A data-driven policy prioritizes regions with high Carbon-Free
        Energy (CFE%) and "Low CO2" indicators, or the Google Cloud Region
        Picker is actively used to balance carbon footprint with cost and
        latency.

-   **Optimize AI and ML workloads**:
    -   [ ] Algorithmic needs are matched to specialized hardware (TPUs) to
        maximize computations per watt, or mathematical techniques like model
        compression and PEFT are applied to reduce computational complexity.

-   **Optimize resource usage**:
    -   [ ] Fully managed services that scale to zero when idle are utilized, or
        Horizontal Pod Autoscaling (HPA) and Vertical Pod Autoscaling (VPA) are
        used in GKE to prevent over-provisioning.
    -   [ ] A formal process exists to upgrade to the newest machine types for
        improved performance-per-watt, or workloads are actively matched to
        specialized machine families.
    -   [ ] Batch jobs are proactively scheduled to run during periods or in
        regions with the highest proportion of CFE, or Spot VMs are utilized for
        non-critical batch jobs.
    -   [ ] "Cold DR" or serverless failover is prioritized to ensure secondary
        regions remain at zero energy consumption until an event occurs, or
        Infrastructure as Code (IaC) is used to rapidly provision a recovery
        environment only when needed.

-   **Develop energy-efficient software**:
    -   [ ] Resource-intensive busy loops or constant polling are replaced with
        event-driven logic, or algorithms with optimal time complexity and data
        structures are prioritized.
    -   [ ] The "Don't Repeat Yourself" (DRY) principle is adhered to with
        regular refactoring, or intelligent caching (e.g., Memorystore) is
        implemented with smart eviction policies.
    -   [ ] The download size of website products is measured and maintained
        against a strict budget, or CI/CD pipelines automate the minimization
        and compression of HTML, CSS, and JS files.
    -   [ ] Static sites or Progressive Web Apps (PWAs) are preferred for faster
        loading, or DOM manipulation is minimized to reduce device power
        consumption.

-   **Optimize data and storage**:
    -   [ ] Object Lifecycle Management is used to automatically move cold data
        to Archive storage, or discovery techniques (e.g., Dataplex) are used to
        identify and eliminate "dark data".

-   **Continuously measure and improve**:
    -   [ ] Carbon data is analyzed by project, region, and service to identify
        gross emitters, or carbon data is joined with Billing data in BigQuery
        to correlate cost and environmental impact.
    -   [ ] A formal GreenOps function defines accountability for carbon
        reduction targets, or verified Carbon Footprint data from BigQuery
        supports formal ESG disclosures.
    -   [ ] Applications are instrumented to measure the specific carbon
        intensity of software features, or automated exports of Carbon Footprint
        data to BigQuery are configured for deep analysis.
    -   [ ] The unattended project recommender and Active Assist are regularly
        used to decommission idle resources, or proactive projects re-architect
        hotspots by shifting workloads to low-carbon regions.

-   **Promote a culture of sustainability**:
    -   [ ] Abstract carbon metrics are transformed into tangible progress
        indicators in annual reports, or sustainability is treated as a
        first-class technical requirement (NFR) tied to KPIs and performance
        reviews.
    -   [ ] Training tailored to specific job roles (e.g., developers on code
        efficiency, FinOps on carbon unit economics) is provided, or teams are
        formally trained to access and interpret carbon footprint data.
