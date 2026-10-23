---
name: google-cloud-waf-operational-excellence
metadata:
  version: 1.1.0
  category: WellArchitectedFramework
description: Generates operational guidance for CI/CD deployment pipelines, observability, monitoring, logging, and incident management on Google Cloud based on the design principles and recommendations in the Operational Excellence pillar of the Google Cloud Well-Architected Framework (WAF). Use when the user asks about operations-related topics like capacity management, change management, incident and problem management, configuration management, deployment automation using infrastructure as code (IaC), or operational health checks for Google Cloud workloads.
skill_id: platforms/google-cloud-waf-operational-excellence
title: Google Cloud WAF Operational Excellence
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-waf-operational-excellence
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-cloud-waf-operational-excellence", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-waf-operational-excellence", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Google Cloud Well-Architected Framework skill for the Operational Excellence pillar

## Overview

The operational excellence pillar in the Google Cloud Well-Architected Framework
provides recommendations to operate workloads efficiently on Google Cloud.
Operational excellence in the cloud involves designing, implementing, and
managing cloud solutions that provide value, performance, security, and
reliability. The recommendations in this pillar help you to continuously improve
and adapt workloads to meet the dynamic and ever-evolving needs in the cloud.

## Core principles

The recommendations in the operational excellence pillar of the Well-Architected
Framework are aligned with the following core principles:

-   **Ensure operational readiness**: Define and measure criteria for a workload
    to be considered ready for production across staffing, processes, tools,
    and governance. Grounding document:
    https://docs.cloud.google.com/architecture/framework/operational-excellence/operational-readiness-and-performance-using-cloudops.md.txt

-   **Manage incidents and problems**: Establish structured processes for
    incident response, communication, and root cause analysis to minimize impact
    and prevent recurrence. Grounding document:
    https://docs.cloud.google.com/architecture/framework/operational-excellence/manage-incidents-and-problems.md.txt

-   **Manage and optimize cloud resources**: Monitor resource utilization and
    right-size environments to maintain performance while ensuring operational
    efficiency. Grounding document:
    https://docs.cloud.google.com/architecture/framework/operational-excellence/manage-and-optimize-cloud-resources.md.txt

-   **Automate and manage change**: Use Infrastructure as Code (IaC) and CI/CD
    pipelines to ensure consistent, repeatable, and low-risk deployments and
    configuration changes. Grounding document:
    https://docs.cloud.google.com/architecture/framework/operational-excellence/automate-and-manage-change.md.txt

-   **Continuously improve and innovate**: Adopt a flexible and agile approach
    to cloud operations, foster a culture of continuous improvement, and
    periodically review your cloud architecture against current business
    goals, technological advancements, and industry trends. Grounding document:
    https://docs.cloud.google.com/architecture/framework/operational-excellence/continuously-improve-and-innovate.md.txt

## Relevant Google Cloud products

The following are *examples* of Google Cloud products and features that are
relevant to operational excellence:

-   **Observability and monitoring**

    -   **Cloud Monitoring**: Full-stack observability for Google Cloud and
        hybrid environments.
    -   **Cloud Logging**: Real-time log management and analysis at scale.
    -   **Error Reporting**: Aggregates and displays errors for running cloud
        services.
    -   **Service Monitoring**: Tools for defining and tracking Service Level
        Objectives (SLOs).

-   **Automation and CI/CD**

    -   **Cloud Build**: Serverless platform for building, testing, and
        deploying software.
    -   **Cloud Deploy**: Managed continuous delivery service for GKE, Cloud
        Run, and GCE.
    -   **Terraform / Infrastructure Manager**: Managed service for
        Infrastructure as Code (IaC) automation.
    -   **Artifact Registry**: Central repository for managing build artifacts
        and container images.

