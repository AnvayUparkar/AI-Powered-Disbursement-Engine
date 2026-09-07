"""Backward compatibility shim — re-exports check_financial."""
from pipeline.nodes.check_financial import check_financial as node3b_financial

__all__ = ["node3b_financial"]
