## Summary
3 files changed, 20 lines added, 12 lines deleted. 6 findings (5 critical, 1 improvement, 0 nitpicks).
Duplicate `self.downsize` definition silently overrides the 5-arg form and breaks all existing callers; the controller adds an SSRF-weakening hardcoded download cap, a stale-cache-driven ImageMagick retry loop that enables CPU DoS and silently admits oversized uploads, and reintroduces exposure to a shell-interpolation sink in `convert_with`.

## Critical

:red_circle: [correctness] Duplicate `self.downsize` definition — 5-arg form is silently overridden, breaking all existing callers in app/models/optimized_image.rb:142 (confidence: 98)
After this patch, `optimized_image.rb` contains two `def self.downsize` definitions in the same class body. Ruby does not support method overloading: the second definition (4-arg: `from, to, dimensions, opts={}`) completely and silently replaces the first (5-arg: `from, to, max_width, max_height, opts={}`). Any existing caller that passes five positional arguments — e.g. `OptimizedImage.downsize(from, to, 800, 600, opts)` — will raise `ArgumentError: wrong number of arguments (given 5, expected 3..4)` at runtime. The 5-arg form that pre-formats the dimension string (`"#{max_width}x#{max_height}"`) is dead code that never executes.
```suggestion
# Remove the old 5-arg definition entirely. Keep only:
def self.downsize(from, to, dimensions, opts={})
  optimize("downsize", from, to, dimensions, opts)
end
# Update all callers to pre-format the dimension string:
# OptimizedImage.downsize(from, to, "#{max_width}x#{max_height}")
```

:red_circle: [correctness] Stale `tempfile.size` cache + no convergence guard — loop always exhausts all 5 attempts and silently accepts oversized result in app/controllers/uploads_controller.rb:63 (confidence: 95)
Two compounding defects in the downsize retry loop. First, `Tempfile#size` returns a cached `File::Stat` value that is not invalidated when ImageMagick overwrites the file in-place; the loop therefore cannot detect that the file shrank, and always runs all 5 iterations even after the target size is reached. Second, if `OptimizedImage.downsize` fails silently (ImageMagick non-zero exit, errors swallowed, or `"80%"` geometry rejected), the file size never decreases. The loop exhausts all 5 attempts applying lossy compression to an unchanging or corrupted file, then continues to `Upload.create_for` with a file that is still over the size limit. No error is surfaced to the caller; an oversized or corrupted upload is silently accepted.
```suggestion
previous_size = nil
while attempt > 0 && File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
  current_size = File.size(tempfile.path)
  break if current_size == previous_size   # no progress — stop early
  previous_size = current_size
  OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: SiteSetting.allow_animated_thumbnails)
  attempt -= 1
end
if File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
  return render_json_error(I18n.t("upload.images.too_large", max_size_kb: SiteSetting.max_image_size_kb))
end
```

