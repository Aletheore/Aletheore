# Pattern B: Token-Mediating Backend (`login_uri` Form POST Redirect) Recipe

This reference provides the complete implementation recipe for **Pattern B
(Token-Mediating Backend via `login_uri` Redirect — IETF
`draft-ietf-oauth-browser-based-apps` § 6.2)**, recommended for server-rendered
web applications and direct form POST redirects.

--------------------------------------------------------------------------------

## 1. Frontend Configuration (Direct Server Form POST)

```html
<!-- HTML Data Attributes Configuration -->
<div id="g_id_onload"
     data-client_id="YOUR_CLIENT_ID.apps.googleusercontent.com"
     data-login_uri="https://example.com/api/auth/callback"
     data-auto_select="true"
     data-ux_mode="redirect">
</div>
<div class="g_id_signin" data-type="standard" data-ux_mode="redirect"></div>

<!-- Or Programmatic GIS Initialization -->
<script>
  google.accounts.id.initialize({
    client_id: 'YOUR_CLIENT_ID.apps.googleusercontent.com',
    login_uri: 'https://example.com/api/auth/callback',
    ux_mode: 'redirect',
    auto_select: true
  });
  google.accounts.id.renderButton(document.getElementById('signin-btn'), {
    theme: 'outline',
    size: 'large'
  });
</script>
```

--------------------------------------------------------------------------------

## 2. Backend Form Callback Handler (FastAPI / Python)

```python
from fastapi import Cookie, FastAPI, Form, HTTPException
from fastapi.responses import RedirectResponse
from google.auth.transport import requests
from google.oauth2 import id_token

app = FastAPI()
GOOGLE_CLIENT_ID = "YOUR_CLIENT_ID.apps.googleusercontent.com"


@app.post("/api/auth/callback")
async def google_login_redirect(
    credential: str = Form(...),
    g_csrf_token: str | None = Form(None),
    g_csrf_token_cookie: str | None = Cookie(None, alias="g_csrf_token"),
):
  # 1. Double-submit CSRF token validation when using login_uri redirect
  if (
      not g_csrf_token
      or not g_csrf_token_cookie
      or g_csrf_token != g_csrf_token_cookie
  ):
    raise HTTPException(
        status_code=400, detail="CSRF check failed on login_uri redirect"
    )

  try:
    # 2. Cryptographically verify ID token
    id_info = id_token.verify_oauth2_token(
        credential, requests.Request(), GOOGLE_CLIENT_ID
    )
    user_id = id_info["sub"]
    user_email = id_info["email"]

    # 3. Establish session & issue secure cookie redirect
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session_id",
        value=create_server_session(user_id, user_email),
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response
  except ValueError as e:
    raise HTTPException(
        status_code=401, detail="Invalid credential token"
    ) from e
```
