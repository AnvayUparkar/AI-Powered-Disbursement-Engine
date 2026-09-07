"""Backward compatibility shim — re-exports fetch_los and fetch_dms."""
from pipeline.nodes.fetch_dms import fetch_dms
from pipeline.nodes.fetch_los import fetch_los


def node1_fetch(state):
    state = fetch_los(state)
    return fetch_dms(state)
