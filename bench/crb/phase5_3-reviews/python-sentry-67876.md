## Summary
3 files changed, 247 lines added, 50 lines deleted. 8 findings (5 critical, 3 improvements, 0 nitpicks).
Confirmed production HTTPError 500 on `get_user_info` (PR's own Sentry bot comment) plus four other unhandled-exception / silent-failure paths in the new OAuth step; fresh-install path bypasses the user-mismatch check the PR claims to add.

## Critical

:red_circle: [correctness] get_user_info raises HTTPError on non-200 — no handler, confirmed production 500 in src/sentry/integrations/github/integration.py:430 (confidence: 97)
`get_user_info(payload["access_token"])` makes an HTTP request to api.github.com/user. There is no try/except around this call. Any non-200 response raises HTTPError, which propagates as an unhandled 500. This is not theoretical: the Sentry bot comment attached to this PR records the exact production failure: `HTTPError: 401 Client Error: Unauthorized for url: https://api.github.com/user` on `/extensions/{provider_id}/setup/`. Users receive a 500 instead of the graceful `error()` page the rest of this view uses.
```suggestion
try:
    authenticated_user_info = get_user_info(payload["access_token"])
except Exception:
    logger.warning("github.oauth.get_user_info_failed", exc_info=True)
    return error(request, self.active_organization)
if "login" not in authenticated_user_info:
    return error(request, self.active_organization)
```

:red_circle: [correctness] Unguarded dict key access on integration.metadata["sender"]["login"] raises KeyError in src/sentry/integrations/github/integration.py:449 (confidence: 95)
The user-mismatch check accesses `integration.metadata["sender"]["login"]` using direct subscript notation. If `metadata` lacks `"sender"`, or if `metadata["sender"]` lacks `"login"` (e.g., the integration was created before this metadata field was populated, or the installation webhook has not fired yet relative to the OAuth flow), Python raises an unhandled `KeyError` that propagates as a 500 Internal Server Error rather than the clean error page this PR intends to show.
```suggestion
sender_login = (integration.metadata or {}).get("sender", {}).get("login")
if sender_login is None or pipeline.fetch_state("github_authenticated_user") != sender_login:
    return error(request, self.active_organization)
```

:red_circle: [correctness] safe_urlopen call is outside the try/except block — network exceptions cause unhandled 500 in src/sentry/integrations/github/integration.py:419 (confidence: 95)
The call `req = safe_urlopen(url=ghip.get_oauth_access_token_url(), data=data)` is placed BEFORE the `try:` block that is meant to absorb token-exchange failures. If `safe_urlopen` raises (SSL error, timeout, DNS failure, connection refused), the exception propagates uncaught, producing an unhandled 500. The most failure-prone I/O call in this flow is the one excluded from the handler.
```suggestion
try:
    req = safe_urlopen(url=ghip.get_oauth_access_token_url(), data=data)
    body = safe_urlread(req).decode("utf-8")
    payload = dict(parse_qsl(body))
except Exception:
    logger.warning("github.oauth.token_exchange_failed", exc_info=True)
    payload = {}
```

:red_circle: [correctness] Bare except Exception swallows all token-exchange failures with no logging in src/sentry/integrations/github/integration.py:421 (confidence: 95)
`except Exception: payload = {}` catches every exception from body read and parse and silently converts it to an empty dict. There is no log statement, no metric, no Sentry event captured. All failure modes — transient GitHub API outage, invalid authorization code, CSRF state-mismatch replay — produce an identical generic "Invalid installation request." page. It is operationally impossible to distinguish attack from outage from misconfiguration in monitoring.
```suggestion
try:
    body = safe_urlread(req).decode("utf-8")
    payload = dict(parse_qsl(body))
except Exception:
    logger.warning("github.oauth.token_exchange_failed", exc_info=True)
    payload = {}
```

:red_circle: [correctness] Fresh-install path (Integration.DoesNotExist) bypasses the new user-mismatch security check entirely in src/sentry/integrations/github/integration.py:487 (confidence: 92)
The PR's stated security goal is "fail on user mismatch." However, when `Integration.objects.get` raises `DoesNotExist` on the first call (first-time install), the code immediately calls `pipeline.next_step()`, skipping the entire user-validation block below. The `github_authenticated_user` stored by `OAuthLoginView` is never compared against any installation metadata on the fresh-install path. The security guarantee applies only to reinstalls. This design gap should be explicitly documented or mitigated — for example by holding the authenticated user in pipeline state and verifying it against the installation webhook payload's sender before allowing a fresh install to advance.
```suggestion
# Fresh install: no existing integration metadata to compare against.
# The user-mismatch guarantee only applies on reinstall. Either document
# the limitation here or coordinate the OAuth user check against the
# webhook payload's sender before advancing.
return pipeline.next_step()
```

## Improvements

:yellow_circle: [correctness] Two Integration.objects.get calls with different status filters create silent failure for DISABLED integrations and a narrow TOCTOU race in src/sentry/integrations/github/integration.py:478 (confidence: 88)
The first query (no status filter) finds any integration. The second query (`status=ACTIVE`) only finds active ones. If an integration exists but is DISABLED, the first query succeeds, `installations_exist` evaluates to False, then the second query raises `DoesNotExist` and returns a generic error — preventing the user from reinstalling a legitimately disabled integration. The two separate queries also introduce a narrow TOCTOU window where the integration could be deleted or status-changed between them.
```suggestion
try:
    integration = Integration.objects.get(external_id=installation_id)
except Integration.DoesNotExist:
    return pipeline.next_step()

installations_exist = OrganizationIntegration.objects.filter(
    integration=integration
).exists()

if installations_exist:
    return error(
        request,
        self.active_organization,
        error_short="Github installed on another Sentry organization.",
        error_long=ERR_INTEGRATION_EXISTS_ON_ANOTHER_ORG,
    )

if integration.status != ObjectStatus.ACTIVE:
    return error(request, self.active_organization)

sender_login = (integration.metadata or {}).get("sender", {}).get("login")
if sender_login is None or pipeline.fetch_state("github_authenticated_user") != sender_login:
    return error(request, self.active_organization)

return pipeline.next_step()
```

:yellow_circle: [correctness] No logging in any OAuthLoginView failure path — operationally invisible in src/sentry/integrations/github/integration.py:386 (confidence: 88)
Six distinct failure paths all call the generic `error()` helper. None emit a log statement, metric, or Sentry event. A state-mismatch CSRF probe is indistinguishable from a transient network failure in monitoring dashboards. There is no way to alert on elevated error rates. The confirmed production failure recorded in the PR's Sentry bot comment demonstrates that at least one failure mode reached production and was not observable until a user reported it.
```suggestion
# At each `return error(request, self.active_organization)` site, add a
# scoped warning log with structured context. Example for the state-mismatch path:
logger.warning(
    "github.oauth.state_mismatch",
    extra={"installation_id": installation_id},
)
return error(request, self.active_organization)
```

:yellow_circle: [cross-file-impact] Hardcoded pipeline state string in 5 test locations is fragile to signature derivation changes in tests/sentry/integrations/github/test_integration.py:235 (confidence: 80)
The state value `9cae5e88803f35ed7970fc131e6e65d3` is hardcoded in 5 separate `urlencode` calls across the test file. If pipeline signature derivation changes (HMAC secret, algorithm, or seed), all five tests will silently start hitting the state-mismatch error path and fail with confusing template assertion errors ("expected dialog success but got error template") rather than a clear "state mismatch" message. The failure mode obscures the root cause and makes the tests brittle.
```suggestion
# Replace each hardcoded `"state": "9cae5e88803f35ed7970fc131e6e65d3"` with
# a dynamic read of the pipeline signature, e.g. via a test helper:
state = self._current_pipeline_signature()
self.client.get(
    "{}?{}".format(self.setup_path, urlencode({"code": "12345678901234567890", "state": state}))
)
```

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: core GitHub integration module (HIGH fan-out — estimated 10+ importers in full sentry codebase) | Sensitive Paths: content-level OAuth/client_secret/access_token handling (paths do not literally match `auth/`/`*token*`/`*secret*` glob patterns but content is security-critical)
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
