using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher {
    // Windows CommandLineToArgvW quoting, including quotes and trailing slashes.
    private static string Quote(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args) {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string runtime = Path.Combine(root, "runtime");
            var arguments = new StringBuilder("-I -B " + Quote(Path.Combine(root, "bootstrap.py")));
            foreach (string arg in args) arguments.Append(" " + Quote(arg));
            var info = new ProcessStartInfo(Path.Combine(runtime, "pythonw.exe"), arguments.ToString());
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.EnvironmentVariables.Remove("PYTHONHOME");
            info.EnvironmentVariables.Remove("PYTHONPATH");
            info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            info.EnvironmentVariables["PATH"] = runtime + ";" + Path.Combine(runtime, "Library", "bin") + ";" + Path.Combine(runtime, "Scripts") + ";" + Environment.GetEnvironmentVariable("PATH");
            using (var process = Process.Start(info)) {
                process.WaitForExit();
                return process.ExitCode;
            }
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Démarrage de CADastrophe", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
