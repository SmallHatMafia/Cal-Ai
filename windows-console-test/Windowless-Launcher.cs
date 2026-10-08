using System;
using System.Diagnostics;
using System.IO;

// Compiled as WindowsApplication: the task itself cannot allocate a console.
public static class WindowlessLauncher {
    public static int Main(string[] args) {
        if (args.Length != 1 || (args[0] != "sync" && args[0] != "watch")) return 2;
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, args[0] == "sync" ? "Run-Silent.ps1" : "Watch-Background.ps1");
            if (!File.Exists(script)) return 3;
            var info = new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using (var child = Process.Start(info)) {
                child.StandardInput.Close();
                // Drain both streams without retaining credentials or raw errors.
                child.OutputDataReceived += (sender, data) => {};
                child.ErrorDataReceived += (sender, data) => {};
                child.BeginOutputReadLine(); child.BeginErrorReadLine();
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch { return 1; }
    }
}
