# Changelog

## [2.27.1](https://github.com/adrighem/PyPluginStore/compare/v2.27.0...v2.27.1) (2026-08-19)


### Bug Fixes

* skip compiling non-Domoticz custom_components, tests, and venv folders ([ff71bb5](https://github.com/adrighem/PyPluginStore/commit/ff71bb542bea4bdcf53db40ad6a4991a48a27e59))
* update release index registry SHA-256 binding ([dc95267](https://github.com/adrighem/PyPluginStore/commit/dc952670876f3ff079fb41835354f64bee3e594e))

## [2.27.0](https://github.com/adrighem/PyPluginStore/compare/v2.26.0...v2.27.0) (2026-08-19)


### Features

* implement parallel theme store architecture (closes [#87](https://github.com/adrighem/PyPluginStore/issues/87)) ([6b188ae](https://github.com/adrighem/PyPluginStore/commit/6b188aeb047244e335fe92b003321210083ae119))


### Bug Fixes

* allow omitted source_revision in release verification (fixes [#150](https://github.com/adrighem/PyPluginStore/issues/150)) ([673b852](https://github.com/adrighem/PyPluginStore/commit/673b8524e18efb88cf63735a44f5a6b026b6a318))
* bypass PEP 668 restrictions to allow isolated dependency installation (fixes [#151](https://github.com/adrighem/PyPluginStore/issues/151)) ([1549a9d](https://github.com/adrighem/PyPluginStore/commit/1549a9d6d676eaf2d58efeebacfb5589c714a43b))
* show revision commits available instead of false positive update for matching Git versions (fixes [#154](https://github.com/adrighem/PyPluginStore/issues/154)) ([ea5c92b](https://github.com/adrighem/PyPluginStore/commit/ea5c92b3803e05d7cf3b7afcc12f75f17f00cb84))


### Documentation

* refactor README to include parallel theme store architecture ([cf07841](https://github.com/adrighem/PyPluginStore/commit/cf0784140e6732bebeeeb0e434ecc556dd3e5809))
* use pypluginstore-social-preview.jpg as full-width banner in README ([023b94d](https://github.com/adrighem/PyPluginStore/commit/023b94d72c850fecd305a130015e02541ff85c35))

## [2.26.0](https://github.com/adrighem/PyPluginStore/compare/v2.25.0...v2.26.0) (2026-08-17)


### Features

* include changed plugin names in weekly registry updates (closes [#147](https://github.com/adrighem/PyPluginStore/issues/147)) ([5a4484b](https://github.com/adrighem/PyPluginStore/commit/5a4484b895d20dbb5cbf8fb6da1dec20ba53585e))
* increase release index validity to 16 days to prevent premature metadata expiration (fixes [#150](https://github.com/adrighem/PyPluginStore/issues/150)) ([9ff35e5](https://github.com/adrighem/PyPluginStore/commit/9ff35e5b08115b13452447b26caecd66c5900de6))
* update Domoticz Python plugin registry ([3404a2d](https://github.com/adrighem/PyPluginStore/commit/3404a2d9913a846ca7f819fa911c6d63fc6e64f2))

## [2.25.0](https://github.com/adrighem/PyPluginStore/compare/v2.24.4...v2.25.0) (2026-08-09)


### Features

* **registry:** add ha-domoticz-sync ([12c8e70](https://github.com/adrighem/PyPluginStore/commit/12c8e701246c9ea06e8a3466541da313bf01bf54))
* **registry:** add Domoticz-Broadlink-Daikin-Plugin; update Solaredge_modbustcp to 2.1.5, domoticz-kpn-experia-v10 to 1.1.2, and ha-domoticz-sync to 0.6.2 ([decf01f](https://github.com/adrighem/PyPluginStore/commit/decf01f524d8cbc7247215530022fc425107d49a))
* **registry:** update FullyKiosk to 1.1.2 and Solaredge_modbustcp to 2.1.2; update ha-domoticz-sync metadata and add its 0.6.1 release ([25f8d7f](https://github.com/adrighem/PyPluginStore/commit/25f8d7f2f7330808ec550fe8ca9413eed4f30d08))


### Bug Fixes

* advance corrected release metadata ([20f74d9](https://github.com/adrighem/PyPluginStore/commit/20f74d94b00f5676f779b14fa4f168803962ed2f))
* align plugin update status handling ([eb9e34d](https://github.com/adrighem/PyPluginStore/commit/eb9e34d1348662c90ba26ab27f8cd513144169f7))
* align release index registry binding ([a733bc9](https://github.com/adrighem/PyPluginStore/commit/a733bc9f791aa58bbcf524cf3c1ccf07356976b7))
* normalize legacy forge source revisions ([0a469a6](https://github.com/adrighem/PyPluginStore/commit/0a469a673019108111adfed37d98b5c612870fbb))
* reconcile release authorities by lineage ([6d116ce](https://github.com/adrighem/PyPluginStore/commit/6d116ceb2f764b9aee3508cb5b71f5eaa8f7faec))
* **ui:** highlight release updates ([8374b34](https://github.com/adrighem/PyPluginStore/commit/8374b34af72e3b155fe071c61de47eed36fe60c7))


### Documentation

* add GNU GPLv3 or later license to project ([9d6eda3](https://github.com/adrighem/PyPluginStore/commit/9d6eda33ea173c0f37cabd8d54687279cf583af5))

## [2.24.4](https://github.com/adrighem/PyPluginStore/compare/v2.24.3...v2.24.4) (2026-07-28)


### Bug Fixes

* shorten restore action labels ([d3529c2](https://github.com/adrighem/PyPluginStore/commit/d3529c2307a3234c15dca0843e2ca2658abf26e6)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)

## [2.24.3](https://github.com/adrighem/PyPluginStore/compare/v2.24.2...v2.24.3) (2026-07-27)


### Bug Fixes

* name verified restore targets ([cbffb6a](https://github.com/adrighem/PyPluginStore/commit/cbffb6a2c41d25b3934c4ae459cb2ca11bc8b3ff)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)

## [2.24.2](https://github.com/adrighem/PyPluginStore/compare/v2.24.1...v2.24.2) (2026-07-27)


### Bug Fixes

* retain dependencies for exact release migrations ([9b4442d](https://github.com/adrighem/PyPluginStore/commit/9b4442d68a7215543bed09f66f69fb3d2d9618c8)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)

## [2.24.1](https://github.com/adrighem/PyPluginStore/compare/v2.24.0...v2.24.1) (2026-07-26)


### Bug Fixes

* **releases:** clarify dependency and Git fallback failures ([1af52fd](https://github.com/adrighem/PyPluginStore/commit/1af52fdade96f977ab98f3413c61d3b599343af4)), refs [#122](https://github.com/adrighem/PyPluginStore/issues/122)

## [2.24.0](https://github.com/adrighem/PyPluginStore/compare/v2.23.0...v2.24.0) (2026-07-26)


### Features

* **releases:** switch Git installations directly to the latest host-certified release ([9557d44](https://github.com/adrighem/PyPluginStore/commit/9557d446ae7166f52923a7102be673044eb3f66d))
* **dependencies:** build immutable shared generations with uv copy mode ([b041e3e](https://github.com/adrighem/PyPluginStore/commit/b041e3eaef492ffdc40e4f4d965cda6d3e65d875))
* **lifecycle:** recover interrupted code and dependency activations ([1d99be3](https://github.com/adrighem/PyPluginStore/commit/1d99be3eecc777ad1888e013f707bc21f31c16d0))
* **ui:** use backend-owned actions and actionable lifecycle status ([960eca9](https://github.com/adrighem/PyPluginStore/commit/960eca9abc8989c772c30313aca5e97d6237eb0a), [0b72610](https://github.com/adrighem/PyPluginStore/commit/0b726105dbbba5e8df43b1423e8906d6a598ec))
* **ui:** show transient activity and bound restart recovery ([b475c83](https://github.com/adrighem/PyPluginStore/commit/b475c830e368f3621ee7fc8db091e5dbdd93a628), [4182f8d](https://github.com/adrighem/PyPluginStore/commit/4182f8d1ff6a68f86fc6c7cedb1bec5b2e0d9045))
* update Domoticz Python plugin registry ([6a300c2](https://github.com/adrighem/PyPluginStore/commit/6a300c21dcd1f925735a3adfff04d8bf5a8a132b))


### Bug Fixes

* **scanner:** exclude plugin template repository ([53eca9d](https://github.com/adrighem/PyPluginStore/commit/53eca9d3ea62cfa8b443173a699dd3e724ea6c32))


### Documentation

* **releases:** describe unified host lifecycle ([be3f9e8](https://github.com/adrighem/PyPluginStore/commit/be3f9e8e485b1a12943f350e8efe6dc6a5a4693a))

## [2.23.0](https://github.com/adrighem/PyPluginStore/compare/v2.22.5...v2.23.0) (2026-07-25)


### Features

* **releases:** discover releases on host refresh ([cf77449](https://github.com/adrighem/PyPluginStore/commit/cf77449f7f59e6c94ef76ac75adcd093159c02bf))
* **releases:** install host-certified release targets ([2558f1a](https://github.com/adrighem/PyPluginStore/commit/2558f1a3c506c3baadde3735885a2753f0487f87))
* **releases:** share provider runtime contracts ([5fd0535](https://github.com/adrighem/PyPluginStore/commit/5fd05354ff7748c645383219af7f983f8412dc2c))
* **ui:** show host-verified release refreshes ([43342df](https://github.com/adrighem/PyPluginStore/commit/43342df49bdd3c6bf2d13f4523f40076b175a362))


### Bug Fixes

* **releases:** preserve metadata recovery compatibility ([5250a8a](https://github.com/adrighem/PyPluginStore/commit/5250a8a350b36a1a88aec185ac20b28b64bdc7e7))
* **releases:** refresh managed releases on demand ([139f1a1](https://github.com/adrighem/PyPluginStore/commit/139f1a1e9965d0c45617d9fd10889011a510182c))
* **ui:** hide current release status details ([79881f7](https://github.com/adrighem/PyPluginStore/commit/79881f7b40d517912f4607a24b0992fd7341cfd9))

## [2.22.5](https://github.com/adrighem/PyPluginStore/compare/v2.22.4...v2.22.5) (2026-07-25)


### Bug Fixes

* **releases:** detect dotted version tags ([1b1bc4e](https://github.com/adrighem/PyPluginStore/commit/1b1bc4eea7907f9f75bedad0ed8e9d3b14e0f3ec))
* **releases:** detect dotted version tags ([305bfff](https://github.com/adrighem/PyPluginStore/commit/305bfffd4b95e658849aad66f9a291fde70b3c44)), closes [#133](https://github.com/adrighem/PyPluginStore/issues/133)

## [2.22.4](https://github.com/adrighem/PyPluginStore/compare/v2.22.3...v2.22.4) (2026-07-24)


### Bug Fixes

* **ui:** scope API bridge device discovery ([d3c6fcc](https://github.com/adrighem/PyPluginStore/commit/d3c6fcc6947f3d8e3fd074811b0035a348a61362)), closes [#127](https://github.com/adrighem/PyPluginStore/issues/127)
* **ui:** scope API bridge discovery ([19a41cd](https://github.com/adrighem/PyPluginStore/commit/19a41cd74ac6c4e0b21c48ff0a7809764197513a))

## [2.22.3](https://github.com/adrighem/PyPluginStore/compare/v2.22.2...v2.22.3) (2026-07-24)


### Bug Fixes

* compare migrated Git roots by identity ([19752f2](https://github.com/adrighem/PyPluginStore/commit/19752f256356cb44c242ca369a88e1f704959b06)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* enforce Windows Git long-path mode ([d5c1956](https://github.com/adrighem/PyPluginStore/commit/d5c1956369f548958c4e15737b3ba3a743b8f209)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* harden Git-to-Release transitions ([f3d2a0d](https://github.com/adrighem/PyPluginStore/commit/f3d2a0d3d1381483293801fa015ef2753554660f)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* identify Git migration revalidation failures ([ada51b0](https://github.com/adrighem/PyPluginStore/commit/ada51b0bf2334315c43cb3e537cc0b141e0b5fce)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* include Git preflight reason in recovery diagnostics ([460b8d7](https://github.com/adrighem/PyPluginStore/commit/460b8d7a164eecddb0d7479676b4d02b37f813c9)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* make release channel transitions folder-safe ([f880eb0](https://github.com/adrighem/PyPluginStore/commit/f880eb0b54bd965fa69f204981cd081c50579dbe))
* make release channel transitions folder-safe ([c41d129](https://github.com/adrighem/PyPluginStore/commit/c41d129e5aa15614a6e91a2fff2844a9d4501582)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* support long Git paths on Windows ([7d67965](https://github.com/adrighem/PyPluginStore/commit/7d679659db986f4648216029167c6de1a511e377)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)
* **ui:** hide installed folder aliases ([7bfe3ff](https://github.com/adrighem/PyPluginStore/commit/7bfe3ffc8dde1d2cf9f6cc8e068b555694bcd49c)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)

## [2.22.2](https://github.com/adrighem/PyPluginStore/compare/v2.22.1...v2.22.2) (2026-07-23)


### Bug Fixes

* **self-update:** decode git output as UTF-8 ([ea42d20](https://github.com/adrighem/PyPluginStore/commit/ea42d2060118b8e4d76fd91d5aaaa57a07f4e667)), closes [#123](https://github.com/adrighem/PyPluginStore/issues/123)

## [2.22.1](https://github.com/adrighem/PyPluginStore/compare/v2.22.0...v2.22.1) (2026-07-23)


### Bug Fixes

* **notifications:** clarify release channel choices ([40faa50](https://github.com/adrighem/PyPluginStore/commit/40faa502781b82f46b50cbb397a0ac7a6d8316a8))
* **notifications:** clarify release channel choices ([295b3dd](https://github.com/adrighem/PyPluginStore/commit/295b3dd80d8d87981b9f95839f7eb674b7a2a54d)), closes [#122](https://github.com/adrighem/PyPluginStore/issues/122)

## [2.22.0](https://github.com/adrighem/PyPluginStore/compare/v2.21.1...v2.22.0) (2026-07-23)


### Features

* **manager:** integrate coherence recovery ([6782866](https://github.com/adrighem/PyPluginStore/commit/6782866f22144da80b8071dece1a45e1958d2b71))
* **manager:** verify runtime identity coherence ([6fde83e](https://github.com/adrighem/PyPluginStore/commit/6fde83e82641ebbeaf3b0d710a0d3558d26aa6fd))
* **ui:** enforce manager identity handshake ([4e2a2bd](https://github.com/adrighem/PyPluginStore/commit/4e2a2bd863a1d27d939eec9dd7156d74248d2401))
* verify manager runtime identity coherence ([88b6de8](https://github.com/adrighem/PyPluginStore/commit/88b6de8aee1a1c9f6482df6f6c1e884bce2a7cf0))


### Bug Fixes

* **manager:** preserve stale-state recovery ([fb3ff4e](https://github.com/adrighem/PyPluginStore/commit/fb3ff4e9199656f2a29e66d0f42ee8633f3c3116))

## [2.21.1](https://github.com/adrighem/PyPluginStore/compare/v2.21.0...v2.21.1) (2026-07-22)


### Bug Fixes

* **releases:** block local updates without Git checkout ([185d62a](https://github.com/adrighem/PyPluginStore/commit/185d62a4b4f6ff9de33b0fc88bc9fffb4b6760a8)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111)
* **releases:** enforce local override channel policy ([7945ce2](https://github.com/adrighem/PyPluginStore/commit/7945ce244835adaedcac1017332d81350cc5e65d))
* **releases:** honor local registry overrides ([0ad164b](https://github.com/adrighem/PyPluginStore/commit/0ad164b3483a173e746b80f0a8495b3ba70afb83)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111)
* **releases:** remove public Git channel switch ([691ea74](https://github.com/adrighem/PyPluginStore/commit/691ea741132742dd5a74c9908c9f46b4e369b212)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111)


### Documentation

* **maintainer:** record issue 111 branch push ([737bfb1](https://github.com/adrighem/PyPluginStore/commit/737bfb101ac8736cfc45b32414a9065011f2e7bf)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111)
* **maintainer:** record issue 111 investigation ([db0f85d](https://github.com/adrighem/PyPluginStore/commit/db0f85d08d212fccbc93425cc487268a60c2908d)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111) [#117](https://github.com/adrighem/PyPluginStore/issues/117)
* **maintainer:** record issue 111 pull request ([41beefb](https://github.com/adrighem/PyPluginStore/commit/41beefbde80716f36599e1b9df059acb67d56142)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111)
* **maintainer:** record issue 117 resolution ([a2bd39e](https://github.com/adrighem/PyPluginStore/commit/a2bd39e65273f085380a820eefda4fafe92f28d6)), closes [#117](https://github.com/adrighem/PyPluginStore/issues/117)

## [2.21.0](https://github.com/adrighem/PyPluginStore/compare/v2.20.0...v2.21.0) (2026-07-21)


### Features

* update Domoticz Python plugin registry ([38fe0bc](https://github.com/adrighem/PyPluginStore/commit/38fe0bcc027861127aff23a3de81e4d6ef75face))


### Bug Fixes

* **releases:** recover and gate release switches ([c27ee9d](https://github.com/adrighem/PyPluginStore/commit/c27ee9d909f097b11b51c3dadce4bf9749db9a7c))
* **releases:** recover and gate release switches ([e4eee28](https://github.com/adrighem/PyPluginStore/commit/e4eee28e5bfa9f56f66068eee59df31dcd28dfa3)), closes [#111](https://github.com/adrighem/PyPluginStore/issues/111)

## [2.20.0](https://github.com/adrighem/PyPluginStore/compare/v2.19.1...v2.20.0) (2026-07-21)


### Features

* **registry:** add explicit package contracts ([f937f42](https://github.com/adrighem/PyPluginStore/commit/f937f42da43a4719dd63e5671582695168d88342))
* **registry:** consume explicit package data ([ef9ceb8](https://github.com/adrighem/PyPluginStore/commit/ef9ceb8e86b649a03a2070322b952a9521bdaa2e))
* **registry:** migrate weekly automation to v2 ([f73ce9b](https://github.com/adrighem/PyPluginStore/commit/f73ce9b8c99a9e807bc41f3c6faa46e8ada734c5))
* **registry:** publish explicit package identities ([0d0057d](https://github.com/adrighem/PyPluginStore/commit/0d0057d4dcdd158f3a2739cb33f0557a4495bb16))
* **releases:** automate safe Git migrations ([8067dca](https://github.com/adrighem/PyPluginStore/commit/8067dca2f38bc5673c0e22dd8e05a1ff06cc9d9d))
* **releases:** certify package identities ([8e5c943](https://github.com/adrighem/PyPluginStore/commit/8e5c94342588ce813572bdfa9a9a6c4c9e7753c1))
* **releases:** recover from rejected releases ([1339fa9](https://github.com/adrighem/PyPluginStore/commit/1339fa92ae2973d2ca6bd8a1e6095f86288bed05))
* **state:** migrate persisted package identities ([2d5ac9d](https://github.com/adrighem/PyPluginStore/commit/2d5ac9d37010b7ee36a9f859e3fe85e957d6fe42))


### Bug Fixes

* **releases:** read registry v2 packages ([7147eb5](https://github.com/adrighem/PyPluginStore/commit/7147eb5505b37ed8c02d672bbef2bde922070de1))
* **releases:** retain removed package tombstones ([fc47018](https://github.com/adrighem/PyPluginStore/commit/fc47018ef4cb861a6a758bfe6816050bf57ca483))
* **releases:** tombstone removed legacy packages ([ce6e8b7](https://github.com/adrighem/PyPluginStore/commit/ce6e8b7e7b642009f2593bb116410ba25933049d))
* **security:** parse Codeberg migration host ([80324db](https://github.com/adrighem/PyPluginStore/commit/80324db7aead177fccbb60bf84415d02fbc12148))
* **ui:** hide idle Git status details ([3b892c7](https://github.com/adrighem/PyPluginStore/commit/3b892c769d78ab41963ce4781fcd5715bec540b7))


### Documentation

* **releases:** explain release-first migration ([77b1499](https://github.com/adrighem/PyPluginStore/commit/77b14995a782aed8e3227c255313c4f4e9b944e7))

## [2.19.1](https://github.com/adrighem/PyPluginStore/compare/v2.19.0...v2.19.1) (2026-07-21)


### Bug Fixes

* **ci:** allow release finalization ([eaa3087](https://github.com/adrighem/PyPluginStore/commit/eaa3087e3750c5d6e30508aa82b119b5ed585d1c))

## [2.19.0](https://github.com/adrighem/PyPluginStore/compare/v2.18.0...v2.19.0) (2026-07-21)


### Features

* add release-first plugin management ([5636fa5](https://github.com/adrighem/PyPluginStore/commit/5636fa5b45d204d0240a5db4d89635d93310c2f6)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **registry:** automate release index updates ([4fa2a2d](https://github.com/adrighem/PyPluginStore/commit/4fa2a2de0f25fe4ccb64a9a15f7d5a89be4285db)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** activate release-first runtime ([c933f48](https://github.com/adrighem/PyPluginStore/commit/c933f481eb413dc3912e6f7cf127fecd58038924)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** add atomic replacement transactions ([f7580fc](https://github.com/adrighem/PyPluginStore/commit/f7580fc943339eb32b8663cbbe7d7cf72da3ab60)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** add delivery and index models ([602573a](https://github.com/adrighem/PyPluginStore/commit/602573aad15896459556f852a852649d14a29873)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** add durable metadata generations ([db4d87d](https://github.com/adrighem/PyPluginStore/commit/db4d87dbd10baa9494487504035a4b9e0ca0c52c)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** add provider-neutral candidate model ([9c9fe43](https://github.com/adrighem/PyPluginStore/commit/9c9fe438df8aeffbd63d2cdfde82d3b3083d00fc)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** add release-first management coordinator ([ae43754](https://github.com/adrighem/PyPluginStore/commit/ae437545b20bce5069d8beb6bf8d0574c690c855)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** add safe Git migration preflight ([2d62822](https://github.com/adrighem/PyPluginStore/commit/2d62822f12cb2f960a06c18055c822ed8f745624)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** certify release artifacts securely ([a873a68](https://github.com/adrighem/PyPluginStore/commit/a873a68b8da192bfec0b5017e9309753904b4af7)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** certify staged artifact trees ([168aa70](https://github.com/adrighem/PyPluginStore/commit/168aa70d855bae5b9edb4bf737b89d959af031e2)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** finalize rollback lifecycle ([bf56e6a](https://github.com/adrighem/PyPluginStore/commit/bf56e6a165ce990ee7795f147be0dcc972337032)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** harden runtime artifact staging ([00abc9e](https://github.com/adrighem/PyPluginStore/commit/00abc9e7803341ecec7b26249339fba97bf9f118)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** migrate Git installs safely ([f27fd54](https://github.com/adrighem/PyPluginStore/commit/f27fd5406c9548d895e9b72b24af2caa249ead6a)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** persist channel preferences ([72eebcd](https://github.com/adrighem/PyPluginStore/commit/72eebcda06769d65ad71e0b5fe892208f7632087)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** persist install audit metadata ([a56a101](https://github.com/adrighem/PyPluginStore/commit/a56a101486a386cf85d9735cadcfb192e6bdeecc)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** preserve reviewed plugin data ([81dd483](https://github.com/adrighem/PyPluginStore/commit/81dd4834432f22ba9413617b0143c9a60f89e51b)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** publish initial release-first index ([44aac97](https://github.com/adrighem/PyPluginStore/commit/44aac978bc8a2eeeb133e89c808cfd346576926a)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** queue locked Windows replacements ([62ed533](https://github.com/adrighem/PyPluginStore/commit/62ed533832270cfb3cae8dde1e7afc9d9d387cc0)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** resolve Forgejo and Codeberg releases ([8dbb9d7](https://github.com/adrighem/PyPluginStore/commit/8dbb9d73615da2809d9501a24194f5c870bba10b)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** resolve generic HTTPS manifests ([5848a14](https://github.com/adrighem/PyPluginStore/commit/5848a14f8f112b25c76940c5a344ece05983f188)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** resolve Gitea releases ([589bb61](https://github.com/adrighem/PyPluginStore/commit/589bb610233ad3ffee447a001d6ab74462d7b344)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** resolve GitHub releases ([54cf8f8](https://github.com/adrighem/PyPluginStore/commit/54cf8f85d240e3ba88271a5486f2243d16410791)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** resolve GitLab releases ([c0fad49](https://github.com/adrighem/PyPluginStore/commit/c0fad49bd9a835c0ab9f46b1088176401b3e653e)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **ui:** add release channel management controls ([473581d](https://github.com/adrighem/PyPluginStore/commit/473581d4d2fae40273ceccb76ad24bd3b864ee02)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* update Domoticz Python plugin registry ([ee958c0](https://github.com/adrighem/PyPluginStore/commit/ee958c0669c28fbb84182bef4b8688f066a2a5bf))
* write new registry entries as objects ([ddac637](https://github.com/adrighem/PyPluginStore/commit/ddac6377d1c457847d3440f0b17d7bfd6fbe1969)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)


### Bug Fixes

* accept self-hosted release capabilities ([3e9b25c](https://github.com/adrighem/PyPluginStore/commit/3e9b25c0dfb6c3069df9ae214f4449856e88ce3b)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* allow selecting plugin card text ([6bf834c](https://github.com/adrighem/PyPluginStore/commit/6bf834cdad2bc8e6a7cf04452e850fb4a8ad3658))
* flush release trees on Windows ([9b204cd](https://github.com/adrighem/PyPluginStore/commit/9b204cd0c8658104dd368d4f551016f154a68c26)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* hide internal release revisions ([fc3af3c](https://github.com/adrighem/PyPluginStore/commit/fc3af3ce463c3ef5328704c8cb02c04b0cb9bced))
* **releases:** bind pagination to host capabilities ([fcff37b](https://github.com/adrighem/PyPluginStore/commit/fcff37be31dc2bba2d9279bf67474b10ca236984)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** bind runtime decisions to fresh state ([33b997f](https://github.com/adrighem/PyPluginStore/commit/33b997f705054eee4517cfc57eee4472c934193c))
* **releases:** harden live provider discovery ([34e3284](https://github.com/adrighem/PyPluginStore/commit/34e32849c541b2879e3f08b9ca0f9605e00c5ba1)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** persist artifact provenance manifest ([2625035](https://github.com/adrighem/PyPluginStore/commit/26250358c8ce156cc555d7b220a23f3793cd7f8b)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* restore Git self-update action ([c362a94](https://github.com/adrighem/PyPluginStore/commit/c362a9491bc9cbe49755e2ab9ef6774a7780b7eb))
* use reliable Windows file identities ([bd13c49](https://github.com/adrighem/PyPluginStore/commit/bd13c4935a8a153be6a9f3143844e34ea3778cc4)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* wrap plugin card actions ([bda0668](https://github.com/adrighem/PyPluginStore/commit/bda06688217e5d18ddac69ab3272dafbed21a514))


### Documentation

* design release-first plugin management ([23642ff](https://github.com/adrighem/PyPluginStore/commit/23642ffc7ec0abd15d8c0ca930fe82d2685be2e8)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **maintainer:** record release-first rollout ([4158ea2](https://github.com/adrighem/PyPluginStore/commit/4158ea2cbfb6876ca44e4028e6ac5bad37d26c20)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)
* **releases:** clarify multi-host capabilities ([7bac9bf](https://github.com/adrighem/PyPluginStore/commit/7bac9bfd2355393fbbd344ed1971765504fbcd87)), closes [#64](https://github.com/adrighem/PyPluginStore/issues/64)

## [2.18.0](https://github.com/adrighem/PyPluginStore/compare/v2.17.1...v2.18.0) (2026-07-16)


### Features

* **registry:** add atomic local registry service ([35b30ec](https://github.com/adrighem/PyPluginStore/commit/35b30ec2ed6c3374b35875b9783ba635251493fd)), closes [#95](https://github.com/adrighem/PyPluginStore/issues/95)
* **registry:** expose local registry management API ([b147193](https://github.com/adrighem/PyPluginStore/commit/b147193ad80b737ab0c6681826757ced8d5f59d9)), closes [#95](https://github.com/adrighem/PyPluginStore/issues/95)
* **ui:** manage local registry entries ([697d93d](https://github.com/adrighem/PyPluginStore/commit/697d93d0ab10a52ff362a6510220f3279efbd39c)), closes [#95](https://github.com/adrighem/PyPluginStore/issues/95)


### Documentation

* **registry:** document local registry manager ([6c29421](https://github.com/adrighem/PyPluginStore/commit/6c294210312f60ec0acd46d9ae6bec6e3e535cbf)), closes [#95](https://github.com/adrighem/PyPluginStore/issues/95)

## [2.17.1](https://github.com/adrighem/PyPluginStore/compare/v2.17.0...v2.17.1) (2026-07-15)


### Bug Fixes

* align local badge with platforms ([6e89809](https://github.com/adrighem/PyPluginStore/commit/6e898091ff3a95edcf34a76a925901bc888d8592)), closes [#98](https://github.com/adrighem/PyPluginStore/issues/98)

## [2.17.0](https://github.com/adrighem/PyPluginStore/compare/v2.16.1...v2.17.0) (2026-07-14)


### Features

* update Domoticz Python plugin registry ([fcba4a6](https://github.com/adrighem/PyPluginStore/commit/fcba4a621018f6eb6169776d57b010577f32cf27))


### Bug Fixes

* align WeatherInfo registry metadata ([cb7e6cf](https://github.com/adrighem/PyPluginStore/commit/cb7e6cfc8b083c63613099eb1a0bd0af199922fb))
* bound registry git validation ([c4ea849](https://github.com/adrighem/PyPluginStore/commit/c4ea849c36ac06684ec54abc3824fecbea8e5584))
* retry transient registry fetches ([7adaadd](https://github.com/adrighem/PyPluginStore/commit/7adaadd655781305fcd81858984a229c31cad505))

## [2.16.1](https://github.com/adrighem/PyPluginStore/compare/v2.16.0...v2.16.1) (2026-07-09)


### Bug Fixes

* keep API payload device disabled ([11d2e0b](https://github.com/adrighem/PyPluginStore/commit/11d2e0ba2b0bbf52da3f1c4c2a7a23daea434656))
* report stale self-update git lock ([7029eed](https://github.com/adrighem/PyPluginStore/commit/7029eeddecfe8e8857f5c1bd660f45e991ed8c04)), closes [#94](https://github.com/adrighem/PyPluginStore/issues/94)
* track PyPluginStore self-update state ([f1d5a2c](https://github.com/adrighem/PyPluginStore/commit/f1d5a2c37da56efe2e179afa44bd882b38f5ed67)), closes [#94](https://github.com/adrighem/PyPluginStore/issues/94)

## [2.16.0](https://github.com/adrighem/PyPluginStore/compare/v2.15.2...v2.16.0) (2026-07-08)


### Features

* native theme support, version 1 ([21387b0](https://github.com/adrighem/PyPluginStore/commit/21387b0261280143976c730346fab089857e4495))


### Bug Fixes

* accept API bridge error responses ([7be417c](https://github.com/adrighem/PyPluginStore/commit/7be417c6bf3d33e9c6eb02ead43cc8b63fd0d9d0))
* align local override branch metadata ([fbeb52c](https://github.com/adrighem/PyPluginStore/commit/fbeb52ce8143de6e67a4ccc405d9dcc8dc714259)), closes [#73](https://github.com/adrighem/PyPluginStore/issues/73)
* align plugin card badges ([e7b12ad](https://github.com/adrighem/PyPluginStore/commit/e7b12ad67883ec6ca167df4487e4ca6d387fdfea)), closes [#92](https://github.com/adrighem/PyPluginStore/issues/92)
* default plugin store to Domoticz theme layout ([865cd79](https://github.com/adrighem/PyPluginStore/commit/865cd79b02aa92c31924f918a145a2b9fb6a013d)), closes [#91](https://github.com/adrighem/PyPluginStore/issues/91)
* fine-tune Domoticz theme support ([ed365ae](https://github.com/adrighem/PyPluginStore/commit/ed365ae3813811026e877f8298bd0982bd274764)), closes [#91](https://github.com/adrighem/PyPluginStore/issues/91)
* warn on installed registry mismatches ([1e0e4e2](https://github.com/adrighem/PyPluginStore/commit/1e0e4e2270c5528e9982c268b874c3cc55b0d837)), closes [#73](https://github.com/adrighem/PyPluginStore/issues/73)


### Documentation

* add registry_local how-to ([434922b](https://github.com/adrighem/PyPluginStore/commit/434922bdd5f6c18ccf9872e4e976a438163f0bf4))
* clarify repo mismatch recovery ([d1a9b49](https://github.com/adrighem/PyPluginStore/commit/d1a9b496116645d3e3626d8c142407c9c99c7243)), closes [#73](https://github.com/adrighem/PyPluginStore/issues/73)
* document repo mismatch warning ([03e3c71](https://github.com/adrighem/PyPluginStore/commit/03e3c713e3c2bb360c0758435a14652487c01c0a)), closes [#73](https://github.com/adrighem/PyPluginStore/issues/73)
* move local registry guidance to configuration ([0d3bade](https://github.com/adrighem/PyPluginStore/commit/0d3bade345bef80f2578bb11d023d8296ba8229f))

## [2.15.2](https://github.com/adrighem/PyPluginStore/compare/v2.15.1...v2.15.2) (2026-07-05)


### Bug Fixes

* harden weekly plugin discovery ([24a63df](https://github.com/adrighem/PyPluginStore/commit/24a63df202e0b83668e97d98106a1765076929d4)), closes [#88](https://github.com/adrighem/PyPluginStore/issues/88)
* stabilize API error responses ([87a8bd8](https://github.com/adrighem/PyPluginStore/commit/87a8bd8aab8b13c03c27a2b0ed0e47bd1918da6a))


### Documentation

* record theme management maintainer notes ([2b07ef5](https://github.com/adrighem/PyPluginStore/commit/2b07ef59989d49d2a3fd278fad86a1f18ad9c64d)), closes [#30](https://github.com/adrighem/PyPluginStore/issues/30) [#87](https://github.com/adrighem/PyPluginStore/issues/87)

## [2.15.1](https://github.com/adrighem/PyPluginStore/compare/v2.15.0...v2.15.1) (2026-07-04)


### Bug Fixes

* align self update git ownership handling ([a9de821](https://github.com/adrighem/PyPluginStore/commit/a9de82121d4de602ac4fec15b3bd2b901a4962e2)), closes [#86](https://github.com/adrighem/PyPluginStore/issues/86)
* avoid ownership repair for managed git repos ([fd284e7](https://github.com/adrighem/PyPluginStore/commit/fd284e7413b7e3b12ff32ba976071bc45fcfa9bf)), closes [#86](https://github.com/adrighem/PyPluginStore/issues/86)
* clarify git ownership diagnostics ([10432fb](https://github.com/adrighem/PyPluginStore/commit/10432fbf4eb52e2823689ca05f58a08af367e53d)), closes [#86](https://github.com/adrighem/PyPluginStore/issues/86)
* prune stale update times during registry scans ([2c94c69](https://github.com/adrighem/PyPluginStore/commit/2c94c6939fc1ad72089d49312fbb4610341cad4f)), closes [#84](https://github.com/adrighem/PyPluginStore/issues/84)


### Documentation

* update issue 86 investigation note ([6156dff](https://github.com/adrighem/PyPluginStore/commit/6156dff64b0e389c89ff2d96f0c40f8444dcbfca)), closes [#86](https://github.com/adrighem/PyPluginStore/issues/86)

## [2.15.0](https://github.com/adrighem/PyPluginStore/compare/v2.14.2...v2.15.0) (2026-07-02)


### Features

* support Codeberg and GitLab plugin repositories ([8a55c7c](https://github.com/adrighem/PyPluginStore/commit/8a55c7c5a7461291152e7321522f512c7aff3dff)), closes [#76](https://github.com/adrighem/PyPluginStore/issues/76)
* update Domoticz Python plugin registry ([9342d41](https://github.com/adrighem/PyPluginStore/commit/9342d41d91aa055a489ec92bd69e41a3c9ddb649))


### Bug Fixes

* make plugin store repository links host-aware ([25d6ad1](https://github.com/adrighem/PyPluginStore/commit/25d6ad1e93874bc63bb8baba6204fc0783c04ee2)), closes [#76](https://github.com/adrighem/PyPluginStore/issues/76)
* preserve registry branches during scans ([8dc31d3](https://github.com/adrighem/PyPluginStore/commit/8dc31d3a5714dc89db1e0620b0ca331ead1c35c8))
* stabilize platform detection metadata ([e13d5b9](https://github.com/adrighem/PyPluginStore/commit/e13d5b94ac00a79ecd08454f19e390b19df81c94))

## [2.14.2](https://github.com/adrighem/PyPluginStore/compare/v2.14.1...v2.14.2) (2026-07-02)


### Bug Fixes

* add non-git badges and implement branch-aware updates (fixes [#73](https://github.com/adrighem/PyPluginStore/issues/73), closes [#74](https://github.com/adrighem/PyPluginStore/issues/74)) ([73b0c1c](https://github.com/adrighem/PyPluginStore/commit/73b0c1c40152a68d2059bf5d4a0a115c762e14d6))
* **updater:** upgrade to robust fetch-and-reset updater sequence (fixes [#73](https://github.com/adrighem/PyPluginStore/issues/73)) ([0b6b003](https://github.com/adrighem/PyPluginStore/commit/0b6b0032123a760a01f2a01f3118e17d0652f440))

## [2.14.1](https://github.com/adrighem/PyPluginStore/compare/v2.14.0...v2.14.1) (2026-07-01)


### Bug Fixes

* bypass Git dubious ownership with safe.directory option ([3292bbb](https://github.com/adrighem/PyPluginStore/commit/3292bbb564d2c040886a76358819154f054d9fe0)), closes [#70](https://github.com/adrighem/PyPluginStore/issues/70)

## [2.14.0](https://github.com/adrighem/PyPluginStore/compare/v2.13.1...v2.14.0) (2026-06-29)


### Features

* add Luxtronik Windows platform ([1814323](https://github.com/adrighem/PyPluginStore/commit/1814323c08066aafacaed7574a288fb7c5ff930c)), closes [#66](https://github.com/adrighem/PyPluginStore/issues/66)


### Bug Fixes

* harden self-update preflight ([28c7f43](https://github.com/adrighem/PyPluginStore/commit/28c7f4343ea4d3a4266f18e72107cf0c28b0cf8d)), closes [#65](https://github.com/adrighem/PyPluginStore/issues/65)
* recover from Git ownership mismatch ([e0de2a6](https://github.com/adrighem/PyPluginStore/commit/e0de2a6475c26ad8bcb905489beba9b9498d3bce)), closes [#69](https://github.com/adrighem/PyPluginStore/issues/69)

## [2.13.1](https://github.com/adrighem/PyPluginStore/compare/v2.13.0...v2.13.1) (2026-06-29)


### Bug Fixes

* avoid self-update API timeout ([47e2d73](https://github.com/adrighem/PyPluginStore/commit/47e2d7380c2b53841fa587ca53e53d2056cbd3f5)), closes [#65](https://github.com/adrighem/PyPluginStore/issues/65)

## [2.13.0](https://github.com/adrighem/PyPluginStore/compare/v2.12.1...v2.13.0) (2026-06-29)


### Features

* add Domoticz-Home-Connect-Plugin & implement lightweight version visibility in UI ([8c8c519](https://github.com/adrighem/PyPluginStore/commit/8c8c519b2dbf85270265398481265ea76e379f98))

## [2.12.1](https://github.com/adrighem/PyPluginStore/compare/v2.12.0...v2.12.1) (2026-06-28)


### Bug Fixes

* clean API bridge payloads ([66ae709](https://github.com/adrighem/PyPluginStore/commit/66ae709eb8cb833baff986be03ae705d49f4778b))


### Documentation

* add clear installation and configuration checks ([d451fda](https://github.com/adrighem/PyPluginStore/commit/d451fda374bce6b59a43d8bbeef2945fed89bd0d))

## [2.12.0](https://github.com/adrighem/PyPluginStore/compare/v2.11.1...v2.12.0) (2026-06-28)


### Features

* infer plugin platform metadata ([24c0d12](https://github.com/adrighem/PyPluginStore/commit/24c0d1226bff620a6c9a57825240df0a9155a2ae))
* remember installed filter state ([52fb697](https://github.com/adrighem/PyPluginStore/commit/52fb697f4c30409963c1de898c46e6ed873dc3f9))
* update Domoticz Python plugin registry ([d0950bb](https://github.com/adrighem/PyPluginStore/commit/d0950bbbf278743f8831510541e9af96fe068d02))


### Bug Fixes

* cache startup update status ([f3686ad](https://github.com/adrighem/PyPluginStore/commit/f3686ad6ce0197f7e191ce08fecc3eee5512fc9b))
* detect Marstek Modbus plugin ([88227a2](https://github.com/adrighem/PyPluginStore/commit/88227a236a062a9d9192c9646fdae8f249609da3))
* find hidden api bridge devices ([fe16a46](https://github.com/adrighem/PyPluginStore/commit/fe16a463704bbb0aec5b695c1671bebf4b8d2413))
* improve plugin discovery and UI bridge ([21cd308](https://github.com/adrighem/PyPluginStore/commit/21cd3081542a62fe4a122f2e7592e2c679e7692a))
* improve restart permission diagnostics ([7b815b0](https://github.com/adrighem/PyPluginStore/commit/7b815b0a02bcb7bf3e464b1b7eaa8ddd8d76809b))
* support domoticz without notification api ([d876414](https://github.com/adrighem/PyPluginStore/commit/d876414a6edd0e05220568ab13b3e277bc085f53))

## [2.11.1](https://github.com/adrighem/PyPluginStore/compare/v2.11.0...v2.11.1) (2026-06-27)


### Bug Fixes

* improve installed plugin detection ([1395e55](https://github.com/adrighem/PyPluginStore/commit/1395e55dea63062573dc1104ecae465c79584ecf))

## [2.11.0](https://github.com/adrighem/PyPluginStore/compare/v2.10.0...v2.11.0) (2026-06-26)


### Features

* detect pre-existing plugin installs ([4cdf56a](https://github.com/adrighem/PyPluginStore/commit/4cdf56a2345a11df5571f0d860722345701b12fa))

## [2.10.0](https://github.com/adrighem/PyPluginStore/compare/v2.9.1...v2.10.0) (2026-06-26)


### Features

* add Windows support runtime ([3bab033](https://github.com/adrighem/PyPluginStore/commit/3bab03358e5f99fa42d04848eacf626689ec045a))

## [2.9.1](https://github.com/adrighem/PyPluginStore/compare/v2.9.0...v2.9.1) (2026-06-22)


### Bug Fixes

* install custom UI icon under Domoticz images ([84eb18e](https://github.com/adrighem/PyPluginStore/commit/84eb18ebb7d2a1d51d1a4ba179cca64e078fb87d))


### Documentation

* document release-please commit requirements ([abb8bd8](https://github.com/adrighem/PyPluginStore/commit/abb8bd86a7fee2e57fe5d228dfbb094b1e022772))

## [2.9.0](https://github.com/adrighem/PyPluginStore/compare/v2.8.2...v2.9.0) (2026-06-21)


### Features

* add new Domoticz Python plugins ([4c2fa40](https://github.com/adrighem/PyPluginStore/commit/4c2fa406f1a795e97c32783c084ce539532985bd))


### Documentation

* refine README logo presentation ([79c1911](https://github.com/adrighem/PyPluginStore/commit/79c1911a6290b70c3e52b39cbe919c172a0e59f7))
* rename store screenshot asset ([dbe78fc](https://github.com/adrighem/PyPluginStore/commit/dbe78fc15582c91d4dd97286cd9aaf4bb70eca5e))
* update README wording ([04216d1](https://github.com/adrighem/PyPluginStore/commit/04216d190d9ff89c9c293a3d8d8f15f6d81e6370))

## [2.8.2](https://github.com/adrighem/PyPluginStore/compare/v2.8.1...v2.8.2) (2026-06-21)


### Bug Fixes

* apply MadPatrick registry refresh intent ([c88f4d3](https://github.com/adrighem/PyPluginStore/commit/c88f4d319a41f2c3fe679e90cbe0af277a3148ef))

## [2.8.1](https://github.com/adrighem/PyPluginStore/compare/v2.8.0...v2.8.1) (2026-06-20)


### Bug Fixes

* parse clone URLs before GitHub normalization ([66bcd4c](https://github.com/adrighem/PyPluginStore/commit/66bcd4ca598d8ea06fdcd05e817e3c85bb2489c9))

## [2.8.0](https://github.com/adrighem/PyPluginStore/compare/v2.7.0...v2.8.0) (2026-06-20)


### Features

* support local registry overlays with MadPatrick ([36f3bcf](https://github.com/adrighem/PyPluginStore/commit/36f3bcf79bbdc942999517f55e074a3d0a0653e8))


### Bug Fixes

* remove unavailable Melotron Python registry entry ([e6d98c8](https://github.com/adrighem/PyPluginStore/commit/e6d98c83da45c39c9015763ded6133c1810e1e25))

## [2.7.0](https://github.com/adrighem/PyPluginStore/compare/v2.6.0...v2.7.0) (2026-06-15)


### Features

* add plugin update controls and cached status refresh with MadPatrick ([81018ad](https://github.com/adrighem/PyPluginStore/commit/81018adcdc9662dfaefec07737db566c34f26f2a))


### Bug Fixes

* clean plugin update time refresh ([86c0c6a](https://github.com/adrighem/PyPluginStore/commit/86c0c6a3bb92ef60a6a8331062e16a93ca2ae8b7))


### Documentation

* clarify generated plugin workflow ([39dd23a](https://github.com/adrighem/PyPluginStore/commit/39dd23a2bd6472de4b39e264d4d1339bcb2022df))

## [2.6.0](https://github.com/adrighem/PyPluginStore/compare/v2.5.0...v2.6.0) (2026-06-14)


### Features

* MadPatrick: Change layout to new Domoticz style
* clean Domoticz affixes from plugin cards ([4b5e805](https://github.com/adrighem/PyPluginStore/commit/4b5e805a8eed1e2b2fa06d18bf50d666462d9ba4))
* improve plugin store controls ([2c102a2](https://github.com/adrighem/PyPluginStore/commit/2c102a212fa244734850413f9c5b0ef1810a6888))


### Bug Fixes

* block core Domoticz repository from registry ([f0693d6](https://github.com/adrighem/PyPluginStore/commit/f0693d614b39c5e36cd076fa6a43d4525b890f50))
* harden plugin scanner registry updates ([80853e1](https://github.com/adrighem/PyPluginStore/commit/80853e1931c05ba5b01f327d7e87ad4b355e42b2))
* remove empty repositories from registry ([ed49951](https://github.com/adrighem/PyPluginStore/commit/ed49951fa6f3976d2875c14c7d8b5f31d1416f6e))
* restore update button colors after MadPatrick layout refresh ([c9d4fd1](https://github.com/adrighem/PyPluginStore/commit/c9d4fd169271b8f1e0a258bbe8f7584a778946b4))
* restore update button state colors ([eb66ded](https://github.com/adrighem/PyPluginStore/commit/eb66ded97bc4308b4e44952fc5758b29fcdf782f))
* skip empty repositories in plugin scanner ([293429b](https://github.com/adrighem/PyPluginStore/commit/293429bac3e159243a2f0a270edb75259b807919))
* strip domoticz-for card title affixes ([251545e](https://github.com/adrighem/PyPluginStore/commit/251545ebe8ef68229da4d75e7e70fbc133e2c76e))


### Documentation

* update store screenshot ([b4e2772](https://github.com/adrighem/PyPluginStore/commit/b4e2772fa9817fc834d3f914bc0d78ca85a1890a))

## [2.5.0](https://github.com/adrighem/PyPluginStore/compare/v2.4.0...v2.5.0) (2026-06-07)


### Features

* add new Domoticz Python plugins ([e414b40](https://github.com/adrighem/PyPluginStore/commit/e414b4095cfb601be8ea2febcce28671d0bece5a))

## [2.4.0](https://github.com/adrighem/PyPluginStore/compare/v2.3.0...v2.4.0) (2026-06-03)


### Features

* add new Domoticz Python plugins ([40e1bf9](https://github.com/adrighem/PyPluginStore/commit/40e1bf91eeb1cf28ce81c54fd727198456133be0))
* split plugin last updated dates into update_times.json ([0eabae9](https://github.com/adrighem/PyPluginStore/commit/0eabae96c6adb9b45b226790409d6094d734ea3b))


### Bug Fixes

* remove deleted tado_domoticz plugin ([0eff094](https://github.com/adrighem/PyPluginStore/commit/0eff094eb25db7326d8e5453da43d055299ff7c7))

## [2.3.0](https://github.com/adrighem/PyPluginStore/compare/v2.2.2...v2.3.0) (2026-05-12)


### Features

* improve github scanner robustness and add missing plugins ([7794c14](https://github.com/adrighem/PyPluginStore/commit/7794c14e57243b16345885d3ea2bcd20e6b913d2))

## [2.2.2](https://github.com/adrighem/PyPluginStore/compare/v2.2.1...v2.2.2) (2026-05-12)


### Bug Fixes

* correct UI mapping for card title and description ([e8000f2](https://github.com/adrighem/PyPluginStore/commit/e8000f28d4a7a40448ea93a9aad9adcb234bce21))

## [2.2.1](https://github.com/adrighem/PyPluginStore/compare/v2.2.0...v2.2.1) (2026-05-11)


### Bug Fixes

* correct capitalization in javascript getElementById ([3c55090](https://github.com/adrighem/PyPluginStore/commit/3c550904126cd31880ce9aa1a98437043812d300))
* remove pp-manager from registry and ignore in monthly scans ([5a7003a](https://github.com/adrighem/PyPluginStore/commit/5a7003a36a1dede054adb13383e86cc7aa9faa88))
* revert plugin key to PP-MANAGER for hardware backward compatibility and remove legacy UI ([68bfbc7](https://github.com/adrighem/PyPluginStore/commit/68bfbc7a807b16e4abae87b4bf7f3d1da31fde07))


### Documentation

* update store screenshot with new PyPluginStore UI ([1f61e6f](https://github.com/adrighem/PyPluginStore/commit/1f61e6f840afee27af928b88e29302d4169d0d88))

## [2.2.0](https://github.com/adrighem/PyPluginStore/compare/v2.1.0...v2.2.0) (2026-05-11)


### Features

* add KPN Experia v10 plugin to registry ([290a08e](https://github.com/adrighem/PyPluginStore/commit/290a08ed7470f415f76a4b4910b4a7e45230d78b))


### Bug Fixes

* revert original repo name and url in fork note and registry ([ad0b51d](https://github.com/adrighem/PyPluginStore/commit/ad0b51df380634743ceeb723e19a114a261f1ee7))

## [2.1.0](https://github.com/adrighem/PyPluginStore/compare/v2.0.0...v2.1.0) (2026-05-10)


### Features

* add 'Repo' button to plugin cards ([8cd1389](https://github.com/adrighem/PyPluginStore/commit/8cd138955799bc793facd69cb81763e06334970e))
* add new Domoticz Python plugins ([c67a813](https://github.com/adrighem/PyPluginStore/commit/c67a8135c3c1d2ee9822acf9895ecbf15b29cb88))
* add new Domoticz Python plugins ([03b13e4](https://github.com/adrighem/PyPluginStore/commit/03b13e4dcb92737356fc248a6298cfbb07de8a3b))
* add search filter and installed-only toggle to dashboard ([7e25c1f](https://github.com/adrighem/PyPluginStore/commit/7e25c1fd8e0e96bd7d866fc5b6cbe62794227ad5))
* implement device bus API and custom HTML dashboard ([01ca5ef](https://github.com/adrighem/PyPluginStore/commit/01ca5efb29232a5619bb202059acff1859d00261))
* improve custom UI autoinstall logic ([5951e29](https://github.com/adrighem/PyPluginStore/commit/5951e2907732723409460c0276a3e303535ddb6d))
* make security scanner smarter by ignoring private IPs and targeting high-risk subprocess calls ([283e0b6](https://github.com/adrighem/PyPluginStore/commit/283e0b635d0c55e209209fa05c06d00164187b32))
* overhaul monthly scan to sync full registry and show 'last updated' in UI ([6cbf85e](https://github.com/adrighem/PyPluginStore/commit/6cbf85efb4583dd275b26eff944ed4f16192db7b))


### Bug Fixes

* add cache busters and absolute paths to API calls ([e74bda8](https://github.com/adrighem/PyPluginStore/commit/e74bda8ecd7aefe0cd5b01b2effe1dcfc377c5e9))
* call init directly to support SPA injection ([f6da725](https://github.com/adrighem/PyPluginStore/commit/f6da725bd1f3316c865180c11fcfe1b7b0d32747))
* echo back tx_id in API responses to unblock frontend polling ([f7f1476](https://github.com/adrighem/PyPluginStore/commit/f7f1476e6cc9f711d716953b31e2108d84360d88))
* ignore version-like strings that look like IPs in User Agents ([08c386b](https://github.com/adrighem/PyPluginStore/commit/08c386b81ef27620efe3ba8d73c42c8748a8a179))
* refactor HTML to snippet and improve SPA init logic ([bab35ca](https://github.com/adrighem/PyPluginStore/commit/bab35caa6bcdeb0c19ee01a8ada2f7631209d787))
* refactor Repo button to simple anchor link ([2821b24](https://github.com/adrighem/PyPluginStore/commit/2821b24af9085886c622e98466e732751153291a))
* refine scanner to ignore version-like IPs and safe json.loads calls ([6fcb4b0](https://github.com/adrighem/PyPluginStore/commit/6fcb4b034ab8279a11210fee531f4d4ffdeceb00))
* resolve NameError for datetime and json imports ([4a6f748](https://github.com/adrighem/PyPluginStore/commit/4a6f748a0ac517bc444fb484a650c317563331b4))
* resolve NameError for home_folder in installDependencies ([15e8709](https://github.com/adrighem/PyPluginStore/commit/15e87091f24e0a6cd0829267a161da5dd12a5d1b))
* resolve XML encoding issue in plugin generator ([b48c517](https://github.com/adrighem/PyPluginStore/commit/b48c5172dc8bee841415e6b9edc27411e20b93e6))
* restore method indentation for is_private_ip ([540627c](https://github.com/adrighem/PyPluginStore/commit/540627cbb3038cd0b0f79b0328fcacbf6aad8005))
* revert to relative paths for subpath support ([facdfa2](https://github.com/adrighem/PyPluginStore/commit/facdfa254825c1096fe29d2377c7502d46e85761))
* update polling to use modern getdevices API syntax ([5c5b9c3](https://github.com/adrighem/PyPluginStore/commit/5c5b9c3089c8b62ba0a9803727bb9a7909c7c183))


### Documentation

* rename dashboard screenshot and update README with new UI instructions ([3130628](https://github.com/adrighem/PyPluginStore/commit/3130628212095524f18c3f67ea3e8ce5debbf8a1))

## [2.0.0](https://github.com/adrighem/PyPluginStore/compare/v1.5.47...v2.0.0) (2026-04-06)


### ⚠ BREAKING CHANGES

* configure release-please to start at 2.0.0 and update plugin files

### Features

* add monthly github action to discover domoticz plugins ([a3dd3df](https://github.com/adrighem/PyPluginStore/commit/a3dd3dff5e13045ebcf0bf60e67024573c7a7c30))
* bump version to 2.0.0 and add release-please workflow ([dce185b](https://github.com/adrighem/PyPluginStore/commit/dce185b34bb9280008a5aea551f94e6487ad862c))
* configure release-please to start at 2.0.0 and update plugin files ([d24f894](https://github.com/adrighem/PyPluginStore/commit/d24f894cad3f6aa4acd71d924be7046367fa69b6))


### Documentation

* update forum link in README ([871a666](https://github.com/adrighem/PyPluginStore/commit/871a666467a13ec764f927cd3ecbb3365560b1cd))
