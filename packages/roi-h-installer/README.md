# ROI-H Installer

This Python 3.12 package owns the user-local ROI-H install, update, rollback, repair, and
uninstall transactions.

Its public interface is:

```python
plan(request) -> InstallPlan
apply(plan) -> InstallResult
inspect() -> InstallationState
```

The first implementation slice supports read-only installation inspection and planning
an initial install from a local trusted release description.
