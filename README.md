# SSH VPN Panel

A web panel for password based OpenSSH SOCKS and local forwarding accounts on Ubuntu 24.04. The private GitHub repository requires your own GitHub access on each server before cloning.

## Components

- Django staff sign in, account creation and editing with active days and simultaneous VPN connection limit, disabling, enabling, password reset, deletion, and audit events.
- Dashboard cards show server CPU, available-memory usage, and root-filesystem disk usage. They refresh every 15 seconds while the dashboard is open and are accessible only to staff.
- PostgreSQL for metadata. New and changed VPN passwords are encrypted before storage and can be revealed by a signed-in staff administrator. Existing passwords created before this feature cannot be recovered from Linux hashes; set a new password to make them available in the list.
- A root owned helper with a fixed set of allowed operations; the web service can run only that helper through sudo.
- Nginx and Gunicorn for the panel, VPN accounts on the server's existing SSH port, and a `menu` command for administration and later SSL issuance.
- An nftables output policy for VPN forwarding sockets that blocks this host, loopback, private, link local, and other nonpublic destination ranges. Ubuntu's local DNS stub is allowed on port 53.

## Security model

VPN users belong to `sshvpn`. A `Match Group sshvpn` block at the end of the main SSH configuration permits password authentication and local forwarding for that group, while `MaxSessions 0` prevents shell, command, and SFTP sessions. Administrator SSH accounts keep their existing authentication and port. Clients must use a forwarding only connection, such as `ssh -N -D 1080 -p YOUR_EXISTING_SSH_PORT vpn_alice@server.example.com`. On the current test VPS that port is 5656.

At account creation, enter the Linux login name, VPN password, number of active days, and maximum simultaneous SSH connections. The expiration clock starts when the account is created. A PAM account hook denies new VPN logins after expiration or when the connection limit is reached. A systemd timer runs every minute to lock expired Linux accounts and disconnect their existing tunnels. The limit counts SSH transport connections, not people or individual SOCKS requests; someone sharing credentials can open many SOCKS requests through one connection. VPN passwords may be shorter than 12 characters, including one character. Empty passwords, line breaks and NUL are rejected, as are duplicate or invalid Linux login names. The separate web administrator password still requires at least 12 characters.

The PAM hook is added only once to `/etc/pam.d/sshd`, before the normal account rules, and bypasses Linux users outside the `sshvpn` group. Its previous file is saved under `/var/backups/sshvpn-panel`. Earlier VPN accounts receive an unlimited policy on upgrade; new accounts receive the selected limits. The hook and the timer must both be working for the limits to be enforced.

The installer keeps the SSH port already configured on the server; it does not open another listener. The egress policy blocks VPN forwarding to this host and private networks. Review existing firewall and cloud network rules for the SSH port. On servers with public address translation, test that forwarding cannot reach the server through its external address. The managed SSH block is appended to `/etc/ssh/sshd_config`; keep it at the end when editing SSH settings later. The installer and upgrade back up SSH configuration before changing it and validate it before reloading the service.

The hamburger menu opens **Settings**. There an administrator can change their own password using the current password, or stage a new shared SSH port. Staging keeps the original port active, validates SSH configuration and verifies that the new port is listening locally. Open a separate SSH connection through the new port from outside the server, then confirm in Settings to close the old port. Cancel restores the original port. Host and provider firewalls may need a rule for the new port. Keep a working administrator session open while changing ports.

The credential display uses a server-side encrypted database field; its encryption key is derived from `DJANGO_SECRET_KEY` in `/etc/sshvpn/panel.env`. Preserve that value in backups. Revealed passwords are served only to authenticated staff with `Cache-Control: no-store`. Enable HTTPS before revealing passwords over an untrusted network; the initial HTTP mode does not encrypt browser traffic.

## Install: HTTP first

For an existing installation, update the checkout and run `sudo bash deploy/upgrade.sh` instead of the fresh installer. If the server uses `/root/.ssh/sshpanel_deploy`, run `GIT_SSH_COMMAND='ssh -i /root/.ssh/sshpanel_deploy -o IdentitiesOnly=yes' git pull --ff-only` to update it. The upgrade backs up the database and configuration and retains the web administrator, VPN accounts, listener addresses, and current TLS mode.

To make a previously loopback-only HTTP panel reachable at the server's public IPv4, run `sudo menu web-public SERVER_PUBLIC_IP` after upgrading. This switches Nginx to port 80 and updates Django's allowed host without changing VPN users or SSH ports. Allow inbound TCP 80 in the host and provider firewalls if needed. Administrator credentials are unencrypted over HTTP until you opt into SSL.

1. Keep an administrator SSH session open and take a server snapshot. Give the server read access to this private GitHub repository.
2. Clone the repository and run `sudo bash deploy/install.sh` from its root. You can pass a domain or IPv4 address as the first argument to skip the host prompt.
3. The **first interactive prompts** ask for the web administrator username and password, including password confirmation. The password input is hidden, is sent to Django through stdin, and is stored only as a hash.
4. Enter the domain or IPv4 address for the panel if prompted. The installer starts the panel on **HTTP port 80** and does not request a certificate.
5. Test VPN forwarding and denied shell/SFTP/internal destinations on the server's existing SSH port.

For example, with an IPv4 address:

```sh
git clone git@github.com:EmadNajafi/sshpanel.git
cd sshpanel
sudo bash deploy/install.sh 203.0.113.10
```

When you choose to use HTTPS later, point your domain to the server, allow ports 80 and 443, and run `sudo menu ssl panel.example.com admin@example.com`. A failed certificate request restores the previous HTTP configuration. Until HTTPS is enabled, administrator credentials travel over HTTP; use a trusted network or an SSH tunnel.

The installer targets Ubuntu 24.04, refuses to replace an existing `/opt/ssh-vpn-panel`, and does not change the existing administrator SSH port or firewall. The server needs an authenticated SSH key or another GitHub credential to clone this private repository.

## Local test without a domain or SSL

Run `sudo bash deploy/install.sh --local-test`. This binds **the web panel** to `127.0.0.1:8080` and leaves it on HTTP, so access it only through an encrypted administrator SSH tunnel:

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 root@SERVER_IP
```

Open `http://localhost:8080/login/` on the computer running the tunnel. The installer asks for and creates the administrator before it starts the web service. When you are ready to use a domain, point its DNS record to the server, make ports 80 and 443 reachable, and run `sudo menu ssl panel.example.com admin@example.com`. This switches the panel from the local test listener to HTTPS. A failed certificate request restores the local Nginx settings. Any preexisting Nginx sites remain active.

`sudo menu` opens an interactive list. Direct commands include `ssl`, `renew`, `status`, `restart`, `logs`, `backup`, `admin`, and `admin-password USERNAME`.

## Verification

On the test VPS (Ubuntu 24.04), the shared-port configuration was tested on the existing SSH port 5656 with a disposable password account. Password authentication and public TCP forwarding succeeded; shell sessions, forwarding to the server's loopback SSH port, and a second concurrent login at a limit of one were denied. The disposable account was deleted afterward. A new administrator SSH connection still succeeded, and the former port 2222 listener was stopped. The web operations create, disable, enable, reset password, and delete passed against PostgreSQL and Linux accounts. The current HTTP-first installer has not been run from scratch on that VPS.

Each new server needs its own verification, particularly if it uses public address translation or an existing firewall. The installer does not automatically expose the web panel in local test mode.
