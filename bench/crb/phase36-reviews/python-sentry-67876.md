# Soliton PR Review — getsentry/sentry#67876

**PR:** fix(security): validate GitHub user during integration installation
**Base:** master · **Head:** fix/oauth-validate-github-integration-user
**Recommendation:** request-changes

## Summary
3 files changed, 247 lines added, 50 lines deleted. 11 findings (3 critical, 8 improvements).
First-install path bypasses the new sender-verification security check entirely; legacy integrations crash with unhandled KeyError.

## Critical

:red_circle: [correctness] First-install path bypasses sender-verification security check entirely in src/sentry/integrations/github/integration.py:195 (confidence: 97)
When no `Integration` row exists yet, `except Integration.DoesNotExist: return pipeline.next_step()` jumps to the next pipeline step, skipping the `github_authenticated_user` vs `sender.login` check. The security check only runs on reinstalls, leaving new installs (the dominant production path) with no sender verification.
```suggestion
# Either verify the authenticated GitHub user against the OAuth-echoed
# installation payload (so the check applies uniformly on both paths),
# or capture + verify the sender before calling pipeline.next_step() in
# the first-install branch. Do NOT fall through unverified.
except Integration.DoesNotExist:
    # TODO: verify pipeline.fetch_state("github_authenticated_user")
    # against the sender login from the OAuth installation payload
    return pipeline.next_step()
```
<details><summary>More context</summary>

`test_github_user_mismatch` seeds a pre-existing Integration row via an `installation` webhook before running the flow, so it exclusively exercises the reinstall branch. No test covers an attacker completing a fresh install with a different GitHub account — which currently succeeds because this branch never reaches the new check. Agents: correctness (97), test-quality (82).
</details>

:red_circle: [correctness] Unguarded KeyError on integration.metadata["sender"]["login"] in src/sentry/integrations/github/integration.py:237 (confidence: 95)
`integration.metadata["sender"]["login"]` uses bare subscripts with no `.get()` or `try/except`. Any `Integration` row whose metadata predates `sender` capture (legacy installs, migrated data, installs where the webhook never landed) raises `KeyError` → 500.
```suggestion
sender_login = integration.metadata.get("sender", {}).get("login")
if sender_login is None:
    return error(request, self.active_organization)
if pipeline.fetch_state("github_authenticated_user") != sender_login:
    return error(request, self.active_organization)
```
<details><summary>More context</summary>

The PR description references a production 401 on `/extensions/{provider_id}/setup/` that Sentry already observed — legacy records reconnecting are a realistic and imminent trigger. Ideally surface a distinct, actionable error message when `sender` is missing, rather than the generic "Invalid installation request." Agents: correctness (95), cross-file-impact (80).
</details>

:red_circle: [cross-file-impact] Inactive integration silently blocks re-installation — behavior regression in src/sentry/integrations/github/integration.py:227 (confidence: 85)
The new second `Integration.objects.get(external_id=..., status=ObjectStatus.ACTIVE)` means any org whose Integration row exists with `status != ACTIVE` (e.g., DISABLED) now hits `DoesNotExist` and receives the generic error page with no recovery path. Previously the fall-through was unconditional.
```suggestion
# Option A — remove status filter; sender check is valid regardless of status:
try:
    integration = Integration.objects.get(external_id=installation_id)
except Integration.DoesNotExist:
    return error(request, self.active_organization)

# Option B — detect inactive integrations explicitly and return a targeted
# error with a remediation path, rather than collapsing into the generic error.
```
<details><summary>More context</summary>

`integration_pending_deletion_exists` only catches `OrganizationIntegration.status=PENDING_DELETION`; an `Integration` with `status=DISABLED` flows through to this branch and is now permanently blocked by the new hard filter. Agents: cross-file-impact (85).
</details>

## Improvements

:yellow_circle: [testing] No test for OAuth state/CSRF mismatch on a valid pipeline in tests/sentry/integrations/github/test_integration.py:382 (confidence: 92)
The `state != pipeline.signature` branch in `OAuthLoginView` has no dedicated test. `test_installation_not_found` uses a mismatched state but is testing a missing installation, not CSRF rejection on an otherwise-valid flow.
```suggestion
@responses.activate
def test_oauth_state_mismatch_returns_error(self):
    self._stub_github()
    resp = self.client.get(self.init_path)
    assert resp.status_code == 302
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "validcode", "state": "tampered-state-value"}),
        )
    )
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
```

:yellow_circle: [testing] No test for GitHub returning an error body instead of access_token in tests/sentry/integrations/github/test_integration.py:113 (confidence: 90)
The `"access_token" not in payload` branch is never exercised. GitHub legitimately returns `error=bad_verification_code` for expired or reused authorization codes, so this is a reachable production failure mode with zero coverage.
```suggestion
@responses.activate
def test_oauth_bad_code_returns_error(self):
    self._stub_github()
    responses.replace(
        responses.POST,
        "https://github.com/login/oauth/access_token",
        body="error=bad_verification_code&error_description=The+code+passed+is+incorrect",
    )
    self.client.get(self.init_path)
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "expired", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
        )
    )
    assert b"Invalid installation request." in resp.content
```

