# SSH VPN Panel

A web panel for password based OpenSSH SOCKS and local forwarding accounts on Ubuntu 24.04. The private GitHub repository requires your own GitHub access on each server before cloning.

## Components

- Django staff sign in, account creation, disabling, enabling, password reset, deletion, and audit events.
- PostgreSQL for metadata. VPN passwords are sent to the Linux account helper and are not stored in the database.
- A root owned helper with a fixed set of allowed operations; the web service can run only that helper through sudo.
- Nginx and Gunicorn for the panel, a separate VPN `sshd` on port 2222, and a `menu` command for administration and later SSL issuance.
- An nftables output policy for VPN forwarding sockets that blocks this host, loopback, private, link local, and other nonpublic destination ranges. Ubuntu's local DNS stub is allowed on port 53.

## Security model

VPN users belong to `sshvpn`. The normal administrator SSH daemon denies that group. The VPN daemon permits password authentication and local forwarding, while `MaxSessions 0` prevents shell, command, and SFTP sessions. Clients must use a forwarding only connection, such as `ssh -N -D 1080 -p 2222 vpn_alice@server.example.com`.

The VPN daemon initially listens on `127.0.0.1:2222`. After testing isolation on a new server, `sudo menu vpn-public` changes it to `0.0.0.0:2222`; `sudo menu vpn-local` restores the loopback listener. The egress policy is required by the VPN daemon's systemd unit. Review any existing firewall and cloud network rules before opening the port. On servers with public address translation, test that forwarding cannot reach the server through its external address.

## Install with a domain and HTTPS

1. Point the domain to the server. Keep an administrator SSH session open and take a server snapshot.
2. Clone this private repository using a GitHub credential or an SSH key with read access.
3. Run `sudo bash deploy/install.sh panel.example.com admin@example.com` from the repository root.
4. Create the web administrator when prompted. Run `sudo menu ssl` to request HTTPS with Certbot, then `sudo menu status`.
5. Test VPN forwarding and denied shell/SFTP/internal destinations before `sudo menu vpn-public`.

The installer targets Ubuntu 24.04, refuses to replace an existing `/opt/ssh-vpn-panel`, and does not change the existing administrator SSH port or firewall. Certificate issuance requires ports 80 and 443 to reach the server.

## Local test without a domain or SSL

Run `sudo bash deploy/install.sh --local-test`. This binds **the web panel** to `127.0.0.1:8080` and leaves it on HTTP, so access it only through an encrypted administrator SSH tunnel:

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 root@SERVER_IP
```

Open `http://localhost:8080/login/` on the computer running the tunnel. On the server, run `sudo menu admin` to create your own web administrator. When you are ready to use a domain, point its DNS record to the server, make ports 80 and 443 reachable, and run `sudo menu ssl panel.example.com admin@example.com`. This switches the panel from the local test listener to HTTPS. A failed certificate request restores the local Nginx settings. Any preexisting Nginx sites remain active.

`sudo menu` opens an interactive list. Direct commands include `ssl`, `renew`, `status`, `restart`, `logs`, `backup`, `admin`, `admin-password USERNAME`, `vpn-public`, and `vpn-local`.

## Verification

On the test VPS (Ubuntu 24.04), installation and migrations completed; all six services were active. A real password authenticated VPN account reached a public web destination through SOCKS but could not reach the server's loopback SSH port or run a shell command. The web operations create, disable, enable, reset password, and delete passed against PostgreSQL and Linux accounts. The project also has four Django tests.

Each new server needs its own verification, particularly if it uses public address translation or an existing firewall. The installer does not automatically expose the web panel in local test mode.
