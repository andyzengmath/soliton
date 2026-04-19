## Summary
3 files changed, 247 lines added, 50 lines deleted. 14 findings (7 critical, 7 improvements, 0 nitpicks).
Security-motivated addition of an OAuth2 authorize step to the GitHub integration installation pipeline ships with several latent crashes (KeyError on `metadata["sender"]["login"]`, unhandled `HTTPError` from `get_user_info`, unnecessarily strict `ObjectStatus.ACTIVE` filter) and material test-coverage gaps on every new error branch.

## Critical

:red_circle: [correctness] KeyError crash if integration.metadata lacks "sender" or "login" in src/sentry/integrations/github/integration.py:235 (confidence: 95)
The check `integration.metadata["sender"]["login"]` uses direct dict indexing, not `.get()`. If the GitHub webhook for the installation has not yet been processed when the user completes the OAuth flow (a real race between asynchronous webhook delivery and the user clicking through), `integration.metadata` may be `{}` or may lack the `"sender"` key entirely. This raises `KeyError` and produces a 500 rather than the intended user-facing error response. Cross-validated by cross-file-impact agent (confidence 80) — the webhook handler is a separate HTTP endpoint with no ordering guarantee against pipeline completion.
```suggestion
sender_login = (integration.metadata or {}).get("sender", {}).get("login")
if sender_login is None or pipeline.fetch_state("github_authenticated_user") != sender_login:
    return error(request, self.active_organization)
```

:red_circle: [correctness] ObjectStatus.ACTIVE check silently rejects non-ACTIVE existing integrations in src/sentry/integrations/github/integration.py:228 (confidence: 92)
The second query `Integration.objects.get(external_id=installation_id, status=ObjectStatus.ACTIVE)` is reached only after an earlier query confirmed an Integration row with this `external_id` exists. The added `status=ACTIVE` filter means a non-ACTIVE integration (e.g., `PENDING`, `DISABLED`) now raises `DoesNotExist`, which is caught and returns a generic "Invalid installation request" error. This silently blocks legitimate re-installation flows with no actionable feedback and is logically inconsistent with the surrounding comment ("OrganizationIntegration does not exist, but Integration does exist").
```suggestion
try:
    integration = Integration.objects.get(external_id=installation_id)
except Integration.DoesNotExist:
    return error(request, self.active_organization)

if integration.status != ObjectStatus.ACTIVE:
    return error(
        request,
        self.active_organization,
        error_short="GitHub installation is not active.",
        error_long=_("The GitHub installation is not currently active. Please try reinstalling."),
    )
```

:red_circle: [correctness] `github_authenticated_user` never set when GitHubInstallation reached without OAuthLoginView in src/sentry/integrations/github/integration.py:236 (confidence: 90)
`pipeline.fetch_state("github_authenticated_user")` returns `None` when `OAuthLoginView` was skipped. The user-mismatch check then degenerates into an implicit "did OAuth run?" test with no distinguishing error message. Worse, because `integration.metadata["sender"]["login"]` is evaluated eagerly (see prior finding), a missing-sender case raises `KeyError` before the intended `error()` return is ever reached.
```suggestion
authenticated_user = pipeline.fetch_state("github_authenticated_user")
if not authenticated_user:
    return error(request, self.active_organization,
                 error_short="OAuth authentication required.",
                 error_long=_("Please complete the GitHub OAuth flow before installing."))
sender_login = (integration.metadata or {}).get("sender", {}).get("login")
if sender_login is None or authenticated_user != sender_login:
    return error(request, self.active_organization)
```