:red_circle: [security] Hardcoded 10MB download limit bypasses admin-configured SSRF/size control in app/controllers/uploads_controller.rb:55 (confidence: 90)
`FileHelper.download(url, 10.megabytes, ...)` replaces a previously configurable `SiteSetting.max_image_size_kb.kilobytes` cap. If an admin lowered `max_image_size_kb` to mitigate bandwidth/disk DoS or SSRF data exfiltration risk, attackers can now force the server to fetch up to 10MB per API request before rejection. The download target is a user-supplied URL, making this an SSRF-reachable path. The setting is still read a few lines later for post-download resizing, creating inconsistent enforcement where the download phase and the resize phase use different limits.
```suggestion
max_bytes = SiteSetting.max_image_size_kb.kilobytes
tempfile = FileHelper.download(url, max_bytes, "discourse-upload-#{type}") rescue nil
```
[References: https://cwe.mitre.org/data/definitions/918.html, https://cwe.mitre.org/data/definitions/400.html]

:red_circle: [security] Synchronous 5x ImageMagick downsize loop in request path enables CPU/memory DoS in app/controllers/uploads_controller.rb:63 (confidence: 85)
The block runs `OptimizedImage.downsize` up to 5 times synchronously inside the controller request lifecycle on attacker-supplied image content. ImageMagick decode and resize operations are CPU- and memory-intensive; adversarial inputs (image bombs, deeply layered or animated content) can consume significant resources per iteration. Five iterations multiply that cost, blocking the web worker for the duration. Because the loop checks file size via a stale Tempfile cache (see related correctness finding), a pathological input will always run all 5 iterations. Concurrent adversarial uploads can exhaust the worker pool and deny service to legitimate users.
```suggestion
# Reject uploads that exceed the configured limit at intake instead of downsizing them inline.
# If inline downsizing is required, move it to a background job with explicit ImageMagick
# resource limits (-limit memory, -limit map, -limit time).
if tempfile && FileHelper.is_image?(filename) && tempfile.size > SiteSetting.max_image_size_kb.kilobytes
  return render_json_error(I18n.t("upload.images.too_large", max_size_kb: SiteSetting.max_image_size_kb))
end
```
[References: https://cwe.mitre.org/data/definitions/400.html, https://imagetragick.com/]

:red_circle: [security] Shell command built via string interpolation of instructions array — shell injection sink in app/models/optimized_image.rb:160 (confidence: 80)
`convert_with` executes the instructions array via backtick interpolation: `` `#{instructions.join(" ")} &> /dev/null` ``. Backticks invoke `/bin/sh -c`, and the command string is constructed by joining array elements with spaces and interpolating into a shell string. Any element containing shell metacharacters (`;`, `&`, `|`, backticks, `$()`, redirection, unquoted whitespace, quotes) will be interpreted by the shell. This is a pre-existing sink, but this PR exercises it up to 5 times per upload via the new retry loop, amplifying the blast radius of any upstream injection bug where filename-derived or SiteSetting-derived values appear in the instructions array.
```suggestion
require "open3"

def self.convert_with(instructions, to)
  _, stderr, status = Open3.capture3(*instructions)
  unless status.success?
    Rails.logger.warn("ImageMagick convert failed: #{stderr}")
    return false
  end
  true
end
```
[References: https://cwe.mitre.org/data/definitions/78.html]

## Improvements

:yellow_circle: [correctness] Client-side max file size hardcoded to 10MB — breaks per-type admin settings and diverges from settings-driven design in app/assets/javascripts/discourse/lib/utilities.js:182 (confidence: 85)
The original code read `Discourse.SiteSettings['max_' + type + '_size_kb']` to enforce per-type, admin-configured upload size limits. The replacement hardcodes `10 * 1024` KB. This produces two UX and policy problems: (1) if an admin has lowered `max_image_size_kb` below 10MB, users receive no client-side rejection between the configured limit and 10MB — they must wait for a server-side 413 to learn the upload was invalid, wasting bandwidth and producing a confusing UX; (2) if an admin has raised the limit above 10MB, valid uploads are incorrectly rejected client-side. The hardcoded value also diverges from the established settings-driven design pattern in this codebase, reducing flexibility and making policy auditing harder. The server remains authoritative, so this is not directly exploitable, but it constitutes a significant UX regression and a maintenance/consistency hazard. (Merged from `correctness` and `consistency` agents, which flagged the same root cause independently.)
```suggestion
var maxSizeKB = Discourse.SiteSettings['max_' + type + '_size_kb'];
```

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: sensitive-path hit on `app/controllers/uploads_controller.rb` (uploads pattern); test-coverage gap 100/100 (3/3 production files touched with zero tests); change-complexity 13/100; file-size-scope 10/100 | Sensitive Paths: app/controllers/uploads_controller.rb
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)

---

### Review metadata
- Agents dispatched: 6 (risk-scorer, correctness, security, cross-file-impact, consistency, hallucination)
- Agents completed: 6
- Agents failed: none
- Recommendation: request-changes
- Review duration: 297303 ms
- **Caveat:** the working directory for this review was a git-metadata shim with no source files on disk. The `cross-file-impact` and `hallucination` agents both returned `FINDINGS_NONE` because they could not grep the repo. This is **not** a clean bill of health on those axes — caller-site breakage from the `self.downsize` / `self.optimize` signature changes and the removal of `self.dimensions` was therefore not verified against the rest of the Discourse codebase, and API-name existence (e.g., `SiteSetting.allow_animated_thumbnails`) was not cross-checked.
