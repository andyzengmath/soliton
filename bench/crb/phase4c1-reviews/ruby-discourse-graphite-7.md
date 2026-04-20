## Summary
32 files changed, 115 lines added, 115 lines deleted. 5 findings (5 critical, 0 improvements).
Bulk mechanical wrap of `scale-color($primary, $lightness: N%)` in `dark-light-choose(...)` to add dark-theme support. The intended transform preserves the light-mode value and complements the lightness for the dark-mode value (e.g. 70% → 30%). Five sites break that invariant and silently change the existing light-theme rendering.

## Critical
:red_circle: [correctness] Light-mode lightness changed from 70% to 30% in app/assets/stylesheets/mobile/modal.scss:102 (confidence: 98)
Before this PR `.custom-message-length` rendered with `scale-color($primary, $lightness: 70%)` (a light/faded hint color). The rewrite wraps it in `dark-light-choose`, but the light-mode argument is `scale-color($primary, $lightness: 30%)` — a much darker color — so the hint text on light themes changes visibly. The sibling rule in `app/assets/stylesheets/desktop/modal.scss:94` performs the correct transform (`$primary 70%` kept on the light side, `$secondary 30%` on the dark side), confirming this is a typo, not an intentional design change.
```suggestion
  color: dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%));
```

:red_circle: [correctness] Light-mode lightness changed from 30% to 70% in app/assets/stylesheets/desktop/topic-post.scss:291 (confidence: 98)
The inner `.reply-to-tab` / ".in-reply-to a" rule previously used `scale-color($primary, $lightness: 30%)` (dark, high-contrast link text). The new wrap swaps the light-mode arg to 70%, making the link text three tones lighter in the existing (light) theme. The dark-mode arg (`$secondary 30%`) is correct; only the light side regressed. Every other site in this PR preserves the original light-mode lightness.
```suggestion
      color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Light-mode lightness changed from 30% to 50% in app/assets/stylesheets/desktop/user.scss:522 (confidence: 97)
The `.name` rule in the user-card block used `scale-color($primary, $lightness: 30%)` (dark, name-emphasis color). The new value uses 50% on the light-mode side, flattening the visual hierarchy between `.name` (was darker/stronger) and the adjacent `.title` (50%, unchanged) — both now render identically in light mode. The intended transform should keep the light value at 30%.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Light-mode lightness changed from 20% to 50% in app/assets/stylesheets/mobile/topic-post.scss:182 (confidence: 97)
`h3` inside `.topic-list` / topic-meta block used `scale-color($primary, $lightness: 20%)` (near-headline-dark). The wrap sets the light-mode value to 50%, which is the `body`-text gray. This demotes the heading contrast in light themes to match body copy — a visibly wrong heading style. Sibling `desktop/topic-post.scss:321` correctly preserves 20%/80% for the parallel rule, confirming the intended paired values.
```suggestion
    color: dark-light-choose(scale-color($primary, $lightness: 20%), scale-color($secondary, $lightness: 80%));
```

:red_circle: [correctness] Light-mode lightness changed from 30% to 50% in app/assets/stylesheets/mobile/user.scss:497 (confidence: 97)
Mirror of the desktop `.name` defect. The mobile user-card `.name` rule previously emitted `scale-color($primary, $lightness: 30%)`; the light-mode arm now emits 50%, collapsing the name/title contrast in the light theme. Neighboring rules in the same block (including the `.title` rule immediately below) are transformed correctly — this stands out as an outlier.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

## Risk Metadata
Risk Score: 35/100 (LOW) | Blast Radius: 32 frontend SCSS files (common/desktop/mobile), 0 backend | Sensitive Paths: none
AI-Authored Likelihood: MEDIUM
