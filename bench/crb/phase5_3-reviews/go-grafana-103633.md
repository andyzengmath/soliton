## Summary
4 files changed, 240 lines added, 33 lines deleted. 5 findings (0 critical, 5 improvements, 0 nitpicks).
Adds a per-resource permission denial cache and a separate "cache-only" lookup path on the authz Check/List hot path. The functional design is sound and aligned with the PR description, but several aspects of the cache wiring (key prefix overlap, hit-but-denied metric path, and unbounded denial-cache key space) deserve a second pass before merge.

## Improvements

:yellow_circle: [correctness] Permission and denial caches share a key prefix and the same backing store in `pkg/services/authz/rbac/cache.go:30` (confidence: 88)
`userPermCacheKey` produces `"<ns>.perm_<uid>_<action>"` while the new `userPermDenialCacheKey` produces `"<ns>.perm_<uid>_<action>_<name>_<parent>"`. Both `permCache` and `permDenialCache` are `cacheWrap` instances built over the *same* underlying `cache` passed to `NewService`, so the wrappers share a single keyspace. If `action`, `name`, or `parent` ever contain an underscore (e.g. action sets like `"dashboards:read_x"`), a permDenial key can string-collide with a permCache key. The typed `cacheWrap[T]` will safely fail the type assertion on read, but the side effect is silent cache eviction/replacement of a real `map[string]bool` entry by a `bool`, which lowers hit rate and creates non-obvious behavior depending on call order. Use a distinct prefix on denial keys.
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
	return namespace + ".permdeny_" + userUID + "_" + action + "_" + name + "_" + parent
}
```

:yellow_circle: [correctness] `permissionCacheUsage` metric is recorded as a miss when the cache was actually hit but did not allow in `pkg/services/authz/rbac/service.go:116` (confidence: 86)
In `Check`, when `getCachedIdentityPermissions` returns cached permissions and `checkPermission` returns `allowed=false`, the code falls through to `s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()` and then queries the DB. From the metric's perspective the cache "missed", but it actually served data — the result was simply that the cached permission set did not contain the requested resource. This will distort the cache-hit-rate dashboard authz oncall is presumably going to use to evaluate the change (and that the PR comments explicitly mention adding a panel for). Either record this as a hit-with-fallthrough using a third label value, or split the counter into a separate `permissionCacheLookup{result="miss|hit_allow|hit_fallthrough"}` series so the denial-cache panel doesn't blend two distinct outcomes.
```suggestion
		if allowed {
			s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
			s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
			return &authzv1.CheckResponse{Allowed: allowed}, nil
		}
		s.metrics.permissionCacheUsage.WithLabelValues("hit_fallthrough", checkReq.Action).Inc()
	} else {
		s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
	}
```

:yellow_circle: [security] Denial cache key space is attacker-controlled and unbounded in `pkg/services/authz/rbac/service.go:116` (confidence: 78)
The denial-cache key embeds `checkReq.Name` and `checkReq.ParentFolder`, both of which come from the request and are not normalized or length-bounded here. An authenticated user can probe many synthetic `(name, parent)` tuples for an action they lack permission on (e.g. listing arbitrary UIDs in a tight loop) and force one denial-cache entry per unique tuple, all bound to the short TTL but still in-memory until expiry. The underlying `cache.NewLocalCache` configuration shown elsewhere in this PR sets only `Expiry` and `CleanupInterval`; if it is also unbounded in entry count, this is a low-cost in-memory amplification vector. Confirm that the local cache has a max-size eviction policy, or add one keyed on entries per `(ns, userUID)` to cap blast radius from a single account.

:yellow_circle: [testing] No regression test for the cache-hit-but-not-allowed → DB fallthrough path in `pkg/services/authz/rbac/service_test.go:893` (confidence: 82)
`TestService_CacheCheck` covers (a) cache hit + allow, (b) cache miss → DB allow, (c) outdated cache + DB allow, and (d) explicit deny entry. The path that is most subject to silent regression — cache hit, `checkPermission` returns `allowed=false` because the resource is not in the cached permission set, and the service must therefore re-query the DB rather than returning `Allowed: false` — is exercised only as a side effect of case (c) (which actually populates a stale cache entry for a *different* resource). Add a test where the cached permission map is non-empty for the action but does not contain the requested resource, and assert both `Allowed=true` after DB lookup *and* that the denial cache was not populated. This is the path that, if broken, would silently regress to the bug this PR is fixing.

:yellow_circle: [correctness] In-process client downgrade from `LocalCache(30s)` to `NoopCache` is a behavior change worth a changelog entry in `pkg/services/authz/rbac.go:101` (confidence: 72)
`ProvideAuthZClient` previously wired the in-proc client through `newRBACClient` which installed a 30-second client-side cache. After this PR the in-proc path uses `NoopCache{}` while the remote path keeps the 30s cache. The PR description justifies this (the in-proc service has its own internal caches, so layering another one stales reads), but downstream consumers — anything that observed RPC-level cache hit rates, or any custom in-proc deployment that relied on the implicit 30s memoization — will see a step-change in DB query rate even though no functional bug exists. Add a `CHANGELOG.md` entry under "Breaking changes" or at minimum a short paragraph in the PR description confirming this is observable in load tests, so SREs aren't surprised.

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: cross-cutting authz hot path (Check/List on every authorized request) | Sensitive Paths: pkg/services/authz/** (auth/authz)
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
