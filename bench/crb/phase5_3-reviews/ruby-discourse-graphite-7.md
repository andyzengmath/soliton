## Summary
32 files changed, 95 lines added, 95 lines deleted. 5 findings (5 critical, 0 improvements, 0 nitpicks).
Mechanical wrap of `scale-color($primary, $lightness: X%)` in `dark-light-choose(..., scale-color($secondary, $lightness: (100-X)%))` to add dark-theme support; in five places the LIGHT-path lightness was also altered, regressing light-theme appearance against the PR's stated intent.

## Critical

:red_circle: [correctness] Light-theme regression: reply `a` color flipped from 30% to 70% lightness in `app/assets/stylesheets/desktop/topic-post.scss`:291 (confidence: 99)
The PR's intent is to preserve the existing light path while adding a dark-theme companion (light = `$primary X%`, dark = `$secondary (100-X)%`). On this line the original was `scale-color($primary, $lightness: 30%)`; the new light path is `scale-color($primary, $lightness: 70%)` — i.e. the bold reply link, previously a strong dark-on-light tone, will now render as a faint near-background gray in light themes. This is a visible regression, not an additive change. Cross-check: every other `30%` conversion in this PR (e.g. `common/base/topic-post.scss:14`, `common/base/user.scss:118`) correctly preserves `30%` on the light path.
```suggestion
      color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Light-theme regression: `.name` color shifted from 30% to 50% lightness in `app/assets/stylesheets/desktop/user.scss`:522 (confidence: 99)
Original: `scale-color($primary, $lightness: 30%)`. New light path: `scale-color($primary, $lightness: 50%)`. The user `.name` previously rendered at the same dark tone as `.username a` two lines above (which the PR converted correctly with 30%/70%); after this change the name becomes noticeably dimmer in light themes and visually inconsistent with the username it sits next to. The dark path's `50%` second argument also no longer follows the `100 − X` rule the rest of the PR uses.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Light-theme regression: `.custom-message-length` color flipped from 70% to 30% lightness in `app/assets/stylesheets/mobile/modal.scss`:102 (confidence: 99)
Original: `scale-color($primary, $lightness: 70%)` (a faint hint color). New light path: `scale-color($primary, $lightness: 30%)` — a dark, high-contrast tone, the opposite of a hint. The desktop counterpart at `app/assets/stylesheets/desktop/modal.scss`:94 was converted correctly to `70%/30%`, so mobile diverges from desktop and from the PR's pattern. The light and dark args here also look like they were transposed (light got the `100-X` value, dark got the original).
```suggestion
  color: dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%));
```

:red_circle: [correctness] Light-theme regression: `h3` color shifted from 20% to 50% lightness in `app/assets/stylesheets/mobile/topic-post.scss`:182 (confidence: 99)
Original: `scale-color($primary, $lightness: 20%)` (near-foreground heading tone). New light path: `scale-color($primary, $lightness: 50%)` — a mid-gray, much weaker than a heading. The desktop sibling at `app/assets/stylesheets/desktop/topic-post.scss`:321 correctly preserved `20%` on the light path with `80%` on the dark path; mobile silently weakens the heading and breaks parity with desktop. Same divergence applies to the `100-X` rule on the dark path (`50%` should be `80%`).
```suggestion
    color: dark-light-choose(scale-color($primary, $lightness: 20%), scale-color($secondary, $lightness: 80%));
```

:red_circle: [correctness] Light-theme regression: `.name` color shifted from 30% to 50% lightness in `app/assets/stylesheets/mobile/user.scss`:497 (confidence: 99)
Original: `scale-color($primary, $lightness: 30%)`. New light path: `scale-color($primary, $lightness: 50%)`. Mirrors the desktop bug at `desktop/user.scss`:522 — the user's name renders dimmer in light themes than it did before the PR, and the `.title` directly below it (which was already at 50%) now collapses visually with the `.name` instead of being a quieter sibling. Both the light and dark lightness values are inconsistent with the additive pattern used everywhere else in this file.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

## Risk Metadata
Risk Score: 18/100 (LOW) | Blast Radius: pure SCSS under `app/assets/stylesheets/**`, 32 files, no JS/Ruby/template consumers; theme-only visual change | Sensitive Paths: none
AI-Authored Likelihood: LOW
