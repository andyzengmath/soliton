## Summary
3 files changed, 20 lines added, 12 lines deleted. 5 findings (3 critical, 2 improvements).
PR introduces auto-downsize on upload but silently overrides admin-configured size limits and redefines `OptimizedImage.downsize`, breaking every existing caller in the codebase.

## Critical

:red_circle: [correctness] Duplicate `self.downsize` definition shadows the 4-arg variant in `app/models/optimized_image.rb`:149 (confidence: 98)
In Ruby, when two methods share the same name, the later definition replaces the earlier one — there is no overloading by arity. The 4-arg `self.downsize(from, to, max_width, max_height, opts={})` defined at line ~144 is immediately overridden by the 3-arg `self.downsize(from, to, dimensions, opts={})` defined below it. Every existing caller in the codebase that invokes `OptimizedImage.downsize(from, to, width, height, opts)` will now raise `ArgumentError: wrong number of arguments (given 5, expected 2..3)` or silently pass a Hash as the `dimensions` string argument (depending on opts). Pick one signature. If you need both call shapes, keep the 4-arg wrapper and rename the new entrypoint (e.g., `downsize_to`) or accept an optional 4th arg with a sentinel.
```suggestion
  def self.downsize(from, to, max_width, max_height = nil, opts = {})
    dimensions = max_height.nil? ? max_width : "#{max_width}x#{max_height}"
    optimize("downsize", from, to, dimensions, opts)
  end
```

:red_circle: [correctness] Client-side size limit hardcoded to 10 MB regardless of upload type in `app/assets/javascripts/discourse/lib/utilities.js`:182 (confidence: 95)
The original code read `Discourse.SiteSettings['max_' + type + '_size_kb']`, which routes to distinct admin-configurable limits per upload type (`max_image_size_kb`, `max_avatar_size_kb`, `max_attachment_size_kb`, etc.). Hardcoding `10 * 1024` means: (a) avatar uploads now accept 10 MB client-side even though the server limit may be 100 KB, producing a confusing UX where the server rejects uploads the client accepted; (b) any admin who configured a lower or higher value for any type is silently ignored. Read the site setting — auto-downsize on the server side (the new `uploads_controller` hunk) does not justify removing the client gate.
```suggestion
    var maxSizeKB = Discourse.SiteSettings['max_' + type + '_size_kb'];
```

:red_circle: [correctness] Remote-URL upload cap hardcoded to 10 MB in `app/controllers/uploads_controller.rb`:55 (confidence: 92)
`FileHelper.download(url, 10.megabytes, ...)` discards `SiteSetting.max_image_size_kb`. For installations with the setting raised above 10 MB, API-initiated uploads of large-but-legal images will now fail at download time before the new downsize loop below even runs. For installations with it lowered, the download cap is higher than intended, expanding the surface for attacker-controlled remote-fetch abuse (SSRF-adjacent: unbounded byte pull from an arbitrary URL). Derive the cap from the site setting, and keep it consistent with the retry/downsize ceiling below.
```suggestion
        tempfile = FileHelper.download(url, SiteSetting.max_image_size_kb.kilobytes, "discourse-upload-#{type}") rescue nil
```

## Improvements

:yellow_circle: [correctness] Downsize loop may never terminate due to stale `tempfile.size` in `app/controllers/uploads_controller.rb`:65 (confidence: 72)
`OptimizedImage.downsize` writes to `tempfile.path` via `convert_with` (which shells out to ImageMagick and writes a new file at `to`). The `tempfile` object's size may be cached from the initial upload; on some platforms `Tempfile#size` stats the path and refreshes, but if the underlying file handle is buffered the condition `tempfile.size > SiteSetting.max_image_size_kb.kilobytes` can read stale bytes. Additionally, 5 iterations of 80% scaling yields ~32.8% of the original linear dimension (so ~10.7% area) — for a pathological 100 MB image with a 1 MB limit, five attempts leave a 10 MB result, still rejected downstream with no user feedback. Re-stat the path explicitly and compute the target scale from the actual ratio rather than fixed 80% * 5.
```suggestion
      if tempfile && tempfile.size > 0 && SiteSetting.max_image_size_kb > 0 && FileHelper.is_image?(filename)
        attempt = 5
        while attempt > 0 && File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
          OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: SiteSetting.allow_animated_thumbnails)
          attempt -= 1
        end
      end
```

:yellow_circle: [testing] No tests accompany the new auto-downsize path or the `optimize`/`downsize` signature change in `app/models/optimized_image.rb`:148 (confidence: 80)
The PR rewrites `self.optimize`'s parameter list (dropping `width, height` in favor of `dimensions`) and adds a server-side downsize loop on upload — both behavioral changes with high blast radius through `OptimizedImage` callers. No spec changes are included. At minimum, add coverage for: (a) `OptimizedImage.downsize` with a single `"NxM"` dimension string, (b) `OptimizedImage.downsize` with `(width, height)` positional pair (to catch the shadowing bug above), (c) `UploadsController#create_upload` with an oversized image that succeeds after downsize, and (d) an oversized image that still exceeds the limit after 5 attempts.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: `OptimizedImage.downsize`/`optimize` are called across image pipeline (uploads, thumbnails, avatars); client validation affects all upload types | Sensitive Paths: `uploads_controller.rb` (remote URL fetch, shell-out via convert_with)
AI-Authored Likelihood: MEDIUM
