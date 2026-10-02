# SSH VPN Panel

A web panel for password based OpenSSH SOCKS and local forwarding accounts on Ubuntu 24.04. The public repository can be installed directly on a new server.

## Components

- Django staff sign in, account creation and editing with active days and simultaneous VPN connection limit, quick 30/60/90-day renewal, traffic reset, disabling, enabling, password reset, deletion, and audit events. The account list shows remaining days; row actions use labeled icons. The copy action includes host, SSH port, username, password, exact expiry in the Persian calendar and Tehran time, and connection limit.
- Dashboard cards show server CPU, available-memory usage, and root-filesystem disk usage. They refresh every 15 seconds while the dashboard is open and are accessible only to staff.
- The account list shows live SSH tunnel connection counts and per-account upload, download and combined traffic. Readings refresh every 15 seconds. A root-owned counter snapshot persists totals across panel restarts and server reboots.
- PostgreSQL for metadata. New and changed VPN passwords are encrypted before storage and can be revealed by a signed-in staff administrator. Existing passwords created before this feature cannot be recovered from Linux hashes; set a new password to make them available in the list.
- Settings → Backup and restore creates a downloadable archive containing the complete PostgreSQL dump, panel secret, managed Linux login hashes and policies, saved traffic totals, and copies of the panel and SSH configuration. Upload that file to a newly installed panel to restore users and administrators.
- A root owned helper with a fixed set of allowed operations; the web service can run only that helper through sudo.
- Nginx and Gunicorn for the panel, VPN accounts on the server's existing SSH port, and a `menu` command for administration and later SSL issuance.
- An nftables output policy for VPN forwarding sockets that blocks this host, loopback, private, link local, and other nonpublic destination ranges. Ubuntu's local DNS stub is allowed on port 53.

## Security model

VPN users belong to `sshvpn`. A `Match Group sshvpn` block at the end of the main SSH configuration permits password authentication and local forwarding for that group, while `MaxSessions 0` prevents shell, command, and SFTP sessions. Administrator SSH accounts keep their existing authentication and port. Clients must use a forwarding only connection, such as `ssh -N -D 1080 -p YOUR_EXISTING_SSH_PORT vpn_alice@server.example.com`.

At account creation, enter the Linux login name, VPN password, number of active days, and maximum simultaneous SSH connections. Set the connection limit to `0` for unlimited connections; the account list displays this as **Unlimited**. The expiration clock starts when the account is created. A PAM account hook denies new VPN logins after expiration or when a positive connection limit is reached. A systemd timer runs every minute to lock expired Linux accounts and disconnect their existing tunnels. The limit counts SSH transport connections, not people or individual SOCKS requests; someone sharing credentials can open many SOCKS requests through one connection. VPN and web administrator passwords can be short, including one character. Empty passwords are rejected, as are duplicate or invalid Linux login names.

The PAM hook is added only once to `/etc/pam.d/sshd`, before the normal account rules, and bypasses Linux users outside the `sshvpn` group. Its previous file is saved under `/var/backups/sshvpn-panel`. Earlier VPN accounts receive an unlimited policy on upgrade; new accounts receive the selected limits. The hook and the timer must both be working for the limits to be enforced.

Online status counts live VPN SSH transports admitted by the PAM hook. Existing connections made before traffic tracking was installed may need to reconnect before they appear online. Network usage starts at the time this feature is installed; past traffic cannot be reconstructed. The counters track IP packets from each VPN user's forwarding sockets, including packet overhead and a small amount of DNS traffic, so they are an approximation of VPN transfer rather than exact application payload. Counter totals are saved on each panel refresh and every minute by `sshvpn-usage.timer`; an abrupt reboot can lose traffic since the last snapshot.

Reset traffic starts a new accounting period at zero using the current network counters as its baseline. It does not disconnect the user's active VPN sessions. Renewal adds days to a future expiry; an expired account starts counting from the renewal time. If the account was otherwise enabled, renewal makes it usable again. Passwords created before encrypted password storage was added cannot be copied until they are changed in the panel. On HTTP, the copy button uses the browser's selection based clipboard fallback and offers a manual copy dialog if clipboard access is blocked.

Click an online account name or its online badge to see the source IP of each active SSH connection. The server records the address supplied by OpenSSH to the PAM account hook; it is the address seen by the VPS, which may be a NAT or upstream proxy address. Already active connections created before IP tracking was installed show an unavailable address until they reconnect. Addresses are only returned to signed-in staff and are not saved as a connection history.

