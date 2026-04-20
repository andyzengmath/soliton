## Summary
3 files changed, 20 lines added, 12 lines deleted. 5 findings (2 critical, 3 improvements, 0 nitpicks).
Ruby method overwrite silently replaces 4-arg `self.downsize` in `optimized_image.rb:141`, and the new downsize loop in `uploads_controller.rb` can let oversized images through.

## Critical

:red_circle: [correctness] Ruby method overwrite: 4-arg `self.downsize` is silently replaced by 3-arg `self.downsize` in app/models/optimized_image.rb:141 (confidence: 99)
Ruby does not support method overloading. The diff defines `def self.downsize(from, to, max_width, max_height, opts={})` and then immediately defines a second `def self.downsize(from, to, dimensions, opts={})`. The second definition completely replaces the first. Any caller that still passes four positional arguments — `OptimizedImage.downsize(from, to, max_width, max_height)` — will have `max_width` bound to `dimensions` and `max_height` absorbed into the `opts={}` default as a non-hash, which will either raise `ArgumentError` or silently misbehave. The `uploads_controller.rb` call uses the new 3-arg signature correctly, but any other callers in the codebase (plugins, jobs, other models) break at runtime with no compile-time warning.
```suggestion
def self.downsize(from, to, dimensions, opts={})
  optimize("downsize", from, to, dimensions, opts)
end
# Remove the 4-arg definition entirely. Update any remaining callers that pass
# max_width/max_height to pre-format the dimension string: "#{max_width}x#{max_height}".
```

:red_circle: [correctness] Downsize loop may not terminate correctly and oversized images silently pass through in app/controllers/uploads_controller.rb:63 (confidence: 95)
The `while attempt > 0 && tempfile.size > SiteSetting.max_image_size_kb.kilobytes` loop has two correctness problems. (1) `tempfile.size` reads from the `Tempfile` handle, which may return a cached/original size after ImageMagick overwrites the file in place — the loop can burn all 5 attempts even when the first downsize succeeded, or miss a successful early exit. (2) There is no post-loop guard: if downsizing fails (ImageMagick unavailable, unsupported format) or 5 iterations at 80% (final size ≈ 33 % of original) still exceed the limit, the oversized file is passed to `Upload.create_for` with no error and no rejection returned to the client.
```suggestion
max_size = SiteSetting.max_image_size_kb.kilobytes
attempt = 5
while attempt > 0 && File.size(tempfile.path) > max_size
  OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: SiteSetting.allow_animated_thumbnails)
  attempt -= 1
end
if File.size(tempfile.path) > max_size
  return render json: failed_json.merge(errors: [I18n.t("upload.images.too_large", max_size_kb: SiteSetting.max_image_size_kb)]), status: 422
end
```

## Improvements

:yellow_circle: [correctness] Hardcoded 10 MB limit ignores `type` parameter and desyncs from server-side SiteSetting in app/assets/javascripts/discourse/lib/utilities.js:182 (confidence: 95)
The replaced line used `Discourse.SiteSettings['max_' + type + '_size_kb']` for a per-upload-type limit (image vs attachment vs avatar, etc.). The new `var maxSizeKB = 10 * 1024;` applies a uniform 10 MB cap regardless of `type`, so the `type` parameter is effectively dead for size enforcement. Non-image uploads whose server-configured limit is smaller (e.g., `max_attachment_size_kb=1024`) are now permitted up to 10 MB client-side and only rejected after upload, degrading UX. The `max_size_kb` value shown in the error message will also always read `10240`, misleading users about the actual server policy. The project pattern uses `SiteSettings` for all configuration-driven values so administrators can tune limits without code changes.
```suggestion
var maxSizeKB = Discourse.SiteSettings['max_' + type + '_size_kb'];
```

:yellow_circle: [consistency] Hardcoded magic number replaces dynamic SiteSetting lookup in 413 error handler in app/assets/javascripts/discourse/lib/utilities.js:246 (confidence: 95)
Line 246 replaces `Discourse.SiteSettings.max_image_size_kb` with hardcoded `10 * 1024` in the 413 error handler. The project pattern uses `SiteSettings` for configuration-driven values; hardcoding duplicates the magic number and prevents admins from adjusting the limit shown to users. If the server-side limit is changed, the client-side error message will display the wrong value (`10240` KB) instead of the actual configured limit.
```suggestion
var maxSizeKB = Discourse.SiteSettings.max_image_size_kb;
```

:yellow_circle: [consistency] Hardcoded `10.megabytes` replaces `SiteSetting.max_image_size_kb.kilobytes` for URL download cap in app/controllers/uploads_controller.rb:55 (confidence: 90)
Line 55 replaces `SiteSetting.max_image_size_kb.kilobytes` with hardcoded `10.megabytes` for the API URL-download size cap. This is inconsistent with the project's config-driven pattern and internally inconsistent with the new downsize loop a few lines below (lines 63-70), which does read `SiteSetting.max_image_size_kb`. A site admin who lowers `max_image_size_kb` will now have URL downloads still accept 10 MB, violating the admin's configured policy and creating a bypass path for the size limit.
```suggestion
tempfile = FileHelper.download(url, SiteSetting.max_image_size_kb.kilobytes, "discourse-upload-#{type}") rescue nil
```

## Risk Metadata
Risk Score: 14/100 (LOW) | Blast Radius: low (3 files, 32 lines total) | Sensitive Paths: none
AI-Authored Likelihood: LOW
