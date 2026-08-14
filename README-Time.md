# STVID system clock checking

Accurate UTC timestamps are essential to STVID observations. The acquisition
programs use the host system clock when constructing FITS filenames and the
`DATE-OBS` and `MJD-OBS` header values. `timecheck.py` provides an independent,
read-only check of that clock; it does not change the time or configure the
operating system's synchronization service.

## Running a check

Run the script from the STVID directory:

```console
python timecheck.py -c configuration.ini
```

Useful command-line overrides are:

```console
python timecheck.py --server pool.ntp.org --samples 5
python timecheck.py --server 0.europe.pool.ntp.org --server 1.europe.pool.ntp.org
python timecheck.py --log-file /var/log/stvid/timecheck.log
```

`--server` and `--samples` override the corresponding configuration settings
for that run. `--server` can be repeated.

Every run prints a human-readable report and appends one compact JSON object to
the log. The log therefore uses the JSON Lines (JSONL) format, despite its
`.log` extension. A run returns one of these process exit codes:

| Exit code | Status | Meaning |
|---:|---|---|
| `0` | `OK` | Successful samples are within the configured limits. |
| `1` | `WARNING` | Offset or delay exceeds a warning limit, or at least one query failed. |
| `2` | `CRITICAL` | No usable NTP response was received or the critical offset was exceeded. |

These exit codes allow cron, systemd, Task Scheduler wrappers, and monitoring
software to raise an alert.

## Configuration

Section capitalization is ignored, so `[Time]` and `[TIME]` are equivalent.
The documented form is:

```ini
[Time]
servers = pool.ntp.org
samples = 3
timeout_s = 2.0
max_offset_ms = 100.0
critical_offset_ms = 1000.0
max_delay_ms = 500.0
log_file = timecheck.log
```

`servers`
: Comma-separated NTP server hostnames or IP addresses. Each server receives
  `samples` queries. This list controls the independent check only; it does not
  reconfigure the NTP servers used by the operating system.

`samples`
: Number of samples taken from every configured server. More samples give a
  more stable median and jitter estimate but take longer, particularly when a
  server is unreachable. The minimum is one.

`timeout_s`
: Network timeout for each NTP query and local diagnostic command. With several
  unreachable servers, the worst-case runtime is approximately the number of
  queries multiplied by this value, plus diagnostic-command timeouts.

`max_offset_ms`
: Warning threshold for the absolute median clock offset. A larger result
  changes an otherwise `OK` check to `WARNING`.

`critical_offset_ms`
: Critical threshold for the absolute median clock offset. It should be larger
  than `max_offset_ms`.

`max_delay_ms`
: Warning threshold for median network round-trip delay. Large or variable
  network delay reduces confidence in an Internet NTP measurement.

`log_file`
: Destination for the append-only JSONL log. A relative path is resolved from
  the directory in which the script is run. Use an absolute path when the
  script may be started by a scheduler with a different working directory.
  Ensure the account running the check can create the directory and append to
  the file.

The supplied thresholds are starting points, not guaranteed STVID accuracy
requirements. They should be tightened after observing the normal offset and
jitter of the station, camera, network, and operating system.

## Interpreting the terminal report

`Median offset`
: The median estimate of `NTP server time - local system time`. A positive
  value means that the local clock appears behind the server; a negative value
  means that it appears ahead.

`Maximum offset`
: The largest absolute offset among the successful samples. A large difference
  between this and the median suggests an outlier or unstable network path.

`Offset jitter`
: The population standard deviation of the successful offset estimates. Low
  jitter makes the median more credible. Persistent increases can reveal
  network degradation or unstable timekeeping.

`Median delay`
: The median estimated NTP round-trip network delay. NTP assumes that much of
  the outward and return delay is symmetric. Strong path asymmetry can bias the
  offset even when the reported delay is moderate.

`Stratum`
: The server's distance from a reference clock. Stratum is not by itself an
  accuracy measurement. A nearby, stable stratum-2 or stratum-3 server can be
  better for this check than a distant stratum-1 server.

