# Maintainer Patterns

- Keep card button copy compact, but do not shorten destructive or restorative
  confirmation text. The button selects the action; the confirmation names the
  verified target and consequence.
- Backend-owned action descriptors must remain intact through rendering. Do not
  reduce them to IDs and then recreate labels in the browser, especially when
  the label depends on verified backend state.
- Release Please omits default-hidden commit types such as `refactor:` from the
  generated changelog. Behavior-changing work must use `feat:` or `fix:`, and
  every release PR must still be audited against the complete tag-to-master
  commit range before merge.
- Generated runtime file: edit `plugin_core.py`, then run `python .github/scripts/generate_plugin.py`.
- Network-backed registry validation must use per-entry timeouts; one slow remote should produce a clear invalid result, not block release checks.
- Weekly registry discovery must require a non-empty root-level `plugin.py` before adding a repository; Domoticz-adjacent integrations, docs, Home Assistant bridges, MCP servers, scripts, and UI repos are not PyPluginStore plugins without that file.
- Git ownership repairs should prefer non-destructive, memory-only configuration overrides (`safe.directory` via `-c`) over disk-level permissions changes (`chown`), especially in Docker volume-mount environments, to avoid permission fights and file management friction on the host.
- Registry source order: remote public registry, bundled fallback, local ignored overlay.
- Installed detection is tiered: matching git remote, recognized `plugin.py` externallink, exact registry key, unique repo/archive folder name with flexible and Domoticz-affix-stripped normalization, then unique `plugin.py` key/name metadata.
- Local registry entries are explicit user intent and should win when they collide with public registry repository aliases.
- A local registry override is Git-only intent: do not consult a persisted public
  Release preference, attach a public Release target, or render a Release switch
  for that entry.
- A Local override over a Release-installed folder is intent, not a channel
  conversion. Block Git updates until **Return to previous Git version** or
  remove/reinstall provides a real Git checkout.
- Public packages do not expose a Release-to-Git switch. Direct users who want
  ongoing branch-based Git updates to use a local override instead.
- Preserve legacy and restore-created `keep_git` values as internal safety
  holds until a release-ID-scoped replacement exists; otherwise restoring Git
  can be undone by the next automatic migration.
- When `registry_local.json` exists but cannot be loaded, fail closed for Release
  management until it is repaired. A genuinely missing local file remains the
  normal public-registry path.
- Local registry writes must use backend-owned per-entry CRUD, exact-byte revisions, and atomic replacement; never let the browser replace the whole registry document or overwrite malformed/stale data.
- Keep plugin folder matching and UI plugin-name cleanup aligned. When changing flexible folder-name matching in the plugin, review/update the UI cleanup logic too, and vice versa, so the visible names users compare match the install-detection names PyPluginStore accepts.
- Custom UI bridge device discovery must include hidden Domoticz devices because users may hide the text payload device from normal overviews.
- Manager coherence needs an exact build ID as well as a semantic version:
  freeze the loaded runtime before self-update, recompute installed files,
  compare the exact deployed page and browser identity, and treat Git revision
  as diagnostic provenance only.
- Keep manager mismatch and self-update recovery guidance in
  `#pypluginstore-status`; card badges may stay compact, but card-specific detail
  renderers can hide or duplicate the authoritative recovery state.
- Manager identity mismatches fail closed for mutations. Legacy, stale, or
  malformed pages retain list/status and restart recovery but must hard-refresh
  before changing plugins or the Local registry.
- The custom UI bridge shares one text device for request and response data; always clear stale responses and treat response-looking payloads as stale bridge data, not inbound commands.
- API bridge error responses may omit `action`; accept them by matching `status: "error"` plus the same `tx_id`, then clear the bridge payload.
- Domoticz Python plugin APIs vary across supported Domoticz versions; optional APIs such as native notifications must be guarded with `hasattr`/`callable` checks.
- Startup update checks must write their result to `self.update_status`; the custom UI only renders green update buttons from the cached status map.
- Runtime/local files should be ignored rather than committed when they contain host-specific state.
- Contributor PRs often include useful product intent with mechanical diff issues; preserve the intent and rework in a smaller local patch.
- GitHub Actions should default to read-only, elevate permissions only on the trusted publishing job, avoid persisted checkout credentials for untrusted code, and pin every external action to a reviewed full commit SHA.
- Public forge release discovery belongs in repository automation; runtime hosts should consume one provider-neutral, digest-pinned index instead of calling GitHub, GitLab, Forgejo, or Gitea APIs.
- Provider `404` responses are not interchangeable: GitHub/GitLab can use them for missing or private repositories, while Forgejo/Gitea may expose a missing release collection that means no published releases. Preserve that distinction in reports.
- Forge-generated archives need an explicit public `User-Agent`, canonical timestamp normalization, and separate transport/tree hashes; commit-addressed contents can remain equivalent even when regenerated compression bytes change.
- Release artifacts never silently fall back to a branch update after verification failure. Automatic Git-to-release migration requires source provenance, safe ancestry, a clean checkout, and no unknown local files.
- Sequence and expiry in an unsigned update index are operational staleness guards, not cryptographic rollback/freeze protection; document the trust boundary and reserve signed metadata for that claim.
