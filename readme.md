# 🔍 Kubernetes Pod Scheduling Analyzer

This tool helps **debug and explain why a specific Kubernetes Pod is not schedulable** on any available nodes in your cluster. It evaluates various scheduling constraints such as node selectors, affinity rules, taints, tolerations, and resource availability.

## 📦 Features

- Explains scheduling failures per node with reasons like:
  - `NodeSelectorMismatch`
  - `NodeAffinityMismatch`
  - `PodAffinityMismatch`
  - `PodAntiAffinityMismatch`
  - `UntoleratedTaint`
  - `InsufficientCPU` or `InsufficientMemory`
  - `NodeNotReady`
- Skips SSL verification to avoid cluster cert issues (customizable).
- Uses the official Kubernetes Python client.

## 📋 Prerequisites

- Python 3.6+
- Access to a Kubernetes cluster (via `~/.kube/config`)
- Required Python packages:
  ```bash
  pip install kubernetes pytz urllib3
