## [0.1.0-alpha.12](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.11...v0.1.0-alpha.12) (2026-09-13)


### Features

* drop the pfc_ prefix from module names ([4c5e607](https://github.com/at-blacknight-projects/pathfinder-core/commit/4c5e607c92f0f2f95ba57730f4258c9e30f2b024))


### Bug Fixes

* **logs:** report every unmanaged writer, purge only the managed types ([0f9e41b](https://github.com/at-blacknight-projects/pathfinder-core/commit/0f9e41bd20973f540a50e507845b4d42b5679ec8))

## [0.1.0-alpha.11](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.10...v0.1.0-alpha.11) (2026-09-13)


### Features

* **logs:** decide what happens to writers nobody named ([ea5351b](https://github.com/at-blacknight-projects/pathfinder-core/commit/ea5351bc9b1f1d0ac319ad804993c2511357d317))


### Bug Fixes

* **logs:** manage SkipWebClientSapMessages, refuse Logs[#0](https://github.com/at-blacknight-projects/pathfinder-core/issues/0).Ready ([29c704c](https://github.com/at-blacknight-projects/pathfinder-core/commit/29c704cf7104613b7ea02b6dccfc5584cb557cf4))
* **logs:** tcp_client IS creatable - autoReconnect was the missing parameter ([7d62a31](https://github.com/at-blacknight-projects/pathfinder-core/commit/7d62a31c4c28ae4c14b9d8f1b6b0f40197e91e23))

## [0.1.0-alpha.10](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.9...v0.1.0-alpha.10) (2026-09-12)


### Features

* **logs:** reconcile every writer on a device in one task ([fff2310](https://github.com/at-blacknight-projects/pathfinder-core/commit/fff231046a9def161e0f698b18b3718113d7a231))


### Performance

* **sapv2:** terminate reads with $DONE and read subtrees in one command ([14c67fe](https://github.com/at-blacknight-projects/pathfinder-core/commit/14c67fe1b594c1f44437c99e4a8d845faf5bbaa4)), closes [MessageLogSettings#0](https://github.com/at-blacknight-projects/MessageLogSettings/issues/0)


### Documentation

* use neutral example addresses throughout ([e3087b3](https://github.com/at-blacknight-projects/pathfinder-core/commit/e3087b3ae03b4ab0bbcee5fec3e55369cb747599))

## [0.1.0-alpha.9](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.8...v0.1.0-alpha.9) (2026-09-11)


### Features

* replace pfc_advanced_options with pfc_startup_script ([e0681f3](https://github.com/at-blacknight-projects/pathfinder-core/commit/e0681f32e0cfb207586d64d5165ee9ee18892b4a))


### Documentation

* confirm no startup-script object exists, and record four surveyed roots ([10fb15a](https://github.com/at-blacknight-projects/pathfinder-core/commit/10fb15aa94b024156390c4c581dec4d764b7acd6)), closes [LegacyPanels#0](https://github.com/at-blacknight-projects/LegacyPanels/issues/0) [Meters#0](https://github.com/at-blacknight-projects/Meters/issues/0) [Requests#0](https://github.com/at-blacknight-projects/Requests/issues/0) [UpdateModerators#0](https://github.com/at-blacknight-projects/UpdateModerators/issues/0) [System#0](https://github.com/at-blacknight-projects/System/issues/0) [.Validation#0](https://github.com/at-blacknight-projects/.Validation/issues/0) [System#0](https://github.com/at-blacknight-projects/System/issues/0) [.SupportLink#0](https://github.com/at-blacknight-projects/.SupportLink/issues/0) [Meters#0](https://github.com/at-blacknight-projects/Meters/issues/0)

## [0.1.0-alpha.8](https://github.com/at-blacknight-projects/pathfinder-core/compare/v0.1.0-alpha.7...v0.1.0-alpha.8) (2026-09-11)


### Bug Fixes

* **advanced:** audit the whole startup script, not just modelled options ([8c9e322](https://github.com/at-blacknight-projects/pathfinder-core/commit/8c9e32250132606a15905ad64d9889558482d4db)), closes [Logs#0](https://github.com/at-blacknight-projects/Logs/issues/0)


### Documentation

* how to install a local build, and the pre-1.0 versioning rules ([9ac83ee](https://github.com/at-blacknight-projects/pathfinder-core/commit/9ac83eea58d05ea5589a6de718c6489edc923cdd))

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