:yellow_circle: [testing] No test for get_user_info returning a response without a login key in tests/sentry/integrations/github/test_integration.py:113 (confidence: 88)
The `"login" not in authenticated_user_info` branch is never exercised — the `/user` stub always returns `{"login": "octocat"}`. An unexpected `/user` shape (e.g., an error object) is a plausible real-world response not covered by the test suite.
```suggestion
@responses.activate
def test_oauth_user_info_missing_login_returns_error(self):
    self._stub_github()
    responses.replace(
        responses.GET,
        self.base_url + "/user",
        json={"message": "Requires authentication"},
        status=401,
    )
    self.client.get(self.init_path)
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "12345678901234567890", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
        )
    )
    assert b"Invalid installation request." in resp.content
```

:yellow_circle: [testing] test_installation_not_found assertion is now too generic to distinguish the not-found case in tests/sentry/integrations/github/test_integration.py:385 (confidence: 87)
The previous assertion was the unique `"The GitHub installation could not be found."`; the new assertion is `"Invalid installation request."` — the same text emitted by state-mismatch, missing-token, and user-mismatch failures. The test no longer distinguishes which failure path was actually triggered.
```suggestion
# Use a dedicated error message for the not-found case (e.g., ERR_INTEGRATION_NOT_FOUND)
# and assert on it specifically, so this test cannot silently pass when a
# different failure path is routed here.
assert b"The GitHub installation could not be found." in resp.content
assert resp.status_code == 200
self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
```

:yellow_circle: [correctness] Broad bare except swallows all exceptions silently in src/sentry/integrations/github/integration.py:128 (confidence: 85)
`except Exception: payload = {}` silently masks `AttributeError`, `UnicodeDecodeError`, and any other unexpected failure with no log or metric. Bugs on this path become invisible in production observability.
```suggestion
import logging
logger = logging.getLogger(__name__)

req = safe_urlopen(url=ghip.get_oauth_access_token_url(), data=data)
try:
    body = safe_urlread(req).decode("utf-8")
    payload = dict(parse_qsl(body))
except (UnicodeDecodeError, ValueError) as e:
    logger.warning("github.oauth.token_parse_error", extra={"error": str(e)})
    payload = {}
```
<details><summary>More context</summary>

`safe_urlopen` is called outside the `try` block, so network errors there remain unhandled entirely. Consider wrapping both calls in the narrowed try or handling network failures explicitly. Agents: correctness (85), consistency (85).
</details>

:yellow_circle: [testing] Hardcoded pipeline state signature is a fragile assertion in tests/sentry/integrations/github/test_integration.py:314 (confidence: 85)
The literal `9cae5e88803f35ed7970fc131e6e65d3` is asserted and reused across 5+ callsites. If `pipeline.signature` generation ever changes, all assertions break simultaneously and the state-check coverage silently disappears.
```suggestion
resp = self.client.get(self.init_path)
assert resp.status_code == 302
parsed = urlparse(resp["Location"])
params = dict(parse_qsl(parsed.query))
actual_state = params["state"]
assert parsed.path == "/login/oauth/authorize"

resp = self.client.get(
    "{}?{}".format(
        self.setup_path,
        urlencode({"code": "12345678901234567890", "state": actual_state}),
    )
)
```

:yellow_circle: [testing] First-install attacker scenario is not tested in tests/sentry/integrations/github/test_integration.py:382 (confidence: 82)
No test covers the first-install path where no `Integration` row exists. A test asserting that an attacker who completes the OAuth step on a fresh install with a different GitHub account is rejected would have caught the Critical bypass above.
```suggestion
@responses.activate
def test_github_user_mismatch_first_install(self):
    self._stub_github()
    responses.replace(
        responses.GET, self.base_url + "/user", json={"login": "attacker"},
    )
    self.client.get(self.init_path)
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "12345678901234567890", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
        )
    )
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: OAuth install pipeline for GitHub — affects every new and re-installed GitHub integration across all Sentry orgs | Sensitive Paths: `integrations/github/`, auth / OAuth flow, known production 401 reported on this URL
AI-Authored Likelihood: N/A

(7 additional findings below confidence threshold of 85 suppressed: OAuth state comparison not constant-time [75], installation_id bound before state validation [70], get_user_info unguarded leaking access_token [65], user-mismatch compare matches when both sides are None [60], None fetch_state null-guard is incidental [82], KeyError duplicate merged into Critical [80], missing type annotation on pipeline param [75]. The constant-time-comparison and early-bind findings are worth manual review given OAuth sensitivity.)