:red_circle: [cross-file-impact] `get_user_info` raises HTTPError on 401 — guard does not catch it in src/sentry/integrations/github/integration.py:134 (confidence: 85)
The new `OAuthLoginView` calls `get_user_info(payload["access_token"])` and then checks `if "login" not in authenticated_user_info`. The Sentry bot comment on this very PR records a live production error: `HTTPError: 401 Client Error: Unauthorized for url: https://api.github.com/user`. This confirms `get_user_info` raises `HTTPError` on non-2xx instead of returning a partial dict — so the `"login" not in ...` guard never fires and the exception propagates unhandled (500), not the intended error template.
```suggestion
try:
    authenticated_user_info = get_user_info(payload["access_token"])
except Exception:
    return error(request, self.active_organization)
if "login" not in authenticated_user_info:
    return error(request, self.active_organization)
```
[References: live production error captured by Sentry bot on this PR — https://sentry.sentry.io/issues/5175299490/]

:red_circle: [testing] No test for missing `access_token` in token-exchange response in src/sentry/integrations/github/integration.py:131 (confidence: 95)
`OAuthLoginView.dispatch` returns `error()` when `"access_token"` is absent from the token-exchange response — the exact path GitHub takes when an authorization code is expired, already used, or forged. The test suite only stubs a successful exchange (`body=f"access_token={access_token}"`). No test overrides the stub to return an empty body, `error=bad_verification_code`, or a non-200. This critical security branch is completely unexercised.
```suggestion
@responses.activate
def test_oauth_missing_access_token(self):
    self._stub_github()
    responses.replace(
        responses.POST,
        "https://github.com/login/oauth/access_token",
        body="error=bad_verification_code",
    )
    self.client.get(self.init_path)
    resp = self.client.get("{}?{}".format(
        self.setup_path,
        urlencode({"code": "badcode", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
    ))
    assert resp.status_code == 200
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
```

:red_circle: [testing] No test for missing `login` field in user-info response in src/sentry/integrations/github/integration.py:135 (confidence: 92)
The missing-`login` guard is never exercised — the stub unconditionally returns `{"login": "octocat"}`. A misconfigured GitHub App scope or an unexpected API shape change could hit this path in production without any test catching the regression.
```suggestion
@responses.activate
def test_oauth_missing_login_in_user_info(self):
    self._stub_github()
    responses.replace(responses.GET, self.base_url + "/user", json={"id": 99})
    self.client.get(self.init_path)
    resp = self.client.get("{}?{}".format(
        self.setup_path,
        urlencode({"code": "12345678901234567890", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
    ))
    assert resp.status_code == 200
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
```

:red_circle: [testing] No test for KeyError when integration.metadata lacks sender/login in src/sentry/integrations/github/integration.py:235 (confidence: 88)
Pairs directly with the first critical finding: no test creates an `Integration` with missing or partial `metadata` and attempts to re-install. If/when the KeyError fires in production, there is no regression harness to catch it.
```suggestion
@responses.activate
def test_reinstall_metadata_missing_sender(self):
    self._stub_github()
    integration = Integration.objects.create(
        provider="github",
        external_id=self.installation_id,
        metadata={},  # no "sender" key
        status=ObjectStatus.ACTIVE,
    )
    self.client.get(self.init_path)
    resp = self.client.get("{}?{}".format(
        self.setup_path,
        urlencode({"code": "12345678901234567890", "state": "9cae5e88803f35ed7970fc131e6e65d3"}),
    ))
    assert resp.status_code == 200
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
```

## Improvements

:yellow_circle: [correctness] safe_urlopen called with `code=None` when GitHub redirects with `error=` in src/sentry/integrations/github/integration.py:116 (confidence: 88)
`data["code"] = request.GET.get("code")` returns `None` if the `code` parameter is absent (e.g., GitHub redirects back with `error=access_denied` on user denial). `safe_urlopen` then sends the string `"None"` to GitHub's token endpoint, making an unnecessary outbound call that always fails. Detect the user-denial path explicitly and short-circuit.
```suggestion
if request.GET.get("error"):
    return error(request, self.active_organization,
                 error_short="GitHub authorization denied.",
                 error_long=_("The GitHub authorization was denied or cancelled."))

code = request.GET.get("code")
if not code:
    return error(request, self.active_organization)

data = {"code": code, "client_id": github_client_id, "client_secret": github_client_secret}
```

:yellow_circle: [correctness] Broad `except Exception` around token exchange swallows all errors silently in src/sentry/integrations/github/integration.py:125 (confidence: 85)
`except Exception: payload = {}` catches and discards every exception from `safe_urlread` and `parse_qsl` — network errors, SSL errors, decoding errors — collapsing them into a generic "Invalid installation request." with no `logger.exception`/`logger.warning`. Misconfigured `client_secret`, a network partition to GitHub, or a malformed response become invisible in production observability — especially painful for an OAuth flow where we also want to detect state/code replay attempts.
```suggestion
try:
    body = safe_urlread(req).decode("utf-8")
    payload = dict(parse_qsl(body))
except Exception:
    logger.warning("github.oauth.token_exchange_failed", exc_info=True)
    payload = {}
```

:yellow_circle: [correctness] `installation_id` rebind on OAuth callback not cross-checked against first-pass value in src/sentry/integrations/github/integration.py:97 (confidence: 82)
`pipeline.bind_state("installation_id", installation_id)` is called on both the first-pass and callback-pass through `OAuthLoginView` when `installation_id` is in GET. The state-check on line 112 guards CSRF for the OAuth token but does not verify that a callback-time `installation_id` matches what was stored during the first pass — so if a callback request supplies a different `installation_id`, it silently overwrites the bound state.
```suggestion
if installation_id:
    stored = pipeline.fetch_state("installation_id")
    if stored and stored != installation_id:
        return error(request, self.active_organization)
    pipeline.bind_state("installation_id", installation_id)
```

:yellow_circle: [security] OAuth authorize URL built via f-string; missing explicit `scope` in src/sentry/integrations/github/integration.py:106 (confidence: 80)
`f"{ghip.get_oauth_authorize_url()}?client_id={github_client_id}&state={state}&redirect_uri={redirect_uri}"` builds the authorize URL without percent-encoding and without an explicit `scope` parameter. `state` is hex and `client_id` is config-controlled, so injection is not currently exploitable, but `redirect_uri` comes from `absolute_uri(reverse(...))` and could contain characters requiring encoding if host config changes. Sending an explicit minimal scope (`read:user`) bounds the token's damage if ever leaked.
```suggestion
from urllib.parse import urlencode
params = urlencode({
    "client_id": github_client_id,
    "state": state,
    "redirect_uri": redirect_uri,
    "scope": "read:user",
})
return self.redirect(f"{ghip.get_oauth_authorize_url()}?{params}")
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/]

:yellow_circle: [testing] Precomputed HMAC in `test_github_user_mismatch` breaks opaquely if payload/secret changes in tests/sentry/integrations/github/test_integration.py:407 (confidence: 87)
`HTTP_X_HUB_SIGNATURE="sha1=d184..."` is a static HMAC, but the test mutates `webhook_event["installation"]["id"]` and `webhook_event["sender"]["login"]` after loading the fixture — so the HMAC no longer matches the serialized payload actually sent. If validation is strict, the webhook is silently rejected and the user-mismatch check is never reached (test passes for the wrong reason). Add an assertion that the Integration row exists in the DB after the webhook POST to prove the setup actually happened.
```suggestion
import hashlib, hmac
payload_bytes = json.dumps(webhook_event).encode("utf-8")
secret = b"<your-test-webhook-secret>"
sig = "sha1=" + hmac.new(secret, payload_bytes, hashlib.sha1).hexdigest()

resp = self.client.post(
    path="/extensions/github/webhook/",
    data=payload_bytes,
    content_type="application/json",
    HTTP_X_GITHUB_EVENT="installation",
    HTTP_X_HUB_SIGNATURE=sig,
    HTTP_X_GITHUB_DELIVERY="00000000-0000-4000-8000-1234567890ab",
)
assert resp.status_code == 204
assert Integration.objects.filter(external_id=self.installation_id).exists()
```

:yellow_circle: [testing] Hardcoded `pipeline.signature` value couples every OAuth test to fixture internals in tests/sentry/integrations/github/test_integration.py:312 (confidence: 85)
`"9cae5e88803f35ed7970fc131e6e65d3"` is hardcoded in `assert_setup_flow`, `test_github_user_mismatch`, `test_installation_not_found`, `test_github_installed_on_another_org`, and `test_github_prevent_install_until_pending_deletion_is_complete`. Any change to the signature algorithm or fixture seeds will cause every one of these tests to fail with a 200 "Invalid installation request." response instead of a clear assertion error — very hard to diagnose. Capture `state` from the actual redirect instead.
```suggestion
resp = self.client.get(self.init_path)
redirect = urlparse(resp["Location"])
assert redirect.path == "/login/oauth/authorize"
qs = dict(parse_qsl(redirect.query))
state = qs["state"]
assert qs["client_id"] == "github-client-id"

resp = self.client.get("{}?{}".format(
    self.setup_path,
    urlencode({"code": "12345678901234567890", "state": state}),
))
```

:yellow_circle: [testing] State-tamper (CSRF) path not tested as a standalone case in src/sentry/integrations/github/integration.py:112 (confidence: 82)
The state-mismatch guard is the primary CSRF protection for the OAuth callback. The only path that exercises a mismatched state is `test_installation_not_found`, where the wrong state is a side-effect of the setup, not a deliberate security scenario. A future refactor could weaken or remove this check without any test catching it.
```suggestion
@responses.activate
def test_oauth_state_tamper_rejected(self):
    self._stub_github()
    resp = self.client.get(self.init_path)
    assert resp.status_code == 302
    resp = self.client.get("{}?{}".format(
        self.setup_path,
        urlencode({"code": "12345678901234567890", "state": "a" * 32}),
    ))
    assert resp.status_code == 200
    self.assertTemplateUsed(resp, "sentry/integrations/github-integration-failed.html")
    assert b"Invalid installation request." in resp.content
```

## Risk Metadata
Risk Score: 33/100 (MEDIUM) | Blast Radius: 2 production files in core Sentry GitHub-integration path (integration.py widely imported; pipeline_advancer.py is a shared integrations routing view) | Sensitive Paths: no direct path match, but code handles `client_secret` and `access_token` in-memory
AI-Authored Likelihood: LOW (idiomatic Python; cross-reference comments to existing OAuth2CallbackView; no generated-by markers)

(6 additional findings below confidence threshold: non-constant-time state comparison (75), installation_id bound pre-authenticity (70), OAuth code exchange missing redirect_uri/revoke (72), broad-except security angle (68), FORWARD_INSTALL_FOR removal import risk (65), get_document_origin JSON-unsafe interpolation (65))
