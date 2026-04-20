## Summary
5 files changed, 44 lines added, 12 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
FrozenError at runtime — frozen string literal mutated in user_serializer.rb:148.

## Critical

:red_circle: [correctness] FrozenError at runtime — `"." << website_host` mutates a frozen string literal in app/serializers/user_serializer.rb:148 (confidence: 85)
The expression `discourse_host.ends_with?("." << website_host)` uses `String#<<` (mutating append) on the string literal `"."`. In Ruby files with `# frozen_string_literal: true` at the top — standard in Rails 6+ codebases — string literals are frozen. Calling `<<` on a frozen string raises `FrozenError` at runtime for any user whose `website_host` reaches the else branch, resulting in a 500 error when serializing that user. Two agents flagged this line: correctness identified the runtime crash risk; consistency independently noted the non-idiomatic mutation pattern on the same token.
```suggestion
discourse_host.ends_with?(".#{website_host}") ? website_host + path : website_host
```

## Improvements

:yellow_circle: [correctness] `include_website_name` allows nil `website_name` through, emitting a spurious null JSON key in app/serializers/user_serializer.rb:134 (confidence: 90)
`include_website_name` returns true whenever `website` is present, but `website_name` can return nil when the URI parses successfully yet yields a nil host (e.g. relative URLs, opaque URIs such as "javascript:void(0)", or "file:///path"). The serializer will then include `website_name: null` in the JSON payload even though the include guard was intended to suppress that. Any API consumer relying on the include contract will observe an unexpected null value.
```suggestion
def include_website_name
  website.present? && URI(website.to_s).host.present? rescue false
end
```

:yellow_circle: [consistency] Template references snake_case `model.website_name` instead of camelCase `model.websiteName` in app/assets/javascripts/discourse/templates/user/user.hbs:66 (confidence: 85)
The template uses `model.website_name` (snake_case), but Discourse's Ember RestModel convention automatically camelCases attributes returned from serializers. The property should be referenced as `model.websiteName` to match the framework's standard behavior, where serializer snake_case fields become camelCase in the JS model layer. As written, the binding will silently resolve to undefined at runtime.
```suggestion
{{#if model.websiteName}}
  {{fa-icon "globe"}}
  {{#if linkWebsite}}
    <a href={{model.website}} rel={{unless removeNoFollow 'nofollow'}} target="_blank">{{model.websiteName}}</a>
  {{else}}
    <span title={{model.website}}>{{model.websiteName}}</span>
  {{/if}}
{{/if}}
```

## Risk Metadata
Risk Score: 14/100 (LOW) | Blast Radius: 0 importers detected (shim repo) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
