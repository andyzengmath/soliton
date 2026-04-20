## Summary
3 files changed, 247 lines added, 50 lines deleted. 7 findings (4 critical, 3 improvements, 0 nitpicks).
3 files changed. 7 findings (4 critical, 3 improvements). safe_urlopen network call outside try/except causes unhandled 500 in src/sentry/integrations/github/integration.py:123

## Critical
:red_circle: [correctness] safe_urlopen call outside try/except — network error causes unhandled 500 in src/sentry/integrations/github/integration.py:123 (confidence: 97)
In OAuthLoginView.dispatch, safe_urlopen(url=ghip.get_oauth_access_token_url(), data=data) is called on the line immediately before the try block. Only safe_urlread and the decode/parse steps are inside the try/except. If GitHub's OAuth token endpoint is unreachable, returns connection refused, or times out, safe_urlopen raises an exception that is not caught. The user sees a 500 instead of the friendly error page.
```suggestion
try:
    req = safe_urlopen(url=ghip.get_oauth_access_token_url(), data=data)
    body = safe_urlread(req).decode("utf-8")
    payload = dict(parse_qsl(body))
except Exception:
    payload = {}
```

:red_circle: [correctness] KeyError crash and None==None bypass in user-mismatch guard in src/sentry/integrations/github/integration.py:235 (confidence: 95)
Two distinct defects exist in the same user-mismatch check at lines 235-239. (1) KeyError crash: integration.metadata["sender"]["login"] uses direct dict key access. If the stored metadata does not contain "sender" (e.g., integration created before this field was standardized or via a different webhook path), or if "sender" is present but "login" is absent, a KeyError is raised. This exception is not caught in dispatch, so Django returns a 500 to the user instead of the intended error page. (2) None==None bypass: pipeline.fetch_state("github_authenticated_user") returns None if the state key was never bound. If integration.metadata["sender"]["login"] is also None (absent key via .get()), then None != None evaluates to False and the mismatch check passes, allowing an installation to proceed without any user verification. Both defects are fixed by the same defensive rewrite.
```suggestion
sender_login = (integration.metadata or {}).get("sender", {}).get("login")
authenticated_user = pipeline.fetch_state("github_authenticated_user")
if not authenticated_user or not sender_login or authenticated_user != sender_login:
    return error(request, self.active_organization)
```

:red_circle: [correctness] get_user_info call not wrapped — HTTP error causes unhandled 500 in src/sentry/integrations/github/integration.py:134 (confidence: 95)
In OAuthLoginView.dispatch, get_user_info(payload["access_token"]) makes an HTTP call to https://api.github.com/user. If this call raises an exception (network error, HTTP 401/403/429, etc.), it is not caught. A production 401 error from this exact URL was already observed after deployment per the Sentry bot comment on the PR, confirming this code path raises an uncaught exception in practice.
```suggestion
try:
    authenticated_user_info = get_user_info(payload["access_token"])
except Exception:
    return error(request, self.active_organization)
if "login" not in authenticated_user_info:
    return error(request, self.active_organization)
```

:red_circle: [testing] test_installation_not_found no longer tests the "installation not found" code path in tests/sentry/integrations/github/test_integration.py:381 (confidence: 92)
Before this PR, test_installation_not_found verified that when GitHub returns no matching installation the pipeline returns "The GitHub installation could not be found." That assertion was deleted. The replacement test sends a wrong state value and asserts on the state-mismatch branch. The original failure path — Integration.DoesNotExist during webhook-triggered install, which now falls through to the new user-mismatch check — has no coverage. The test name is actively misleading: it is now a state-mismatch test with a wrong name.
```suggestion
Rename the current test to test_state_mismatch to match what it exercises. Add a new test_installation_not_found that covers the Integration.DoesNotExist path in GitHubInstallation.dispatch (pipeline.next_step()) and the second DoesNotExist catch (error() response).
```

## Improvements
:yellow_circle: [correctness] User-mismatch check is not applied on brand-new installations (Integration.DoesNotExist path) in src/sentry/integrations/github/integration.py:195 (confidence: 85)
The new security check only executes when an Integration record already exists in Sentry's DB but has no OrganizationIntegration. When Integration.DoesNotExist is caught, pipeline.next_step() is returned immediately, skipping the user-mismatch check entirely. An actor installing a GitHub app for the first time can bypass user-mismatch validation because there is no prior integration.metadata["sender"]["login"] to compare against. Whether this gap is acceptable depends on whether the intent was to protect only re-installation scenarios.
```suggestion
Either document explicitly that the check is intentionally scoped to re-installation scenarios and that first-time installs rely on state/signature for CSRF protection, or defer the identity check to a later pipeline step (after the webhook creates the Integration record) so it applies to all installs uniformly.
```

:yellow_circle: [testing] test_github_user_mismatch assertion does not distinguish which error branch fired in tests/sentry/integrations/github/test_integration.py:382 (confidence: 88)
The test asserts `b"Invalid installation request." in resp.content`. That exact string is the default error_short value returned by every call to the shared error() helper — state mismatch, token exchange failure, missing login, and the user-mismatch guard all produce identical output. If the test accidentally triggers an earlier error branch (e.g., a state mismatch in setup_path_2), the assertion still passes, giving false confidence that the user-mismatch path specifically was exercised.
```suggestion
Introduce a dedicated error_short for the user-mismatch branch (e.g., "GitHub user mismatch.") in integration.py and assert on that string in the test, making it uniquely tied to the code path under test.
```

:yellow_circle: [testing] HMAC signature in test_github_user_mismatch is hardcoded with no verification in tests/sentry/integrations/github/test_integration.py:402 (confidence: 85)
The webhook POST uses a hardcoded SHA1 HMAC signature. The payload is built by mutating INSTALLATION_EVENT_EXAMPLE at runtime (changing installation.id and sender.login). If the hardcoded SHA1 does not match the final serialized payload using the test webhook secret, the webhook handler returns 400 and the Integration record is never stored. In that case resp.status_code == 204 would catch the breakage, but if the 204 assertion were ever removed or weakened, the later "Invalid installation request." assertion would still pass — for the wrong reason, with no integration in the DB.
```suggestion
import hmac, hashlib
body = json.dumps(webhook_event).encode()
sig = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
# use `sig` as HTTP_X_HUB_SIGNATURE and `body` as POST data
```

## Risk Metadata
Risk Score: 44/100 (MEDIUM) | Blast Radius: integration.py is core GitHub integration module (~8 estimated importers); pipeline_advancer.py is shared pipeline utility | Sensitive Paths: none matched by filename globs, but file contents handle OAuth client_secret and access_token exchange
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
