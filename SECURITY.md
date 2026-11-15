# Security

This is a small community-built admin service, not an independently audited security product. Exposing it publicly creates an administration surface. Prefer HTTPS through a trusted reverse proxy and an additional identity layer such as Cloudflare Access on the admin hostname.

Do not publish passwords, access links, QR codes, tunnel tokens, state files, credential backups, or traffic databases. Keep admin and VPN hostnames separate. Complete initial password setup locally before publishing administration. Do not reuse the demo addresses or email in a real installation.

The app is designed around one trusted administrator. It does not implement multiple admin roles, 2FA, device quotas, or a strict real-time traffic cap. Reverse-proxy forwarding headers must come from a trusted connector. If you change the proxy architecture, review the Host, HTTPS, cookie and client-address assumptions first.

Do not disclose credentials in public issues. Use GitHub private vulnerability reporting if the repository owner has enabled it; otherwise contact the owner privately before sharing an exploit. No guaranteed security response time is provided.
