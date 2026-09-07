"""Backward compatibility shim — re-exports push_results."""
from pipeline.nodes.push_results import push_results as node6_push

__all__ = ["node6_push"]
