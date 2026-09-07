"""Backward compatibility shim — Checker node retired in favor of node-level retries."""
def node_checker(state):
    return state

__all__ = ["node_checker"]
