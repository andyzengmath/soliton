## Summary
3 files changed, 20 lines added, 12 lines deleted. 6 findings (2 critical, 4 improvements).
Image downsizing logic introduces a duplicate `self.downsize` definition that silently shadows the four-arg variant, a breaking signature change to `optimize`, and a retry loop whose termination check relies on stale `Tempfile#size` data.

## Critical
:red_circle: [correctness] Duplicate `self.downsize` definition silently overwrites the four-arg version in app/models/optimized_image.rb:144 (confidence: 98)
Two `def self.downsize` methods are declared back-to-back. In Ruby, the second definition replaces the first, so the four-positional-arg form `downsize(from, to, max_width, max_height, opts={})` becomes unreachable — only the three-arg `downsize(from, to, dimensions, opts={})` survives. Any caller still passing `(from, to, max_width, max_height)` will now receive `max_width` as the `dimensions` string and raise `ArgumentError` (or worse, silently call `optimize` with wrong arguments). Either rename one of the methods, collapse them into a single method that branches on argument shape, or delete the four-arg form explicitly with a deprecation note.
```suggestion
  def self.downsize(from, to, dimensions, opts={})
    optimize("downsize", from, to, dimensions, opts)
  end
```

:red_circle: [cross-file-impact] Public API signature change for `OptimizedImage.optimize`/`resize`/`downsize` breaks external callers in app/models/optimized_image.rb:141 (confidence: 92)
`optimize` changed from `(operation, from, to, width, height, opts={})` to `(operation, from, to, dimensions, opts={})`, and `resize`/`downsize` now embed `"#{width}x#{height}"` at the call site. Any caller outside this PR (plugins, jobs, tests, other controllers) that passed five positional args will now bind `width` to the `dimensions` parameter and `height` to `opts`, producing a corrupt ImageMagick instruction string and a TypeError when `opts[:allow_animation]` is read on a numeric. The removed `self.dimensions(width, height)` helper is also a public-ish module method that may be referenced elsewhere. Audit all in-tree callers before merge and consider keeping the helper as a thin shim during the transition.
```suggestion
  # Keep the helper to avoid breaking out-of-tree callers
  def self.dimensions(width, height)
    "#{width}x#{height}"
  end
```

## Improvements
:yellow_circle: [correctness] Hardcoded 10 MB cap bypasses `max_image_size_kb` / `max_attachment_size_kb` site settings in app/assets/javascripts/discourse/lib/utilities.js:182 (confidence: 90)
`maxSizeKB = 10 * 1024` (and the duplicate at line 246) replaces the dynamic `Discourse.SiteSettings['max_' + type + '_size_kb']` lookup. Admins who configured a smaller cap in Site Settings will see client-side validation accept files the server still rejects, producing confusing 422/413 errors instead of the existing inline message. The same hardcoding appears server-side in `uploads_controller.rb:55` (`FileHelper.download(url, 10.megabytes, ...)`). Source these from `SiteSettings` consistently — the new "downsize on upload" feature should raise the *download* cap, not eliminate the configurable validation cap.
```suggestion
    var maxSizeKB = Discourse.SiteSettings['max_' + type + '_size_kb'];
```

:yellow_circle: [correctness] Retry loop reads stale `tempfile.size` after in-place rewrite in app/controllers/uploads_controller.rb:64 (confidence: 88)
The loop calls `OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", ...)` and then re-checks `tempfile.size` to decide whether to continue. `Tempfile#size` is forwarded to the underlying `File` object, which caches the stat result — after ImageMagick rewrites the file out-of-band the size reported here can lag, causing either premature exit (file still too large but loop thinks it shrank enough) or an extra unnecessary pass. Re-stat explicitly with `File.size(tempfile.path)` (and call `tempfile.rewind` if the file handle will be re-read downstream by `Upload.create_for`).
```suggestion
      if tempfile && tempfile.size > 0 && SiteSetting.max_image_size_kb > 0 && FileHelper.is_image?(filename)
        attempt = 5
        while attempt > 0 && File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
          OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: SiteSetting.allow_animated_thumbnails)
          attempt -= 1
        end
        tempfile.rewind
      end
```

:yellow_circle: [correctness] Loop may exhaust attempts and still upload an over-limit image in app/controllers/uploads_controller.rb:62 (confidence: 85)
After five 80 %-of-previous-size passes the file is ~33 % of the original; for a user uploading a 30 MB photo with a 5 MB cap this still leaves a ~10 MB file that proceeds to `Upload.create_for`. The loop has no fallback when `attempt` hits zero with the file still over `max_image_size_kb` — the outer code will then try to persist an upload that violates the site's own limit, defeating the purpose of the cap. Either reject with `render_400_with_custom_error` when the loop exits over-limit, or compute the required scale ratio analytically (`sqrt(target / current)`) so a single pass guarantees compliance.

:yellow_circle: [testing] New auto-downsize feature has no test coverage in app/controllers/uploads_controller.rb:63 (confidence: 80)
The new branch (image detection + retry loop + ImageMagick invocation) is non-trivial control flow over uploaded user data — exactly the surface where a regression silently corrupts files or bypasses size limits. There are no spec changes accompanying the controller change. Add at least: (1) a request spec exercising upload of an oversized image and asserting the persisted upload is within `max_image_size_kb`; (2) a model spec for `OptimizedImage.downsize/3` and the new `optimize` signature, including the duplicate-method shadowing behavior identified above.

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: image-processing core + upload controller (server) + client-side validator | Sensitive Paths: app/controllers/uploads_controller.rb (upload boundary, user-supplied file processing), app/models/optimized_image.rb (shells out to ImageMagick via backticks)
AI-Authored Likelihood: LOW