The installer keeps the SSH port already configured on the server; it does not open another listener. The egress policy blocks VPN forwarding to this host and private networks. Review existing firewall and cloud network rules for the SSH port. On servers with public address translation, test that forwarding cannot reach the server through its external address. The managed SSH block is appended to `/etc/ssh/sshd_config`; keep it at the end when editing SSH settings later. The installer and upgrade back up SSH configuration before changing it and validate it before reloading the service.

The hamburger menu opens **Settings**. There an administrator can change their own password using the current password, or stage a new shared SSH port. Staging keeps the original port active, validates SSH configuration and verifies that the new port is listening locally. Open a separate SSH connection through the new port from outside the server, then confirm in Settings to close the old port. Cancel restores the original port. Host and provider firewalls may need a rule for the new port. Keep a working administrator session open while changing ports.

The credential display uses a server-side encrypted database field; its encryption key is derived from `DJANGO_SECRET_KEY` in `/etc/sshvpn/panel.env`. Preserve that value in backups. Passwords are always shown in the staff-only account list, which is served with `Cache-Control: no-store`. Enable HTTPS before opening the account list over an untrusted network; the initial HTTP mode does not encrypt browser traffic.

## Backup and migration

Open **Settings → Backup and restore**, create a backup, then download its `.tar.gz` file. No backup password is required. The archive is **not encrypted**: it includes the entire PostgreSQL database (administrators, VPN credentials, referrals, audit history and all other rows), the key required to decrypt VPN passwords, every managed VPN account's Linux login hash and lock state, account policy, password aging fields, and saved traffic totals. Every data member in new backups has a SHA-256 checksum verified before restoration. The source panel environment, Nginx and SSH configurations are also included in the archive for reference. Keep the archive private. Linux UIDs are reassigned on the destination to avoid conflicts. Active connections and their transient source IPs cannot be moved to another server; users reconnect after restore.

For migration, install the same version of this panel on a fresh Ubuntu 24.04 server, open **Settings → Backup and restore**, upload the file and confirm. The destination database and administrator accounts are replaced. Restore checks the archive, stages its database in a temporary PostgreSQL database and checks that VPN account names match before changing the live installation. A root-only pre-restore recovery copy is saved under `/var/backups/sshvpn-panel/`. After restoration, sign in with an administrator from the backup. The destination IP/domain, private path, TLS mode, web and SSH ports, and database connection remain local, so the new server remains reachable. Existing VPN accounts absent from the backup stop the restore; use a fresh destination to avoid discarding them. Uploads are limited to 1 GiB. Enable HTTPS or use an administrator SSH tunnel before downloading or uploading sensitive backup files on an untrusted network; HTTP does not protect browser traffic. Older `.svpb` backups remain on disk but require the earlier passphrase-based release to restore.

## Install: HTTP first

In **Settings → Web address**, an administrator can set a single-segment URL path and the current HTTP or HTTPS listener port. Existing installations keep their current address until the form is submitted. Open the new URL and confirm it there within five minutes; an unconfirmed change or a failed local health check restores the previous Nginx and panel configuration. Allow the chosen port through host and provider firewalls first. HTTPS changes preserve the installed certificate and keep port 80 for renewal. The path is an address choice, not an authentication or TLS substitute.

Run the same one-line command below on an existing installation to upgrade it. It detects the installed panel and checks the repository for a newer revision without asking for administrator credentials or an IP address. If the installed revision is current, it prints a short message and stops; otherwise it runs `deploy/upgrade.sh`. Successful installs and upgrades record the installed revision. Older installations without this record receive one upgrade to establish it. The upgrade backs up the database and configuration and retains the web administrator, VPN accounts, listener addresses, and current TLS mode. You can also run `git pull --ff-only` followed by `sudo bash deploy/upgrade.sh` from `/root/sshpanel`.

To make a previously loopback-only HTTP panel reachable at the server's public IPv4, run `sudo menu web-public SERVER_PUBLIC_IP` after upgrading. This switches Nginx to port 80 and updates Django's allowed host without changing VPN users or SSH ports. Allow inbound TCP 80 in the host and provider firewalls if needed. Administrator credentials are unencrypted over HTTP until you opt into SSL.

