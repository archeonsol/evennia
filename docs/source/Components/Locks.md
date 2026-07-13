# Locks (removed)

The runtime lockstring evaluator was removed in R3F. This page remains only so
older documentation links have a useful destination.

Use [Capability Authorization](./Authorization.md). There is no `LockHandler`,
lock-function registry, permission hierarchy, or superuser bypass in the live
engine. Existing databases must be converted with the offline import and
deployment finalization commands before starting a capability-only release.
