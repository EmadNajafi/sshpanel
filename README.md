# SSH VPN Panel

A web panel for password-based OpenSSH SOCKS/local-forwarding accounts on Ubuntu 24.04. VPN users are separate Linux accounts in the `sshvpn` group. A dedicated `sshd` listens on port 2222, while the server's normal administrative SSH service remains separate.

## Current functions

- Staff-only web sign-in with Django sessions and CSRF protection.
- Create, disable, enable, reset password, and delete VPN accounts.
- PostgreSQL account metadata and audit events. VPN passwords are passed directly to `chpasswd` and are never stored in PostgreSQL.
- A root-owned helper with a fixed set of operations. The web process has sudo access only to this helper.
- Nginx reverse proxy, Gunicorn service, separate VPN `sshd` service, and a server `sshvpn-menu` command for certificate issuance, renewal, service status, restart, logs, and database backup.

## Security boundary

`MaxSessions 0` on the VPN SSH service prevents shell, command, and SFTP sessions while allowing forwarding. The main administrative SSH service denies the `sshvpn` group. VPN clients must request forwarding without a session, for example `ssh -N -D 1080 -p 2222 vpn_alice@server.example.com`. The VPN service initially binds to `127.0.0.1` until egress isolation is verified on the test VPS.

**Deployment is not yet approved for production.** OpenSSH local forwarding can reach destinations accessible from the server, including its own loopback and private network. A network egress policy must be implemented and verified on the test VPS before binding port 2222 publicly. Installation also needs an end-to-end test on Ubuntu 24.04. Do not expose the VPN SSH port or issue user credentials until these checks pass.

## Planned installation on a disposable Ubuntu 24.04 VPS

1. Point a domain's DNS record at the VPS. Preserve an existing administrator SSH session and backup/snapshot before installation.
2. Clone this repository. From its root, run `sudo bash deploy/install.sh panel.example.com admin@example.com`.
3. Create the web administrator when prompted. Run `sudo menu ssl` to obtain HTTPS with Certbot. Calling `sudo menu` without arguments opens the interactive server menu (or use `sudo sshvpn-menu` if another program already owns `menu`).
4. Check `sudo menu status`. Verify that the normal admin SSH service still accepts its administrator account and denies `vpn_` accounts.
5. Verify that VPN users can use `ssh -N -D`, cannot open a shell or SFTP, and cannot forward to the server's local or private services. Only then allow client traffic to port 2222.

The installer creates `/etc/sshvpn/panel.env` for secrets, installs PostgreSQL locally, and creates systemd services. It refuses to overwrite an existing `/opt/ssh-vpn-panel` installation. Ports 80 and 443 must be reachable for certificate issuance. The firewall is not changed automatically, so the existing administrator port remains under the server operator's control.

## Local checks

With dependencies from `requirements.txt` installed, set placeholder `DJANGO_SECRET_KEY`, `PANEL_DOMAIN`, and `DB_PASSWORD` variables, then run:

```sh
python manage.py test --settings=sshvpn.test_settings
python manage.py makemigrations --check --dry-run --settings=sshvpn.test_settings
python manage.py check --deploy
```

The test settings use an in-memory SQLite database; deployed settings require PostgreSQL.

## Project layout

- `accounts/`: forms, web operations, models, audit log, and tests.
- `helper/sshvpnctl`: root-owned system account helper.
- `deploy/`: Ubuntu installer, systemd units, dedicated SSH configuration, and server menu.
- `sshvpn/`: Django configuration and routes.
