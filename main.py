import argparse
from kubernetes import client, config
from datetime import datetime
import pytz
from kubernetes.client import V1Pod
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def load_k8s_config():
    try:
        config.load_kube_config()
        c = client.Configuration.get_default_copy()
        c.verify_ssl = False  # Skip TLS verification if needed
        client.Configuration.set_default(c)
    except Exception as e:
        print(f"Failed to load kubeconfig: {e}")
        exit(1)

def parse_resource(val):
    if val is None:
        return 0
    if val.endswith("m"):
        return int(val[:-1]) / 1000
    elif val.endswith("Ki"):
        return int(val[:-2]) * 1024
    elif val.endswith("Mi"):
        return int(val[:-2]) * 1024 * 1024
    elif val.endswith("Gi"):
        return int(val[:-2]) * 1024 * 1024 * 1024
    else:
        try:
            return float(val)
        except:
            return 0

def get_pod_requests(pod_spec):
    total_cpu = 0
    total_mem = 0
    for container in pod_spec.containers:
        req = container.resources.requests or {}
        total_cpu += parse_resource(req.get("cpu", "0"))
        total_mem += parse_resource(req.get("memory", "0"))
    return {"cpu": total_cpu, "memory": total_mem}

def tolerates_taint(tolerations, taint):
    for t in tolerations:
        if t.key != taint.key:
            continue
        if t.effect and t.effect != taint.effect:
            continue
        operator = t.operator or "Equal"
        if operator == "Exists":
            return True
        elif operator == "Equal":
            if t.value == taint.value:
                return True
    return False

def check_node_affinity(node, node_affinity):
    if not node_affinity or not node_affinity.required_during_scheduling_ignored_during_execution:
        return True

    node_labels = node.metadata.labels or {}
    for term in node_affinity.required_during_scheduling_ignored_during_execution.node_selector_terms:
        match = True
        for expr in term.match_expressions:
            key = expr.key
            values = expr.values or []
            operator = expr.operator
            if operator == "In" and node_labels.get(key) not in values:
                match = False
            elif operator == "NotIn" and node_labels.get(key) in values:
                match = False
            elif operator == "Exists" and key not in node_labels:
                match = False
            elif operator == "DoesNotExist" and key in node_labels:
                match = False
        if match:
            return True
    return False

def check_pod_affinity(pod, node, pod_affinity):
    if not pod_affinity:
        return True

    node_name = node.metadata.name
    for term in pod_affinity.required_during_scheduling_ignored_during_execution:
        if term.label_selector and term.label_selector.match_labels:
            if term.topology_key == "kubernetes.io/hostname":
                if term.label_selector.match_labels.get("app") != pod.metadata.labels.get("app"):
                    return False
    return True

def check_pod_antiaffinity(pod, node, pod_antiaffinity, all_pods_on_node):
    if not pod_antiaffinity:
        return True

    node_name = node.metadata.name

    for term in pod_antiaffinity.required_during_scheduling_ignored_during_execution:
        selector = term.label_selector
        if term.topology_key != "kubernetes.io/hostname":
            continue  # this script only supports hostname-level anti-affinity

        if selector and selector.match_labels:
            for existing_pod in all_pods_on_node:
                if all(existing_pod.metadata.labels.get(k) == v for k, v in selector.match_labels.items()):
                    return False
    return True
def get_anti_affinity_hostnames(pod_name: str, namespace: str) -> list:

    v1 = client.CoreV1Api()

    try:
        pod: V1Pod = v1.read_namespaced_pod(pod_name, namespace)
    except client.exceptions.ApiException as e:
        print(f"Failed to fetch pod {pod_name}: {e}")
        return []

    affinity = pod.spec.affinity
    if not affinity or not affinity.pod_anti_affinity:
        return []

    anti_affinity_terms = affinity.pod_anti_affinity.required_during_scheduling_ignored_during_execution
    if not anti_affinity_terms:
        return []

    try:
        all_pods = v1.list_namespaced_pod(namespace).items
    except client.exceptions.ApiException as e:
        print(f"Failed to list pods in namespace {namespace}: {e}")
        return []

    matching_hostnames = set()

    for term in anti_affinity_terms:
        label_selector = term.label_selector
        if not label_selector:
            continue

        match_labels = label_selector.match_labels or {}
        match_expressions = label_selector.match_expressions or []

        for other_pod in all_pods:
            if other_pod.metadata.uid == pod.metadata.uid:
                continue  # skip self

            pod_labels = other_pod.metadata.labels or {}
            match = True

            # matchLabels
            for k, v in match_labels.items():
                if pod_labels.get(k) != v:
                    match = False
                    break

            # matchExpressions
            if match:
                for expr in match_expressions:
                    key = expr.key
                    values = expr.values or []
                    operator = expr.operator

                    if operator == "In":
                        if pod_labels.get(key) not in values:
                            match = False
                            break
                    elif operator == "NotIn":
                        if pod_labels.get(key) in values:
                            match = False
                            break
                    elif operator == "Exists":
                        if key not in pod_labels:
                            match = False
                            break
                    elif operator == "DoesNotExist":
                        if key in pod_labels:
                            match = False
                            break

            if match and other_pod.spec.node_name:
                matching_hostnames.add(other_pod.spec.node_name)

    return list(matching_hostnames)

