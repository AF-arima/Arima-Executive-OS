# Production email delivery

Arima Executive OS sends account verification, password reset, welcome, login
notification, and security-alert emails through the existing transactional email
service. Production deployments should use Resend over HTTPS rather than SMTP.

## Resend configuration

1. Verify a sender domain in Resend.
2. Create a send-only API key in Resend.
3. Add the following Railway variables through the Railway dashboard. Do not
   commit or paste API keys into source control or chat.

```dotenv
ENVIRONMENT=production
EMAIL_PROVIDER=resend
RESEND_API_KEY=<send-only-resend-api-key>
SMTP_FROM_EMAIL=no-reply@your-verified-domain.example
SMTP_FROM_NAME=Arima Executive OS
```

`SMTP_FROM_EMAIL` and `SMTP_FROM_NAME` are the established sender variable
names and are used by both providers. `EMAIL_FROM_ADDRESS` and
`EMAIL_FROM_NAME` remain supported aliases for existing deployments.

The Resend adapter makes an HTTPS `POST` to `https://api.resend.com/emails`.
It requires no SMTP ports or Resend SDK, and preserves the existing email
templates and authentication flow.

## Optional SMTP fallback

Only select SMTP deliberately:

```dotenv
EMAIL_PROVIDER=smtp
SMTP_FROM_EMAIL=no-reply@your-verified-domain.example
SMTP_FROM_NAME=Arima Executive OS
SMTP_HOST=<external-smtp-host>
SMTP_PORT=587
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<smtp-password>
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

The application rejects localhost SMTP and unencrypted SMTP configuration in
production. Railway may block outbound SMTP ports, so Resend is the supported
production option.

## Verification

After Railway redeploys, register a disposable test account and use the
verification link it receives. Then verify login, refresh, logout, and the
protected dashboard. Resend requires the sender domain to be verified before
it can deliver to arbitrary recipients.

See the official [Resend send-email API](https://resend.com/docs/api-reference/emails/send-email)
and [domain verification guidance](https://resend.com/docs/knowledge-base/how-do-i-create-an-email-address-or-sender-in-resend).