-   **Resource management and optimization**

    -   **Recommender (Active Assist)**: Automatically identifies idle or
        over-provisioned resources and right-sizing opportunities.
    -   **Autoscaling (Compute Engine MIGs, GKE HPA/VPA, Cloud Run)**:
        Dynamically allocates resources based on demand to prevent the risks of
        both over-provisioning and under-provisioning.
    -   **Resource Manager**: Hierarchical management of resources across
        organizations, folders, and projects.

-   **Incident response**

    -   **Incident response & management (IRM)**: Structured tools and processes
        for managing operational disruptions.

## Workload assessment questions

Ask appropriate questions to understand operations-related requirements and
constraints of the workload and the user's organization. Choose questions from
the following list:

-   **Operational readiness and performance**

    -   How do you define and measure operational readiness for your cloud
        workloads and what specific criteria or metrics do you use?
    -   Describe your process for defining, tracking, and achieving SLOs for
        your critical workloads.
    -   How do you ensure that your workloads can seamlessly leverage multiple
        machine types or families for VMs during capacity constraints?
    -   What is your strategy for modernizing your cloud resources to avoid
        technical debt and availability risks?

-   **Incident and problem management**

    -   Describe your incident management process, including roles,
        responsibilities, and communication channels.
    -   How do you conduct post-incident reviews (PIRs) to identify root causes
        and implement preventive measures?

-   **Resource management and optimization**

    -   How do you ensure that your cloud resources are right-sized for your
        workloads, and what tools or techniques do you use?

-   **Change automation**

    -   Describe your change management process, including approval workflows,
        testing procedures, and deployment strategies.
    -   How do you automate deployments, ensure their consistency and manage
        configuration?

-   **Continuous improvement**

    -   How do you ensure that your cloud operations are continuously adapting
        to meet evolving business needs and technological advancements?

## Validation checklist

Use the following checklist to evaluate the architecture's alignment with
operational excellence recommendations:

-   **Operational readiness**

    -   [ ] A formal framework or set of criteria exists to assess operational
        readiness across staffing, processes, tooling, and governance before
        deployment to production.
    -   [ ] Service Level Indicators (SLIs) based on user journeys, Service
        Level Objectives (SLOs), and error budgets are explicitly defined and
        monitored using Cloud Monitoring.
    -   [ ] Workloads achieve continuous availability by using automated
        spillover mapping (via Managed Instance Groups (Flexible MIGs) with
        mixed-instance templates or GKE Custom Compute Classes) to seamlessly
        utilize cross-generation machine types across multiple zones in a
        region.
    -   [ ] Workloads follow a "Migration-by-Design" strategy that leverages
        instance flexibility and automated spillover mapping to naturally
        transition workloads to newer hardware generations (evergreen capacity),
        decouple workloads from rigid, specific machine types to avoid capacity
        constraints, and align deployments with Google Cloud's most obtainable
        physical fleet to minimize availability risks.

-   **Incident management**

    -   [ ] Incident response roles (such as an Incident Commander),
        communication channels, escalation paths, and automated response tasks
        are clearly defined and documented.
    -   [ ] A structured, blameless post-incident review (PIR) process is
        followed for all major incidents to conduct thorough root cause analysis
        and implement preventive measures.

-   **Change automation**

    -   [ ] All infrastructure changes are performed using Infrastructure as
        Code (IaC) stored in version control (using modern tools like Terraform)
        to ensure consistency and enable automated deployments.
    -   [ ] CI/CD pipelines (using Cloud Build for continuous integration and
        Cloud Deploy for continuous delivery) are integrated with automated
        testing for all deployment changes.

-   **Resource optimization**

    -   [ ] Resource utilization is regularly reviewed, and resources are
        right-sized using Recommender (Active Assist) and autoscaling to
        mitigate the risks of over-provisioning and under-provisioning.

-   **Culture of improvement**

    -   [ ] The organization uses agile operational practices, iterative
        workflows, and adaptable operating models.
    -   [ ] Leadership fosters a culture of continuous improvement,
        psychological safety, and ongoing learning.
    -   [ ] Teams periodically review their cloud architecture against business
        goals, industry trends, and technological advancements.
