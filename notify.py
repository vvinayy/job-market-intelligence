"""Windows toast notifications for unattended runs.

The liveness check and the scrape both run from Task Scheduler with their
output going to a log file. A log nobody reads is how three days of snapshots
were lost once already -- a failure that prints loudly into a file is still
silent. This surfaces the outcome where it will actually be seen.

Uses the WinRT toast API through PowerShell. No module to install: BurntToast
is not present on this machine, but Windows.UI.Notifications is built in.

Notifying is never allowed to affect the run. Every failure here is swallowed
-- a missing toast is a cosmetic problem, and a run that succeeded must not
report failure because a notification did not render.
"""

import subprocess
from xml.sax.saxutils import escape

# The registered PowerShell shortcut. A toast needs an AppId that Windows
# already knows about; borrowing this one avoids registering our own.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

TIMEOUT_SECONDS = 15

_SCRIPT = """
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime]
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml(@'
{xml}
'@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def preview() -> None:
    """Preview both toast styles, labelled so they cannot be mistaken for real.

    Exists because a hand-written sample alarm carrying invented numbers is
    indistinguishable from a genuine one, and a false alarm nobody can identify
    is worse than no alarm. The [TEST] prefix is added here, not by the caller,
    so it cannot be left off.
    """
    toast("[TEST] Liveness check done",
          "Sample only - no run happened. Real ones report actual counts.")
    toast("[TEST] Liveness check ABORTED",
          "Sample only - no run happened, nothing was written. "
          "A real abort means the run stopped and will retry tomorrow.",
          urgent=True)


def toast(title: str, message: str, urgent: bool = False) -> bool:
    """Show a Windows toast. Returns whether it was sent, never raises.

    `urgent` keeps it on screen until dismissed, for the cases worth
    interrupting someone over -- an aborted run, a failed write.
    """
    duration = ' duration="long"' if urgent else ""
    xml = (
        f'<toast{duration}><visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text>"
        f"<text>{escape(message)}</text>"
        f"</binding></visual></toast>"
    )
    script = _SCRIPT.format(xml=xml, app_id=APP_ID)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:                      # noqa: BLE001 - cosmetic, never fatal
        return False
