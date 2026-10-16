# Security policy

## Supported versions
Only the latest release receives security fixes.

## Reporting a vulnerability
Please **do not open a public issue** for a security problem.

Use GitHub's private reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Include the
version, what you did, what happened, and (if you can) a small file or script that shows it. You should get a first answer within
a few days. When a fix is released the report is credited unless you prefer otherwise.

## What the app does and does not do
- No telemetry. It connects only to the servers you configure and, when you ask for it, to github.com to download a portable Git
  (verified against the SHA-256 published with the release) or to look for a newer release.
- Passwords you type for FTP, SFTP, WebDAV and cloud locations are kept in memory only and are not written to settings or sessions.
- Files are only changed by actions you start (copy, sync, save, restore); overwritten files can be backed up first.
- Windows installers published here are SHA-256 checksummed; code signing is added when a certificate is available.
