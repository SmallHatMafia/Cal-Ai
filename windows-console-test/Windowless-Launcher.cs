using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using System.Management.Automation;
using System.Management.Automation.Host;
using System.Management.Automation.Runspaces;

// WindowsApplication plus an in-process engine: no PowerShell ConsoleHost.
public static class WindowlessLauncher {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern uint GetLongPathName(string path, StringBuilder output, uint size);
    [DllImport("kernel32.dll")] static extern uint GetConsoleProcessList(uint[] ids, uint count);
    public static bool ConsoleAttached() { return GetConsoleProcessList(new uint[32], 32) > 0; }
    public static string Canonical(string path) {
        var output=new StringBuilder(32768);
        uint count=GetLongPathName(Path.GetFullPath(path), output, 32768);
        if(count==0 || count>=32768) throw new IOException("TASK_PATH_UNAVAILABLE");
        return output.ToString().TrimEnd((char)92);
    }
    private sealed class SilentHost : PSHost {
        private readonly Guid id = Guid.NewGuid();
        public int ExitCode; public bool Exiting;
        public override Guid InstanceId { get { return id; } }
        public override string Name { get { return "WindowlessBackground"; } }
        public override Version Version { get { return new Version(1, 0); } }
        public override CultureInfo CurrentCulture { get { return CultureInfo.CurrentCulture; } }
        public override CultureInfo CurrentUICulture { get { return CultureInfo.CurrentUICulture; } }
        public override PSHostUserInterface UI { get { return null; } }
        public override void SetShouldExit(int code) { ExitCode = code; Exiting = true; }
        public override void NotifyBeginApplication() {}
        public override void NotifyEndApplication() {}
        public override void EnterNestedPrompt() { throw new InvalidOperationException("INTERACTIVE_PROMPT_FORBIDDEN"); }
        public override void ExitNestedPrompt() {}
    }
    private static void TestFailure(string detail) {
        if(Environment.GetEnvironmentVariable("WINDOWLESS_TEST_DIAGNOSTICS")=="1")
            File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"test-failure.txt"),detail);
    }
    public static int Main(string[] args) {
        if (args.Length != 1 || (args[0] != "sync" && args[0] != "watch" && args[0] != "host")) return 2;
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string name = args[0] == "sync" ? "Run-Silent.ps1" : args[0] == "watch" ? "Watch-Background.ps1" : "Background-Host.ps1";
            string script = Path.Combine(root, name);
            if (!File.Exists(script)) return 3;
            Directory.SetCurrentDirectory(root);
            var host = new SilentHost();
            var state = InitialSessionState.CreateDefault();
            state.ExecutionPolicy = Microsoft.PowerShell.ExecutionPolicy.Bypass;
            using (var runspace = RunspaceFactory.CreateRunspace(host, state)) {
                runspace.Open();
                using (var shell = PowerShell.Create()) {
                    shell.Runspace = runspace;
                    shell.AddCommand(script);
                    // Only the private host's existing Node pipe carries readiness/exit data.
                    if (args[0] == "host") {
                        var output = new PSDataCollection<PSObject>();
                        output.DataAdded += (sender, data) => { Console.Out.WriteLine(output[data.Index].ToString()); Console.Out.Flush(); };
                        var running = shell.BeginInvoke<PSObject, PSObject>(null, output);
                        shell.EndInvoke(running);
                    } else { shell.Invoke(); }
                    if(shell.HadErrors) { foreach(var error in shell.Streams.Error) TestFailure(error.ToString()+" | "+error.FullyQualifiedErrorId); }
                    var exit=runspace.SessionStateProxy.GetVariable("LASTEXITCODE");
                    return host.Exiting ? host.ExitCode : shell.HadErrors ? 1 : exit is int ? (int)exit : 0;
                }
            }
        } catch(Exception error) { TestFailure(error.ToString()); return 1; }
    }
}