def explain_why_pod_cannot_schedule(pod, nodes, namespace):
    pod_spec = pod.spec
    tolerations = pod_spec.tolerations or []
    node_selector = pod_spec.node_selector or {}
    pod_requests = get_pod_requests(pod_spec)

    node_affinity = pod_spec.affinity.node_affinity if pod_spec.affinity else None
    pod_affinity = pod_spec.affinity.pod_affinity if pod_spec.affinity else None

    anti_affinity_host_name = get_anti_affinity_hostnames(pod.metadata.name, namespace)
    results = []

    for node in nodes:
        node_name = node.metadata.name
        reason_list = []

        # 1. NodeSelector mismatch
        node_labels = node.metadata.labels or {}
        for key, val in node_selector.items():
            if node_labels.get(key) != val:
                reason_list.append(f"NodeSelectorMismatch({key}={val})")

        # 2. Node readiness
        conditions = {c.type: c.status for c in node.status.conditions}
        if conditions.get("Ready") != "True":
            reason_list.append("NodeNotReady")

        # 3. NodeAffinity mismatch
        if not check_node_affinity(node, node_affinity):
            reason_list.append("NodeAffinityMismatch")

        # 4. PodAffinity mismatch
        if pod_affinity and not check_pod_affinity(pod, node, pod_affinity):
            reason_list.append("PodAffinityMismatch")

        # 5. PodAntiAffinity mismatch 
        if node_name in anti_affinity_host_name:
            reason_list.append("PodAntiAffinityMismatch")

        # 6. Taints
        taints = node.spec.taints or []
        for taint in taints:
            if taint.effect in ["NoSchedule", "NoExecute"] and not tolerates_taint(tolerations, taint):
                reason_list.append(f"UntoleratedTaint({taint.key}={taint.value})")

        # 7. Resource availability
        allocatable = node.status.allocatable
        cpu = parse_resource(allocatable.get("cpu", "0"))
        mem = parse_resource(allocatable.get("memory", "0"))

        if pod_requests["cpu"] > cpu:
            reason_list.append("InsufficientCPU")
        if pod_requests["memory"] > mem:
            reason_list.append("InsufficientMemory")

        if not reason_list:
            reason_list = ["✓ Schedulable"]
        results.append((node_name, reason_list))

    return results

def main(pod_name, namespace):
    load_k8s_config()
    v1 = client.CoreV1Api()

    try:
        pod = v1.read_namespaced_pod(pod_name, namespace)
    except client.exceptions.ApiException as e:
        print(f"Error retrieving pod: {e}")
        exit(1)

    pod_nodeSelector = v1.read_namespaced_pod(name=pod_name, namespace=namespace).spec.node_selector
    # print(v1.list_node().items)
    # nodes = v1.list_node(label_selector=pod_nodeSelector).items
    nodes = v1.list_node().items
    
    results = explain_why_pod_cannot_schedule(pod, nodes, namespace)

    print(f"\nScheduling Analysis for Pod: {pod_name} (Namespace: {namespace})\n")
    print(f"{'NODE':<40} REASONS")
    print("=" * 80)
    for node_name, reasons in results:
        print(f"{node_name:<40} {', '.join(reasons)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explain why a pod cannot be scheduled on each node.")
    parser.add_argument("pod", help="Pod name")
    parser.add_argument("-n", "--namespace", required=True, help="Namespace of the pod")
    args = parser.parse_args()
    main(args.pod, args.namespace)
