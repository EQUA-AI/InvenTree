"""Right-Part Finder (Feature #13) deterministic part verification package.

This package owns the part verification aggregate behavior: typed requirement
schema, immutable policy, fail-closed scope, source adapters, bounded candidate
retrieval, hard compatibility evaluation, survivor ranking, advisory
availability, staleness revalidation, and transactional command services.

Persistence lives in part.verification_models; nothing in this package writes
catalog, asset, work, order, approval, or stock state.
"""
