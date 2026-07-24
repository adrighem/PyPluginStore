# Release Tag Discovery Compatibility

## Goal

Recognize repositories whose stable releases use `v.1.2.3` tags without
mistaking prerelease tags for stable releases or broadening unrelated package
policies.

## Requirements

- Release discovery recognizes a finite allowlist of numeric stable-tag
  conventions: the default `1.2.3`/`v1.2.3` form and the dotted
  `v.1.2.3` form.
- Release display versions normalize both `v1.2.3` and `v.1.2.3` to
  `1.2.3`.
- Newly discovered packages receive the convention inferred from their latest
  recognized stable release.
- Existing packages using the default policy are moved to the dotted policy
  when the weekly scan later observes a dotted stable release.
- Package-specific reviewed tag policies remain unchanged.
- The Zigbee2Mqtt release index entry resolves to upstream stable release
  `v.3.1.0`.

## Acceptance Criteria

- Provider tests cover stable selection and version normalization for `v.`
  tags.
- Registry scanner tests cover new and existing package behavior.
- Registry validation and release-index generation tests pass.
- The generated runtime plugin remains synchronized with `plugin_core.py`.

## Out of Scope

- Automatic inference of arbitrary tag formats or arbitrary regular
  expressions.
- Prerelease-channel support.
- Changing Git-to-release migration safety checks.
