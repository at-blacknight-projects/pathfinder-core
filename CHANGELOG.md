# Changelog

## 0.1.0 (unreleased)

Initial release.

- `pfc_logs` — reconcile `Logs#0`: UDP syslog writers, log subscriptions and
  MessageLogSettings, with read-back verification on every change.
- `pfc_survey` — read-only schema introspection via `rfs` and the
  `Constructor` hidden property.
- `pfc_users`, `pfc_access`, `pfc_devices` — documented stubs that fail loudly,
  carrying the measured design constraints for each subtree.
- Shared SapV2 client and an enforced, default-deny runtime/config boundary.
