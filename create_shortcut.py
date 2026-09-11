import subprocess

def create_desktop_shortcut():
    ps_script = """
    $desktop = [Environment]::GetFolderPath('Desktop')
    $target_exe = 'd:\\antigravityProject\\gemini-quota-monitor\\dist\\GeminiQuotaMonitor.exe'
    $WshShell = New-Object -comObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("$desktop\\Gemini用量监控.lnk")
    $Shortcut.TargetPath = $target_exe
    $Shortcut.WorkingDirectory = 'd:\\antigravityProject\\gemini-quota-monitor\\dist'
    $Shortcut.Description = 'Gemini 5小时与周用量监控工具'
    $Shortcut.Save()
    Write-Host "Created shortcut successfully at: $desktop\\Gemini用量监控.lnk"
    """

    res = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True)
    print("STDOUT:", res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)

if __name__ == "__main__":
    create_desktop_shortcut()
