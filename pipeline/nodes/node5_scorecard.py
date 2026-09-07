"""Backward compatibility shim — re-exports generate_scorecard."""
from pipeline.nodes.generate_scorecard import generate_scorecard as node5_scorecard

__all__ = ["node5_scorecard"]