`Local synchronization services`
: Raw diagnostic output from tools found on the host, including `timedatectl`,
  `chronyc`, `ntpq`, or `w32tm`. It shows such information as the selected
  source, last synchronization, leap status, daemon offset, and configured
  peers when the relevant tool provides it.

The overall `OK`, `WARNING`, or `CRITICAL` assessment currently uses the
independent NTP samples only. The local-service output is recorded for human or
downstream analysis but is not parsed into the status. Consequently, `OK`
means that the host clock agreed with the queried server at check time; it does
not prove that an operating-system synchronization service is enabled or will
keep the clock accurate afterward.

The check also measures host-clock agreement, not the latency between photon
arrival, camera exposure, driver delivery, and STVID's timestamp call. Camera
and driver latency must be characterized separately when sub-frame timing
accuracy is required.

## Choosing time servers

Prefer, in order:

1. A well-maintained observatory, organization, or network-provider NTP server
   reached over a short and stable network path.
2. The same reliable servers used by the host's synchronization daemon, making
   differences easier to interpret.
3. A reputable public NTP service or the NTP Pool.

For a general installation, `pool.ntp.org` selects nearby volunteer servers.
Several independently resolved pool names can be configured, for example:

```ini
servers = 0.pool.ntp.org, 1.pool.ntp.org, 2.pool.ntp.org
```

