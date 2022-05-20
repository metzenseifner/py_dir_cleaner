# Directory Cleaner

The directory cleaner uses two criteria to remove files:

1. name
2. age

The name is matched or skipped based on match and exclude patterns. The age is
matched using a KeepDuration, that is measured in days.

The directory cleaner was conceived with the purpose of cleaning up the build
output directory of a build system that pushes some output of a git repo source
build to some target directory organized by repository name followed by branch name. 



# Configuration

The example configuration adds anything under SearchPaths to its purview, and
matches subdirectories and exlcudes subdirectories. Excluded patterns should take
precedence over matched patterns (e.g. you provide the same pattern in both sections).

```
[SearchPaths]
Path1=/mnt/remote/smb/semanticallyrelevantname_build_hidden/projects

[MatchPatterns]
Path1=branches

[ExcludePatterns]
Path1=master

[KeepDurations]
# days
Path1=1
```

# Usage


## Cleaner Service Unit

```
# /etc/systemd/system/semanticallyrelevantname_clean.service
[Unit]
Description=Clean gitlab projects that are not master and are too old

[Service]
Type=exec
ExecStart=sh -c '/bin/python3 /root/dir_cleaner.py /root/dir_cleaner.conf'

[Install]
WantedBy=multi-user.target
```


## Cleaner Timer Unit

```
# /etc/systemd/system/semanticallyrelevantname_clean.timer
[Unit]
Description=Timer to periodically trigger the corresponding service. Will not run if previous call has is still running (active).
Requires=semanticallyrelevantname_clean.service

[Timer]
Unit=semanticallyrelevantname_clean.service
OnCalendar=*-*-* 01:00:00

[Install]
WantedBy=timers.target
```
