# projects-backup

Single git backup of project working files. Filled by backup.ps1
(robocopy mirror of sources into subfolders, secret scan, commit, push to branch projects-backup).
Sources and their own .git are NOT touched.

Subfolders = projects. Secrets (.env, *.key, credentials*, token*, *.log) are NOT included:
each .env is replaced by a *.example with the same keys and empty values.

Restore a project: copy its subfolder back, put your own .env in place.
