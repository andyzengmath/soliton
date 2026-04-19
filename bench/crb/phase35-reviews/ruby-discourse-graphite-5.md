## Summary
5 files changed, 54 lines added, 13 lines deleted. 9 findings (2 critical, 7 improvements).
Style-only refactor replacing `float`-based layout with new flexbox mixins introduces several latent layout no-ops (flex properties on non-flex children), removes spacing compensations without replacement, and ships a mixin library with dead/invalid vendor-prefixed declarations.

## Critical

:red_circle: [correctness] `@include order(2)` on `.extra-info-wrapper` is a no-op — parent is not a flex container in app/assets/stylesheets/common/base/topic.scss:27 (confidence: 95)
The diff adds `@include order(2)` to `.extra-info-wrapper`, but `order` only takes effect on a direct child of a flex/grid container. Nothing in this PR turns the parent of `.extra-info-wrapper` into a flex container — the only new flex container is `.d-header .contents` (header.scss), and `.extra-info-wrapper` is not a direct child of it. The declaration compiles cleanly but is silently ignored at runtime, so the intended reorder never happens. This is a logic error: the intent is visible, the effect is absent.
```suggestion
// Make the parent of .extra-info-wrapper a flex container first,
// e.g. if its parent is .topic-header-contents:
.topic-header-contents {
  @include flexbox();
  @include align-items(center);
}
.extra-info-wrapper {
  @include order(2);
  line-height: 1.5;
}
```

:red_circle: [correctness] `.panel` relies on being a direct child of `.contents` — any intermediate wrapper silently breaks the right-align in app/assets/stylesheets/common/base/header.scss:33 (confidence: 95)
`.panel` previously used `float: right` for right alignment. The diff removes the float and replaces it with `margin-left: auto; @include order(3);`, both of which require `.panel` to be a **direct** child of the new flex container (`.contents`). If `.panel` is nested inside any intermediate wrapper (e.g. a `.header-buttons`, `.title`, or `.contents > .wrapper` block that exists in some Discourse builds/themes), neither `margin-left: auto` nor `order: 3` applies, and `.panel` collapses to its intrinsic position with no right-alignment — a visible layout regression. Additionally, `.title { float: left }` is deleted entirely, so if `.title` is still a direct child of `.contents` it defaults to `order: 0` and participates in flex ordering implicitly, which may interleave unpredictably with `.panel`'s `order: 3` depending on source order.
```suggestion
.contents {
  margin: 8px 0;

  @include flexbox();
  @include align-items(center);

  // Make ordering of direct flex children explicit to avoid
  // relying on DOM source order:
  .title              { @include order(1); }
  .extra-info-wrapper { @include order(2); }
  .panel              { @include order(3); margin-left: auto; position: relative; }
}
```

## Improvements

:yellow_circle: [hallucination] `-ms-align-items` is not a real CSS property in app/assets/stylesheets/common/foundation/mixins.scss:141 (confidence: 95)
The `align-items` mixin emits `-ms-align-items: $alignment;`. Microsoft never shipped a vendor-prefixed `-ms-align-items` — IE10's 2012 intermediate flexbox used `-ms-flex-align` (already emitted on the preceding line), and IE11 implemented unprefixed `align-items`. This line is dead code that ships in every compiled stylesheet using the mixin and is silently dropped by all browsers, inflating output and misleading future maintainers into believing IE10 alignment is doubly-covered.
```suggestion
@mixin align-items($alignment) {
  -webkit-box-align: $alignment;
  -webkit-align-items: $alignment;
  -ms-flex-align: $alignment;
  align-items: $alignment;
}
```

:yellow_circle: [hallucination] `display: -moz-box` is not a valid CSS value for web content in app/assets/stylesheets/common/foundation/mixins.scss:122 (confidence: 90)
The `flexbox` mixin emits `display: -moz-box;`. Firefox never exposed `-moz-box` as a public CSS `display` value — it was a XUL-only primitive for browser-chrome layout and was never parsed for web HTML documents. Firefox jumped straight from no-flexbox to unprefixed `display: flex` at version 22 (2013). The declaration is invalid in web CSS and is dropped by every Firefox version.
```suggestion
@mixin flexbox() {
  display: -webkit-box;
  display: -ms-flexbox;
  display: -webkit-flex;
  display: flex;
}
```

:yellow_circle: [hallucination] `display: -moz-inline-box` is not a valid CSS value for web content in app/assets/stylesheets/common/foundation/mixins.scss:131 (confidence: 90)
Like `-moz-box`, `-moz-inline-box` was a XUL-only value and was never a valid CSS `display` for HTML documents. Firefox 22+ supports unprefixed `display: inline-flex`. The line is silently dropped by Gecko and adds noise to every compiled stylesheet that uses `@include inline-flex()`.
```suggestion
@mixin inline-flex() {
  display: -webkit-inline-box;
  display: -webkit-inline-flex;
  display: -ms-inline-flexbox;
  display: inline-flex;
}
```

