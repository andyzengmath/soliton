## Summary
3 files changed, 247 lines added, 50 lines deleted. 3 findings (2 critical, 1 improvement, 0 nitpicks).
Adds a GitHub OAuth authorize step to verify the installing user matches the GitHub app `sender`; logic is sound, but the new OAuth callback has two unhandled-exception paths — one of which is already firing in production per the Sentry bot comment on this PR.

## Critical
:red_circle: [correctness] `get_user_info` raises HTTPError on 401 — unhandled, crashes the setup view in src/sentry/integrations/github/integration.py:435 (confidence: 92)
After exchanging the OAuth code, the view calls `authenticated_user_info = get_user_info(payload["access_token"])` and then guards the result with `if "login" not in authenticated_user_info`. But `get_user_info` (from `sentry.identity.github`) wraps `requests.get(...).json()` and raises `requests.HTTPError` on non-2xx responses — it does not return an empty dict. The Sentry bot comment on this PR already observed this firing in production: `HTTPError: 401 Client Error: Unauthorized for url: https://api.github.com/user` at `/extensions/{provider_id}/setup/` (issue 5175299490). A 401 happens routinely when GitHub invalidates the just-minted access_token (e.g., code re-use, race), so users see a stack-traced 500 instead of the intended "Invalid installation request" page.
```suggestion
        try:
            authenticated_user_info = get_user_info(payload["access_token"])
        except Exception:
            return error(request, self.active_organization)
        if "login" not in authenticated_user_info:
            return error(request, self.active_organization)
```
[References: https://sentry.sentry.io/issues/5175299490/]

:red_circle: [correctness] Unchecked nested dict access on `integration.metadata["sender"]["login"]` can raise KeyError in src/sentry/integrations/github/integration.py:499 (confidence: 86)
`GitHubInstallation.dispatch` compares the authenticated user against `integration.metadata["sender"]["login"]`. `Integration.metadata` is a free-form JSON blob populated by webhook payloads. Older integrations (installed before the webhook started persisting `sender`) or integrations whose metadata was migrated/overwritten will not have `metadata["sender"]`; the comparison then raises `KeyError` and crashes the setup view with a 500. The related test `test_github_user_mismatch` only exercises the happy path where a webhook with `sender.login` was just delivered. The intended failure mode here is "reject with the invalid-installation page", not 500.
```suggestion
        sender_login = (integration.metadata or {}).get("sender", {}).get("login")
        if (
            sender_login is None
            or pipeline.fetch_state("github_authenticated_user") != sender_login
        ):
            return error(request, self.active_organization)
```

## Improvements
:yellow_circle: [security] State parameter comparison is not constant-time in src/sentry/integrations/github/integration.py:413 (confidence: 85)
`if request.GET.get("state") != pipeline.signature:` performs a short-circuiting byte-wise comparison against a server-controlled HMAC-like `pipeline.signature`. While the pipeline signature is short-lived, this is exactly the comparison pattern that `hmac.compare_digest` exists to replace; timing-safe comparison is Sentry's house style for signature checks (see `sentry/utils/security.py`, webhook signature verification in this same file's `/webhook/` handler). Switching is cheap and eliminates the whole class of timing side-channel arguments during security review.
```suggestion
        import hmac
        state = request.GET.get("state") or ""
        if not hmac.compare_digest(state, pipeline.signature or ""):
            return error(request, self.active_organization)
```

## Risk Metadata
Risk Score: 62/100 (MEDIUM-HIGH) | Blast Radius: GitHub integration install pipeline (1 auth-sensitive file, 1 dispatcher, 1 test file) | Sensitive Paths: `integrations/github/` (auth/identity), `frontend/pipeline_advancer.py` (routing)
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold suppressed: f-string construction of OAuth authorize URL without `urlencode` of `redirect_uri`; bare `except Exception` swallowing OAuth exchange errors without logging.)