1. Keep an administrator SSH session open and take a server snapshot. Open a root shell with `sudo -i` if needed.
2. Run the bootstrap command below. It clears the terminal, clones the public repository to `/root/sshpanel`, and runs `deploy/install.sh`. You can pass a domain or IPv4 address after the process substitution to override automatic IPv4 detection.
3. The installer offers the default web administrator `admin` with password `123456`. Confirm it or choose your own username and password. A custom password is hidden while entering it and confirmed once. The password is sent to Django through stdin and stored only as a hash. The chosen credentials are printed once at the end of installation; keep the terminal output private. Change the public default password after first login.
4. The installer detects the server's public IPv4 address automatically. If detection fails, rerun it with a domain or IPv4 address as an argument. It starts the panel on **HTTP port 80** with a generated path, prints the full URL and administrator credentials, and does not request a certificate.
5. Test VPN forwarding and denied shell/SFTP/internal destinations on the server's existing SSH port.

From an interactive root shell, run this command:

```sh
bash <(curl -fsSL --ipv4 https://raw.githubusercontent.com/EmadNajafi/sshpanel/main/install.sh)
```

When you choose to use HTTPS later, point your domain to the server, allow ports 80 and 443, and run `sudo menu ssl panel.example.com admin@example.com`. A failed certificate request restores the previous HTTP configuration. Until HTTPS is enabled, administrator credentials travel over HTTP; use a trusted network or an SSH tunnel.

The installer targets Ubuntu 24.04. Its one-line bootstrap upgrades an existing installation and refuses to overwrite a partial installation. It does not change the existing administrator SSH port or firewall. It installs Git if needed, downloads all project files from GitHub, and runs entirely on the destination server. No GitHub credentials are required.

## Local test without a domain or SSL

Run `sudo bash deploy/install.sh --local-test`. This binds **the web panel** to `127.0.0.1:8080` and leaves it on HTTP, so access it only through an encrypted administrator SSH tunnel:

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 root@SERVER_IP
```

Open the full `http://localhost:8080/.../` URL printed by the installer on the computer running the tunnel. The installer asks for and creates the administrator before it starts the web service. When you are ready to use a domain, point its DNS record to the server, make ports 80 and 443 reachable, and run `sudo menu ssl panel.example.com admin@example.com`. This switches the panel from the local test listener to HTTPS while retaining its path. A failed certificate request restores the local Nginx settings. Any preexisting Nginx sites remain active.

`sudo menu` opens an interactive list. Direct commands include `ssl`, `renew`, `status`, `restart`, `logs`, `backup`, `admin`, and `admin-password USERNAME`.

To remove the installation, inspect the plan with `sudo menu uninstall --dry-run`, then run `sudo menu uninstall` in an interactive terminal and type `DELETE SSHVPN`. This permanently removes the panel database, web administrators, VPN Linux accounts and their policies, all local panel backups, services, Nginx site, dedicated certificate, firewall tables, and `/root/sshpanel` checkout. It removes the panel's SSH/PAM hooks while preserving the current administrator SSH port. It also purges Nginx when no other site or custom configuration uses it. For a server already uninstalled with an older release, run `bash <(curl -fsSL https://raw.githubusercontent.com/EmadNajafi/sshpanel/main/deploy/purge-unused-nginx.sh)` from an interactive root Bash shell; it checks for other Nginx sites and asks for `PURGE NGINX`. PostgreSQL, unrelated sites, shared packages, and system journals remain. Download any backup you need before uninstalling.

If an older uninstall left VPN Linux accounts behind, run `bash <(curl -fsSL https://raw.githubusercontent.com/EmadNajafi/sshpanel/main/deploy/cleanup-vpn-users.sh) --dry-run` as root to list them. Run the same command without `--dry-run` and type `DELETE VPN USERS` to lock the accounts, terminate their sessions, delete them, and remove the dedicated `sshvpn` group. The script only selects accounts whose primary group is `sshvpn`, home is `/var/empty`, and shell is `/usr/sbin/nologin`.

## Verification

On an Ubuntu 24.04 test VPS, the shared-port configuration was tested with a disposable password account. Password authentication and public TCP forwarding succeeded; shell sessions, forwarding to the server's loopback SSH port, and a second concurrent login at a limit of one were denied. The disposable account was deleted afterward. A new administrator SSH connection still succeeded. The web operations create, disable, enable, reset password, and delete passed against PostgreSQL and Linux accounts.

Each new server needs its own verification, particularly if it uses public address translation or an existing firewall. The installer does not automatically expose the web panel in local test mode.