:yellow_circle: [cross-file-impact] Removing `.d-header .title { float: left }` is a silent breaking change for themes/plugins that relied on that float context in app/assets/stylesheets/common/base/header.scss:13 (confidence: 90)
The diff deletes the `.title { float: left }` selector entirely, not just migrates it. Discourse supports third-party themes and plugins; any stylesheet that assumed `.d-header .title` established a left-floated box (e.g. for clearfix, sibling positioning, or a mobile override that overrode `float: left` back to `none`) now sees changed layout semantics with no compile-time warning. Even within core, removing the selector rather than keeping it as `{ /* now handled by flex parent */ }` loses a documentation anchor for downstream overrides.
```suggestion
// Preserve the selector as a flex-child anchor so that
// theme/plugin overrides still find a target, and document the change:
.title {
  // float: left; // removed — now a flex child of .contents (see above)
  @include order(1);
}
```

:yellow_circle: [correctness] `.small-action-desc` loses its 4em left indent with no replacement spacing — content abuts `.topic-avatar` in app/assets/stylesheets/common/base/topic-post.scss:277 (confidence: 87)
Before: `padding: 0.5em 0 0.5em 4em; margin-top: 5px;` — the 4em gave explicit visual separation from the avatar on the left. After: `padding: 0 1.5%;`. Once `.small-action` becomes `display: flex`, the gap between `.topic-avatar` and `.small-action-desc` is driven entirely by flex behavior, and no `gap`, `margin`, or compensating padding is introduced. 1.5% of container width is both narrower than the original 4em on typical desktop widths and variable, so system messages ("topic closed", "topic archived") will appear visually crammed against the avatar.
```suggestion
.small-action {
  @include flexbox();
  @include align-items(center);

  .small-action-desc {
    padding: 0 1.5%;
    margin-left: 1em; // restore separation from .topic-avatar lost with the 4em padding
    text-transform: uppercase;
    font-weight: bold;
    font-size: 0.9em;
  }
}
```

:yellow_circle: [correctness] `align-items` mixin forwards `$alignment` verbatim to `-webkit-box-align` / `-ms-flex-align`, which use a different value vocabulary in app/assets/stylesheets/common/foundation/mixins.scss:137 (confidence: 88)
`-webkit-box-align` and `-ms-flex-align` accept `start | end | center | baseline | stretch`, while modern `align-items` accepts `flex-start | flex-end | center | baseline | stretch`. Current callers pass `center` and `baseline`, which happen to be valid for both vocabularies, so this PR is safe today. But any future `@include align-items(flex-start)` or `@include align-items(flex-end)` will silently emit `-webkit-box-align: flex-start` (invalid, dropped) with no compile-time error — a latent footgun that undermines the whole purpose of the legacy prefixes.
```suggestion
@mixin align-items($alignment) {
  // Map modern flex-start/flex-end to legacy start/end for the 2009/2012 prefixes
  $legacy: if($alignment == flex-start, start,
            if($alignment == flex-end, end, $alignment));
  -webkit-box-align: $legacy;
  -webkit-align-items: $alignment;
  -ms-flex-align: $legacy;
  align-items: $alignment;
}
```

:yellow_circle: [consistency] `align-items` mixin uses 4-space indentation while every other mixin in the file uses 2-space in app/assets/stylesheets/common/foundation/mixins.scss:137 (confidence: 85)
The `flexbox`, `inline-flex`, and `order` mixins in this diff all use 2-space indentation for their declarations; only `align-items` uses 4-space indentation, and on one of its lines (`align-items:$alignment;`) drops the space after the colon. This breaks the file's internal convention and makes the mixin visually stand out for the wrong reason.
```suggestion
@mixin align-items($alignment) {
  -webkit-box-align: $alignment;
  -webkit-align-items: $alignment;
  -ms-flex-align: $alignment;
  align-items: $alignment;
}
```

## Risk Metadata
Risk Score: 17/100 (LOW) | Blast Radius: stylesheet-only, no JS/Ruby impact, but `mixins.scss` is a foundational file transitively imported by most Discourse stylesheets | Sensitive Paths: none
AI-Authored Likelihood: MEDIUM — the `mixins.scss` block is a textbook vendor-prefix flexbox boilerplate (widely copy-pasted from CSS-tricks / Bourbon-style references), with uniform structure and two provably-invalid declarations (`-ms-align-items`, `display: -moz-box`) characteristic of mechanical prefix expansion rather than hand-tested code.

(5 additional findings below confidence threshold: nitpick-level formatting issues in `mixins.scss` — trailing whitespace on `-ms-flex-align` line, extra blank lines introduced in `badges.css.scss`, inconsistent blank-line separation between mixin definitions, `.badge-wrapper.bullet { margin-top: 5px }` removal without compensation, and `-webkit-box-ordinal-group` 1-indexed vs `order` 0-indexed off-by-one for future callers passing `order(0)` or `order(1)`.)
