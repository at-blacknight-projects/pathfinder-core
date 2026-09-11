## [0.1.0-alpha.7](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.6...v0.1.0-alpha.7) (2026-09-11)


### Features

* pfc_logs owns rotation; startup script parsed as raw commands ([1c17d81](https://github.com/at-blacknight-projects/pathfinder-core/commit/1c17d818c67456ebf2f92fc346dace06cce3d568)), closes [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [LogRotator#0](https://github.com/at-blacknight-projects/LogRotator/issues/0) [.RotateRule#0](https://github.com/at-blacknight-projects/.RotateRule/issues/0) [LogRotator#0](https://github.com/at-blacknight-projects/LogRotator/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0)

## [0.1.0-alpha.6](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.5...v0.1.0-alpha.6) (2026-09-11)


### Features

* **logs,advanced:** return a diff so --check --diff shows the change ([74ec80d](https://github.com/at-blacknight-projects/pathfinder-core/commit/74ec80d809e39f8e31f698c3f376d1829e0b4372))


### Bug Fixes

* **advanced:** changes are runtime-only; the startup script wins at reboot ([cbf6b3a](https://github.com/at-blacknight-projects/pathfinder-core/commit/cbf6b3aba4ace27e7cc9a9028c588b912288dbfc)), closes [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [.LogRotator#0](https://github.com/at-blacknight-projects/.LogRotator/issues/0) [.RotateRule#0](https://github.com/at-blacknight-projects/.RotateRule/issues/0)

## [0.1.0-alpha.5](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.4...v0.1.0-alpha.5) (2026-09-11)


### Bug Fixes

* **sapv2:** stop replies being attributed to the wrong command ([69366b4](https://github.com/at-blacknight-projects/pathfinder-core/commit/69366b48967d3ccb35b14d6976d6e648ee4b5668)), closes [System#0](https://github.com/at-blacknight-projects/System/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0)

## [0.1.0-alpha.4](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.3...v0.1.0-alpha.4) (2026-09-11)


### Bug Fixes

* **advanced:** report durability as observed, measured across a real reboot ([bc17452](https://github.com/at-blacknight-projects/pathfinder-core/commit/bc174523781e1a16eb8e53abb78de5cdfa227391)), closes [System#0](https://github.com/at-blacknight-projects/System/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0)

## [0.1.0-alpha.3](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.2...v0.1.0-alpha.3) (2026-09-11)


### Features

* **advanced:** manage Advanced options, including all log rotation knobs ([f860ed9](https://github.com/at-blacknight-projects/pathfinder-core/commit/f860ed90a98a2160bd497d2b3f1ccacd220cd9fb)), closes [RotateRule#0](https://github.com/at-blacknight-projects/RotateRule/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [.LogRotator#0](https://github.com/at-blacknight-projects/.LogRotator/issues/0) [.RotateRule#0](https://github.com/at-blacknight-projects/.RotateRule/issues/0) [LogicFlows#0](https://github.com/at-blacknight-projects/LogicFlows/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0) [Routers#0](https://github.com/at-blacknight-projects/Routers/issues/0) [LogicFlows#0](https://github.com/at-blacknight-projects/LogicFlows/issues/0) [RotateRule#0](https://github.com/at-blacknight-projects/RotateRule/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0) [.LogRotator#0](https://github.com/at-blacknight-projects/.LogRotator/issues/0) [.RotateRule#0](https://github.com/at-blacknight-projects/.RotateRule/issues/0) [LogicFlows#0](https://github.com/at-blacknight-projects/LogicFlows/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0) [Routers#0](https://github.com/at-blacknight-projects/Routers/issues/0) [LogicFlows#0](https://github.com/at-blacknight-projects/LogicFlows/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0) [Devices#0](https://github.com/at-blacknight-projects/Devices/issues/0)
* **logs:** support all four writer types, not just UDP syslog ([7b4ebd9](https://github.com/at-blacknight-projects/pathfinder-core/commit/7b4ebd96385bcde41e5b0d921e8232b86aa4bb50)), closes [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0)

## [0.1.0-alpha.2](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.1...v0.1.0-alpha.2) (2026-09-11)


### Bug Fixes

* **sapv2:** detect an inert session instead of trusting a silent login ([0ca041e](https://github.com/at-blacknight-projects/pathfinder-core/commit/0ca041ea69f8b734d53abb1e3524eeff7fe1db46)), closes [System#0](https://github.com/at-blacknight-projects/System/issues/0)

## [0.1.0-alpha.1](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.0.0...v0.1.0-alpha.1) (2026-09-11)


### Features

* **pathfinder_core:** SapV2 client, boundary registry and pfc_logs reconciler ([487f4fb](https://github.com/at-blacknight-projects/pathfinder-core/commit/487f4fb0c2fb512e9907ece512b91064c3e30335)), closes [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0)


### Bug Fixes

* **docs:** repair invalid DOCUMENTATION YAML and add CI ([b3ffc4a](https://github.com/at-blacknight-projects/pathfinder-core/commit/b3ffc4a347b1383161650ef7116e30d5afdfa0b9))
