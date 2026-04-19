## Summary
32 files changed, 106 lines added, 106 lines deleted. 5 findings (5 critical, 0 improvements, 0 nitpicks).
Mechanical SCSS migration wraps `scale-color($primary, $lightness: X%)` in `dark-light-choose(..., scale-color($secondary, $lightness: (100-X)%))`, but five call sites silently change the original *primary* lightness, causing visible light-theme regressions.

## Critical

:red_circle: [correctness] Primary lightness silently changed from 30% to 70% — light-theme link color regression in `app/assets/stylesheets/desktop/topic-post.scss`:619 (confidence: 98)
Original rule was `color: scale-color($primary, $lightness: 30%)`. The expected migration preserves the primary lightness and mirrors it on `$secondary`: `dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))`. Instead the diff emits `dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%))` — the two arms are transposed. On light themes the `.linked-to` anchor is now rendered at lightness 70% (much paler/faded) where the design originally called for 30% (darker, more legible). On dark themes the color is darker than intended (30% on `$secondary` instead of 70%). All other 30%-lightness call sites in the same file (e.g. line 260, 280, 333, 339) were migrated correctly, making this a localized copy-paste error.
```suggestion
      color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Primary lightness silently changed from 30% to 50% — user-card `.name` darker on light theme in `app/assets/stylesheets/desktop/user.scss`:772 (confidence: 96)
Original rule was `color: scale-color($primary, $lightness: 30%)`. Correct migration: `dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))`. Diff instead produces `dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))` — both arms collapsed to 50% which also happens to be the adjacent `.title` value. The user-card `.name` now renders at 50% lightness on light themes instead of the original 30%, eliminating the deliberate contrast between `.name` (darker, primary weight) and `.title` (lighter, secondary weight). Compare to `.username a` on diff line 334 which was correctly migrated `30% → 70%`.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Primary and secondary lightness transposed — `.custom-message-length` inverted on both themes in `app/assets/stylesheets/mobile/modal.scss`:871 (confidence: 97)
Original rule was `color: scale-color($primary, $lightness: 70%)`. Correct migration: `dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%))`. Diff produces `dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))` — both arms swapped. On light themes the character-count hint now renders at lightness 30% (much darker) instead of the original 70% (muted). On dark themes it renders at 70% on `$secondary` instead of the correct 30%. Notably, the structurally identical `.custom-message-length` rule in `desktop/modal.scss` (diff line 467) was migrated correctly as 70%/30%, making this a mobile-only divergence from the desktop counterpart.
```suggestion
  color: dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%));
```

:red_circle: [correctness] Primary lightness silently changed from 20% to 50% — linked-topic `h3` lost prominence on light theme in `app/assets/stylesheets/mobile/topic-post.scss`:942 (confidence: 97)
Original rule was `color: scale-color($primary, $lightness: 20%)`. Correct migration: `dark-light-choose(scale-color($primary, $lightness: 20%), scale-color($secondary, $lightness: 80%))`. Diff produces `dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))` — both arms collapsed to 50%. On light themes the heading renders at 50% lightness instead of the original 20% (much weaker/lighter than intended); on dark themes it renders at 50% instead of 80% (much darker). The desktop counterpart at diff line 633 is correctly migrated as 20%/80%, so this is a mobile-specific regression that breaks desktop/mobile parity for the `.linked-topic h3` element.
```suggestion
    color: dark-light-choose(scale-color($primary, $lightness: 20%), scale-color($secondary, $lightness: 80%));
```

:red_circle: [correctness] Primary lightness silently changed from 30% to 50% — mobile user-card `.name` mirrors desktop bug in `app/assets/stylesheets/mobile/user.scss`:1044 (confidence: 96)
Original rule was `color: scale-color($primary, $lightness: 30%)`. Correct migration: `dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))`. Diff produces `dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))`. Same class of error as `desktop/user.scss:772` — the 30% primary was replaced with a 50%/50% pair, eliminating the intended contrast between `.name` and the adjacent `.title` element. Because desktop and mobile both regressed identically, the systemic cause appears to be a rule in the migration script / author's mental model that incorrectly treated `30% → 50%/50%` as equivalent to the 50%/50% pass-through pattern used for genuine 50% sources.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

## Risk Metadata
Risk Score: 23/100 (LOW) | Blast Radius: SCSS stylesheets only, no JS/Ruby importers | Sensitive Paths: none hit
AI-Authored Likelihood: MEDIUM (106 uniform mechanical substitutions across 32 files; pattern strongly suggests scripted or AI-assisted batch transformation, with 5 localized deviations indicating either script edge-cases or manual edits that broke the rule)
