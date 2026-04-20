## Summary
32 files changed, 104 lines added, 104 lines deleted. 5 findings (2 critical, 3 improvements).
PR is a mechanical refactor wrapping every `scale-color($primary, $lightness: N%)` in `dark-light-choose(scale-color($primary, $lightness: N%), scale-color($secondary, $lightness: (100-N)%))` so dark themes pick the secondary palette. Of ~104 substitutions, 5 deviate from the pattern: 2 swap the light-arg with the dark-arg (visible shade flips) and 3 silently alter the light-theme lightness value.

## Critical
:red_circle: [correctness] Light-arg / dark-arg swapped on `.post-controls .replies a` in app/assets/stylesheets/desktop/topic-post.scss:291 (confidence: 97)
Original rule was `color: scale-color($primary, $lightness: 30%)`. The substitution produced `dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%))` — the two arguments have been transposed. `dark-light-choose(light-val, dark-val)` gives light themes the first arg, so light-theme users now render this bold reply-link at 70% lightness (nearly washed out) instead of the original 30% (visibly dark). The pair sums to 100, but the pre-PR intent was for light themes to keep their existing shade; this is the only 30%→(70%,30%) hunk in the PR — every other `$lightness: 30%` migration in the diff correctly produces `(30%, 70%)`.
```suggestion
      color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:red_circle: [correctness] Light-arg / dark-arg swapped on `.custom-message-length` in app/assets/stylesheets/mobile/modal.scss:102 (confidence: 97)
Original rule was `color: scale-color($primary, $lightness: 70%)` (a light muted hint for character-count feedback). The substitution produced `dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))` — arguments transposed. Light-theme mobile users will render the hint at 30% lightness (visibly dark primary) instead of the original 70% muted gray. The desktop counterpart at app/assets/stylesheets/desktop/modal.scss (same `.custom-message-length` rule) correctly uses `(70%, 30%)`, so the mobile override is now inconsistent with desktop and with the rest of the PR.
```suggestion
  color: dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%));
```

## Improvements
:yellow_circle: [correctness] Light-theme lightness silently changed 30% → 50% on second-factor `.name` in app/assets/stylesheets/desktop/user.scss:522 (confidence: 92)
Original rule was `.name { color: scale-color($primary, $lightness: 30%); }`. The substitution produced `dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))` — the light-arg silently promoted to 50%. This collapses the original visual distinction from the sibling `.title` rule at line 526, which is also 50%/50%: `.name` (name, darker) and `.title` (job title, lighter) now render identically in both themes. Every other 30% migration in this PR correctly preserves 30% in the light-arg.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:yellow_circle: [correctness] Light-theme lightness silently changed 30% → 50% on second-factor `.name` in app/assets/stylesheets/mobile/user.scss:497 (confidence: 92)
Same mistake as desktop/user.scss:522 in the mobile copy. Original was `.name { color: scale-color($primary, $lightness: 30%); }` and the substitution produced `(50%, 50%)`. Mobile light theme users will also lose the `.name` vs `.title` visual contrast.
```suggestion
        color: dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%));
```

:yellow_circle: [correctness] Light-theme lightness silently changed 20% → 50% on link-preview `h3` in app/assets/stylesheets/mobile/topic-post.scss:182 (confidence: 92)
Original rule was `h3 { color: scale-color($primary, $lightness: 20%); }` (a near-primary dark color for onebox/link-preview heading text). The substitution produced `dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))` — the light-arg silently promoted from 20% to 50%. The parallel desktop rule at app/assets/stylesheets/desktop/topic-post.scss:321 (same `h3` block) correctly preserves 20%, so mobile and desktop will now render link-preview headings at different lightnesses on light themes.
```suggestion
    color: dark-light-choose(scale-color($primary, $lightness: 20%), scale-color($secondary, $lightness: 80%));
```

## Risk Metadata
Risk Score: 24/100 (LOW) | Blast Radius: 32 SCSS files in app/assets/stylesheets/ (no Ruby/JS callers, CSS-cascade-only impact) | Sensitive Paths: none
AI-Authored Likelihood: MEDIUM (all 104 substitutions follow an extremely uniform scripted pattern; the 5 deviations are the kind of off-by-one slips consistent with a regex-assisted bulk rewrite)