Regional names such as `0.europe.pool.ntp.org` are also available. The NTP Pool
advises ordinary clients not to use excessive numbers of volunteer servers and
suggests a local or ISP server when a good one is available. See the
[NTP Pool usage guidance](https://www.ntppool.org/en/use.html).

Google Public NTP is available through `time.google.com`, but it serves
leap-smeared time. Do not mix it with servers that use conventional leap-second
handling: the services can intentionally disagree during a smear window. See
the [Google Public NTP FAQ](https://developers.google.com/time/faq).

For meaningful trends, keep the configured server policy stable. A pool name
can resolve to different physical servers over time, so a change in offset or
delay may reflect a different server or route rather than a change in the local
clock. The `peer` address in each JSONL sample records which endpoint replied.

Public NTP normally uses UDP port 123. DNS failure, firewalls, captive portals,
or networks that block UDP 123 will produce failed samples. Do not interpret
that alone as proof that the local clock is wrong.

## Reviewing the log

Each line in `timecheck.log` is a complete record with a UTC timestamp, host,
platform, thresholds, summary, individual NTP samples, and raw clock-service
diagnostics. For example, this Python snippet prints a compact history:

```python
import json

with open("timecheck.log", encoding="utf-8") as stream:
    for line in stream:
        record = json.loads(line)
        summary = record["summary"]
        print(
            record["timestamp_utc"],
            record["status"],
            summary["median_offset_ms"],
            summary["offset_jitter_ms"],
            summary["median_delay_ms"],
        )
```

Look for:

- increasing absolute median offset;
- increasing jitter or network delay;
- gaps in `timestamp_utc`, indicating that scheduled checks did not run;
- repeated query failures or changes in responding peer addresses;
- `Not synchronised`, stale updates, bad leap status, or loss of the selected
  source in the local-service diagnostics.

The script appends indefinitely and does not rotate the log. Use the operating
system's normal log rotation or archival facilities if file growth becomes
material.

### Plotting trends

The headless-safe analysis utility reads the JSONL log and creates three PNG
figures under the Git-ignored `graphics/timecheck/` directory:

```console
python tools/timecheck_analysis.py timecheck.log
```

- `clock-offset.png` shows median signed offset, maximum absolute offset, and
  the configured warning and critical thresholds.
- `ntp-quality.png` shows network delay, offset jitter, and successful-query
  percentage.
- `check-status.png` shows status changes and check runtime.

Matplotlib's non-interactive backend is used by default, so this works on a
headless Raspberry Pi or Ubuntu host. On a desktop, add `--show` to display the
figures after saving them:

```console
python tools/timecheck_analysis.py timecheck.log --show
```

Logs containing several hosts can be filtered, as can long time ranges:

```console
python tools/timecheck_analysis.py timecheck.log --host observatory-pi --days 30
python tools/timecheck_analysis.py timecheck.log --output-dir /srv/stvid/time-plots
```

Malformed or partially written log lines are reported and skipped without
discarding the remaining history.

## Scheduling regular checks

Run checks often enough to reveal degradation before an observing session. A
five-minute interval is a reasonable initial operational setting; stations can
adjust it after measuring their clock stability. Avoid very aggressive polling
of public volunteer servers.

Example cron entry, using absolute paths:

```cron
*/5 * * * * cd /opt/STVID && /opt/STVID/.venv/bin/python /opt/STVID/timecheck.py -c /opt/STVID/configuration.ini >/dev/null 2>&1
```

Because the JSONL report is written by the script, redirecting terminal output
does not discard the machine-readable result. During initial setup, retain or
inspect terminal output so operational errors are visible.

On Windows, create a Task Scheduler task that runs the desired Python
executable with arguments similar to:

```text
C:\path\to\STVID\timecheck.py -c C:\path\to\STVID\configuration.ini
```

Set the task's working directory to the STVID directory, or configure an
absolute `log_file` path.

## Responding to a warning or critical result

First repeat the check. A single result can be distorted by transient network
congestion. Then compare several servers and inspect the local-service output.
Do not manually step the clock while STVID is acquiring imagery.

### Ubuntu and Raspberry Pi OS

Determine which service is active before changing anything:

```console
timedatectl status
systemctl status chrony
systemctl status systemd-timesyncd
```

Only one synchronization daemon should control the clock. Recent Ubuntu
installations commonly use Chrony, while upgraded or older installations may
still use systemd-timesyncd. Ubuntu describes these variants in its
[time-synchronization documentation](https://ubuntu.com/server/docs/about-time-synchronisation/).

For Chrony, inspect its state with:

```console
chronyc tracking
chronyc sources -v
chronyc activity
```

After correcting configuration or connectivity, wait for convergence:

```console
chronyc waitsync 60 0.01
```

This example waits for synchronization with no more than approximately 10 ms
of remaining correction. Chrony normally slews the clock rather than jumping
it. `chronyc makestep` forces a discontinuity and can seriously affect running
software; reserve stepping for startup or maintenance when acquisition and
other time-sensitive processes are stopped. See the
[Chrony command documentation](https://chrony-project.org/doc/4.4/chronyc.html).

For systemd-timesyncd, inspect and enable synchronization with:

```console
timedatectl timesync-status
sudo timedatectl set-ntp true
```

If there is no response, check DNS, Internet connectivity, UDP port 123, the
configured server names, and service logs. Re-run `timecheck.py` after the
daemon reports synchronization.

### Windows

Use an Administrator Command Prompt or PowerShell window for changes. Start
with read-only inspection:

```console
w32tm /query /status /verbose
w32tm /query /peers
w32tm /query /configuration
```

Confirm that **Settings > Time & language > Date & time > Set time
automatically** is enabled. If the Windows Time service is configured but has
not recently synchronized, request rediscovery and synchronization:

```console
w32tm /resync /rediscover
```

Then repeat `w32tm /query /status /verbose` and `timecheck.py`. Microsoft
documents the status fields and W32Time controls in
[Windows Time Service tools and settings](https://learn.microsoft.com/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings).

If resynchronization reports that no time data is available, check the peer
name, DNS resolution, UDP port 123, firewall policy, and whether the selected
server actually provides NTP. Domain-joined Windows systems may be required to
follow the Active Directory time hierarchy; do not replace that policy with a
public server without consulting the domain administrator. Microsoft's
[W32Time resynchronization troubleshooting](https://learn.microsoft.com/troubleshoot/windows-server/active-directory/error-message-run-w32tm-resync-no-time-data-available)
describes common causes.

## Operational safety

- Run and review a check before acquisition starts.
- Prefer gradual daemon-controlled correction to manual clock changes.
- Stop acquisition before any action that might step the clock.
- After correction, wait for the daemon to stabilize and obtain several
  successful checks before relying on new FITS timestamps.
- Preserve `timecheck.log` with the observation archive so later processing can
  assess the clock condition around each observing session.
- Treat a sustained trend as more significant than one isolated Internet NTP
  sample.
