"""Backward compatibility shim — re-exports check_kyc."""
from pipeline.nodes.check_kyc import check_kyc as node3a_identity

__all__ = ["node3a_identity"]
