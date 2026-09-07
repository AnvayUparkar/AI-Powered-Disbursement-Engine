"""Backward compatibility shim — re-exports idp_scan and llm_structure."""
from pipeline.nodes.idp_scan import idp_scan
from pipeline.nodes.llm_structure import llm_structure


def node2_extract(state):
    state = idp_scan(state)
    return llm_structure(state)
