# EduVault

EduVault is a multi-user Flask web app for importing learning resources, uploading verified certificates, tracking progress, and sharing a public professional portfolio.

## Stack

- Flask server-rendered HTML
- Bootstrap and custom CSS
- SQLite with persistent Docker volumes
- Google OAuth via Authlib
- Gunicorn behind Caddy for VPS/domain deployment

## Local Development

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
$env:FLASK_SECRET_KEY="dev-secret"
$env:ADMIN_EMAIL="admin@example.com"
$env:ADMIN_PASSWORD="ChangeMe123!"
python app.py
```

Open `http://localhost:5055`.

Google OAuth is optional locally. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `APP_BASE_URL` to enable it.

Connector metadata is optional. EduVault can import Coursera, Udemy, and YouTube links with URL/title fallbacks. Set `YOUTUBE_API_KEY`, `UDEMY_CLIENT_ID`, and `UDEMY_CLIENT_SECRET` for richer provider metadata where available.

Feedback email uses SMTP. For Gmail, set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME`, and an app password in `SMTP_PASSWORD`. Feedback is sent to `FEEDBACK_RECIPIENT`, which defaults to `eduvaultai.com@gmail.com`.

## Docker VPS Deployment

1. Copy `.env.example` to `.env`.
2. Set `DOMAIN`, `APP_BASE_URL`, `FLASK_SECRET_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`.
3. Add Google OAuth credentials if you want Google sign-in enabled.
4. Point your domain's DNS A record to the VPS.
5. Start the stack:

```bash
docker compose up --build -d
```

Caddy will request and renew HTTPS certificates automatically for `DOMAIN`.

## Important Routes

- `/` public home
- `/courses` public course catalog
- `/register` and `/login`
- `/dashboard`
- `/certificates`
- `/resources`
- `/analytics`
- `/settings`
- `/admin`

## Notes

- Uploaded certificates are stored in the `eduvault_uploads` Docker volume.
- SQLite data is stored in the `eduvault_data` Docker volume.
- Billing, LinkedIn, and Coursera integrations are visible as polished coming-soon UI. Google OAuth is implemented when credentials are configured.
