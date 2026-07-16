"""WS2 Azure validation suites.

Deterministic tests in this package run in the default suite. Target-host
integration tests are opt-in: they skip unless ``AIMMS_AZURE_INTEGRATION=1``
is set, and must only be run from the approved hosting environment whose
managed identity carries the pilot roles. They never print, log, or persist
tokens or other secrets.
"""
