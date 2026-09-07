"""Backward compatibility shim — re-exports compile_report."""
from pipeline.nodes.compile_report import compile_report as node4_compile

__all__ = ["node4_compile"]
