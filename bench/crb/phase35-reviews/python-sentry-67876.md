Warning: correctness agent timed out (5/6 agents completed)

## Summary
3 files changed, 247 lines added, 50 lines deleted. 7 findings (3 critical, 4 improvements). Unhandled HTTPError 401 from `get_user_info()` at `src/sentry/integrations/github/integration.py:134` is already firing in production per the PR's own Sentry bot comment.

## Critical

:red_circle: [cross-file-impact] get_user_info() called without exception handling — confirmed 401 error in production in src/sentry/integrations/github/integration.py:134 (confidence: 90)
In the new `OAuthLoginView.dispatch`, `get_user_info(payload["access_token"])` is called bare with no try/except. The Sentry bot's own comment on this PR records a deployed runtime error: `HTTPError: 401 Client Error: Unauthorized for url: https://api.github.com/user`. If the GitHub `/user` endpoint returns a non-2xx response, the exception propagates unhandled, producing a 500 instead of the graceful `error(...)` response. The downstream check `if "login" not in authenticated_user_info` only runs when the HTTP call succeeds, so it cannot substitute for exception handling on the call itself.
```suggestion
try:
    authenticated_user_info = get_user_info(payload["access_token"])
except Exception:
    logger.warning("github.oauth.user-info-failed", exc_info=True)
    return error(request, self.active_organization)

if "login" not in authenticated_user_info:
    return error(request, self.active_organization)
```
[References: PR comment from @sentry bot — https://sentry.sentry.io/issues/5175299490/?referrer=github-pr-bot]

:red_circle: [security] KeyError on integration.metadata["sender"]["login"] — fail-open 500 instead of fail-closed error page in src/sentry/integrations/github/integration.py:235 (confidence: 90)
The new installer-authenticity check accesses `integration.metadata["sender"]["login"]` with direct dictionary indexing. `metadata` is populated from the GitHub webhook payload processed elsewhere; any integration record that lacks a `sender` key (created before this field was persisted, created via an alternate code path, or created by a webhook replay with a partial body) will raise `KeyError` here and return a 500 instead of the intended fail-closed error page. Authorization gates must fail closed explicitly rather than via unhandled exceptions — a later refactor that makes `sender.login` optional or renames it would silently turn the security check into a crash rather than an authorization denial. The same pattern is repeated at the error-branch near line 478-485.
```suggestion
expected_login = (integration.metadata or {}).get("sender", {}).get("login")
authenticated_login = pipeline.fetch_state("github_authenticated_user")
if not expected_login or not authenticated_login or expected_login != authenticated_login:
    return error(request, self.active_organization)
```
[References: https://cwe.mitre.org/data/definitions/754.html, https://owasp.org/www-project-top-ten/2021/A01_2021-Broken_Access_Control/]

:red_circle: [testing] No test covers the missing access_token branch or the bare-except path in OAuthLoginView in src/sentry/integrations/github/integration.py:125 (confidence: 95)
`OAuthLoginView` has two distinct error paths that are completely untested in this PR: (1) the `except Exception: payload = {}` block that fires when `safe_urlopen`/`safe_urlread` raises (network error, malformed body, decode failure), and (2) the `if "access_token" not in payload: return error(...)` branch that fires when GitHub returns an OAuth error body such as `error=bad_verification_code`. Because the bare `except` silently swallows exceptions and falls through to the missing-token check, a transient network error and a legitimate OAuth rejection produce the same observable behavior — but neither scenario is exercised. Given that a production 401 from `get_user_info` (separate critical finding above) is already firing on this exact code path, the absence of error-path tests represents significant ongoing regression risk.
```suggestion
@responses.activate
def test_oauth_exchange_missing_access_token(self):
    self._stub_github()
    responses.replace(
        responses.POST,
        "https://github.com/login/oauth/access_token",
        body="error=bad_verification_code",
    )
    self.client.get(self.init_path)
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "bad-code", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
        )
    )
    assert resp.status_code == 200
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content

@responses.activate
def test_oauth_exchange_network_error(self):
    self._stub_github()
    responses.replace(
        responses.POST,
        "https://github.com/login/oauth/access_token",
        body=Exception("connection refused"),
    )
    self.client.get(self.init_path)
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "any-code", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
        )
    )
    assert resp.status_code == 200
    assert b"Invalid installation request." in resp.content
```

## Improvements

:yellow_circle: [testing] test_github_user_mismatch cannot confirm it reaches the user-mismatch branch rather than an earlier error path in tests/sentry/integrations/github/test_integration.py:384 (confidence: 90)
The test asserts `b"Invalid installation request."` in the response, but this is the same generic message emitted by four distinct error paths: state mismatch, token-exchange failure, missing `login` in user info, and the installer-authenticity mismatch. Because `_stub_github` always returns `{"login": "octocat"}` for `/user`, the test does reach the user-mismatch branch today — but only if the hardcoded state string `"9cae5e88803f35ed7970fc131e6e65d3"` happens to equal `pipeline.signature`. If the signature derivation ever drifts, this test silently hits the state-mismatch path instead and keeps passing, giving false confidence on the core security check this PR exists to add.
```suggestion
# Capture the real signature from the OAuth redirect rather than hardcoding it.
resp = self.client.get(init_path_2)
redirect = urlparse(resp["Location"])
state = dict(parse_qsl(redirect.query))["state"]

setup_path_2 = "{}?{}".format(
    self.setup_path,
    urlencode({"code": "12345678901234567890", "state": state}),
)
resp = self.client.get(setup_path_2)

# Prove the user-mismatch branch fired by confirming the /user call happened.
user_calls = [c for c in responses.calls if c.request.url.endswith("/user")]
assert len(user_calls) == 1
assert b"Invalid installation request." in resp.content
```

:yellow_circle: [testing] CSRF state-mismatch protection has no dedicated named test in tests/sentry/integrations/github/test_integration.py:372 (confidence: 88)
The CSRF/state-mismatch protection (`if request.GET.get("state") != pipeline.signature`) is a security-critical check introduced by this PR. Its only coverage is a side effect of `test_installation_not_found`, which was repurposed to send a mismatched state value. There is no test named or documented to describe CSRF protection, so anyone auditing security test coverage or bisecting a regression cannot identify from the test suite that the protection exists and is verified.
```suggestion
@responses.activate
def test_state_mismatch_returns_error(self):
    self._stub_github()
    self.client.get(self.init_path)
    resp = self.client.get(
        "{}?{}".format(
            self.setup_path,
            urlencode({"code": "12345678901234567890", "state": "deadbeef" * 4}),
        )
    )
    assert resp.status_code == 200
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
    # Confirm no token exchange was attempted once state was rejected.
    token_calls = [c for c in responses.calls if "access_token" in c.request.url]
    assert len(token_calls) == 0
```

:yellow_circle: [testing] Hardcoded magic state string and positional responses.calls index create brittle tests in tests/sentry/integrations/github/test_integration.py:314 (confidence: 85)
The state value `"9cae5e88803f35ed7970fc131e6e65d3"` is hardcoded in five separate test locations and relies on an implicit assumption that it equals `pipeline.signature`. If the signature derivation changes (different seed, different algorithm, different session scope) all five callsites silently hit the state-mismatch path rather than failing loudly at the hardcoded assertion. Separately, `auth_header = responses.calls[2].request.headers["Authorization"]` depends on the exact insertion order of HTTP stubs — adding any stub before index 2 shifts every subsequent index and breaks unrelated assertions.
```suggestion
# Parse the real state out of the OAuth redirect instead of hardcoding it.
resp = self.client.get(self.init_path)
redirect = urlparse(resp["Location"])
state = dict(parse_qsl(redirect.query))["state"]

# Filter responses.calls by URL rather than by positional index.
app_token_call = next(
    c for c in responses.calls
    if f"/app/installations/{self.installation_id}/access_tokens" in c.request.url
)
assert app_token_call.request.headers["Authorization"] == "Bearer jwt_token_1"
```

:yellow_circle: [testing] pipeline_advancer.py change has no test coverage in this PR in src/sentry/web/frontend/pipeline_advancer.py:34 (confidence: 82)
The refactor removes `FORWARD_INSTALL_FOR = ["github"]` and inlines the check as `provider_id == "github"`. No test verifies the direct-from-GitHub install redirect for the github provider (the `setup_action == "install"` and `pipeline is None` branch), and no test confirms that the forwarding does NOT trigger for other providers. A future change that accidentally broadens or narrows this condition would not be caught.
```suggestion
def test_pipeline_advancer_forwards_github_direct_install(self):
    resp = self.client.get(
        reverse("sentry-extension-setup", kwargs={"provider_id": "github"}),
        data={"setup_action": "install", "installation_id": "123"},
    )
    assert resp.status_code == 302

def test_pipeline_advancer_does_not_forward_non_github_direct_install(self):
    resp = self.client.get(
        reverse("sentry-extension-setup", kwargs={"provider_id": "slack"}),
        data={"setup_action": "install"},
    )
    assert resp.status_code != 302 or "github" not in resp["Location"]
```

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: ~8 estimated importers across provider registry, URL conf, and related integration code; get_pipeline_views() is invoked for every GitHub integration install | Sensitive Paths: OAuth credential exchange (client_secret, access_token) in src/sentry/integrations/github/integration.py
AI-Authored Likelihood: LOW

(8 additional findings below confidence threshold 85: non-constant-time state comparison [sec, 70], f-string OAuth URL assembly [sec, 62], broad except Exception swallows OAuth diagnostics [sec, 60], generic `error()` helper name [consistency, 75], generic `get_document_origin()` helper name [consistency, 70], FORWARD_INSTALL_FOR inlining loses extension point [consistency, 72], FORWARD_INSTALL_FOR removal ImportError risk for external callers [xfile, 65], sentry.identity.github import circular-dependency risk [xfile, 65])
