## Summary
3 files changed, 20 lines added, 12 lines deleted. 8 findings (6 critical, 2 improvements, 0 nitpicks).
Duplicate `self.downsize` definition in `optimized_image.rb` silently overrides the four-argument version; several companion issues (stale tempfile size, hardcoded upload ceilings, decompression-bomb DoS, missing tests) compound the risk.

## Critical

:red_circle: [correctness] Duplicate `self.downsize` definition silently overrides the four-argument version in app/models/optimized_image.rb:64 (confidence: 99)
The diff defines `self.downsize(from, to, max_width, max_height, opts={})` at line 64 and `self.downsize(from, to, dimensions, opts={})` at line 71 in the same class body. Ruby's last-definition-wins semantics silently discard the first method. Any existing caller passing four positional arguments (from, to, width, height) will bind the height value into `opts`, producing a TypeError or malformed ImageMagick geometry string at runtime. The author's apparent intent for both forms to coexist is not achievable without method overloading or explicit arity dispatch, which Ruby does not support natively. Additionally, the `optimize` method's arity changed from 6 to 5 arguments in the same diff, and any caller of the now-removed `OptimizedImage.dimensions(w, h)` helper will receive `NoMethodError`.
```suggestion
# Remove the first (dead) four-arg definition and keep only the dimension-string form:
def self.downsize(from, to, dimensions, opts={})
  optimize("downsize", from, to, dimensions, opts)
end
```
[References: Ruby method redefinition semantics — https://ruby-doc.org/core/Module.html]

:red_circle: [correctness] While-loop re-reads stale `tempfile.size` after in-place downsize in app/controllers/uploads_controller.rb:63 (confidence: 95)
After `OptimizedImage.downsize(tempfile.path, tempfile.path, ...)` writes to the file via an external ImageMagick shell invocation, the Ruby `Tempfile` object's cached stat is not automatically refreshed. Calling `tempfile.size` returns the value from before the write, so the loop condition may remain true even after the file has been reduced below the threshold. This can cause all five attempts to execute unnecessarily, or cause the loop to exit on the wrong iteration boundary and produce an incorrect size report downstream.
```suggestion
if tempfile && File.size(tempfile.path) > 0 && SiteSetting.max_image_size_kb > 0 && FileHelper.is_image?(filename)
  attempt = 5
  while attempt > 0 && File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
    OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: SiteSetting.allow_animated_thumbnails)
    attempt -= 1
  end
end
```

:red_circle: [testing] Refactored `downsize`, `optimize`, and `resize` signatures have no test coverage in app/models/optimized_image.rb:64 (confidence: 95)
Method signatures for `downsize`, `optimize`, and `resize` changed in this diff; the `dimensions` helper was removed entirely. No spec file appears in the diff, and no `spec/models/optimized_image_spec.rb` exists in the repository. The silent method-override bug (two `self.downsize` definitions) would be caught immediately by a minimal RSpec stub of `convert_with` combined with an argument matcher verifying the dimension string flows end-to-end. Without tests, future signature changes carry the same silent-override risk.
```suggestion
# spec/models/optimized_image_spec.rb
RSpec.describe OptimizedImage do
  describe ".downsize" do
    it "forwards a pre-formatted dimension string to convert_with" do
      expect(OptimizedImage).to receive(:convert_with) do |instructions, _to|
        expect(instructions.join(" ")).to include("80%")
      end
      OptimizedImage.downsize("/tmp/in.jpg", "/tmp/out.jpg", "80%")
    end

    it "rejects or routes the legacy 4-positional-arg form explicitly" do
      expect { OptimizedImage.downsize("/tmp/in.jpg", "/tmp/out.jpg", 100, 200) }
        .to raise_error(ArgumentError)
    end
  end
end
```

:red_circle: [testing] Auto-downsize retry loop has no test coverage across any of its branches in app/controllers/uploads_controller.rb:41 (confidence: 92)
The new while-loop introduces at least six distinct execution paths — already-under-limit (loop never entered), converges within budget, exhausts all five attempts and passes the file through, non-image upload (loop skipped), `max_image_size_kb == 0` (loop skipped), and `OptimizedImage.downsize` failure mid-loop. None of these branches are covered by any spec in the diff, leaving the silent pass-through on loop exhaustion entirely undetected by CI.
```suggestion
# spec/controllers/uploads_controller_spec.rb
RSpec.describe UploadsController do
  describe "#create_upload image auto-downsize" do
    before do
      allow(FileHelper).to receive(:is_image?).and_return(true)
      SiteSetting.max_image_size_kb = 10_240
    end

    it "calls OptimizedImage.downsize until the image fits" do
      allow_any_instance_of(Tempfile).to receive(:size).and_return(15.megabytes, 12.megabytes, 9.megabytes)
      expect(OptimizedImage).to receive(:downsize).twice
    end

    it "stops after 5 attempts and rejects the upload if still oversized" do
      allow_any_instance_of(Tempfile).to receive(:size).and_return(15.megabytes)
      expect(OptimizedImage).to receive(:downsize).exactly(5).times
    end
  end
end
```

:red_circle: [security] Decompression-bomb DoS via repeated unconstrained ImageMagick invocations on untrusted input in app/controllers/uploads_controller.rb:63 (confidence: 90)
The loop invokes ImageMagick (shelled out via `convert_with`) up to five times on untrusted image input without first checking pixel dimensions. An attacker can upload a file that is small on disk but expands to gigabytes of RAM during decode — a classic decompression bomb (for example, a highly compressed PNG claiming 50000×50000 pixels). Each iteration allocates this RAM independently. The download ceiling was also raised to 10MB (a separate finding), increasing the surface area. For animated or vector content, an 80% scale pass may not reduce the byte count below the threshold, guaranteeing all five iterations fire and pinning the worker for the duration.
```suggestion
if tempfile && File.size(tempfile.path) > 0 && SiteSetting.max_image_size_kb > 0 && FileHelper.is_image?(filename)
  w, h = FastImage.size(tempfile.path)
  raise Discourse::InvalidParameters.new(:file) if w.nil? || h.nil? || (w * h) > SiteSetting.max_image_megapixels * 1_000_000

  if File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
    OptimizedImage.downsize(
      tempfile.path,
      tempfile.path,
      "#{SiteSetting.max_image_width}x#{SiteSetting.max_image_height}",
      allow_animation: SiteSetting.allow_animated_thumbnails
    )
    raise Discourse::InvalidParameters.new(:file) if File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
  end
end
```
[References: ImageMagick security policy — https://imagemagick.org/script/security-policy.php; CVE-2016-3714 (ImageTragick)]

:red_circle: [security] Remote-download byte ceiling hardcoded to 10MB, silently bypassing admin `max_image_size_kb` setting in app/controllers/uploads_controller.rb:55 (confidence: 85)
The original code passed `SiteSetting.max_image_size_kb.kilobytes` as the download size ceiling to `FileHelper.download`. The diff replaces this with the literal `10.megabytes`. Admins who have deliberately configured a low `max_image_size_kb` (for example 500KB for bandwidth control or abuse prevention) have that policy silently ignored on the API upload path. The inflated ceiling also widens the SSRF amplification window if `FileHelper.download` lacks strict host allowlisting, since a larger response body is fetched from an attacker-controlled URL before any content validation occurs.
```suggestion
tempfile = FileHelper.download(url, SiteSetting.max_image_size_kb.kilobytes, "discourse-upload-#{type}") rescue nil
```

## Improvements

:yellow_circle: [correctness] Hardcoded 10MB client-side upload limit ignores per-type `max_*_size_kb` site settings in app/assets/javascripts/discourse/lib/utilities.js:182 (confidence: 92)
The original expression `Discourse.SiteSettings['max_' + type + '_size_kb']` respected per-type admin limits for images, attachments, and other upload types. The diff replaces this with the literal `10 * 1024`, breaking the dynamic lookup for all upload types. Admins who configure stricter per-type limits will find those limits bypassed on the client side, producing confusing UX when the server subsequently rejects the upload with a 413 or validation error. The companion change at line 246 (413 error message handler) also references a hardcoded maximum, compounding the inconsistency.
```suggestion
var maxSizeKB = Discourse.SiteSettings['max_' + type + '_size_kb'] || (10 * 1024);
```

:yellow_circle: [correctness] Loop silently passes oversized image through to `Upload.create_for` after exhausting all five attempts in app/controllers/uploads_controller.rb:60 (confidence: 88)
After the while-loop exits on `attempt == 0`, control falls through to `Upload.create_for` regardless of whether the file is still over the size limit. No error is rendered, no log entry is written, and the user receives a success response for an upload that violated the configured size policy. If `OptimizedImage.downsize` fails silently (for example ImageMagick is missing or returns a non-zero exit), all five iterations are wasted and the policy bypass goes undetected by both the user and site admins.
```suggestion
attempt = 5
while attempt > 0 && File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
  prev_size = File.size(tempfile.path)
  OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: SiteSetting.allow_animated_thumbnails)
  attempt -= 1
  break if File.size(tempfile.path) >= prev_size
end

if File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
  return render_json_error(I18n.t("upload.images.too_large", max_size_kb: SiteSetting.max_image_size_kb))
end
```

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: 3 widely-referenced files (utilities.js shared lib, uploads_controller, optimized_image model) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
